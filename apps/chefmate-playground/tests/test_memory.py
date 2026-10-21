"""Memory ownership, optimistic concurrency and bounded evidence retrieval."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from chefmate_agent import ToolExecutionContext
from chefmate_playground.memory import MemoryConflict, MemoryRepository, make_memory_tools
from chefmate_playground.sessions import SessionRepository


def test_user_notes_and_assistant_summary_have_separate_ownership_and_revision(tmp_path):
    memory = MemoryRepository(tmp_path / "sessions.db")
    user = memory.update_user("不吃香菜；少辣", 0)
    assert user["revision"] == 1
    saved = memory.update_summary("喜欢清淡；家里有电磁炉", 1, source_session_id="source-chat")
    assert saved["user_notes"] == "不吃香菜；少辣"
    assert saved["summary_source"] == "source-chat" and saved["revision"] == 2
    assert MemoryRepository(memory.path).context()["summary"] == "喜欢清淡；家里有电磁炉"
    with pytest.raises(MemoryConflict):
        MemoryRepository(memory.path).update_user("过期编辑", 1)
    assert memory.snapshot()["user_notes"] == "不吃香菜；少辣"


def test_disable_stops_model_read_search_and_summary_but_allows_user_editing(tmp_path):
    memory = MemoryRepository(tmp_path / "sessions.db")
    user = memory.update_user("少盐", 0, enabled=False)
    assert memory.context() == {}
    assert memory.search_history("")["status"] == "memory_disabled"
    assert memory.update_summary("禁止写入", user["revision"])["status"] == "memory_disabled"
    tools = {tool.name: tool for tool in make_memory_tools(memory)}
    context = ToolExecutionContext({}, None, [])
    assert tools["read_long_term_memory"].handler({}, context)["status"] == "memory_disabled"
    assert all(tool.required_skill == "personal-memory" for tool in tools.values())
    assert memory.update_user("少盐、少辣", user["revision"])["user_notes"] == "少盐、少辣"


def test_clear_forgets_both_memory_sections_without_reusing_old_revision(tmp_path):
    memory = MemoryRepository(tmp_path / "sessions.db")
    memory.update_user("不要葱", 0)
    memory.update_summary("常做炒蛋", 1, source_session_id="chat")
    state = memory.clear(2)
    assert state["summary"] == state["user_notes"] == ""
    assert state["summary_source"] is None and state["revision"] == 3
    with pytest.raises(MemoryConflict):
        memory.update_summary("旧模型不能复活记忆", 2)


def test_cancelled_memory_write_is_not_committed(tmp_path):
    memory = MemoryRepository(tmp_path / "sessions.db")
    cancelled = asyncio.Event()
    cancelled.set()
    state = memory.update_summary("不能写", 0, cancelled=cancelled)
    assert state["status"] == "turn_closed"
    assert memory.snapshot()["revision"] == 0


def test_late_summary_cannot_write_after_owning_turn_closed(tmp_path):
    sessions = SessionRepository(tmp_path / "sessions.db")
    state = sessions.create()
    memory = MemoryRepository(sessions.path)
    result = memory.update_summary("迟到的总结", 0, source_session_id=state["id"], active_turn=(state["id"], "stale-turn"))
    assert result["status"] == "turn_closed"
    assert memory.snapshot()["summary"] == ""


def test_history_reports_evidence_and_distinguishes_proposals_from_completed_meals(tmp_path):
    sessions = SessionRepository(tmp_path / "sessions.db")
    completed = sessions.create()
    completed.update(title="昨天的番茄炒蛋", messages=[
        {"role": "assistant", "content": "也可以试试番茄牛腩。", "created_at": "2026-10-20T11:00:00+00:00"},
        {"role": "user", "content": "番茄炒蛋做好了，少糖很好吃", "created_at": "2026-10-20T11:01:00+00:00"},
    ], workspace={"stage": "completed", "meal_plan": [{"recipe": {"name": "番茄炒蛋"}}]})
    sessions.save(completed)
    attempted = sessions.create()
    attempted.update(workspace={"stage": "cooking", "meal_plan": [{"recipe": {"name": "番茄汤"}}]})
    sessions.save(attempted)
    memory = MemoryRepository(sessions.path)
    result = memory.search_history("番茄", limit=10)
    meals = [item for item in result["items"] if item["kind"] == "meal"]
    assert {(item["recipe_name"], item["status"]) for item in meals} == {
        ("番茄炒蛋", "completed"), ("番茄汤", "attempted")}
    assert any(item.get("role") == "user" and "少糖" in item["content"] for item in result["items"])
    assert not any(item.get("recipe_name") == "番茄牛腩" for item in result["items"])
    assert all(item["date_unknown"] and item["created_at"] is None for item in meals)
    assert len(memory.search_history("番茄", limit=1)["items"]) == 1


def test_history_excerpt_budget_and_journal_survive_deleted_chat(tmp_path):
    class Journal:
        def search(self, query, limit):
            return [{"id": "meal-1", "date": "2026-10-20", "status": "completed", "diners": 2,
                     "recipes": [{"recipe_id": "egg", "name": "炒滑蛋"}], "notes": "很嫩" * 1000}]

    memory = MemoryRepository(tmp_path / "sessions.db", journal=Journal())
    result = memory.search_history("炒滑蛋", kind="meals")
    assert result["items"][0]["recipe_names"] == ["炒滑蛋"]
    assert result["items"][0]["entry_id"] == "meal-1"
    assert len(result["items"][0]["content"]) <= 452
    assert len(json.dumps(memory.context(), ensure_ascii=False)) < 12000
    for query, limit in (("x" * 201, 6), ("x", 11), ("x", True)):
        with pytest.raises(ValueError):
            memory.search_history(query, limit)


def test_summary_tool_uses_current_session_provenance_and_rejects_stale_writes(tmp_path):
    memory = MemoryRepository(tmp_path / "sessions.db")
    tools = {tool.name: tool for tool in make_memory_tools(memory)}
    context = ToolExecutionContext({}, SimpleNamespace(session_id="trusted-current-chat", cancelled=None), [])
    result = tools["maintain_memory_summary"].handler({"summary": "不吃香菜", "expected_revision": 0}, context)
    assert result["status"] == "ok"
    assert result["memory"]["summary_source"] == "trusted-current-chat"
    conflict = tools["maintain_memory_summary"].handler({"summary": "覆盖", "expected_revision": 0}, context)
    assert conflict["status"] == "revision_conflict"
    assert memory.snapshot()["summary"] == "不吃香菜"


def test_memory_limits_do_not_turn_empty_or_unknown_values_into_claims(tmp_path):
    memory = MemoryRepository(tmp_path / "sessions.db")
    assert memory.snapshot()["summary"] == ""
    assert memory.search_history("没有的菜")["items"] == []
    with pytest.raises(ValueError):
        memory.update_user("a" * 6001, 0)
    with pytest.raises(ValueError):
        memory.update_summary("a" * 3001, 0)
    with pytest.raises(ValueError):
        memory.update_user("note", 0, enabled=1)


def test_journal_evidence_deduplicates_workspace_and_prep_is_not_cooking_history(tmp_path):
    sessions = SessionRepository(tmp_path / "sessions.db")
    current = sessions.create()
    current["workspace"] = {"meal_id": "meal", "stage": "completed", "started_at": "2026-10-20T10:00:00+00:00",
                            "meal_plan": [{"recipe": {"name": "炒蛋"}}]}
    sessions.save(current)
    prep = sessions.create()
    prep["workspace"] = {"stage": "prep", "meal_plan": [{"recipe": {"name": "炒蛋"}}]}
    sessions.save(prep)
    class Journal:
        def search(self, query, limit):
            return [{"id": "entry", "meal_id": "meal", "session_id": current["id"],
                     "started_at": "2026-10-20T10:00:00+00:00", "completed_at": "2026-10-20T10:15:00+00:00",
                     "date": "2026-10-20", "status": "completed", "recipes": [{"name": "炒蛋"}]}]
    memory = MemoryRepository(sessions.path, Journal())
    result = memory.search_history("炒蛋", kind="meals")
    assert len(result["items"]) == 1 and result["items"][0]["source"] == "journal"
    assert result["items"][0]["meal_id"] == "meal"
    assert result["items"][0]["created_at"] == "2026-10-20T10:15:00+00:00"
