"""Read-only local FoodData Central catalogue. No network access at query time."""

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_TYPES = ("Foundation", "SR Legacy")
SCHEMA_VERSION = 2


class FoodDataError(ValueError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class LocalFoodData:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.progress = lambda message: None

    @contextmanager
    def connection(self):
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise FoodDataError("USDA 本地库版本不兼容，请重新导入")
            yield db
        except sqlite3.Error as exc:
            raise FoodDataError("USDA 本地库未安装、不可读或已损坏，请运行导入命令", {
                "error_type": type(exc).__name__, "backend": "local",
            }) from None
        finally:
            if db is not None:
                db.close()

    def status(self) -> dict:
        try:
            with self.connection() as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise FoodDataError("USDA 本地库完整性检查失败")
                datasets = [dict(row) for row in db.execute("SELECT * FROM datasets ORDER BY data_type")]
                count = db.execute("SELECT count(*) FROM foods").fetchone()[0]
                indexed = db.execute("SELECT count(*) FROM foods_fts").fetchone()[0]
                if (len(datasets) != 2 or {d['data_type'] for d in datasets} != set(DATA_TYPES)
                        or sum(d['record_count'] for d in datasets) != count or indexed != count):
                    raise FoodDataError("USDA 本地库不完整，请重新导入两份数据")
                return {"ready": True, "path": str(self.path), "count": count, "datasets": datasets}
        except FoodDataError as exc:
            return {"ready": False, "path": str(self.path), "error": str(exc), "count": 0, "datasets": []}

    def require_ready(self):
        result = self.status()
        if not result["ready"]:
            raise FoodDataError(result["error"])
        return result

    def list_datasets(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM datasets ORDER BY data_type")]

    def get_raw_food(self, fdc_id: str) -> dict:
        return self.food(fdc_id)

    def get_food(self, fdc_id: str):
        from .records import FoodRecord
        from .nutrients import parse_nutrients

        raw = self.get_raw_food(fdc_id)
        return FoodRecord(fdc_id=str(raw["fdcId"]), description=raw["description"],
                          data_type=raw["dataType"], nutrients_per_100g=parse_nutrients(raw),
                          provenance=self.provenance(fdc_id))

    def search(self, query: str, limit: int = 15) -> list[dict]:
        if not query.strip():
            raise ValueError("请输入 USDA 搜索词（建议英文食材名及生熟状态）")
        if not 1 <= limit <= 100:
            raise ValueError("候选数量必须为 1–100")
        tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold(), re.UNICODE)))[:32]
        if not tokens:
            return []
        terms = [f'"{token}"' for token in tokens]
        # Preserve identity during relaxed retrieval: a rare preparation word
        # must not make "peeled potato" return only peeled fruits.
        expressions = [" AND ".join(terms)]
        if "peeled" in tokens:
            expressions.append(" AND ".join('"without skin"' if t == "peeled" else f'"{t}"' for t in tokens))
        preparation = {"peeled", "unpeeled", "skinless", "boneless", "without", "with", "skin"}
        relaxed = [t for t in tokens if t not in preparation]
        if relaxed and relaxed != tokens:
            expressions.append(" AND ".join(f'"{t}"' for t in relaxed))
        identity = [t for t in relaxed if t not in {"raw", "cooked", "fresh", "unprepared"}]
        fallback = identity or tokens
        expressions.extend([" AND ".join(f'"{t}"' for t in fallback), " OR ".join(f'"{t}"' for t in fallback)])
        candidates = []
        seen = set()
        with self.connection() as db:
            for expression in dict.fromkeys(expressions):
                rows = db.execute(
                    "SELECT f.fdc_id,f.description,f.data_type FROM foods_fts "
                    "JOIN foods f ON f.fdc_id=foods_fts.rowid WHERE foods_fts MATCH ? "
                    "ORDER BY bm25(foods_fts), CASE f.data_type WHEN 'Foundation' THEN 0 ELSE 1 END, f.fdc_id LIMIT ?",
                    (expression, limit),
                )
                for row in rows:
                    if row["fdc_id"] not in seen:
                        seen.add(row["fdc_id"])
                        candidates.append({"fdcId": row["fdc_id"], "description": row["description"],
                                           "dataType": row["data_type"]})
                if len(candidates) >= limit:
                    break
        return candidates[:limit]

    def food(self, fdc_id: str, *, refresh: bool = False) -> dict:
        # refresh retains its historical signature; reads always use the installed dataset.
        if not str(fdc_id).isdigit() or not 0 < int(fdc_id) <= 2**63 - 1:
            raise ValueError("FDC ID 必须是有效正整数")
        with self.connection() as db:
            row = db.execute("SELECT raw_json FROM foods WHERE fdc_id=?", (int(fdc_id),)).fetchone()
            if row is None:
                raise FoodDataError(f"本地 USDA 库中不存在 FDC ID {fdc_id}", {"fdc_id": str(fdc_id)})
            return json.loads(row[0])

    def provenance(self, fdc_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT d.* FROM datasets d JOIN foods f ON f.data_type=d.data_type "
                             "WHERE f.fdc_id=?", (int(fdc_id),)).fetchone()
            if row is None:
                raise FoodDataError("本地 USDA 来源记录不存在")
            return {"backend": "local", **dict(row)}
