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

    def delete(self, session_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return json.loads(row[0]) if row else None


def public_session(state: dict, *, reply_suggestions: bool = True) -> dict:
    workspace = state.get("workspace") or {}
    recipes = workspace.get("meal_plan", [])
    active = next((recipe for recipe in recipes if recipe.get("recipe_id") == workspace.get("active_recipe_id")),
                  recipes[0] if recipes else {})
    result = {**{key: value for key, value in state.items() if key not in {"history", "workspace", "image_uploads"}},
            "conversation_state": {"stage": workspace.get("stage", "idea"), "recipe_count": len(recipes),
                "recipe_names": [recipe.get("recipe", {}).get("name", "菜品") for recipe in recipes],
                "active_recipe_name": active.get("recipe", {}).get("name"),
                "choosing": bool(workspace.get("candidates")) or any(
                    view.get("kind") == "preference_form" for view in workspace.get("components", []))}}
    if not reply_suggestions:
        result["messages"] = [{**message, "suggested_replies": []} if message.get("role") == "assistant" else message
                              for message in result.get("messages", [])]
        if result.get("last_run"):
            result["last_run"] = {**result["last_run"], "result": {
                **result["last_run"].get("result", {}), "suggested_replies": [],
            }}
    return result
