"""Safe preference HTTP/tool contracts never expose general application configuration."""

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chefmate_agent import ToolExecutionContext
from chefmate_agent.runner import TurnRuntimeContext
from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.components import DEFAULT_COMPONENTS
from chefmate_playground.settings import build_agent, load_overrides
from chefmate_playground.ui_preferences import AppPreferences, PreferencesUpdateRequest, make_preferences_tools
from test_app import Agent, Service


def application(tmp_path):
    return create_app(PlaygroundConfig(sessions_database=tmp_path / "preferences.db", api_key="private-test-key",
        model="fixture-model", base_url="https://private.example/v1", reply_suggestion_count=5), service=Service(), agent=Agent())


def test_appearance_seed_preserves_interaction_and_delta_persists_during_a_run(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        before = client.get("/api/ui-preferences").json()
        assert set(before["values"]) == set(AppPreferences.model_fields)
        assert before["initialized"] is False and before["values"]["reply_suggestion_count"] == 5
        assert "private" not in str(before) and "model" not in str(before)
        app.state.manager.active["busy"] = object()
        try:
            saved = client.patch("/api/ui-preferences", json={"revision": before["revision"], "changes": {
                "theme": "dark", "dark_palette": "night"}})
        finally:
            app.state.manager.active.clear()
        assert saved.status_code == 200
        current = saved.json()
        assert current["initialized"] is True and current["values"]["theme"] == "dark"
        assert current["values"]["reply_suggestion_count"] == 5
        assert client.get("/api/settings").json()["ui_preferences"] == current
        assert client.patch("/api/ui-preferences", json={"revision": current["revision"],
            "changes": {"font_pairing": "wenkai"}}).status_code == 200
        restored = load_overrides(PlaygroundConfig(sessions_database=tmp_path / "preferences.db"))
        assert restored.ui_preferences["theme"] == "dark" and restored.ui_preferences["font_pairing"] == "wenkai"
        assert restored.reply_suggestion_count == 5 and restored.api_key == "private-test-key"


def test_preference_conflict_returns_current_safe_snapshot_and_does_not_overwrite(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        before = client.get("/api/ui-preferences").json()
        saved = client.patch("/api/ui-preferences", json={"revision": before["revision"], "changes": {"theme": "dark"}}).json()
        conflict = client.patch("/api/ui-preferences", json={"revision": before["revision"], "changes": {"font_pairing": "serif"}})
        assert conflict.status_code == 409 and conflict.json()["detail"]["preferences"] == saved
        assert client.get("/api/ui-preferences").json() == saved
        assert "private-test-key" not in conflict.text


@pytest.mark.parametrize("changes", [
    {"api_key": "cannot-write"}, {"model": "cannot-write"}, {"developer": True}, {"runtime": {}},
    {"context": {"max_history_chars": 99}}, {"enabled_components": []}, {"ui_preferences": {"theme": "dark"}},
    {"theme": {"api_key": "nested"}}, {"__proto__": {"theme": "dark"}}, {"constructor": {}},
    {"theme": "dark", "base_url": "https://other.example"}, {"inline_limit": True}, {"inline_limit": "1"},
    {"reply_suggestions": 0}, {"reply_suggestion_count": True}, {"light_palette": "night"}, {},
])
def test_unknown_nested_dangerous_and_wrong_typed_preferences_fail_closed(tmp_path, changes):
    app = application(tmp_path)
    with TestClient(app) as client:
        before = client.get("/api/ui-preferences").json()
        with pytest.raises(ValidationError):
            PreferencesUpdateRequest.model_validate({"revision": before["revision"], "changes": changes})
        response = client.patch("/api/ui-preferences", json={"revision": before["revision"], "changes": changes})
        assert response.status_code == 422 and isinstance(response.json()["detail"], str)
        assert client.get("/api/ui-preferences").json() == before
        assert not (tmp_path / "settings.json").exists()


def test_tools_use_real_caller_guard_delta_and_only_public_snapshot(tmp_path):
    app = application(tmp_path)
    context = app.state.context
    tools = {tool.name: tool for tool in make_preferences_tools(context.preferences)}
    assert all(tool.required_skill == "app-preferences" for tool in tools.values())
    read = tools["get_app_preferences"].handler({}, ToolExecutionContext({}, None, []))
    assert set(read) == {"status", "preferences"} and "private" not in str(read)
    values = {"revision": read["preferences"]["revision"], "changes": {"theme": "dark"}}
    assert asyncio.run(tools["update_app_preferences"].handler(values, ToolExecutionContext({}, None, [])))["status"] == "session_required"
    session_id = context.repository.create()["id"]
    context.repository.mutate(session_id, lambda state: state.update(running=True, active_turn={"id": "turn"}))
    host = context.workspace.for_turn(session_id, "turn", asyncio.Event(), 0)
    execution = ToolExecutionContext({}, host, [{"role": "user", "content": "把主题改成深色"}])
    saved = asyncio.run(tools["update_app_preferences"].handler(values, execution))
    assert saved["status"] == "ok" and saved["changed"] == {"theme": "dark"}
    assert saved["preferences"]["values"]["font_pairing"] == "mixed"
    assert asyncio.run(tools["update_app_preferences"].handler(values, execution))["status"] == "preferences_changed"
    host.cancelled.set()
    assert asyncio.run(tools["update_app_preferences"].handler({"revision": saved["preferences"]["revision"],
        "changes": {"density": "compact"}}, execution))["status"] == "turn_closed"
    assert context.preferences.read()["values"]["density"] == "comfortable"


def test_preference_skill_catalog_and_disabled_execution_are_bound(tmp_path):
    app = application(tmp_path)
    context = app.state.context
    disabled = replace(context.config, enabled_components=tuple(name for name in DEFAULT_COMPONENTS if name != "app-preferences"))
    agent, components = build_agent(disabled, context.retrieval, memory=context.memory, cookbook=context.cookbook,
                                    journal=context.journal, preferences=context.preferences)
    runtime = TurnRuntimeContext(ui_context={"stage": "idea"})
    assert "get_app_preferences" not in {tool.name for tool in agent._model_tools(runtime)}
    result = asyncio.run(agent._make_tool("get_app_preferences", runtime, [], "forged").ainvoke({}))
    assert result["status"] == "skill_disabled"
    catalog = {item["name"]: item for item in agent.tool_catalog({"stage": "idea"})}
    assert catalog["update_app_preferences"]["required_skill"] == "app-preferences"
    assert catalog["update_app_preferences"]["available"] is False
    assert components.by_id["app-preferences"].tools == ("get_app_preferences", "update_app_preferences")


def test_reply_policy_update_preserves_model_config_and_updates_future_turns(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        before = client.get("/api/ui-preferences").json()
        response = client.patch("/api/ui-preferences", json={"revision": before["revision"],
            "changes": {"reply_suggestions": False, "reply_suggestion_count": 2}})
        assert response.status_code == 200
        assert response.json()["initialized"] is False
        assert app.state.manager.agent.config.reply_suggestions is False
        assert app.state.manager.agent.config.reply_suggestion_count == 2
        assert app.state.manager.agent.config.model == "fixture-model"
        assert app.state.manager.reply_suggestions is False
        assert load_overrides(app.state.config).reply_suggestion_count == 2


def test_native_tool_patch_keeps_unsupplied_preferences_and_revision_ignores_secrets(tmp_path):
    app = application(tmp_path)
    context = app.state.context
    original = context.preferences.read()
    saved = asyncio.run(context.preferences.update(original["revision"], {"theme": "dark", "font_pairing": "wenkai"}))
    context.config = replace(context.config, api_key="another-secret", model="another-model")
    assert context.preferences.read() == saved["preferences"]
    session_id = context.repository.create()["id"]
    context.repository.mutate(session_id, lambda state: state.update(running=True, active_turn={"id": "native-turn"}))
    host = context.workspace.for_turn(session_id, "native-turn", asyncio.Event(), 0)
    agent, _ = build_agent(context.config, context.retrieval, memory=context.memory, cookbook=context.cookbook,
                           journal=context.journal, preferences=context.preferences)
    runtime = TurnRuntimeContext(ui_context={"stage": "idea"}, host_context=host)
    arguments = {"revision": saved["preferences"]["revision"], "changes": {"density": "compact"}}
    tool = agent._make_tool("update_app_preferences", runtime, [], "native-preference", tool_arguments=arguments)
    result = asyncio.run(tool.ainvoke(arguments))
    assert result["status"] == "ok" and result["changed"] == {"density": "compact"}
    assert result["preferences"]["values"]["theme"] == "dark"
    assert result["preferences"]["values"]["font_pairing"] == "wenkai"


def test_failed_preference_persistence_leaves_the_live_snapshot_unchanged(tmp_path, monkeypatch):
    app = application(tmp_path)
    with TestClient(app) as client:
        before = client.get("/api/ui-preferences").json()
        def fail(_):
            raise OSError("test storage failure")
        monkeypatch.setattr("chefmate_playground.settings.persist_settings", fail)
        response = client.patch("/api/ui-preferences", json={"revision": before["revision"], "changes": {"theme": "dark"}})
        assert response.status_code == 503
        assert client.get("/api/ui-preferences").json() == before
