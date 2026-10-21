"""Observed cooking history, stored independently from deletable chat sessions."""

import hashlib
import json
import math
import sqlite3
from datetime import date as Day, datetime, timedelta, timezone
from pathlib import Path

from chefmate_storage.queries import NUTRIENTS
from chefmate_workflow import WorkflowService

LOCAL_ZONE = timezone(timedelta(hours=8))
NUTRITION_NOTICE = "营养为整餐制作量；均分参考按实际用餐人数计算，不代表个人实际摄入。未知值不补零，估算保留标记。"


def _number(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _local_day(value):
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("Cooking times must include a timezone")
    return instant.astimezone(LOCAL_ZONE).date().isoformat()


def _bounded(value, maximum=366):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Invalid journal range")
    return value


def _nutrition(recipes, diners):
    totals = {}
    known = {}
    coverage = {}
    estimated_fields = set()
    for key in NUTRIENTS:
        values = [(recipe.get("nutrition") or {}).get("total", {}).get(key) for recipe in recipes]
        reliable = [value for value in values if _number(value)]
        coverage[key] = {"known": len(reliable), "total": len(values)}
        known[key] = round(sum(reliable), 3) if reliable else None
        totals[key] = known[key] if values and len(reliable) == len(values) else None
        if any((recipe.get("nutrition") or {}).get("estimated")
               or key in (recipe.get("nutrition") or {}).get("estimated_total_fields", []) for recipe in recipes):
            estimated_fields.add(key)
    return {"meal_total": totals, "known_total": known, "coverage": coverage,
            "per_diner_reference": {key: round(value / diners, 3) if value is not None and _number(diners) and diners > 0 else None
                                    for key, value in totals.items()},
            "estimated": bool(estimated_fields), "estimated_fields": sorted(estimated_fields),
            "incomplete": any(value is None for value in totals.values()), "notice": NUTRITION_NOTICE}


class JournalRepository:
    def __init__(self, path: Path | str, *, now=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(timezone.utc))
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS food_journal ("
                               "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, meal_id TEXT NOT NULL, "
                               "day TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, "
                               "status TEXT NOT NULL CHECK(status IN ('attempted','completed')), "
                               "snapshot TEXT NOT NULL, rating INTEGER, notes TEXT NOT NULL DEFAULT '', "
                               "UNIQUE(session_id,meal_id))")
            connection.execute("CREATE INDEX IF NOT EXISTS food_journal_day ON food_journal(day)")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def sync_session(self, state: dict) -> bool:
        """Record only observed starts/completions after the session transaction commits.

        The first completion freezes final recipe and nutrition evidence. Repeated reads,
        retries, or a later chat deletion cannot rewrite or duplicate that snapshot.
        """
        workspace = state.get("workspace") or {}
        if not workspace.get("meal_id") or not workspace.get("started_at"):
            return False
        snapshot = WorkflowService(None, lambda: workspace, None).meal_record()
        if snapshot is None:
            return False
        session_id = state["id"]
        identity = hashlib.sha256(f"{session_id}:{snapshot['meal_id']}".encode()).hexdigest()[:32]
        day = _local_day(snapshot["completed_at"] or snapshot["started_at"])
        snapshot["nutrition"] = _nutrition(snapshot["recipes"], snapshot["diners"])
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO food_journal(id,session_id,meal_id,day,started_at,completed_at,status,snapshot) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(session_id,meal_id) DO UPDATE SET "
                "day=excluded.day,completed_at=excluded.completed_at,status=excluded.status,snapshot=excluded.snapshot "
                "WHERE food_journal.status='attempted' AND excluded.status='completed'",
                (identity, session_id, snapshot["meal_id"], day, snapshot["started_at"], snapshot["completed_at"],
                 snapshot["status"], encoded))
            return cursor.rowcount > 0

    @staticmethod
    def _entry(row):
        return {"id": row["id"], "session_id": row["session_id"], "date": row["day"],
                **json.loads(row["snapshot"]), "rating": row["rating"], "notes": row["notes"]}

    def get(self, entry_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM food_journal WHERE id=?", (entry_id,)).fetchone()
            return self._entry(row) if row else None

    def entries(self, date: str | None = None, *, days: int = 30, limit: int = 100) -> list[dict]:
        _bounded(days)
        _bounded(limit, 1000)
        today = self._now().astimezone(LOCAL_ZONE).date()
        if date is not None:
            day = Day.fromisoformat(date).isoformat()
            condition, values = "day=?", (day,)
        else:
            condition, values = "day>=? AND day<=?", ((today - timedelta(days=days - 1)).isoformat(), today.isoformat())
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM food_journal WHERE {condition} "
                                      "ORDER BY COALESCE(completed_at,started_at) DESC,id LIMIT ?", (*values, limit))
            return [self._entry(row) for row in rows]

    def feedback(self, entry_id: str, *, rating: int | None = None, notes: str = "") -> dict:
        if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
            raise ValueError("请用 1 到 5 分评价这顿饭。")
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError("做饭心得请控制在 2000 字以内。")
        with self._connect() as connection:
            cursor = connection.execute("UPDATE food_journal SET rating=?,notes=? WHERE id=?", (rating, notes.strip(), entry_id))
            if cursor.rowcount == 0:
                raise KeyError(entry_id)
        return self.get(entry_id)

    def search(self, query: str = "", *, limit: int = 20) -> list[dict]:
        """Bounded literal search for history tools; no chat dump enters model context."""
        _bounded(limit, 100)
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Invalid journal query")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM food_journal WHERE snapshot LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\' "
                "ORDER BY COALESCE(completed_at,started_at) DESC,id LIMIT ?", (f"%{escaped}%", f"%{escaped}%", limit))
            return [{"id": entry["id"], "meal_id": entry["meal_id"], "session_id": entry["session_id"],
                     "date": entry["date"], "started_at": entry["started_at"], "completed_at": entry["completed_at"],
                     "status": entry["status"],
                     "recipes": [{"recipe_id": recipe["recipe_id"], "name": recipe["name"]} for recipe in entry["recipes"]],
                     "diners": entry["diners"], "rating": entry["rating"], "notes": entry["notes"][:500]}
                    for entry in map(self._entry, rows)]

    def summary(self, days: int = 7) -> dict:
        _bounded(days)
        # Do not silently truncate analytical totals at the UI list's paging limit.
        today = self._now().astimezone(LOCAL_ZONE).date()
        start = today - timedelta(days=days - 1)
        with self._connect() as connection:
            rows = [self._entry(row) for row in connection.execute(
                "SELECT * FROM food_journal WHERE day>=? AND day<=? ORDER BY day", (start.isoformat(), today.isoformat()))]
        completed = [entry for entry in rows if entry["status"] == "completed"]
        daily = []
        for offset in range(days):
            day = (start + timedelta(days=offset)).isoformat()
            entries = [entry for entry in rows if entry["date"] == day]
            cooked = [entry for entry in entries if entry["status"] == "completed"]
            daily.append({"date": day, "completed_meals": len(cooked), "attempted_meals": len(entries),
                          "dish_count": sum(len(entry["recipes"]) for entry in cooked),
                          "nutrition": self._aggregate_nutrition(cooked)})
        return {"period": {"from": start.isoformat(), "to": today.isoformat(), "days": days},
                "completed_meals": len(completed), "attempted_meals": len(rows),
                "completed_dishes": sum(len(entry["recipes"]) for entry in completed),
                "unique_recipes": len({recipe["recipe_id"] for entry in rows for recipe in entry["recipes"]}),
                "days": daily, "nutrition": self._aggregate_nutrition(completed), "notice": NUTRITION_NOTICE}

    @staticmethod
    def _aggregate_nutrition(entries):
        output = {"meal_total": {}, "per_diner_reference": {}, "known_total": {}, "coverage": {}}
        for key in NUTRIENTS:
            totals = [entry["nutrition"]["meal_total"].get(key) for entry in entries]
            known = [entry["nutrition"]["known_total"].get(key) for entry in entries]
            references = [entry["nutrition"]["per_diner_reference"].get(key) for entry in entries]
            reliable = [value for value in totals if _number(value)]
            partial = [value for value in known if _number(value)]
            output["known_total"][key] = round(sum(partial), 3) if partial else None
            output["meal_total"][key] = round(sum(reliable), 3) if totals and len(reliable) == len(totals) else None
            output["per_diner_reference"][key] = (round(sum(references), 3) if references and all(_number(v) for v in references) else None)
            output["coverage"][key] = {"known": len(reliable), "total": len(totals)}
        output.update(estimated=any(entry["nutrition"]["estimated"] for entry in entries),
                      incomplete=any(entry["nutrition"]["incomplete"] for entry in entries), notice=NUTRITION_NOTICE)
        return output
