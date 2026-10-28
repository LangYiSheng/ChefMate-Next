"""A conversational next-step request persists a scoped card update through SSE."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from test_app import Agent, Service, events

SNAPSHOT = {"data_version": "fixture", "imported_at": "2026-10-24T00:00:00+00:00"}


class CookingService(Service):
    def get_recipe(self, request):
        return {"status": "ok", "snapshot": SNAPSHOT, "recipe": {
            "recipe_id": "r1", "name": "测试汤", "steps": ["清洗食材", "加水煮开", "盛盘"],
            "ingredients": [{"ingredient_id": "water", "canonical_name": "水"}]}}


class NavigationAgent(Agent):
    async def run_turn(self, messages, on_event=None, *, ui_context=None, tool_context=None, **kwargs):
        self.calls += 1
        result = tool_context.execute("navigate_cooking_step", {"expected_revision": ui_context["revision"], "direction": "next"},
                                      SimpleNamespace(messages=messages, call_id="next-step"))
        assert result["status"] == "ok"
        for ui_action in result["actions"]:
            await on_event({"type": "ui_action", "action": ui_action})
        return {"status": "completed", "assistant_text": "下一步加水煮开。", "actions": result["actions"],
                "messages": [*messages, {"role": "assistant", "content": "下一步加水煮开。"}]}


def test_next_request_and_replay_move_one_session_card_only_once(tmp_path):
    agent = NavigationAgent()
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "navigation.db"), service=CookingService(), agent=agent)
    with TestClient(app) as client:
        session = client.post("/api/sessions").json()["id"]
        other = client.post("/api/sessions").json()["id"]
        workflow = app.state.workspace.service(session)
        chosen = workflow.execute_tool("select_recipes", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT, "expected_revision": 0},
                                       approved_recipes=[{"recipe_id": "r1", "data_snapshot": SNAPSHOT}])
        assert chosen["status"] == "ok"
        workflow.execute_tool("advance_cooking_stage", {"stage": "prep"})
        workflow.execute_tool("update_prep_items", {"expected_revision": workflow.model_snapshot()["revision"],
            "items": [{"item_id": "r1:ingredient:0", "status": "ready"}]})
        cooking = workflow.execute_tool("advance_cooking_stage", {"stage": "cooking"})
        old_card = cooking["actions"][0]["component"]["id"]
        uri = f"/api/sessions/{session}"
        body = {"message": "下一步吧", "request_id": "next-once"}
        response = client.post(uri + "/turn", json=body)
        display = next(event for event in events(response) if event["type"] == "ui_action")
        card = display["action"]["component"]
        assert display["session_id"] == session and card["id"] == old_card
        assert card["data"]["active_step_id"] == "r1:step:1"
        assert card["data"]["steps"][0]["status"] == "pending"
        assert events(client.post(uri + "/turn", json=body))[-1]["status"] == "completed" and agent.calls == 1
        saved = client.get(uri).json()
        assert saved["messages"][-1]["actions"][0]["component"]["data"]["active_step_id"] == "r1:step:1"
        assert client.get(f"/api/sessions/{other}/workspace").json()["ui_snapshot"]["active_step_id"] is None
