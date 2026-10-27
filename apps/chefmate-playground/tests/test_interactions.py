"""Real HTTP domain operations and trusted snapshot boundaries, with no provider requests."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.interactions import TurnClosed, WorkspaceBinding
from chefmate_playground.runtime import TurnManager
from chefmate_playground.sessions import SessionRepository
from test_app import Agent, Service, events


class FormAgent(Agent):
    def __init__(self):
        super().__init__()
        self.contexts = []

    async def run_turn(self, messages, on_event=None, cancel_event=None, *, ui_context=None, tool_context=None):
        self.contexts.append(copy.deepcopy(ui_context))
        self.calls += 1
        actions = []
        if self.calls == 1:
            result = tool_context.execute("show_preference_form", {
                "fields": ["ingredient_terms", "max_total_time_min", "servings"],
            }, SimpleNamespace(messages=messages, call_id="form"))
            assert result["status"] == "ok"
            actions = result["actions"]
            await on_event({"type": "ui_action", "action": actions[0]})
        return {"status": "completed", "assistant_text": "已读取当前表单", "messages": [
            *messages, {"role": "assistant", "content": "已读取当前表单"}], "actions": actions,
            "tool_calls": [], "recipes": [], "selected_recipe": None}


def form_app(tmp_path, agent):
    return create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "ui.db"), service=Service(), agent=agent)


def test_model_presentation_user_edits_and_next_turn_trusted_snapshot(tmp_path):
    agent = FormAgent()
    with TestClient(form_app(tmp_path, agent)) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = f"/api/sessions/{session_id}"
        response = client.post(uri + "/turn", json={"message": "让我选一选条件"})
        assert events(response)[0]["type"] == "ui_action"
        assert all(event["session_id"] == session_id and event["sequence"] > 0 for event in events(response))
        workspace = client.get(uri + "/workspace").json()
        assert workspace["views"][0]["kind"] == "preference_form"
        original_revision = workspace["workspace"]["revision"]
        edit = {"type": "update_preferences", "action_id": "my-edit", "base_revision": original_revision,
                "view_id": workspace["views"][0]["id"],
                "payload": {"values": {"ingredient_terms": ["虾"], "max_total_time_min": 30}}}
        saved = client.post(uri + "/actions", json=edit).json()
        assert saved["status"] == "ok"
        assert saved["workspace"]["preferences"]["ingredient_terms"] == ["虾"]
        assert saved["workspace"]["preferences"]["max_total_time_min"] == 30
        assert saved["workspace"]["recent_actions"][-1]["type"] == "update_preferences"
        replay = client.post(uri + "/actions", json=edit).json()
        assert replay["workspace"]["revision"] == saved["workspace"]["revision"]
        stale = client.post(uri + "/turn", json={"message": "找菜", "ui_snapshot": workspace["workspace"]})
        assert stale.status_code == 409
        assert len(client.get(uri).json()["messages"]) == 2
        forged = copy.deepcopy(saved["workspace"])
        forged["preferences"]["ingredient_terms"] = ["伪造的界面值"]
        reply = client.post(uri + "/turn", json={"message": "按我刚改的条件找", "ui_snapshot": forged})
        assert events(reply)[-1]["status"] == "completed"
        assert agent.contexts[-1]["preferences"]["ingredient_terms"] == ["虾"]
        assert agent.contexts[-1]["preferences"]["max_total_time_min"] == 30
        # Refresh/restart preserves authoritative values, while raw frozen workspace stays private.
        assert "workspace" not in client.get(uri).json()
    with TestClient(form_app(tmp_path, FormAgent())) as client:
        assert client.get(uri + "/workspace").json()["workspace"]["preferences"]["ingredient_terms"] == ["虾"]


def test_action_schema_and_id_collision_cannot_overwrite_values(tmp_path):
    with TestClient(form_app(tmp_path, FormAgent())) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = f"/api/sessions/{session_id}"
        first = {"type": "update_preferences", "action_id": "edit", "base_revision": 0,
                 "payload": {"values": {"max_total_time_min": 30}}}
        assert client.post(uri + "/actions", json=first).json()["status"] == "ok"
        duplicate = {**first, "base_revision": 1, "payload": {"values": {"max_total_time_min": 120}}}
        assert client.post(uri + "/actions", json=duplicate).json()["status"] == "action_id_conflict"
        assert client.post(uri + "/actions", json={**first, "type": "execute_sql"}).status_code == 422
        assert client.post(uri + "/actions", json={**first, "payload": {"values": {"arbitrary": True}}}).status_code == 422
        current = client.get(uri + "/workspace").json()["workspace"]
        assert current["revision"] == 1 and current["preferences"]["max_total_time_min"] == 30
        assert {item["id"] for item in client.get("/api/components").json()["items"]} == {
            "meal-planning", "pantry-preparation", "cooking-guide", "recipe-authoring", "personal-memory", "food-journal", "app-preferences"}


def test_stage_is_agent_owned_and_published_on_conversation(tmp_path):
    class StageAgent(Agent):
        async def run_turn(self, messages, **kwargs):
            snapshot = Service().get_search_options()["snapshot"]
            snapshot["imported_at"] = "2026-01-01T00:00:00+00:00"
            evidence = {"role": "tool", "name": "search_recipes", "content": json.dumps({
                "status": "ok", "snapshot": snapshot, "items": [{"recipe_id": "r1"}]})}
            result = kwargs["tool_context"].execute("select_recipes", {
                "recipe_ids": ["r1"], "data_snapshot": snapshot,
                "expected_revision": kwargs["ui_context"]["revision"]},
                SimpleNamespace(messages=[*messages, evidence], call_id="stage"))
            assert result["status"] == "ok"
            return {"status": "completed", "assistant_text": "一起选菜吧", "messages": [
                *messages, {"role": "assistant", "content": "一起选菜吧"}], "actions": result["actions"]}

    with TestClient(form_app(tmp_path, StageAgent())) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = f"/api/sessions/{session_id}"
        denied = client.post(uri + "/actions", json={"type": "change_stage", "action_id": "jump",
                                                    "base_revision": 0, "payload": {"stage": "planning"}})
        assert denied.status_code == 403
        assert client.get(uri + "/workspace").json()["ui_snapshot"]["revision"] == 0
        assert events(client.post(uri + "/turn", json={"message": "帮我开始选菜"}))[-1]["status"] == "completed"
        state=client.get(uri).json()["conversation_state"]
        assert state["stage"] == "planning" and state["recipe_count"] == 1
        assert state["recipe_names"] == ["测试菜"]
        assert client.get("/api/sessions").json()["items"][0]["conversation_state"]["stage"] == "planning"


def test_active_model_cannot_overwrite_concurrent_user_edit(tmp_path):
    async def exercise():
        repository = SessionRepository(tmp_path / "race.db")
        session_id = repository.create()["id"]
        binding = WorkspaceBinding(repository, Service())
        started, release = asyncio.Event(), asyncio.Event()

        class PausedAgent:
            async def run_turn(self, messages, on_event=None, cancel_event=None, *, ui_context=None, tool_context=None):
                context = SimpleNamespace(messages=messages, call_id="first")
                assert tool_context.execute("show_preference_form", {}, context)["status"] == "ok"
                started.set()
                await release.wait()
                context.call_id = "stale"
                stale = tool_context.execute("show_preference_form", {}, context)
                assert stale["status"] == "revision_conflict"
                return {"status": "completed", "assistant_text": "保留用户修改", "messages": [
                    *messages, {"role": "assistant", "content": "保留用户修改"}], "actions": []}

        manager = TurnManager(repository, PausedAgent(), binding)
        turn, _ = await manager.begin(session_id, "展示表单")
        await started.wait()
        user = binding.service(session_id).apply_action({"type": "update_preferences", "action_id": "user-edit",
                                                       "base_revision": 0, "payload": {"values": {"servings": 3}}})
        assert user["status"] == "ok"
        release.set()
        await turn.task
        actual = binding.service(session_id).model_snapshot()
        assert actual["revision"] == 1 and actual["preferences"]["servings"] == 3
        assert repository.get(session_id)["last_status"] == "completed"
        await manager.close()
    asyncio.run(exercise())


def test_cancelled_run_host_context_rejects_late_task_write(tmp_path):
    async def exercise():
        repository = SessionRepository(tmp_path / "closed.db")
        session_id = repository.create()["id"]
        binding = WorkspaceBinding(repository, Service())

        class WaitingAgent:
            async def run_turn(self, *args, **kwargs):
                await asyncio.sleep(60)

        manager = TurnManager(repository, WaitingAgent(), binding)
        turn, _ = await manager.begin(session_id, "展示表单")
        await manager.cancel(session_id)
        await turn.task
        result = turn.host_context.execute("show_preference_form", {}, SimpleNamespace(messages=[], call_id="late"))
        assert result["status"] == "turn_closed"
        assert binding.service(session_id).model_snapshot()["revision"] == 0
        await manager.close()
    asyncio.run(exercise())


def test_cancel_during_synchronous_operation_rolls_back_transaction(tmp_path):
    repository = SessionRepository(tmp_path / "cancel-in-operation.db")
    session_id = repository.create()["id"]
    repository.mutate(session_id, lambda state: state.update(running=True, active_turn={"id": "active"}))
    cancelled = asyncio.Event()
    binding = WorkspaceBinding(repository, Service())
    scoped = binding.for_turn(session_id, "active", cancelled, 0)

    def late_operation(workspace):
        workspace.update(revision=1, preferences={"servings": 9})
        cancelled.set()
        return {"status": "ok"}

    with pytest.raises(TurnClosed):
        scoped._mutate(late_operation)
    assert scoped.expected_revision == 0
    assert binding.service(session_id).model_snapshot()["revision"] == 0
    assert repository.get(session_id)["workspace"] == {}
