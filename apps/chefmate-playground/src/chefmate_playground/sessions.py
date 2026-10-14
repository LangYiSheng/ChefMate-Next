"""App-local conversation persistence, independent of the read-only recipe store."""

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import TypeVar

MutationResult = TypeVar("MutationResult")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, state TEXT NOT NULL)"
            )
        # A process restart cannot preserve a pending provider call.
        for session in self.list():
            if session.get("running"):
                session.update(running=False, active_turn=None, last_status="interrupted",
                               last_error={"code": "interrupted", "message": "服务重启，上一轮已中断。", "retryable": True})
                self.save(session)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def list(self) -> list[dict]:
        with self._lock, self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT state FROM sessions ORDER BY updated_at DESC, id"
            )]

    def get(self, session_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, state: dict) -> None:
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id,updated_at,state) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,state=excluded.state",
                (state["id"], state["updated_at"], encoded),
            )

    def mutate(self, session_id: str, operation: Callable[[dict], MutationResult]) -> MutationResult:
        """Read and update one aggregate atomically, preserving concurrent UI/agent fields."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            state = json.loads(row[0])
            result = operation(state)
            encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
            connection.execute("UPDATE sessions SET updated_at=?,state=? WHERE id=?",
                               (state["updated_at"], encoded, session_id))
            return result

    def create(self) -> dict:
        timestamp = now()
        state = {
            "id": str(uuid4()), "title": "新对话", "created_at": timestamp, "updated_at": timestamp,
            "messages": [], "history": [], "running": False, "active_turn": None,
            "last_status": "idle", "last_error": None, "last_run": None,
            "workspace": {},
        }
        self.save(state)
        return state

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))


def public_session(state: dict) -> dict:
    workspace = state.get("workspace") or {}
    return {**{key: value for key, value in state.items() if key not in {"history", "workspace"}},
            "conversation_state": {"stage": workspace.get("stage", "idea"),
                                   "recipe_count": len(workspace.get("meal_plan", []))}}
