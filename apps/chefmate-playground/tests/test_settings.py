"""设置验收使用隔离目录，不访问真实密钥或模型。"""
import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.settings import load_overrides, persist_settings, public_settings, resolve_model
from test_app import Agent, Service


def config(tmp_path):
    return PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "sessions.db", api_key="old-private-key",
                            model="old-model", base_url="https://old.example/v1")


def test_secrets_are_never_returned_and_new_host_cannot_reuse_old_key(tmp_path):
    initial = config(tmp_path)
    assert "old-private-key" not in str(public_settings(initial))
    with pytest.raises(ValueError):
        resolve_model(initial, {"model": "new", "base_url": "https://new.example/v1"})
    assert resolve_model(initial, {"model": "new", "base_url": initial.base_url}).api_key == initial.api_key
    with pytest.raises(ValueError):
        resolve_model(initial, {"model": "new", "base_url": "https://user:password@new.example/v1"})


def test_model_save_preserves_float_timeout_defaults_and_reloads(tmp_path):
    initial = replace(config(tmp_path), turn_timeout_s=300.0, model_timeout_s=120.0)
    candidate = resolve_model(initial, {"model": "new-model", "base_url": initial.base_url})
    persist_settings(candidate)
    restored = load_overrides(initial)
    assert restored.model == "new-model"
    assert restored.turn_timeout_s == 300.0
    assert restored.model_timeout_s == 120.0


def test_model_image_limit_applies_persists_and_rejects_invalid_values(tmp_path):
    initial = config(tmp_path)
    app = create_app(initial, service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        assert before["model"]["max_images"] == 3
        values = {**before["model"], "supports_images": True, "max_images": 7}
        saved = client.patch("/api/settings", json={"revision": before["revision"],
                             "section": "model", "values": values})
        assert saved.status_code == 200
        assert saved.json()["model"]["max_images"] == 7
        assert client.get("/api/status").json()["max_images"] == 7
        assert app.state.manager.agent.config.max_images == 7
        assert load_overrides(initial).max_images == 7
        for invalid in (0, 13, 2.5, True):
            response = client.patch("/api/settings", json={"revision": saved.json()["revision"],
                                    "section": "model", "values": {**values, "max_images": invalid}})
            assert response.status_code == 422
            assert load_overrides(initial).max_images == 7


def test_context_budget_saves_applies_and_survives_reload(tmp_path):
    initial = config(tmp_path)
    app = create_app(initial, service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        values = {"max_history_chars": 450000, "max_tool_result_chars": 75000}
        saved = client.patch("/api/settings", json={"revision": before["revision"], "section": "context", "values": values})
        assert saved.status_code == 200
        assert saved.json()["context"] == values
        assert app.state.manager.agent.config.max_history_chars == 450000
        assert app.state.manager.agent.config.max_tool_result_chars == 75000
        restored = load_overrides(initial)
        assert restored.max_history_chars == 450000
        assert restored.max_tool_result_chars == 75000
        invalid = client.patch("/api/settings", json={"revision": saved.json()["revision"], "section": "context",
            "values": {**values, "max_history_chars": 2000001}})
        assert invalid.status_code == 422
        assert load_overrides(initial).max_history_chars == 450000


def test_reply_suggestions_toggle_is_strict_applies_and_survives_reload(tmp_path):
    initial = config(tmp_path)
    app = create_app(initial, service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        assert before["interaction"] == {"reply_suggestions": True, "reply_suggestion_count": 3}
        saved = client.patch("/api/settings", json={"revision": before["revision"], "section": "interaction",
                                                   "values": {"reply_suggestions": False}})
        assert saved.status_code == 200
        assert saved.json()["interaction"] == {"reply_suggestions": False, "reply_suggestion_count": 3}
        assert client.get("/api/status").json()["reply_suggestions"] is False
        assert app.state.manager.reply_suggestions is False
        assert app.state.manager.agent.config.reply_suggestions is False
        assert load_overrides(initial).reply_suggestions is False
        for invalid in ("false", 0, None):
            response = client.patch("/api/settings", json={"revision": saved.json()["revision"],
                "section": "interaction", "values": {"reply_suggestions": invalid}})
            assert response.status_code == 422
        assert load_overrides(initial).reply_suggestions is False


def test_reply_count_applies_to_model_runtime_and_persistence_with_strict_bounds(tmp_path):
    initial = config(tmp_path)
    app = create_app(initial, service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        saved = client.patch("/api/settings", json={"revision": before["revision"], "section": "interaction",
            "values": {"reply_suggestions": True, "reply_suggestion_count": 5}})
        assert saved.status_code == 200
        assert saved.json()["interaction"]["reply_suggestion_count"] == 5
        assert client.get("/api/status").json()["reply_suggestion_count"] == 5
        assert app.state.manager.reply_suggestion_count == 5
        assert app.state.manager.agent.config.reply_suggestion_count == 5
        assert load_overrides(initial).reply_suggestion_count == 5
        for invalid in (0, 7, True, "3", 2.5, None):
            response = client.patch("/api/settings", json={"revision": saved.json()["revision"],
                "section": "interaction", "values": {"reply_suggestions": True, "reply_suggestion_count": invalid}})
            assert response.status_code == 422
        assert load_overrides(initial).reply_suggestion_count == 5


def test_persisted_model_applies_atomically_survives_reload_and_rejects_stale_tab(tmp_path):
    initial = config(tmp_path)
    app = create_app(initial, service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        body = {"revision": before["revision"], "section": "model",
                "values": {"model": "new-model", "base_url": "https://new.example/v1", "api_key": "new-private-key"}}
        saved = client.patch("/api/settings", json=body)
        assert saved.status_code == 200
        assert "private-key" not in saved.text
        assert client.get("/api/status").json()["model"] == "new-model"
        assert app.state.manager.agent.config.model == "new-model"
        assert client.patch("/api/settings", json=body).status_code == 409
        reloaded = load_overrides(initial)
        assert reloaded.model == "new-model"
        assert reloaded.api_key == "new-private-key"
        assert reloaded.base_url == "https://new.example/v1"


def test_active_run_blocks_settings_without_interrupting_it(tmp_path):
    app = create_app(config(tmp_path), service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        app.state.manager.active["busy"] = object()
        try:
            response = client.patch("/api/settings", json={"revision": before["revision"], "section": "model",
                "values": {"model": "changed", "base_url": "https://old.example/v1"}})
            assert response.status_code == 409
            assert app.state.config.model == "old-model"
            assert "busy" in app.state.manager.active
        finally:
            app.state.manager.active.clear()
        assert not (tmp_path / "settings.json").exists()


def test_invalid_skill_and_runtime_reject_before_writing(tmp_path):
    app = create_app(config(tmp_path), service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        for section, values in [("components", {"enabled_components": ["../outside"]}),
                                ("runtime", {**before["runtime"], "max_tool_calls": 1000})]:
            assert client.patch("/api/settings", json={"revision": before["revision"], "section": section,
                                                       "values": values}).status_code == 422
        assert client.get("/api/settings").status_code == 200
        assert not (tmp_path / "settings.json").exists()


def test_invalid_settings_request_never_echoes_submitted_secret(tmp_path):
    with TestClient(create_app(config(tmp_path), service=Service(), agent=Agent())) as client:
        response = client.patch("/api/settings", json={"section": "model", "values": {"api_key": "submitted-private-key"}})
        assert response.status_code == 422
        assert "submitted-private-key" not in response.text


def test_native_probe_rejects_text_tool_markup_and_keeps_errors_redacted(monkeypatch, tmp_path):
    from chefmate_playground.settings import probe_model
    from langchain_core.messages import AIMessage
    class FakeModel:
        def __init__(self, **kwargs):
            pass
        def bind_tools(self, *args, **kwargs):
            return self
        async def ainvoke(self, messages):
            return AIMessage(content='<tool_call>connection_echo chefmate</tool_call>')
    monkeypatch.setattr("chefmate_agent.provider.IntegrityChatOpenAI", FakeModel)
    assert asyncio.run(probe_model(replace(config(tmp_path), stream_tool_calls=False)))["ok"] is False


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
def test_probe_checks_native_roundtrip_for_the_selected_protocol(monkeypatch, tmp_path, stream, protocol):
    from chefmate_playground.settings import probe_model
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
    seen = {}
    class FakeModel:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        def bind_tools(self, *args, **kwargs):
            assert kwargs["tool_choice"] == "connection_echo"
            return self
        async def astream(self, messages):
            yield AIMessageChunk(content="", tool_call_chunks=[{"name": "connection_echo",
                "args": '{"value":"chefmate"}', "id": "probe-call", "index": 0}])
        async def ainvoke(self, messages):
            if len(messages) == 1:
                return AIMessage(content="", tool_calls=[{"name": "connection_echo", "args": {"value": "chefmate"},
                                                         "id": "probe-call"}])
            assert isinstance(messages[-1], ToolMessage)
            assert messages[-1].tool_call_id == "probe-call"
            return AIMessage(content="OK")
    monkeypatch.setattr("chefmate_agent.provider.IntegrityChatOpenAI", FakeModel)
    result = asyncio.run(probe_model(replace(config(tmp_path), api_format=protocol, stream_tool_calls=stream)))
    assert result["ok"] is True
    assert seen["use_responses_api"] is (protocol == "responses")
    assert seen["streaming"] is stream

