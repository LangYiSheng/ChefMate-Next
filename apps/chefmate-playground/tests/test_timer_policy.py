"""Cooking settings and timer actions through isolated real HTTP boundaries."""

import importlib
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from chefmate_playground import PlaygroundConfig
from chefmate_playground.components import ComponentRegistry
from chefmate_playground.config import APP_ROOT
from chefmate_playground.sessions import SessionRepository
from chefmate_playground.settings import load_overrides
from test_cooking_timers import CookingSource, SNAPSHOT, timers


@pytest.fixture
def kitchen(tmp_path, monkeypatch):
    app_module = importlib.import_module("chefmate_playground.app")
    monkeypatch.setattr(app_module, "build_agent", lambda config, source, **kwargs: (
        SimpleNamespace(), ComponentRegistry(config.enabled_components, instruction_root=APP_ROOT / "skills")))
    config = PlaygroundConfig(sessions_database=tmp_path / "timer-policy.db")
    repository = SessionRepository(config.sessions_database)
    app = app_module.create_app(config, service=CookingSource(), repository=repository)
    session_id = repository.create()["id"]
    uri = f"/api/sessions/{session_id}"
    domain = app.state.workspace.service(session_id)
    result = domain.execute_tool("select_recipes", {"recipe_ids": ["eggs"], "data_snapshot": SNAPSHOT,
        "expected_revision": 0}, approved_recipes=[{"recipe_id": "eggs", "data_snapshot": SNAPSHOT}])
    assert result["status"] == "ok"
    assert domain.execute_tool("advance_cooking_stage", {"stage": "prep"})["status"] == "ok"
    prep = domain.execute_tool("show_prep_checklist", {})
    with TestClient(app) as client:
        def workspace():
            response = client.get(uri + "/workspace")
            assert response.status_code == 200
            return response.json()

        def action(kind, payload):
            response = client.post(uri + "/actions", json={"type": kind, "payload": payload,
                "action_id": str(uuid4()), "base_revision": workspace()["ui_snapshot"]["revision"]})
            assert response.status_code == 200
            return response.json()

        def save(limit=3, clear=False):
            before = client.get("/api/settings").json()
            response = client.patch("/api/settings", json={"revision": before["revision"], "section": "cooking",
                "values": {"timer_limit": limit, "clear_timer_on_step_change": clear}})
            assert response.status_code == 200, response.text
            return response.json()

        for item in next(view["data"]["items"] for view in prep["views"] if view["kind"] == "prep_checklist"):
            assert action("check_prep_item", {"item_id": item["id"], "status": "ready"})["status"] == "ok"
        assert domain.execute_tool("advance_cooking_stage", {"stage": "cooking"})["status"] == "ok"
        assert domain.execute_tool("show_cooking_guide", {"recipe_id": "eggs"})["status"] == "ok"
        yield SimpleNamespace(client=client, config=config, repository=repository, session_id=session_id,
            app=app, domain=domain, uri=uri, action=action, workspace=workspace, save=save)


def start(kitchen, label="厨房计时", **extra):
    return kitchen.action("start_timer", {"seconds": 300, "label": label, **extra})


def test_default_policy_limits_real_http_actions_to_three_live_timers(kitchen):
    expected = {"timer_limit": 3, "clear_timer_on_step_change": False}
    assert kitchen.client.get("/api/settings").json()["cooking"] == expected
    assert kitchen.workspace()["ui_snapshot"]["cooking_settings"] == expected
    for label in ("锅一", "锅二", "锅三"):
        assert start(kitchen, label)["status"] == "ok"
    assert kitchen.workspace()["ui_snapshot"]["timer_capacity"] == {
        "limit": 3, "active_count": 3, "available_slots": 0, "over_limit": False}
    before = kitchen.repository.get(kitchen.session_id)
    denied = start(kitchen, "锅四")
    assert denied["status"] == "timer_limit" and len(timers(denied)) == 3
    assert kitchen.repository.get(kitchen.session_id)["workspace"] == before["workspace"]


def test_single_mode_preserves_existing_clocks_and_applies_after_saved_settings_reload(kitchen):
    for label in ("已开一", "已开二"):
        assert start(kitchen, label)["status"] == "ok"
    before = timers(kitchen.workspace())
    saved = kitchen.save(1)
    assert saved["cooking"] == {"timer_limit": 1, "clear_timer_on_step_change": False}
    assert [(timer["id"], timer["deadline_at"], timer["status"]) for timer in timers(kitchen.workspace())] == [
        (timer["id"], timer["deadline_at"], timer["status"]) for timer in before]
    assert start(kitchen, "不允许新增")["status"] == "timer_limit"
    # The previously created service must read the current policy, too.
    assert kitchen.domain.model_snapshot()["cooking_settings"]["timer_limit"] == 1
    assert kitchen.domain.model_snapshot()["timer_capacity"] == {
        "limit": 1, "active_count": 2, "available_slots": 0, "over_limit": True}
    restored = load_overrides(kitchen.config)
    app_module = importlib.import_module("chefmate_playground.app")
    reloaded = app_module.create_app(restored, service=CookingSource())
    with TestClient(reloaded) as client:
        settings = client.get("/api/settings").json()
        assert settings["cooking"] == saved["cooking"]
        snapshot = client.get(kitchen.uri + "/workspace").json()["ui_snapshot"]
        assert snapshot["cooking_settings"] == saved["cooking"]
        response = client.post(kitchen.uri + "/actions", json={"action_id": str(uuid4()), "type": "start_timer",
            "base_revision": snapshot["revision"], "payload": {"seconds": 30, "label": "重启后仍限制"}})
        assert response.json()["status"] == "timer_limit"
    for timer in before:
        assert kitchen.action("cancel_timer", {"timer_id": timer["id"]})["status"] == "ok"
    assert start(kitchen, "重新启动一个")["status"] == "ok"
    assert start(kitchen, "单模式第二个")["status"] == "timer_limit"


def test_one_step_has_one_clock_and_a_new_start_replaces_its_terminal_record(kitchen):
    params = {"recipe_id": "eggs", "step_id": "eggs:step:0"}
    first = start(kitchen, "同一步", **params)
    initial = timers(first)[0]
    duplicate = start(kitchen, "同一步", **params)
    assert duplicate["status"] == "ok" and len(timers(duplicate)) == 1
    assert timers(duplicate)[0]["id"] == initial["id"]
    assert timers(duplicate)[0]["deadline_at"] == initial["deadline_at"]
    assert start(kitchen, "另一时长", seconds=600, **params)["status"] == "timer_already_exists"
    assert kitchen.action("cancel_timer", {"timer_id": initial["id"]})["status"] == "ok"
    restarted = start(kitchen, "重来", **params)
    matches = [timer for timer in timers(restarted) if timer["step_id"] == params["step_id"]]
    assert restarted["status"] == "ok" and len(matches) == 1 and matches[0]["id"] != initial["id"]


def test_auto_clear_follows_real_active_step_changes_and_leaves_browsing_and_manual_timers_alone(kitchen):
    step0 = {"recipe_id": "eggs", "step_id": "eggs:step:0"}
    step1 = {"recipe_id": "eggs", "step_id": "eggs:step:1"}
    first = start(kitchen, "第一步", **step0)
    first_id = timers(first)[0]["id"]
    assert kitchen.action("set_active_step", step1)["status"] == "ok"
    assert any(timer["id"] == first_id for timer in timers(kitchen.workspace()))
    kitchen.save(3, True)
    assert kitchen.action("set_active_step", step0)["status"] == "ok"
    assert start(kitchen, "手工锅钟")["status"] == "ok"
    assert start(kitchen, "下一步", **step1)["status"] == "ok"
    before = {timer["id"] for timer in timers(kitchen.workspace())}
    # Read/browse operations do not represent an actual active-step transition.
    assert kitchen.action("open_recipe_detail", {"recipe_id": "eggs"})["status"] == "ok"
    assert kitchen.action("set_active_step", step0)["status"] == "ok"
    assert {timer["id"] for timer in timers(kitchen.workspace())} == before
    done = kitchen.action("complete_step", {**step0, "completed": True})
    assert done["status"] == "ok" and done["ui_snapshot"]["active_step_id"] == step1["step_id"]
    remaining = timers(done)
    assert not any(timer["id"] == first_id for timer in remaining)
    assert {timer["label"] for timer in remaining} == {"手工锅钟", "下一步"}


def test_cooking_policy_validation_is_strict_and_rejected_values_are_not_persisted(kitchen):
    initial = kitchen.client.get("/api/settings").json()
    for limit, clear in [(0, False), (2, False), (4, False), (True, False), ("1", False), (1, "false"), (1, None)]:
        response = kitchen.client.patch("/api/settings", json={"revision": initial["revision"], "section": "cooking",
            "values": {"timer_limit": limit, "clear_timer_on_step_change": clear}})
        assert response.status_code == 422
    assert kitchen.client.get("/api/settings").json()["cooking"] == initial["cooking"]
    assert not (kitchen.config.sessions_database.parent / "settings.json").exists()


def test_cooking_policy_can_change_during_a_run_without_rebuilding_or_interrupting_its_agent(kitchen):
    manager = kitchen.app.state.manager
    agent = manager.agent
    active = object()
    manager.active[kitchen.session_id] = active
    try:
        saved = kitchen.save(1, True)
        assert saved["cooking"] == {"timer_limit": 1, "clear_timer_on_step_change": True}
        assert manager.agent is agent and manager.active[kitchen.session_id] is active
        assert kitchen.domain.model_snapshot()["cooking_settings"] == saved["cooking"]
        blocked = kitchen.client.patch("/api/settings", json={"revision": saved["revision"], "section": "interaction",
            "values": {"reply_suggestions": False}})
        assert blocked.status_code == 409
    finally:
        manager.active.pop(kitchen.session_id, None)
