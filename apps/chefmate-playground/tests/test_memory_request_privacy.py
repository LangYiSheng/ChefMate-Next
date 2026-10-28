"""Real HTTP consent gates project private tool history only at model entry."""

import copy
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from chefmate_agent import AgentConfig, ChefMateAgent
from chefmate_playground.components import ComponentRegistry
from chefmate_playground.config import APP_ROOT
from chefmate_workflow import UIAction, UISnapshot

from test_personal_tenant_isolation import isolated_app as _isolated_app
from test_tenancy import PROFILE, register

isolated_app = _isolated_app
PRIVATE_MARKERS = (
    "PRIVATE_READ_RECEIPT", "PRIVATE_SEARCH_QUERY", "PRIVATE_SEARCH_RECEIPT",
    "PRIVATE_SUMMARY_ARGUMENT", "PRIVATE_SUMMARY_RECEIPT",
)
VISIBLE_TEXT = "VISIBLE_OLD_ASSISTANT_CONVERSATION"


class RecordingModel(BaseChatModel):
    histories: list = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self):
        return "offline-memory-privacy-recording-model"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _generate(self, messages, **kwargs):
        raise NotImplementedError("Only the local async model is used")

    async def _agenerate(self, messages, **kwargs):
        self.histories.append(copy.deepcopy(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="只查看本次餐单。"))])

    async def _astream(self, messages, **kwargs):
        self.histories.append(copy.deepcopy(messages))
        yield ChatGenerationChunk(message=AIMessageChunk(content="只查看本次餐单。",
            usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}))


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _private_history():
    history = [{"role": "user", "content": "之前读取过个人资料。"}]
    for index, (name, arguments, result) in enumerate((
        ("read_long_term_memory", {}, {"status": "ok", "memory": {"user_notes": PRIVATE_MARKERS[0]}}),
        ("search_personal_history", {"query": PRIVATE_MARKERS[1], "limit": 6, "kind": "all"},
         {"status": "ok", "items": [{"text": PRIVATE_MARKERS[2]}]}),
        ("maintain_memory_summary", {"expected_revision": 1, "summary": PRIVATE_MARKERS[3]},
         {"status": "ok", "memory": {"summary": PRIVATE_MARKERS[4]}}),
    )):
        identity = "old-private-call-" + str(index)
        history.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": identity, "type": "function", "function": {"name": name, "arguments": _encoded(arguments)},
        }]})
        history.append({"role": "tool", "name": name, "tool_call_id": identity, "content": _encoded(result)})
    history.append({"role": "assistant", "content": VISIBLE_TEXT})
    return history


def _install_recording_agent(app, context, user_id, *, component_enabled):
    names = tuple(name for name in context.config.enabled_components
                  if component_enabled or name != "personal-memory")
    control = app.state.pool.control.state.context
    control.config = replace(control.config, enabled_components=names)
    context.config = replace(context.config, enabled_components=names)
    components = ComponentRegistry(names, instruction_root=APP_ROOT / "skills", resources={
        "retrieval": context.retrieval, "memory": context.memory, "cookbook": context.cookbook,
        "journal": context.journal, "preferences": context.preferences,
    })
    model = RecordingModel()
    agent = ChefMateAgent(context.retrieval, AgentConfig(model="offline-privacy-model", reply_suggestions=False),
        model=model, extra_tools=components.build_tools(), skills=components.instructions,
        ui_context_model=UISnapshot, action_model=UIAction)
    context.agent = context.manager.agent = agent
    context.components = components
    # Match the applied global revision so acquiring this same tenant does not
    # replace its explicitly injected local model with a provider-backed agent.
    app.state.pool.revisions[user_id] = app.state.pool.revision()
    return model


@pytest.mark.parametrize("mode", ["component_disabled", "user_disabled", "enabled_empty", "revoke_at_entry"])
def test_private_history_request_respects_both_switches_without_rewriting_history(isolated_app, mode, monkeypatch):
    app = isolated_app
    owner = next(user for user in app.state.accounts.list_users() if user["role"] == "admin")
    app.state.accounts.update_pricing(owner["id"], {"input_per_million": "0", "output_per_million": "0"})
    with TestClient(app) as client:
        user = register(client, "request-privacy")
        preferences = client.get("/api/personal-preferences").json()
        assert client.patch("/api/personal-preferences", json={
            "revision": preferences["revision"], "enabled": mode != "user_disabled",
        }).status_code == 200
        session = client.post("/api/sessions").json()
        context = app.state.pool.workspaces[user["id"]].state.context
        # Empty current context must not hide that private results from a prior
        # turn are still included when memory is allowed.
        context.memory.clear(context.memory.snapshot()["revision"])
        model = _install_recording_agent(app, context, user["id"],
            component_enabled=mode != "component_disabled")
        saved = context.repository.get(session["id"])
        history = _private_history()
        saved["history"] = copy.deepcopy(history)
        saved["messages"] = [{"id": "old-user", "role": "user", "content": history[0]["content"]},
                             {"id": "old-assistant", "role": "assistant", "content": VISIBLE_TEXT}]
        context.repository.save(saved)
        assert not any(context.memory.context().get(key) for key in ("user_notes", "summary", "kitchen_profile"))
        if mode == "revoke_at_entry":
            original = context.manager.account_usage.before_model

            def revoke_then_check(turn, measurement):
                assert measurement["memory_attached"] is True
                service = context.personal_preferences
                service.update(service.snapshot()["revision"], {"enabled": False})
                original(turn, measurement)

            monkeypatch.setattr(context.manager.account_usage, "before_model", revoke_then_check)
        response = client.post(f"/api/sessions/{session['id']}/turn", json={"message": "只看本次餐单。"})
        assert response.status_code == 200, response.text
        after = context.repository.get(session["id"])
        assert after["history"][:len(history)] == history
        if mode == "revoke_at_entry":
            assert '"code": "profile_consent_changed"' in response.text
            assert model.histories == []
            assert after["last_status"] == "error"
            return
        assert after["last_status"] == "completed", after["last_error"]
        assert len(model.histories) == 1
        sent = _encoded([message.model_dump(mode="json") for message in model.histories[0]])
        assert VISIBLE_TEXT in sent
        assert PROFILE["display_name"] in sent
        assert "当前用户称呼" in sent
        permitted = mode == "enabled_empty"
        capture = context.repository.debug_run(session["id"], after["last_run"]["id"])
        assert capture["debug_trace"]
        captured = _encoded(capture["debug_trace"])
        for marker in PRIVATE_MARKERS:
            assert (marker in sent) is permitted
            assert (marker in captured) is permitted
        usage = after["messages"][-1]["context_usage"]
        assert usage["memory_attached"] is permitted
