"""Canonical ingredients, mapping decisions and vocabulary transactions."""

import json
import re
import sqlite3

from chefmate_contracts import Difficulty

from data_cleaning.models.repository import Repository, encode, now
from data_cleaning.schemas.cleaning import StandardIngredient, TagThresholds


class CatalogRepository(Repository):
    @staticmethod
    def invalidate(db: sqlite3.Connection) -> None:
        db.execute(
            "UPDATE recipe_profiles SET status='pending_review',reviewed_at=NULL,updated_at=? WHERE status<>'rejected'",
            (now(),),
        )

    @staticmethod
    def check_enum(db: sqlite3.Connection, kind: str, codes: list[str]) -> None:
        for code in codes:
            if not db.execute(
                "SELECT 1 FROM enum_options WHERE kind=? AND code=? AND status='approved'", (kind, code)
            ).fetchone():
                raise ValueError(f"未启用的选项：{kind}/{code}")

    def standards(self, approved_only: bool = False) -> list[dict]:
        with self.connection() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM standard_ingredients WHERE (?=0 OR status='approved') ORDER BY canonical_name",
                    (int(approved_only),),
                )
            ]
            for row in rows:
                row["aliases"] = [
                    r[0]
                    for r in db.execute(
                        "SELECT alias FROM ingredient_aliases WHERE ingredient_id=? ORDER BY alias",
                        (row["ingredient_id"],),
                    )
                ]
                row["allergens"] = json.loads(row.pop("allergens_json"))
            return rows

    def standard(self, ingredient_id: str) -> dict:
        return next((r for r in self.standards() if r["ingredient_id"] == ingredient_id), {})

    @staticmethod
    def require_standard(db: sqlite3.Connection, ingredient_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM standard_ingredients WHERE ingredient_id=? AND status='approved'", (ingredient_id,)
        ).fetchone()
        if not row:
            raise ValueError("标准食材不存在或尚未审核通过")
        return row

    def save_standard(self, item: StandardIngredient, expected: str | None = None) -> None:
        item = StandardIngredient.model_validate(item.model_dump())
        if not item.canonical_name.strip() or any(not a.strip() for a in item.aliases):
            raise ValueError("食材名和别名不能为空")
        with self.connection(write=True) as db:
            self.check_enum(db, "category", [item.category])
            self.check_enum(db, "allergen", item.allergens)
            old = db.execute(
                "SELECT * FROM standard_ingredients WHERE ingredient_id=?", (item.ingredient_id,)
            ).fetchone()
            if old and expected != old["updated_at"]:
                raise ValueError("标准食材已存在或被修改，请重新载入")
            for name in [item.canonical_name, *item.aliases]:
                conflict = db.execute(
                    "SELECT ingredient_id FROM standard_ingredients WHERE canonical_name=? AND ingredient_id<>? AND status<>'rejected' UNION SELECT ingredient_id FROM ingredient_aliases WHERE alias=? AND ingredient_id<>?",
                    (name, item.ingredient_id, name, item.ingredient_id),
                ).fetchone()
                if conflict:
                    raise ValueError(f"名称/别名冲突：{name}")
            stamp = now()
            db.execute(
                """INSERT INTO standard_ingredients(ingredient_id,canonical_name,category,status,allergens_json,review_note,reviewed_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(ingredient_id) DO UPDATE SET canonical_name=excluded.canonical_name,
                category=excluded.category,status=excluded.status,allergens_json=excluded.allergens_json,review_note=excluded.review_note,
                reviewed_at=excluded.reviewed_at,updated_at=excluded.updated_at""",
                (
                    item.ingredient_id,
                    item.canonical_name,
                    item.category,
                    item.status,
                    encode(item.allergens),
                    item.review_note,
                    stamp if item.status in ("approved", "rejected") else None,
                    stamp,
                    stamp,
                ),
            )
            db.execute("DELETE FROM ingredient_aliases WHERE ingredient_id=?", (item.ingredient_id,))
            db.executemany(
                "INSERT INTO ingredient_aliases VALUES(?,?,?)",
                [(a, item.ingredient_id, stamp) for a in set(item.aliases) if a != item.canonical_name],
            )
            db.execute(
                "UPDATE raw_ingredients SET status='pending_review',reviewed_at=NULL,updated_at=? WHERE canonical_ingredient_id=? AND match_method IN ('canonical','alias')",
                (stamp, item.ingredient_id),
            )
            if old and (
                old["canonical_name"] != item.canonical_name
                or old["category"] != item.category
                or item.status != "approved"
            ):
                db.execute(
                    "UPDATE standard_ingredients SET nutrition_match_status='pending_review',nutrition_reviewed_at=NULL WHERE ingredient_id=?",
                    (item.ingredient_id,),
                )
                db.execute(
                    "UPDATE raw_ingredients SET status='pending_review',reviewed_at=NULL,updated_at=? WHERE canonical_ingredient_id=?",
                    (stamp, item.ingredient_id),
                )
            self.invalidate(db)
            self._audit(db, None, "standard_saved", item.model_dump())

    def seed_standards(self) -> int:
        from data_cleaning.config import ROOT

        count = 0
        for item in json.loads((ROOT / "vocab/standard_ingredients.json").read_text(encoding="utf-8")):
            if not self.standard(item["ingredient_id"]):
                self.save_standard(StandardIngredient.model_validate(item))
                count += 1
        return count

    def raw_detail(self, raw_id: int) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM raw_ingredients WHERE id=?", (raw_id,)).fetchone()
            if not row:
                raise ValueError("原始食材已不存在")
            return dict(row)

    def match_raw(
        self,
        raw_id: int,
        ingredient_id: str | None,
        *,
        expected: str,
        status: str,
        method: str,
        confidence: float | None = None,
        reason: str = "",
        candidates: list | None = None,
        note: str = "",
        error: str | None = None,
        fixed: bool = False,
    ) -> None:
        if status not in ("approved", "pending_review", "rejected", "failed"):
            raise ValueError("不支持的匹配状态")
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM raw_ingredients WHERE id=?", (raw_id,)).fetchone()
            if not row or row["updated_at"] != expected:
                raise ValueError("食材已被其他任务修改")
            if ingredient_id:
                if status == "pending_review":
                    if not db.execute(
                        "SELECT 1 FROM standard_ingredients WHERE ingredient_id=? AND status<>'rejected'",
                        (ingredient_id,),
                    ).fetchone():
                        raise ValueError("标准食材不存在或已拒绝")
                else:
                    self.require_standard(db, ingredient_id)
            if status == "approved" and not ingredient_id:
                raise ValueError("通过审核必须选择标准食材")
            stamp = now()
            db.execute(
                """UPDATE raw_ingredients SET canonical_ingredient_id=?,status=?,match_method=?,confidence=?,
                match_reason=?,candidates_json=?,review_note=?,error_message=?,reviewed_at=?,updated_at=? WHERE id=?""",
                (
                    ingredient_id,
                    status,
                    method,
                    confidence,
                    reason,
                    encode(candidates or []),
                    note,
                    error,
                    stamp if status in ("approved", "rejected") else None,
                    stamp,
                    raw_id,
                ),
            )
            if fixed:
                if status != "approved":
                    raise ValueError("只有人工通过后才能设置固定映射")
                self._fixed(db, row["raw_name"], ingredient_id, "approved", note)
            self.invalidate(db)
            self._audit(
                db,
                None,
                "ingredient_match",
                {
                    "raw_id": raw_id,
                    "ingredient_id": ingredient_id,
                    "status": status,
                    "method": method,
                    "note": note,
                    "candidates": candidates,
                },
            )

    def fixed_mappings(self) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM fixed_mappings ORDER BY raw_name")]

    def _fixed(self, db: sqlite3.Connection, raw_name: str, ingredient_id: str, status: str, note: str) -> None:
        self.require_standard(db, ingredient_id)
        collision = db.execute(
            "SELECT ingredient_id FROM standard_ingredients WHERE canonical_name=? AND status='approved' UNION SELECT a.ingredient_id FROM ingredient_aliases a JOIN standard_ingredients s ON s.ingredient_id=a.ingredient_id WHERE a.alias=? AND s.status='approved'",
            (raw_name, raw_name),
        ).fetchall()
        if any(r[0] != ingredient_id for r in collision):
            raise ValueError("固定映射与优先的名称/别名精确匹配冲突，请先修正词表")
        stamp = now()
        db.execute(
            """INSERT INTO fixed_mappings VALUES(?,?,?,?,?,?) ON CONFLICT(raw_name) DO UPDATE SET
            ingredient_id=excluded.ingredient_id,status=excluded.status,review_note=excluded.review_note,updated_at=excluded.updated_at""",
            (raw_name, ingredient_id, status, note, stamp, stamp),
        )

    def save_fixed(self, raw_name: str, ingredient_id: str, enabled: bool, note: str = "") -> None:
        if not raw_name.strip():
            raise ValueError("原始名称不能为空")
        with self.connection(write=True) as db:
            self._fixed(db, raw_name, ingredient_id, "approved" if enabled else "rejected", note)
            # Existing automatic decisions must be recomputed; manual decisions remain authoritative.
            db.execute(
                "UPDATE raw_ingredients SET status='pending_review',updated_at=? WHERE raw_name=? AND match_method='fixed'",
                (now(), raw_name),
            )
            self.invalidate(db)
            self._audit(
                db,
                None,
                "fixed_saved",
                {"raw_name": raw_name, "target": ingredient_id, "enabled": enabled, "note": note},
            )

    def delete_fixed(self, raw_name: str) -> None:
        with self.connection(write=True) as db:
            old = db.execute("SELECT * FROM fixed_mappings WHERE raw_name=?", (raw_name,)).fetchone()
            db.execute("DELETE FROM fixed_mappings WHERE raw_name=?", (raw_name,))
            db.execute(
                "UPDATE raw_ingredients SET status='pending_review',updated_at=? WHERE raw_name=? AND match_method='fixed'",
                (now(), raw_name),
            )
            self.invalidate(db)
            self._audit(db, None, "fixed_deleted", dict(old) if old else {"raw_name": raw_name})

    def merge_raw(self, raw_ids: list[int], target: str, note: str = "") -> None:
        with self.connection(write=True) as db:
            destination = self.require_standard(db, target)
            for raw_id in set(raw_ids):
                row = db.execute("SELECT raw_name FROM raw_ingredients WHERE id=?", (raw_id,)).fetchone()
                if not row:
                    raise ValueError("原始食材不存在")
                self._fixed(db, row[0], target, "approved", note)
                existing = db.execute(
                    "SELECT ingredient_id FROM ingredient_aliases WHERE alias=?", (row[0],)
                ).fetchone()
                if existing and existing[0] != target:
                    raise ValueError("别名已属于其他标准食材，请先合并标准食材")
                if row[0] != destination["canonical_name"]:
                    db.execute("INSERT OR IGNORE INTO ingredient_aliases VALUES(?,?,?)", (row[0], target, now()))
                db.execute(
                    "UPDATE raw_ingredients SET canonical_ingredient_id=?,status='approved',match_method='human',review_note=?,reviewed_at=?,updated_at=? WHERE id=?",
                    (target, note, now(), now(), raw_id),
                )
            self.invalidate(db)
            self._audit(db, None, "raw_merged", {"raw_ids": raw_ids, "target": target, "note": note})

    def merge_standards(self, sources: list[str], target: str, note: str = "") -> None:
        with self.connection(write=True) as db:
            destination = self.require_standard(db, target)
            allergens = set(json.loads(destination["allergens_json"]))
            for source in set(sources) - {target}:
                old = self.require_standard(db, source)
                allergens.update(json.loads(old["allergens_json"]))
                db.execute("UPDATE ingredient_aliases SET ingredient_id=? WHERE ingredient_id=?", (target, source))
                db.execute(
                    "INSERT OR REPLACE INTO ingredient_aliases VALUES(?,?,?)", (old["canonical_name"], target, now())
                )
                db.execute(
                    "UPDATE fixed_mappings SET ingredient_id=?,updated_at=? WHERE ingredient_id=?",
                    (target, now(), source),
                )
                db.execute(
                    "UPDATE raw_ingredients SET canonical_ingredient_id=?,review_note=?,updated_at=? WHERE canonical_ingredient_id=?",
                    (target, note, now(), source),
                )
                db.execute(
                    "UPDATE standard_ingredients SET status='rejected',review_note=?,updated_at=? WHERE ingredient_id=?",
                    (f"合并到 {target}：{note}", now(), source),
                )
            # Different source foods may have different nutrition; target must be reviewed again.
            db.execute(
                "UPDATE standard_ingredients SET allergens_json=?,nutrition_match_status='pending_review',nutrition_reviewed_at=NULL,updated_at=? WHERE ingredient_id=?",
                (encode(sorted(allergens)), now(), target),
            )
            self.invalidate(db)
            self._audit(db, None, "standards_merged", {"sources": sources, "target": target, "note": note})

    def save_enum(self, kind: str, code: str, label: str, enabled: bool) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", code) or not label.strip():
            raise ValueError("选项 code 需为英文小写标识，显示名不能为空")
        if kind == "difficulty" and code not in set(Difficulty):
            raise ValueError("难度只允许 beginner / normal / advanced")
        with self.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM enum_options WHERE kind=?", (kind,)).fetchone():
                raise ValueError("未知选项类型")
            db.execute(
                "INSERT INTO enum_options VALUES(?,?,?,?) ON CONFLICT(kind,code) DO UPDATE SET label=excluded.label,status=excluded.status",
                (kind, code, label, "approved" if enabled else "rejected"),
            )
            self.invalidate(db)
            self._audit(db, None, "enum_saved", {"kind": kind, "code": code, "label": label, "enabled": enabled})

    def thresholds(self) -> TagThresholds:
        with self.connection() as db:
            row = db.execute("SELECT value_json FROM settings WHERE key='nutrition_thresholds'").fetchone()
            return TagThresholds.model_validate_json(row[0]) if row else TagThresholds()

    def save_thresholds(self, value: TagThresholds) -> None:
        value = TagThresholds.model_validate(value.model_dump())
        with self.connection(write=True) as db:
            db.execute(
                "INSERT INTO settings VALUES('nutrition_thresholds',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (value.model_dump_json(),),
            )
            self._audit(db, None, "nutrition_thresholds", value.model_dump())

    def service_call(
        self,
        service: str,
        purpose: str,
        entity_id: str,
        status: str,
        request: dict,
        response=None,
        error: str | None = None,
    ) -> None:
        from data_cleaning.services.redaction import redact

        with self.connection(write=True) as db:
            db.execute(
                "INSERT INTO service_calls(service,purpose,entity_id,status,request_json,response_json,error_message,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    service,
                    purpose,
                    entity_id,
                    status,
                    encode(redact(request)),
                    encode(redact(response)),
                    redact(error),
                    now(),
                ),
            )

    def service_history(self, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM service_calls ORDER BY id DESC LIMIT ?", (limit,))]

    def audit_history(self, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,))]
