"""HTTP/persistence tests use an injected agent, never a production mock fallback."""

import asyncio
import json

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.runtime import TurnConflict, TurnManager
from chefmate_playground.sessions import SessionRepository


class Service:
    def get_search_options(self):
        return {"status": "ok", "snapshot": {"data_version": "fixture", "imported_at": "2026-01-01"},
                "recipe_count": 1}

    def find_ingredients(self, request):
        return {"status": "ok", "items": [{"ingredient_id": "shrimp", "canonical_name": "虾"}]}

    def search_recipes(self, request):
        return {"status": "ok", "total": 1, "items": [{"recipe_id": "r1", "name": "测试菜"}]}

    def get_recipe(self, request):
        if (request.get("expected_snapshot") or {}).get("data_version") == "old":
            return {"status": "snapshot_changed", "message": "数据已更新"}
        if request["recipe_id"] == "missing":
            return {"status": "not_found"}
        return {"status": "ok", "recipe": {"recipe_id": "r1", "name": "测试菜", "steps": ["测试步骤"]}}


class Agent:
    def __init__(self):
        self.calls = 0

    async def run_turn(self, messages, on_event=None, cancel_event=None, *, ui_context=None, tool_context=None):
        self.calls += 1
        await on_event({"type": "tool_start", "call_id": "c1", "name": "find_ingredients", "arguments": {}})
        await on_event({"type": "tool_end", "call_id": "c1", "name": "find_ingredients", "result": {"status": "ok"}})
        await on_event({"type": "assistant_delta", "text": "查到了"})
        return {"status": "completed", "assistant_text": "查到了测试菜", "messages": [
            *messages, {"role": "assistant", "content": "查到了测试菜"}],
            "tool_calls": [], "recipes": [{"recipe_id": "r1", "name": "测试菜"}], "selected_recipe": None}


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def build(tmp_path, agent=None):
    config = PlaygroundConfig(sessions_database=tmp_path / "sessions.db")
    return create_app(config, service=Service(), agent=agent or Agent())


def test_real_http_flow_persists_history_and_request_replay(tmp_path):
    agent = Agent()
    app = build(tmp_path, agent)
    with TestClient(app) as client:
        assert client.get("/api/status").json()["ready"] is True
        session = client.post("/api/sessions").json()
        uri = f"/api/sessions/{session['id']}"
        first = client.post(uri + "/turn", json={"message": "我想吃虾", "request_id": "same-request"})
        assert first.status_code == 200
        assert [event["type"] for event in events(first)] == [
            "tool_start", "tool_end", "assistant_delta", "result", "done",
        ]
        replay = client.post(uri + "/turn", json={"message": "我想吃虾", "request_id": "same-request"})
        assert events(replay)[-1]["status"] == "completed"
        assert agent.calls == 1
        saved = client.get(uri).json()
        assert "history" not in saved
        assert len(saved["messages"]) == 2
        assert saved["messages"][-1]["recipes"][0]["recipe_id"] == "r1"
        assert client.get("/api/recipes/r1").json()["recipe"]["steps"] == ["测试步骤"]
        assert client.get("/api/recipes/missing").status_code == 404
        assert client.post("/api/tools/find_ingredients", json={"queries": ["虾"]}).status_code == 200
        assert client.post("/api/tools/does_not_exist", json={}).status_code == 404
        stale = client.post("/api/tools/get_recipe", json={
            "recipe_id": "r1", "expected_snapshot": {"data_version": "old"},
        }).json()
        assert stale["status"] == "snapshot_changed"
        assert "recipe" not in stale
    with TestClient(build(tmp_path)) as client:
        assert len(client.get(uri).json()["messages"]) == 2


def test_input_validation_and_same_origin_boundary(tmp_path):
    with TestClient(build(tmp_path)) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = f"/api/sessions/{session_id}/turn"
        assert client.post(uri, json={"message": "   "}).status_code == 422
        assert client.post(uri, json={"message": "ok", "unexpected": True}).status_code == 422
        assert client.post("/api/sessions", headers={"Origin": "https://external.example"}).status_code == 403
        assert client.post("/api/sessions", headers={"Origin": "http://testserver"}).status_code == 200
        assert client.get("/api/sessions", headers={"Host": "external.example"}).status_code == 403


def test_cancel_before_task_starts_and_same_session_conflict(tmp_path):
    class WaitingAgent:
        async def run_turn(self, *args, **kwargs):
            await asyncio.sleep(60)

    async def exercise():
        repo = SessionRepository(tmp_path / "cancel.db")
        state = repo.create()
        manager = TurnManager(repo, WaitingAgent())
        turn, _ = await manager.begin(state["id"], "我想吃虾")
        try:
            await manager.begin(state["id"], "第二条")
        except TurnConflict:
            pass
        else:
            raise AssertionError("overlapping turns were accepted")
        assert await manager.cancel(state["id"])
        await turn.task
        saved = repo.get(state["id"])
        assert saved["running"] is False
        assert saved["last_status"] == "cancelled"
        assert saved["history"] == []
        assert len(saved["messages"]) == 1
        await manager.close()

    asyncio.run(exercise())


def test_failed_retry_does_not_duplicate_user_and_recovery_is_durable(tmp_path):
    class FlakyAgent(Agent):
        async def run_turn(self, messages, on_event=None, cancel_event=None, *, ui_context=None, tool_context=None):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("do not reveal a provider token or DSN")
            return await super().run_turn(messages, on_event, cancel_event,
                                          ui_context=ui_context, tool_context=tool_context)

    with TestClient(build(tmp_path, FlakyAgent())) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = f"/api/sessions/{session_id}"
        failed = client.post(uri + "/turn", json={"message": "我想吃虾"})
        assert "provider token" not in failed.text
        assert any(event["type"] == "error" for event in events(failed))
        assert len(client.get(uri).json()["messages"]) == 1
        success = client.post(uri + "/turn", json={"message": "我想吃虾", "retry": True})
        assert events(success)[-1]["status"] == "completed"
        assert len(client.get(uri).json()["messages"]) == 2


def test_restart_marks_pending_turn_interrupted(tmp_path):
    path = tmp_path / "restart.db"
    original = SessionRepository(path)
    state = original.create()
    state.update(running=True, active_turn={"id": "old"})
    original.save(state)
    reopened = SessionRepository(path).get(state["id"])
    assert reopened["running"] is False
    assert reopened["last_error"]["code"] == "interrupted"


def test_request_id_cannot_replay_a_different_message(tmp_path):
    agent = Agent()
    with TestClient(build(tmp_path, agent)) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = f"/api/sessions/{session_id}/turn"
        assert client.post(uri, json={"message": "我想吃虾", "request_id": "one"}).status_code == 200
        assert client.post(uri, json={"message": "我想吃鸡蛋", "request_id": "one"}).status_code == 409
        assert agent.calls == 1


def test_followup_after_failed_turn_retains_human_intent_without_failed_tool_history(tmp_path):
    class FollowupAgent(Agent):
        async def run_turn(self, messages, on_event=None, cancel_event=None, *, ui_context=None, tool_context=None):
            if self.calls == 0:
                self.calls += 1
                await on_event({"type": "tool_end", "call_id": "failed-turn-tool", "name": "find_ingredients",
                                "result": {"status": "ok"}})
                raise RuntimeError("failed before completing reply")
            assert [{"role": m["role"], "content": m["content"]} for m in messages] == [
                {"role": "user", "content": "我想吃虾"},
                {"role": "user", "content": "半小时以内"},
            ]
            return await super().run_turn(messages, on_event, cancel_event,
                                          ui_context=ui_context, tool_context=tool_context)

    with TestClient(build(tmp_path, FollowupAgent())) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = f"/api/sessions/{session_id}"
        client.post(uri + "/turn", json={"message": "我想吃虾"})
        result = client.post(uri + "/turn", json={"message": "半小时以内"})
        assert events(result)[-1]["status"] == "completed"
        assert [item["role"] for item in client.get(uri).json()["messages"]] == ["user", "user", "assistant"]
