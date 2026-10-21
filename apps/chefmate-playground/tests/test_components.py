"""Component contracts drive the actual agent boundary, not a second UI list."""

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from chefmate_agent import InjectedToolSpec
from chefmate_agent.runner import TurnRuntimeContext
from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.components import COMPONENTS, DEFAULT_COMPONENTS, ComponentRegistry, ComponentSpec
from chefmate_playground.config import APP_ROOT
from chefmate_playground.cookbook import CookbookRepository
from chefmate_playground.journal import JournalRepository
from chefmate_playground.memory import MemoryRepository
from chefmate_playground.settings import build_agent, load_overrides, persist_settings, public_settings
from test_app import Agent, Service


def registry(enabled=(), **kwargs):
    return ComponentRegistry(enabled, instruction_root=APP_ROOT / "skills", **kwargs)


def test_manifest_metadata_includes_guidance_tools_ui_and_ownership():
    components = registry(DEFAULT_COMPONENTS)
    assert {item["name"] for item in components.metadata()} >= {"个人菜谱", "饮食记录", "长期记忆"}
    assert {item["id"] for item in components.metadata() if item["enabled"]} == set(DEFAULT_COMPONENTS)
    plans = components.by_id["meal-planning"]
    assert "meal_plan" in plans.ui_kinds and "select_recipes" in plans.tools
    food = next(item for item in components.metadata() if item["id"] == "food-journal")
    assert food["ui"]["pages"] == ["journal"]
    assert food["dependencies"] == ["journal"] and not food["available"]
    assert food["instructions"] == {"entrypoint": "skills/food-journal/SKILL.md", "loading": "on_demand"}
    assert "food-journal" in {item["name"] for item in components.instructions.list_metadata()}


def test_component_manifest_rejects_duplicate_owners_and_unowned_factory_tools():
    with pytest.raises(ValueError, match="one explicit owner"):
        registry(specs=(ComponentSpec("a", "A", "A", ("same",)), ComponentSpec("b", "B", "B", ("same",))))
    with pytest.raises(ValueError, match="one explicit owner"):
        registry(specs=(ComponentSpec("a", "A", "A", (), stage_targets=("prep",)),
                        ComponentSpec("b", "B", "B", (), stage_targets=("prep",))))
    class Empty(BaseModel):
        pass
    spec = InjectedToolSpec("undeclared_write", "unlisted operation", Empty, lambda request, context: {})
    with pytest.raises(ValueError, match="no system component"):
        registry().bind_tool_specs([spec])
    misplaced = replace(spec, name="create_recipe_draft", required_skill="personal-memory")
    with pytest.raises(ValueError, match="ownership mismatch"):
        registry().bind_tool_specs([misplaced])


def test_component_factory_cannot_silently_omit_declared_tools(monkeypatch):
    monkeypatch.setattr("chefmate_playground.cookbook.make_recipe_tools", lambda _: [])
    components = registry(resources={"cookbook": object()})
    with pytest.raises(ValueError, match="factory omitted declared tools"):
        components.build_tools()


def test_actual_disabled_components_are_hidden_and_reject_fabricated_execution(tmp_path):
    path = tmp_path / "components.db"
    journal = JournalRepository(path)
    memory = MemoryRepository(path, journal=journal)
    cookbook = CookbookRepository(path)
    config = PlaygroundConfig(sessions_database=path, api_key="test-key", model="test-model", enabled_components=())
    agent, components = build_agent(config, Service(), memory=memory, cookbook=cookbook, journal=journal)
    context = TurnRuntimeContext(ui_context={"stage": "idea"})
    hidden = {"read_long_term_memory", "create_recipe_draft", "create_food_log_draft", "get_search_options"}
    assert hidden.isdisjoint({tool.name for tool in agent._model_tools(context)})
    assert "ask_user" in {tool.name for tool in agent._model_tools(context)}
    for name, request in [("read_long_term_memory", {}), ("get_search_options", {}),
                          ("advance_cooking_stage", {"stage": "cooking"})]:
        result = asyncio.run(agent._make_tool(name, context, [], "forged").ainvoke(request))
        assert result["status"] == "skill_disabled"
    assert components.enabled_ids == ()
    assert all(item["available"] for item in components.metadata())


def test_generated_stage_targets_keep_each_transition_owners_permission():
    names = ["advance_cooking_stage", *(tool for item in COMPONENTS for tool in item.tools)]
    bindings = registry().tool_bindings(names)
    assert bindings["advance_cooking_stage"] == {
        "idea": "meal-planning", "planning": "meal-planning", "prep": "pantry-preparation",
        "cooking": "cooking-guide", "completed": "cooking-guide"}
    assert bindings["create_food_log_draft"] == "food-journal"
    assert bindings["confirm_recipe_deletion"] == "recipe-authoring"


def test_current_components_config_roundtrips_and_old_skills_format_is_rejected(tmp_path):
    config = PlaygroundConfig(sessions_database=tmp_path / "config.db", enabled_components=("food-journal",))
    persist_settings(config)
    assert load_overrides(PlaygroundConfig(sessions_database=config.sessions_database)).enabled_components == ("food-journal",)
    assert "enabled_skills" not in public_settings(config)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"enabled_skills": ["meal-planning"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="设置文件无效"):
        load_overrides(config)
    for invalid in (("unknown",), ("food-journal", "food-journal"), (["food-journal"],)):
        with pytest.raises(ValueError, match="系统组件配置无效"):
            replace(config, enabled_components=invalid)


def test_component_settings_cas_and_debug_catalog_match_live_runtime(tmp_path):
    config = PlaygroundConfig(sessions_database=tmp_path / "http.db", api_key="test-key", model="test-model")
    app = create_app(config, service=Service(), agent=Agent())
    with TestClient(app) as client:
        initial = client.get("/api/settings").json()
        assert initial["available_components"] == list(DEFAULT_COMPONENTS)
        assert all(item["available"] for item in initial["components"])
        body = {"revision": initial["revision"], "section": "components", "values": {"enabled_components": []}}
        saved = client.patch("/api/settings", json=body)
        assert saved.status_code == 200 and saved.json()["enabled_components"] == []
        assert client.patch("/api/settings", json=body).status_code == 409
        assert not any(item["enabled"] for item in client.get("/api/components").json()["items"])
        assert client.get("/api/status").json()["enabled_components"] == []
        catalog = client.get("/api/debug/catalog", headers={"X-ChefMate-Developer": "1"}).json()
        assert "components" in catalog and "skills" not in catalog
        assert not next(item for item in catalog["tools"] if item["name"] == "create_food_log_draft")["available"]
        assert client.get("/api/skills").status_code == 404
        assert client.patch("/api/settings", json={"revision": saved.json()["revision"], "section": "skills",
                                                  "values": {"enabled_skills": []}}).status_code == 422


@pytest.mark.parametrize("names", [["missing"], ["food-journal", "food-journal"], [None], [{"id": "food-journal"}]])
def test_invalid_component_selection_cannot_replace_or_persist_configuration(tmp_path, names):
    config = PlaygroundConfig(sessions_database=tmp_path / "invalid.db")
    app = create_app(config, service=Service(), agent=Agent())
    with TestClient(app) as client:
        before = client.get("/api/settings").json()
        response = client.patch("/api/settings", json={"revision": before["revision"], "section": "components",
                                                       "values": {"enabled_components": names}})
        assert response.status_code == 422
        assert client.get("/api/settings").json()["revision"] == before["revision"]
        assert not (tmp_path / "settings.json").exists()
