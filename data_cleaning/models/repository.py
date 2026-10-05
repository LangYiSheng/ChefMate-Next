"""Short-lived SQLite connections, transactional mutations and immutable source history."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from chefmate_contracts import default_vocabulary

from data_cleaning.config import ROOT
from data_cleaning.schemas.recipe import Recipe, Status
from data_cleaning.services.logging_service import log_event


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Repository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if isinstance(exc, sqlite3.Error):
                log_event("database_error", error=exc)
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            migrations = sorted((ROOT / "models/migrations").glob("*.sql"))
            if version > len(migrations):
                raise ValueError("数据库版本较新，请升级程序，禁止降级打开")
            for number, migration in enumerate(migrations, 1):
                if number > version:
                    # execute individually: executescript would implicitly commit.
                    for sql in migration.read_text(encoding="utf-8").split(";"):
                        if sql.strip():
                            db.execute(sql)
                    db.execute(f"PRAGMA user_version={number}")
            self._seed(db)

    @staticmethod
    def _seed(db: sqlite3.Connection) -> None:
        enums = default_vocabulary()
        db.executemany(
            "INSERT OR IGNORE INTO enum_options(kind,code,label) VALUES(?,?,?)",
            [(entry.kind.value, entry.code, entry.label) for entry in enums],
        )

    @staticmethod
    def _audit(db: sqlite3.Connection, recipe_id: str | None, action: str, data: Any) -> None:
        db.execute(
            "INSERT INTO audit_events(recipe_id,action,data_json,created_at) VALUES(?,?,?,?)",
            (recipe_id, action, encode(data), now()),
        )

    def ingest(
        self, recipe_id: str, source: str, digest: str, raw: bytes, text: str | None, error: str | None = None
    ) -> bool:
        with self.connection(write=True) as db:
            previous = db.execute("SELECT * FROM recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
            if previous and previous["content_hash"] == digest:
                return False
            revision = previous["revision"] + 1 if previous else 1
            stamp = now()
            db.execute(
                "INSERT INTO source_versions VALUES(?,?,?,?,?,?,?)",
                (recipe_id, revision, source, digest, raw, text, stamp),
            )
            db.execute(
                """INSERT INTO recipes(recipe_id,source,revision,content_hash,name,
                       extract_status,extract_error,status,error_message,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(recipe_id) DO UPDATE SET
                       revision=excluded.revision,content_hash=excluded.content_hash,
                       extract_status=excluded.extract_status,extract_error=excluded.extract_error,
                       status=excluded.status,error_message=excluded.error_message,
                       name=excluded.name,extracted_json=NULL,review_note=NULL,reviewed_at=NULL,
                       updated_at=excluded.updated_at""",
                (
                    recipe_id,
                    source,
                    revision,
                    digest,
                    Path(source).stem,
                    Status.FAILED if error else Status.PENDING,
                    error,
                    Status.PENDING_REVIEW if error else Status.PENDING,
                    error,
                    stamp,
                    stamp,
                ),
            )
            db.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,))
            self._audit(db, recipe_id, "source_import", {"revision": revision, "content_hash": digest})
            return True

    def list_recipes(self, status: str = "", search: str = "") -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    """SELECT recipe_id,name,source,revision,extract_status,
                    status,extract_error,reviewed_at,updated_at FROM recipes
                    WHERE (?='' OR status=?) AND instr(lower(name),lower(?))>0 ORDER BY name,recipe_id""",
                    (status, status, search),
                )
            ]

    def get_recipe(self, recipe_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                """SELECT r.*,s.raw_text FROM recipes r JOIN source_versions s
                ON r.recipe_id=s.recipe_id AND r.revision=s.revision WHERE r.recipe_id=?""",
                (recipe_id,),
            ).fetchone()
            if row is None:
                raise ValueError("菜谱已不存在，请刷新")
            return dict(row)

    def history(self, recipe_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM audit_events WHERE recipe_id=? ORDER BY id DESC", (recipe_id,))
            ]

    def source_history(self, recipe_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT revision,content_hash,raw_text,created_at FROM source_versions WHERE recipe_id=? ORDER BY revision DESC",
                    (recipe_id,),
                )
            ]

    @staticmethod
    def _check_current(db: sqlite3.Connection, recipe_id: str, expected_updated_at: str) -> None:
        row = db.execute("SELECT updated_at FROM recipes WHERE recipe_id=?", (recipe_id,)).fetchone()
        if not row or row[0] != expected_updated_at:
            raise ValueError("数据已被其他任务修改，请刷新后重试")

    def save_extraction(
        self,
        recipe: Recipe,
        expected_updated_at: str,
        *,
        action: str = "extract",
        review_status: Status = Status.PENDING_REVIEW,
        note: str = "",
    ) -> None:
        if review_status not in (Status.PENDING_REVIEW, Status.APPROVED, Status.REJECTED):
            raise ValueError("不支持的审核状态")
        with self.connection(write=True) as db:
            self._check_current(db, recipe.recipe_id, expected_updated_at)
            source = db.execute("SELECT source FROM recipes WHERE recipe_id=?", (recipe.recipe_id,)).fetchone()[0]
            if source != recipe.source:
                raise ValueError("不可修改来源标识")
            stamp = now()
            db.execute(
                """UPDATE recipes SET name=?,extracted_json=?,extract_status=?,extract_error=NULL,
                status=?,error_message=NULL,review_note=?,reviewed_at=?,updated_at=? WHERE recipe_id=?""",
                (
                    recipe.name,
                    recipe.model_dump_json(),
                    Status.PENDING_REVIEW,
                    review_status,
                    note,
                    stamp if review_status in (Status.APPROVED, Status.REJECTED) else None,
                    stamp,
                    recipe.recipe_id,
                ),
            )
            db.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe.recipe_id,))
            for ordinal, item in enumerate(recipe.ingredients):
                db.execute(
                    "INSERT OR IGNORE INTO raw_ingredients(raw_name,created_at,updated_at) VALUES(?,?,?)",
                    (item.raw_name, stamp, stamp),
                )
                raw_id = db.execute("SELECT id FROM raw_ingredients WHERE raw_name=?", (item.raw_name,)).fetchone()[0]
                db.execute(
                    "INSERT INTO recipe_ingredients VALUES(?,?,?,?)",
                    (recipe.recipe_id, ordinal, raw_id, item.model_dump_json()),
                )
            self._audit(
                db,
                recipe.recipe_id,
                action,
                {"status": review_status, "review_note": note, "recipe": recipe.model_dump()},
            )

    def fail_extraction(self, recipe_id: str, expected_updated_at: str, error: str) -> None:
        with self.connection(write=True) as db:
            self._check_current(db, recipe_id, expected_updated_at)
            # Old versions remain in audit_events; stale active results cannot be exported.
            db.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (recipe_id,))
            db.execute(
                """UPDATE recipes SET extract_status=?,extract_error=?,status=?,error_message=?,
                extracted_json=NULL,reviewed_at=NULL,review_note=NULL,updated_at=? WHERE recipe_id=?""",
                (Status.FAILED, error, Status.PENDING_REVIEW, error, now(), recipe_id),
            )
            self._audit(db, recipe_id, "extract_failed", {"error": error})

    def reject(self, recipe_id: str, expected_updated_at: str, note: str) -> None:
        """Allow rejecting an unparseable recipe without inventing valid ingredients."""
        with self.connection(write=True) as db:
            self._check_current(db, recipe_id, expected_updated_at)
            stamp = now()
            db.execute(
                "UPDATE recipes SET status=?,review_note=?,reviewed_at=?,updated_at=? WHERE recipe_id=?",
                (Status.REJECTED, note, stamp, stamp, recipe_id),
            )
            self._audit(db, recipe_id, "human_reject", {"review_note": note})

    def ingredient_list(self, search: str = "", status: str = "", sort: str = "count") -> list[dict]:
        order = "occurrences DESC,i.raw_name" if sort == "count" else "i.raw_name"
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    f"""SELECT i.id,i.raw_name,i.status,i.canonical_ingredient_id,
                COUNT(*) AS occurrences,COUNT(DISTINCT ri.recipe_id) AS recipe_count
                FROM raw_ingredients i JOIN recipe_ingredients ri ON ri.raw_ingredient_id=i.id
                WHERE instr(lower(i.raw_name),lower(?))>0 AND (?='' OR i.status=?)
                GROUP BY i.id ORDER BY {order}""",
                    (search, status, status),
                )
            ]

    def ingredient_recipes(self, raw_id: int) -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    """SELECT DISTINCT r.recipe_id,r.name,r.status
                FROM recipes r JOIN recipe_ingredients ri ON ri.recipe_id=r.recipe_id
                WHERE ri.raw_ingredient_id=? ORDER BY r.name""",
                    (raw_id,),
                )
            ]

    def enums(self) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM enum_options ORDER BY kind,code")]

    def dashboard(self) -> dict[str, int]:
        with self.connection() as db:
            total = db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            extracted = db.execute("SELECT COUNT(*) FROM recipes WHERE extracted_json IS NOT NULL").fetchone()[0]
            failed = db.execute("SELECT COUNT(*) FROM recipes WHERE extract_status=?", (Status.FAILED,)).fetchone()[0]
            approved = db.execute("SELECT COUNT(*) FROM recipes WHERE status=?", (Status.APPROVED,)).fetchone()[0]
            review = db.execute("SELECT COUNT(*) FROM recipes WHERE status=?", (Status.PENDING_REVIEW,)).fetchone()[0]
            raw = db.execute("SELECT COUNT(DISTINCT raw_ingredient_id) FROM recipe_ingredients").fetchone()[0]
            return {
                "菜谱总数": total,
                "已提取": extracted,
                "提取失败": failed,
                "原始食材数": raw,
                "待匹配食材": raw,
                "已审核菜谱": approved,
                "待审核菜谱": review,
            }

    def export_snapshot(self, approved_only: bool = True) -> dict[str, Any]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM recipes WHERE extracted_json IS NOT NULL AND (?=0 OR status=?) ORDER BY recipe_id",
                (int(approved_only), Status.APPROVED),
            ).fetchall()
            recipes = []
            names: set[str] = set()
            for row in rows:
                recipe = Recipe.model_validate_json(row["extracted_json"]).model_dump()
                names.update(i["raw_name"] for i in recipe["ingredients"])
                recipe["provenance"] = {
                    key: row[key]
                    for key in (
                        "revision",
                        "content_hash",
                        "status",
                        "review_note",
                        "reviewed_at",
                        "created_at",
                        "updated_at",
                    )
                }
                source = db.execute(
                    "SELECT raw_text FROM source_versions WHERE recipe_id=? AND revision=?",
                    (row["recipe_id"], row["revision"]),
                ).fetchone()
                recipe["raw_source_text"] = source[0]
                recipes.append(recipe)
            raw = [
                dict(r) for r in db.execute("SELECT * FROM raw_ingredients ORDER BY raw_name") if r["raw_name"] in names
            ]
            enums = [dict(r) for r in db.execute("SELECT * FROM enum_options ORDER BY kind,code")]
            return {
                "recipes": recipes,
                "raw_ingredients": raw,
                "ingredients": [],
                "ingredient_aliases": [],
                "fixed_mappings": [],
                "enums": enums,
            }

    def clear(self, confirmation: str) -> None:
        if confirmation != "CLEAR":
            raise ValueError("必须准确输入 CLEAR 才可清空")
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM batch_runs WHERE status IN ('queued','running','stopping')").fetchone():
                raise ValueError("请先停止批量任务，再清空数据库")
            for table in (
                "converted_recipes",
                "batch_items",
                "batch_runs",
                "recipe_profiles",
                "fixed_mappings",
                "ingredient_aliases",
                "recipe_ingredients",
                "raw_ingredients",
                "standard_ingredients",
                "usda_cache",
                "service_calls",
                "settings",
                "recipes",
                "source_versions",
                "audit_events",
                "enum_options",
            ):
                db.execute(f"DELETE FROM {table}")
            self._seed(db)
            self._audit(db, None, "database_cleared", {})
        log_event("database_cleared")
