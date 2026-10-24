"""Kitchen display events retain identity in SSE and session history."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from test_app import Agent, events
from test_interactions import form_app


class DisplayAgent(Agent):
    async def run_turn(self, messages, on_event=None, *, tool_context=None, **kwargs):
        presented = tool_context.execute("show_preference_form", {}, SimpleNamespace(messages=messages, call_id="form"))
        displayed = tool_context.execute("set_kitchen_display", {"mode": "focus"},
                                         SimpleNamespace(messages=messages, call_id="focus"))
        actions = [*presented["actions"], *displayed["actions"]]
        for ui_action in actions:
            await on_event({"type": "ui_action", "action": ui_action})
        return {"status": "completed", "assistant_text": "已进入专注模式", "messages": [
            *messages, {"role": "assistant", "content": "已进入专注模式"}], "actions": actions}


def test_display_sse_and_history_are_scoped_to_the_originating_session(tmp_path):
    with TestClient(form_app(tmp_path, DisplayAgent())) as client:
        session_id = client.post("/api/sessions").json()["id"]
        other_id = client.post("/api/sessions").json()["id"]
        response = client.post(f"/api/sessions/{session_id}/turn", json={"message": "全屏查看条件"})
        display_event = next(event for event in events(response)
                             if event["type"] == "ui_action" and event["action"]["kind"] == "set_kitchen_display")
        assert display_event["session_id"] == session_id
        request = display_event["action"]
        assert request["action_id"].startswith("agent:") and request["action_id"].endswith(":focus")
        assert request["mode"] == "focus" and request["view_kind"] == "preference_form"
        final = client.get(f"/api/sessions/{session_id}").json()
        assert request in final["messages"][-1]["actions"]
        assert client.get(f"/api/sessions/{other_id}").json()["messages"] == []
        snapshot = client.get(f"/api/sessions/{session_id}/workspace").json()["ui_snapshot"]
        assert snapshot["revision"] == 0 and snapshot["stage"] == "idea"
