"""Bounded, user-owned long-term context independent of individual chat histories."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .sessions import now


class MemoryConflict(ValueError):
    pass


class MemoryRepository:
    """A single local profile; assistant writes never replace user-authored notes.

    Revision checks run under SQLite's write transaction, so another browser,
    conversation or process cannot silently overwrite a newer preference.
    """

    def __init__(self, path: Path, journal=None):
        self.path = Path(path)
        self.journal = journal
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS personal_memory ("
                               "id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, "
                               "enabled INTEGER NOT NULL, user_notes TEXT NOT NULL, summary TEXT NOT NULL, "
                               "updated_at TEXT NOT NULL, summary_source TEXT)")
            connection.execute("INSERT OR IGNORE INTO personal_memory VALUES(1,0,1,'','',?,NULL)", (now(),))

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def _read(connection):
        row = connection.execute("SELECT revision,enabled,user_notes,summary,updated_at,summary_source "
                                 "FROM personal_memory WHERE id=1").fetchone()
        return dict(zip(("revision", "enabled", "user_notes", "summary", "updated_at", "summary_source"), row))

    def snapshot(self) -> dict:
        with self._connect() as connection:
            result = self._read(connection)
        result["enabled"] = bool(result["enabled"])
        return result

    def _update(self, expected_revision, changes, *, cancelled=None, active_turn=None):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("记忆版本无效。")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._read(connection)
            if state["revision"] != expected_revision:
                raise MemoryConflict("记忆已在另一处更新，请重新读取后复核。")
            if active_turn is not None:
                session_id, turn_id = active_turn
                row = connection.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                session = json.loads(row[0]) if row else {}
                if not session.get("running") or (session.get("active_turn") or {}).get("id") != turn_id:
                    return {"status": "turn_closed", "message": "本轮已经结束，没有修改长期记忆。"}
            if cancelled is not None and cancelled.is_set():
                return {"status": "turn_closed", "message": "本轮已停止，没有修改长期记忆。"}
            state.update(changes, revision=state["revision"] + 1, updated_at=now())
            connection.execute("UPDATE personal_memory SET revision=?,enabled=?,user_notes=?,summary=?,"
                               "updated_at=?,summary_source=? WHERE id=1",
                               tuple(state[key] for key in ("revision", "enabled", "user_notes", "summary",
                                                             "updated_at", "summary_source")))
            if cancelled is not None and cancelled.is_set():
                connection.rollback()
                return {"status": "turn_closed", "message": "本轮已停止，没有修改长期记忆。"}
        state["enabled"] = bool(state["enabled"])
        return state

    def update_user(self, user_notes: str, expected_revision: int, *, enabled: bool | None = None) -> dict:
        if not isinstance(user_notes, str) or len(user_notes) > 6000:
            raise ValueError("自定义记忆最多6000字。")
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("记忆开关必须为布尔值。")
        changes = {"user_notes": user_notes.strip()}
        if enabled is not None:
            changes["enabled"] = enabled
        return self._update(expected_revision, changes)

    def update_summary(self, summary: str, expected_revision: int, *, source_session_id: str | None = None,
                       cancelled=None, active_turn=None) -> dict:
        if not isinstance(summary, str) or len(summary) > 3000:
            raise ValueError("助手总结最多3000字。")
        if not self.snapshot()["enabled"]:
            return {"status": "memory_disabled", "message": "长期记忆已关闭。"}
        return self._update(expected_revision, {"summary": summary.strip(), "summary_source": source_session_id},
                            cancelled=cancelled, active_turn=active_turn)

    def clear(self, expected_revision: int) -> dict:
        return self._update(expected_revision, {"user_notes": "", "summary": "", "summary_source": None})

    def context(self) -> dict:
        state = self.snapshot()
        if not state["enabled"]:
            return {}
        return {key: state[key] for key in ("revision", "user_notes", "summary", "updated_at")}

    @staticmethod
    def _snippet(text, query, length=450):
        text = str(text)
        position = text.casefold().find(query.casefold()) if query else 0
        start = max(0, position - 100)
        return ("…" if start else "") + text[start:start + length] + ("…" if len(text) > start + length else "")

    def search_history(self, query: str, limit: int = 6, kind: str = "all", *, session_id=None) -> dict:
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("历史检索关键词最多200字；留空查看最近记录。")
        if type(limit) is not int or not 1 <= limit <= 10 or kind not in {"all", "chats", "meals"}:
            raise ValueError("历史检索范围无效。")
        if not self.snapshot()["enabled"]:
            return {"status": "memory_disabled", "message": "长期记忆已关闭。", "items": []}
        query = query.strip()
        needle = query.casefold()
        items = []
        with self._connect() as connection:
            has_sessions = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
            rows = connection.execute("SELECT id,state FROM sessions ORDER BY updated_at DESC,id LIMIT 250").fetchall() if has_sessions else []
        for identity, raw in rows:
            state = json.loads(raw)
            title = str(state.get("title", "对话"))[:100]
            if kind != "meals":
                for message in reversed(state.get("messages", [])):
                    content = message.get("content", "")
                    if message.get("role") not in {"user", "assistant"} or not isinstance(content, str):
                        continue
                    # The current human message is not evidence of an earlier event.
                    if identity == session_id and message.get("role") == "user" and message is state["messages"][-1]:
                        continue
                    if needle in content.casefold():
                        items.append({"kind": "chat", "session_id": identity, "title": title,
                                      "role": message["role"], "created_at": message.get("created_at"),
                                      "content": self._snippet(content, query)})
                    if len(items) >= limit * 3:
                        break
            if kind != "chats":
                workspace = state.get("workspace") or {}
                stage = workspace.get("stage", "idea")
                for recipe in workspace.get("meal_plan", []):
                    name = str(recipe.get("recipe", {}).get("name", ""))
                    if needle in name.casefold() and stage in {"cooking", "completed"}:
                        event_time = workspace.get("completed_at") or workspace.get("started_at")
                        items.append({"kind": "meal", "source": "workspace_snapshot", "session_id": identity,
                                      "meal_id": workspace.get("meal_id"), "title": title,
                                      "created_at": event_time, "date_unknown": not bool(event_time),
                                      "observed_at": state.get("updated_at"), "recipe_name": name[:120],
                                      "status": "completed" if stage == "completed" else "attempted",
                                      "content": f"{'已完成' if stage == 'completed' else '尝试过'}：{name[:120]}"})
            if len(items) >= limit * 3:
                break
        # A journal can preserve meals after their originating chat is reset or deleted.
        if kind != "chats" and self.journal is not None and hasattr(self.journal, "search"):
            records = self.journal.search(query, limit=limit)
            if isinstance(records, dict):
                records = records.get("items", [])
            for record in list(records)[:limit]:
                if not isinstance(record, dict):
                    continue
                names = [str(recipe.get("name", ""))[:120] for recipe in record.get("recipes", [])
                         if isinstance(recipe, dict)]
                content = record.get("content") or record.get("recipe_name") or record.get("name") or "、".join(names)
                if record.get("notes"):
                    content += "；反馈：" + str(record["notes"])[:300]
                items = [item for item in items if not (
                    item.get("source") == "workspace_snapshot" and record.get("session_id")
                    and item.get("session_id") == record["session_id"]
                    and (record.get("meal_id") and item.get("meal_id") == record["meal_id"]
                         or item.get("meal_id") is None and item.get("recipe_name") in names))]
                items.append({"kind": "meal", "source": "journal", **{key: record[key] for key in
                    ("session_id", "created_at", "date", "recipe_name", "status", "title", "rating", "diners") if key in record},
                    "created_at": record.get("completed_at") or record.get("started_at") or record.get("created_at"),
                    "meal_id": record.get("meal_id"), "entry_id": record.get("id"), "recipe_names": names,
                    "content": self._snippet(content, query)})
        items.sort(key=lambda item: str(item.get("created_at") or item.get("date") or ""), reverse=True)
        return {"status": "ok", "items": items[:limit], "limit": limit,
                "scope": "最近250段会话与已记录餐食，返回相关片段；助手建议不等于已做或已吃。"
                         "date_unknown表示旧任务没有做菜日期，observed_at仅为快照更新时间，不能当做菜日期。"}


class ReadMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SummaryRequest(ReadMemoryRequest):
    expected_revision: Annotated[int, Field(ge=0)]
    summary: Annotated[str, Field(max_length=3000)]


class HistoryRequest(ReadMemoryRequest):
    query: Annotated[str, Field(max_length=200)] = ""
    limit: Annotated[int, Field(ge=1, le=10)] = 6
    kind: Literal["all", "chats", "meals"] = "all"


def make_memory_tools(repository: MemoryRepository):
    from chefmate_agent import InjectedToolSpec

    def read(request, context):
        state = repository.snapshot()
        if not state["enabled"]:
            return {"status": "memory_disabled", "message": "长期记忆已关闭。"}
        return {"status": "ok", "memory": state}

    def summarize(request, context):
        host = context.host_context
        try:
            result = repository.update_summary(**request, source_session_id=getattr(host, "session_id", None),
                                               cancelled=getattr(host, "cancelled", None),
                                               active_turn=(host.session_id, host.turn_id)
                                               if getattr(host, "binding", None) is not None else None)
        except MemoryConflict:
            return {"status": "revision_conflict", "message": "长期记忆已更新，请先重新读取并复核。"}
        if result.get("status"):
            return result
        return {"status": "ok", "memory": result}

    def history(request, context):
        return repository.search_history(**request, session_id=getattr(context.host_context, "session_id", None))

    return [
        InjectedToolSpec("read_long_term_memory", "读取用户自定义记忆、助手总结及最新版本；用户自定义内容优先，不能改写。",
                         ReadMemoryRequest, read, required_skill="personal-memory"),
        InjectedToolSpec("maintain_memory_summary", "根据用户明确表达的稳定口味、忌口和烹饪习惯维护至多3000字总记忆。"
                         "只替换助手总结，不改用户记忆；须保留有效事实并删除冲突/过时内容，expected_revision来自最新读取。",
                         SummaryRequest, summarize, effect="write", required_skill="personal-memory"),
        InjectedToolSpec("search_personal_history", "按关键词读取有界历史聊天/尝试与完成的餐食片段；query留空查看最近记录，默认最多6条；"
                         "kind为all/chats/meals。记录做完不代表吃完。", HistoryRequest, history,
                         required_skill="personal-memory"),
    ]
