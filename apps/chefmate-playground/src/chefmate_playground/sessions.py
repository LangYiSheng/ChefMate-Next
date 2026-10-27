"""App-local conversation persistence, independent of the read-only recipe store."""

from __future__ import annotations

import json
import base64
import binascii
from .database import Database
import threading
import zlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import TypeVar

MutationResult = TypeVar("MutationResult")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionRepository:
    def __init__(self, path: Path | Database):
        self.database = Database.coerce(path)
        self.path = self.database.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, state TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS session_debug "
                "(session_id TEXT NOT NULL, turn_id TEXT NOT NULL, created_at TEXT NOT NULL, "
                "summary TEXT NOT NULL, body BLOB NOT NULL, PRIMARY KEY(session_id,turn_id))"
            )
            pin = self.database.json_boolean("state", "pinned")
            messages = self.database.json_array_length("state", "messages")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS sessions_sidebar ON sessions "
                f"(({pin}) DESC, updated_at DESC, id) WHERE {messages} > 0"
            )
        # A process restart cannot preserve a pending provider call.
        for session in self.list():
            if session.get("running"):
                turn_id = (session.get("active_turn") or {}).get("id")
                capture = self.debug_run(session["id"], turn_id) if turn_id else None
                if capture:
                    capture["status"] = "interrupted"
                    self.save_debug_run(session["id"], turn_id, capture)
                session.update(running=False, active_turn=None, last_status="interrupted",
                               last_error={"code": "interrupted", "message": "服务重启，上一轮已中断。", "retryable": True})
                self.save(session)

    def _connect(self):
        return self.database.connect(timeout=5)

    def list(self) -> list[dict]:
        with self._lock, self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT state FROM sessions ORDER BY updated_at DESC, id"
            )]

    def get(self, session_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def page(self, *, limit: int = 20, cursor: str | None = None) -> tuple[list[dict], str | None]:
        """Read only one sidebar page; the cursor follows stable pin/activity/id order."""
        pin = self.database.json_boolean("state", "pinned")
        condition = self.database.json_array_length("state", "messages") + " > 0"
        arguments = []
        if cursor:
            try:
                anchor = json.loads(base64.urlsafe_b64decode(cursor.encode('ascii')))
                if (not isinstance(anchor, list) or len(anchor) != 3 or type(anchor[0]) is not int
                        or anchor[0] not in (0, 1) or not all(isinstance(value, str) and value for value in anchor[1:])):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError, binascii.Error):
                raise ValueError("会话列表位置已失效，请重新读取。") from None
            pinned, updated, session_id = anchor
            condition += f" AND ({pin} < ? OR ({pin} = ? AND updated_at < ?) OR ({pin} = ? AND updated_at = ? AND id > ?))"
            arguments.extend((pinned, pinned, updated, pinned, updated, session_id))
        with self._lock, self._connect() as connection:
            rows = list(connection.execute(
                f"SELECT state FROM sessions WHERE {condition} ORDER BY {pin} DESC, updated_at DESC, id LIMIT ?",
                (*arguments, limit + 1),
            ))
        states = [json.loads(row[0]) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = states[-1]
            anchor = [int(bool(last.get('pinned'))), last['updated_at'], last['id']]
            next_cursor = base64.urlsafe_b64encode(json.dumps(anchor).encode()).decode('ascii')
        return states, next_cursor

    def update_metadata(self, session_id: str, *, title: str | None = None, pinned: bool | None = None) -> dict:
        def update(state):
            if title is not None:
                state.update(title=title, title_custom=True)
            if pinned is not None:
                state['pinned'] = pinned
            # Metadata edits do not alter the last conversation activity time.
            return state
        return self.mutate(session_id, update)

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
            "workspace": {}, "pinned": False, "title_custom": False,
        }
        self.save(state)
        return state

    def delete(self, session_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            connection.execute("DELETE FROM session_debug WHERE session_id=?", (session_id,))
            return json.loads(row[0]) if row else None

    def save_debug_run(self, session_id: str, turn_id: str, payload: dict) -> None:
        """Keep exact, already-redacted request captures outside ordinary chat/history JSON."""
        body = zlib.compress(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        summary = {key: payload.get(key) for key in ("turn_id", "request_id", "message", "status", "created_at")}
        summary["model_calls"] = len(payload.get("debug_trace", []))
        with self._lock, self._connect() as connection:
            if not connection.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                return
            connection.execute(
                "INSERT INTO session_debug(session_id,turn_id,created_at,summary,body) VALUES(?,?,?,?,?) "
                "ON CONFLICT(session_id,turn_id) DO UPDATE SET summary=excluded.summary,body=excluded.body",
                (session_id, turn_id, payload["created_at"], json.dumps(summary, ensure_ascii=False), body),
            )
            connection.execute(
                "DELETE FROM session_debug WHERE session_id=? AND turn_id NOT IN "
                "(SELECT turn_id FROM session_debug WHERE session_id=? ORDER BY created_at DESC LIMIT 20)",
                (session_id, session_id),
            )

    def debug_runs(self, session_id: str) -> list[dict]:
        with self._lock, self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT summary FROM session_debug WHERE session_id=? ORDER BY created_at DESC", (session_id,)
            )]

    def debug_run(self, session_id: str, turn_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT body FROM session_debug WHERE session_id=? AND turn_id=?",
                                     (session_id, turn_id)).fetchone()
            return json.loads(zlib.decompress(row[0])) if row else None


def public_session(state: dict, *, reply_suggestions: bool = True) -> dict:
    workspace = state.get("workspace") or {}
    if workspace:
        from chefmate_workflow import WorkspaceState
        workspace = WorkspaceState.model_validate(workspace).model_dump(mode="json")
    recipes = workspace.get("meal_plan", [])
    active = next((recipe for recipe in recipes if recipe.get("recipe_id") == workspace.get("active_recipe_id")),
                  recipes[0] if recipes else {})
    question = workspace.get("pending_question") if not state.get("running") else None
    result = {**{key: value for key, value in state.items() if key not in {"history", "workspace", "image_uploads"}},
            "awaiting_user": bool(question), "pending_question": question,
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
