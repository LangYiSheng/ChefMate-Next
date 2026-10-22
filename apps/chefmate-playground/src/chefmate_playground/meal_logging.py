"""Personal intake persistence with revision and turn guards.

Models, source evidence, and agent tools have dedicated modules; the repository
owns SQLite transactions and never publishes an unconfirmed draft implicitly.
"""

import hashlib
import json
import sqlite3
from datetime import date as Day, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .food_log_models import (
    CHAT_COMPONENT_PRESENTATION as CHAT_COMPONENT_PRESENTATION,
    FoodLogModel as FoodLogModel,
    FoodLogNutrition as FoodLogNutrition,
    FoodLogItem as FoodLogItem,
    FoodLogDraftRequest as FoodLogDraftRequest,
    FoodLogSearchRequest as FoodLogSearchRequest,
    FoodLogIdentityRequest as FoodLogIdentityRequest,
    ReviseFoodLogDraftRequest as ReviseFoodLogDraftRequest,
    ConfirmFoodLogRequest as ConfirmFoodLogRequest,
    LOCAL_ZONE,
    MACROS as MACROS,
    INTAKE_NOTICE as INTAKE_NOTICE,
    _encoded as _encoded,
    _nutrition,
    intake_summary as intake_summary,
)
from .food_log_evidence import _turn_guard as _turn_guard
from .food_log_tools import make_meal_logging_tools as make_meal_logging_tools


class FoodLogRepository:
    def __init__(self, path: Path | str, *, now=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS personal_food_logs ("
                "id TEXT PRIMARY KEY,status TEXT NOT NULL,revision INTEGER NOT NULL,day TEXT NOT NULL,"
                "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,source_session_id TEXT,"
                "operation_id TEXT UNIQUE,fingerprint TEXT NOT NULL,body TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS personal_food_logs_day ON personal_food_logs(day,status)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _public(row):
        return (
            {
                **json.loads(row["body"]),
                "id": row["id"],
                "status": row["status"],
                "revision": row["revision"],
                "date": row["day"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "source_session_id": row["source_session_id"],
                "record_kind": "personal_intake",
            }
            if row
            else None
        )

    def _day(self, value):
        today = self._now().astimezone(LOCAL_ZONE).date()
        day = Day.fromisoformat(value) if value else today
        if day > today or day < today - timedelta(days=3660):
            raise ValueError("记录日期应在今天或之前十年内")
        return day.isoformat()

    def _body(self, values, *, image_ids=()):
        document = FoodLogDraftRequest.model_validate(values).model_dump(mode="json")
        document["date"] = self._day(document["date"])
        if document["source_kind"] == "image" and not image_ids:
            raise ValueError("本轮没有图片，不能标记为照片记录")
        return {
            "document": document,
            "nutrition": _nutrition(document["items"]),
            "source_image_ids": list(image_ids) if document["source_kind"] == "image" else [],
        }

    def get(self, identity, *, include_deleted=False):
        with self._connect() as db:
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            return record if record and (include_deleted or record["status"] != "deleted") else None

    def create_draft(
        self, values, *, session_id=None, image_ids=(), operation_id=None, guard=None, active_turn_id=None
    ):
        body = self._body(values, image_ids=image_ids)
        fingerprint = hashlib.sha256(_encoded([session_id, body]).encode()).hexdigest()
        stamp, identity = self._now().isoformat(), "food-" + str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已结束，没有保存饮食草稿")
            if operation_id:
                row = db.execute("SELECT * FROM personal_food_logs WHERE operation_id=?", (operation_id,)).fetchone()
                if row:
                    if row["fingerprint"] != fingerprint:
                        raise ValueError("同一操作已保存其他饮食内容，请核对已有记录")
                    return self._public(row)
            if guard:
                guard()
            db.execute(
                "INSERT INTO personal_food_logs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    "draft",
                    1,
                    body["document"]["date"],
                    stamp,
                    stamp,
                    session_id,
                    operation_id,
                    fingerprint,
                    _encoded(body),
                ),
            )
            if guard:
                guard()
        return self.get(identity)

    def update_draft(
        self,
        identity,
        revision,
        values,
        *,
        guard=None,
        session_id=None,
        active_turn_id=None,
        source_image_ids=None,
        allow_source_change=False,
    ):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已结束，没有修改饮食草稿")
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if type(revision) is not int or record["status"] != "draft" or record["revision"] != revision:
                raise ValueError("饮食草稿已更新或已确认，请刷新后核对")
            if not allow_source_change and values.get("source_kind", "text") != record["document"]["source_kind"]:
                raise ValueError("请保留原始来源类型")
            images = record["source_image_ids"] if source_image_ids is None else list(source_image_ids)
            if not set(record["source_image_ids"]).issubset(images):
                raise ValueError("原始图片来源不能移除")
            body = self._body(values, image_ids=images)
            body["source_image_ids"] = images  # Original provenance survives an explicit switch to a text source.
            if guard:
                guard()
            db.execute(
                "UPDATE personal_food_logs SET body=?,day=?,revision=revision+1,updated_at=? WHERE id=?",
                (_encoded(body), body["document"]["date"], self._now().isoformat(), identity),
            )
            if guard:
                guard()
        return self.get(identity)

    def publish(self, identity, revision, *, guard=None, session_id=None, active_turn_id=None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已结束，没有确认饮食记录")
            if guard:
                guard()
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if record["status"] == "saved" and type(revision) is int and revision == record["revision"] - 1:
                return record
            if type(revision) is not int or record["status"] != "draft" or record["revision"] != revision:
                raise ValueError("饮食记录版本已变化，请刷新后核对")
            db.execute(
                "UPDATE personal_food_logs SET status='saved',revision=revision+1,updated_at=? WHERE id=?",
                (self._now().isoformat(), identity),
            )
            if guard:
                guard()
        return self.get(identity)

    def delete(self, identity, revision):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if record["status"] == "deleted" and type(revision) is int and revision == record["revision"] - 1:
                return record
            if (
                type(revision) is not int
                or record["status"] not in {"draft", "saved"}
                or record["revision"] != revision
            ):
                raise ValueError("饮食记录版本已变化，请刷新后核对")
            body = {
                key: record[key]
                for key in ("document", "nutrition", "source_image_ids", "cooked_source")
                if key in record
            }
            body["deleted_from"] = record["status"]
            db.execute(
                "UPDATE personal_food_logs SET status='deleted',revision=revision+1,updated_at=?,body=? WHERE id=?",
                (self._now().isoformat(), _encoded(body), identity),
            )
        return self.get(identity, include_deleted=True)

    def restore(self, identity, revision):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if type(revision) is not int or record["status"] != "deleted" or record["revision"] != revision:
                raise ValueError("饮食记录版本已变化，请刷新后核对")
            body = {
                key: record[key]
                for key in ("document", "nutrition", "source_image_ids", "cooked_source")
                if key in record
            }
            db.execute(
                "UPDATE personal_food_logs SET status=?,revision=revision+1,updated_at=?,body=? WHERE id=?",
                (record["deleted_from"], self._now().isoformat(), _encoded(body), identity),
            )
        return self.get(identity)

    def list(self, *, status="saved", query="", days=30, limit=100, offset=0):
        if (
            status not in {"draft", "saved", "all", "deleted"}
            or type(days) is not int
            or not 1 <= days <= 366
            or type(limit) is not int
            or not 1 <= limit <= 1000
            or type(offset) is not int
            or not 0 <= offset <= 100000
            or not isinstance(query, str)
            or len(query) > 200
        ):
            raise ValueError("饮食记录查询条件无效")
        today = self._now().astimezone(LOCAL_ZONE).date()
        start = (today - timedelta(days=days - 1)).isoformat()
        with self._connect() as db:
            rows = [
                self._public(row)
                for row in db.execute(
                    "SELECT * FROM personal_food_logs WHERE day>=? AND day<=? ORDER BY day DESC,created_at DESC,id",
                    (start, today.isoformat()),
                )
            ]
        records = [
            record
            for record in rows
            if (record["status"] in {"draft", "saved"} if status == "all" else record["status"] == status)
            and (not query.strip() or query.strip().casefold() in _encoded(record["document"]).casefold())
        ]
        return {"items": records[offset : offset + limit], "total": len(records), "offset": offset, "limit": limit}

    def summary(self, days=7):
        if type(days) is not int or not 1 <= days <= 366:
            raise ValueError("饮食汇总日期范围无效")
        today = self._now().astimezone(LOCAL_ZONE).date()
        start = today - timedelta(days=days - 1)
        with self._connect() as db:
            records = [
                self._public(row)
                for row in db.execute(
                    "SELECT * FROM personal_food_logs WHERE status='saved' AND day>=? AND day<=? ORDER BY day,id",
                    (start.isoformat(), today.isoformat()),
                )
            ]
        daily = [
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                **intake_summary(
                    [record for record in records if record["date"] == (start + timedelta(days=offset)).isoformat()]
                ),
            }
            for offset in range(days)
        ]
        return {
            **intake_summary(records),
            "period": {"from": start.isoformat(), "to": today.isoformat(), "days": days},
            "days": daily,
            "today": daily[-1],
        }
