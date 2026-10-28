"""Durable, content-free model usage ledger, independent of chat deletion."""

import json
import sqlite3
from .database import Database
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chefmate_agent.telemetry import TOKEN_FIELDS, normalize_usage_call, number, usage_summary

LOCAL_ZONE = timezone(timedelta(hours=8))


def _model(value):
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return "".join(character for character in value.strip()[:200] if ord(character) >= 32)


class UsageRepository:
    def __init__(self, path: Path | str | Database, *, now=None):
        self.database = Database.coerce(path)
        self.path = self.database.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS model_usage_ledger ("
                       "session_id TEXT NOT NULL, turn_id TEXT NOT NULL, day TEXT NOT NULL, "
                       "created_at TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL, "
                       "PRIMARY KEY(session_id,turn_id))")
            db.execute("CREATE INDEX IF NOT EXISTS model_usage_day ON model_usage_ledger(day)")

    def _connect(self):
        return self.database.connect(row_factory=sqlite3.Row, timeout=10)

    def record(self, session_id, turn_id, usage, *, model="", created_at=None, status="running"):
        if not session_id or not turn_id or not isinstance(usage, dict):
            return False
        calls = {}
        for index, item in enumerate(usage.get("calls", [])):
            if not isinstance(item, dict):
                continue
            round_number = number(item.get("round"))
            if round_number is None:
                round_number = index + 1
            calls[round_number] = normalize_usage_call({
                "round": round_number, "model": _model(item.get("model") or model),
                "provider_attempts": number(item.get("provider_attempts")) or None,
                **{key: number(item.get(key)) for key in TOKEN_FIELDS},
                "total_tokens_source": item.get("total_tokens_source")})
        attempts = max(number(usage.get("attempted_calls")) or 0, number(usage.get("model_calls")) or 0, len(calls))
        if not attempts:
            return False
        instant = datetime.fromisoformat(created_at) if created_at else self._now()
        if instant.tzinfo is None:
            raise ValueError("Usage timestamps need a timezone")
        day = instant.astimezone(LOCAL_ZONE).date().isoformat()
        status = status if status in {"running", "completed", "cancelled", "error", "interrupted"} else "error"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT body,status FROM model_usage_ledger WHERE session_id=? AND turn_id=?",
                                  (session_id, turn_id)).fetchone()
            if previous:
                old = json.loads(previous["body"])
                if not model:
                    model = old.get("model", "")
                attempts = max(attempts, old["attempted_calls"])
                merged = {item["round"]: item for item in old["calls"]}
                for round_number, call in calls.items():
                    earlier = normalize_usage_call(merged.get(round_number, {}))
                    updated = {**call, **{key: earlier[key] for key in TOKEN_FIELDS if key != "total_tokens"
                                         and call[key] is None and earlier.get(key) is not None}}
                    if call["total_tokens_source"] == "reported":
                        pass
                    elif earlier["total_tokens_source"] == "reported":
                        updated.update(total_tokens=earlier["total_tokens"], total_tokens_source="reported")
                    else:
                        # Both observations identify this same turn/round. Merge
                        # its operands, then derive once; never add turn totals.
                        updated.update(total_tokens=None, total_tokens_source=None)
                    merged[round_number] = normalize_usage_call(updated)
                    merged[round_number]["provider_attempts"] = max(
                        number(call.get("provider_attempts")) or 0, number(earlier.get("provider_attempts")) or 0) or None
                    if call["model"] == "unknown" and earlier.get("model"):
                        merged[round_number]["model"] = earlier["model"]
                calls = merged
                if status == "running" and previous["status"] != "running":
                    status = previous["status"]
            body = json.dumps({"calls": list(sorted(calls.values(), key=lambda item: item["round"])),
                               "attempted_calls": attempts, "model": _model(model)},
                              ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            db.execute("INSERT INTO model_usage_ledger VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,turn_id) "
                       "DO UPDATE SET status=excluded.status,body=excluded.body",
                       (session_id, turn_id, day, instant.isoformat(), status, body))
        return True

    def backfill(self, repository):
        """Only retained observed usage is recoverable; never label old calls with today's model."""
        sessions = repository.list()
        for session in sessions:
            session_id = session["id"]
            for message in session.get("messages", []):
                if message.get("role") == "assistant" and message.get("turn_id"):
                    self.record(session_id, message["turn_id"], message.get("usage", {}),
                                created_at=message.get("created_at") or session["created_at"], status="completed")
            # Failed/cancelled requests may only have a retained diagnostic run.
            for run in repository.debug_runs(session_id):
                capture = repository.debug_run(session_id, run["turn_id"]) or {}
                usage = dict(capture.get("usage") or {})
                usage["attempted_calls"] = max(number(usage.get("attempted_calls")) or 0,
                                                len(capture.get("debug_trace") or []))
                self.record(session_id, run["turn_id"], usage, created_at=run["created_at"],
                            status="completed" if run.get("status") == "waiting_for_user" else run.get("status", "error"))
        active = {(session["id"], (session.get("active_turn") or {}).get("id"))
                  for session in sessions if session.get("running")}
        # A crash can happen after ledger persistence and before the diagnostic
        # row. Do not leave that observed request marked as currently running.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT session_id,turn_id FROM model_usage_ledger WHERE status='running'").fetchall()
            for row in pending:
                if (row["session_id"], row["turn_id"]) not in active:
                    db.execute("UPDATE model_usage_ledger SET status='interrupted' WHERE session_id=? AND turn_id=?",
                               (row["session_id"], row["turn_id"]))

    @staticmethod
    def _summary(rows):
        calls, attempts = [], 0
        for row in rows:
            body = row["body"]
            calls.extend(body["calls"])
            attempts += body["attempted_calls"]
        result = usage_summary(calls, attempted_calls=attempts)
        result.pop("calls")
        result.update(turns=len(rows), incomplete=bool(result["model_calls"]) and any(
                          result[key] is None for key in ("input_tokens", "output_tokens", "total_tokens")),
                      statuses={status: sum(row["status"] == status for row in rows)
                                for status in ("running", "completed", "cancelled", "error", "interrupted")})
        return result

    def summary(self, *, days=30):
        if days is not None and (type(days) is not int or not 1 <= days <= 366):
            raise ValueError("Invalid usage range")
        today = self._now().astimezone(LOCAL_ZONE).date()
        start = today - timedelta(days=days - 1) if days is not None else None
        with self._connect() as db:
            all_rows = [{**dict(row), "body": json.loads(row["body"])} for row in db.execute(
                "SELECT day,status,created_at,body FROM model_usage_ledger ORDER BY created_at")]
        selected = [row for row in all_rows if row["day"] <= today.isoformat()
                    and (start is None or row["day"] >= start.isoformat())]
        model_rows = {}
        for row in selected:
            groups = {}
            for call in row["body"]["calls"]:
                groups.setdefault(call["model"], []).append(call)
            missing = row["body"]["attempted_calls"] - len(row["body"]["calls"])
            if missing:
                groups.setdefault(row["body"].get("model") or "unknown", [])
            for model, calls in groups.items():
                attempted = len(calls) + (missing if model == (row["body"].get("model") or "unknown") else 0)
                model_rows.setdefault(model, []).append({**row, "body": {"calls": calls, "attempted_calls": attempted}})
        daily = [{"date": day, **self._summary([row for row in selected if row["day"] == day])}
                 for day in sorted({row["day"] for row in selected})]
        return {"period": {"from": start.isoformat() if start else min((row["day"] for row in selected), default=None),
                           "to": today.isoformat(), "days": days},
                "summary": self._summary(selected),
                "today": self._summary([row for row in all_rows if row["day"] == today.isoformat()]),
                "all_time": self._summary(all_rows), "daily": daily,
                "models": [{"model": model, **self._summary(rows)} for model, rows in sorted(model_rows.items())],
                "notice": "使用模型已报告用量；同次调用输入与输出均完整时精确相加得到总量，并另列派生次数。"
                          "缺失项保留未知，小计与覆盖次数另列。删除聊天不删除用量统计。"}
