"""Durable human answers and actual, private developer request captures."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.sessions import SessionRepository
from test_app import Agent, Service, events

DEVELOPER = {"X-ChefMate-Developer": "1"}


class DebugAgent(Agent):
    def tool_catalog(self, ui_context=None):
        return [{"name": "find_ingredients", "description": "查真实食材", "parameters": {"type": "object"},
                 "available": True, "allowed_stages": None}]

    async def run_turn(self, messages, on_event=None, **kwargs):
        await on_event({"type": "debug_context", "context": {
            "round": 1, "system": "真实系统提示", "messages": [{"role": "user", "content": messages[-1]["content"]},
                {"role": "tool", "content": json.dumps({"password": "private-password", "count": 2})}],
            "tools": self.tool_catalog(), "reasoning_content": "private-reasoning",
            "image": "data:image/jpeg;base64,YWJj", "credential": "Bearer private-token",
        }})
        await on_event({"type": "skill_event", "name": "meal-planning", "status": "ok", "call_id": "skill-1", "chars": 100})
        if messages[-1]["content"] == "失败":
            raise RuntimeError("provider failure")
        return await super().run_turn(messages, on_event=on_event, **kwargs)


@pytest.mark.parametrize("message,status", [("你好", "completed"), ("失败", "error")])
def test_debug_captures_are_opt_in_redacted_durable_and_never_ordinary_sse(tmp_path, message, status):
    config = PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "debug.db")
    with TestClient(create_app(config, service=Service(), agent=DebugAgent())) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        stream = client.post(uri + "/turn", json={"message": message})
        assert all(item["type"] not in {"debug_context", "skill_event"} for item in events(stream))
        assert "debug_trace" not in json.dumps(client.get(uri).json())
        assert client.get(uri + "/debug").status_code == 404
        debug = client.get(uri + "/debug", headers=DEVELOPER).json()
        assert debug["capture"]["status"] == status
        assert debug["capture"]["debug_trace"][0]["system"] == "真实系统提示"
        assert debug["capture"]["skill_events"][0]["name"] == "meal-planning"
        encoded = json.dumps(debug)
        assert all(secret not in encoded for secret in ("private-password", "private-token", "private-reasoning", "YWJj"))
        assert debug["tools"][0]["name"] == "find_ingredients"
        turn_id = debug["capture"]["turn_id"]
        assert client.get(uri + "/debug?turn_id=missing", headers=DEVELOPER).status_code == 404
    with TestClient(create_app(config, service=Service(), agent=DebugAgent())) as client:
        assert client.get(uri + f"/debug?turn_id={turn_id}", headers=DEVELOPER).json()["capture"]["status"] == status


def test_parameter_integrity_update_enriches_one_request_instead_of_counting_twice(tmp_path):
    class BoundaryAgent(Agent):
        async def run_turn(self, messages, *, on_event, **kwargs):
            trace = {"round": 1, "system": "产品提示", "messages": [], "tools": []}
            await on_event({"type": "debug_context", "context": trace})
            await on_event({"type": "debug_context", "context": {
                **trace, "parameter_integrity": {"protocol": "responses", "status": "verified", "calls": []}}})
            # Exercise the interrupted/failure fallback that uses emitted records.
            raise RuntimeError("test interruption")

    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "boundary.db"), service=Service(), agent=BoundaryAgent())
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        response = client.post(uri + "/turn", json={"message": "边界记录"})
        assert all(event["type"] != "debug_context" for event in events(response))
        data = client.get(uri + "/debug", headers=DEVELOPER).json()
        assert data["runs"][0]["model_calls"] == 1
        assert len(data["capture"]["debug_trace"]) == 1
        assert data["capture"]["debug_trace"][0]["parameter_integrity"]["status"] == "verified"


class QuestionAgent(Agent):
    async def run_turn(self, messages, *, on_event, tool_context, **kwargs):
        self.calls += 1
        if self.calls == 1:
            result = tool_context.execute("ask_user", {"question": "想用哪种厨具？", "options": [
                {"id": "pot", "label": "汤锅"}, {"id": "wok", "label": "炒锅"}], "allow_custom": True},
                SimpleNamespace(messages=messages, call_id="question-1"))
            assert result["status"] == "ok"
            question = result["pending_question"]
            return {"status": "completed", "assistant_text": question["question"], "messages": [
                *messages, {"role": "assistant", "content": question["question"]}], "awaiting_user": True,
                "pending_question": question, "actions": result.get("actions", [])}
        assert kwargs["ui_context"]["last_answer"]["labels"] == ["汤锅"]
        return {"status": "completed", "assistant_text": "按汤锅继续。", "messages": [
            *messages, {"role": "assistant", "content": "按汤锅继续。"}]}


def test_waiting_question_blocks_turn_until_atomic_answer_then_continues_once(tmp_path):
    agent = QuestionAgent()
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "questions.db"), service=Service(), agent=agent)
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        final = next(item for item in events(client.post(uri + "/turn", json={"message": "先问我厨具"})) if item["type"] == "result")
        assert final["awaiting_user"] is True and final["suggested_replies"] == []
        saved = client.get(uri).json()
        assert saved["awaiting_user"] and client.get("/api/sessions").json()["items"][0]["awaiting_user"]
        assert client.post(uri + "/turn", json={"message": "绕过问题"}).status_code == 409
        snapshot = client.get(uri + "/workspace").json()["ui_snapshot"]
        question = snapshot["pending_question"]
        answer = {"type": "answer_question", "action_id": "answer-once", "base_revision": snapshot["revision"],
                  "payload": {"question_id": question["id"], "option_ids": ["pot"], "text": ""}}
        response = client.post(uri + "/actions", json=answer).json()
        assert response["status"] == "ok"
        assert client.post(uri + "/actions", json=answer).json()["workspace"]["revision"] == response["workspace"]["revision"]
        assert client.get(uri).json()["awaiting_user"] is False
        request = {"message": "我选择汤锅，请继续。", "request_id": "answer:" + question["id"]}
        assert events(client.post(uri + "/turn", json=request))[-1]["status"] == "completed"
        assert events(client.post(uri + "/turn", json=request))[-1]["status"] == "completed"
        assert agent.calls == 2
        assert events(client.post(uri + "/turn", json={"message": "再解释一下"}))[-1]["status"] == "completed"
        assert client.post(uri + "/turn", json=request).status_code == 409
        assert agent.calls == 3
        assert any(item.get("request_id") == request["request_id"] for item in client.get(uri).json()["messages"])


def test_debug_retention_keeps_recent_twenty_and_session_delete_removes_records(tmp_path):
    repo = SessionRepository(tmp_path / "retention.db")
    identity = repo.create()["id"]
    for index in range(22):
        repo.save_debug_run(identity, str(index), {"turn_id": str(index), "created_at": f"2026-10-21T00:00:{index:02d}",
                                                  "debug_trace": [{"system": "原文" * 1000}], "status": "completed"})
    assert len(repo.debug_runs(identity)) == 20 and repo.debug_run(identity, "0") is None
    assert repo.debug_run(identity, "21")["debug_trace"][0]["system"] == "原文" * 1000
    repo.delete(identity)
    assert repo.debug_runs(identity) == []
