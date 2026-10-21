"""Persistent timers through real HTTP actions, with a clock and no provider."""

import copy
from datetime import datetime, timedelta, timezone
import importlib
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from chefmate_agent import SkillRegistry
from chefmate_playground import PlaygroundConfig
from chefmate_playground.sessions import SessionRepository
from chefmate_workflow import WorkflowService


SNAPSHOT = {"data_version": "cooking-http-fixture", "imported_at": "2026-10-21T00:00:00+00:00"}


class CookingSource:
    recipe = {"recipe_id": "eggs", "name": "炒滑蛋", "servings": 1., "estimated_servings": False,
        "total_time_min": 5., "difficulty": "beginner", "warnings": [],
        "ingredients": [{"ordinal": 0, "ingredient_id": "egg", "canonical_name": "鸡蛋", "raw_name": "鸡蛋",
            "raw_text": "鸡蛋2个", "raw_amount": "2", "raw_unit": "个", "grams": None,
            "scalable": True, "estimated_grams": False}],
        "steps": [{"instruction": "小火轻推30秒。", "timer_seconds": 30}, "离火盛盘。"]}

    def get_recipe(self, request):
        assert request == {"recipe_id": "eggs", "expected_snapshot": SNAPSHOT}
        return {"status": "ok", "snapshot": copy.deepcopy(SNAPSHOT), "recipe": copy.deepcopy(self.recipe)}

    def get_search_options(self):
        return {"status": "ok", "snapshot": copy.deepcopy(SNAPSHOT), "recipe_count": 1}


@pytest.fixture
def kitchen_http(tmp_path, monkeypatch):
    app_module = importlib.import_module("chefmate_playground.app")
    interactions = importlib.import_module("chefmate_playground.interactions")
    clock = [datetime(2026, 10, 21, tzinfo=timezone.utc)]
    monkeypatch.setattr(interactions, "WorkflowService", lambda *args, **kwargs: WorkflowService(
        *args, **kwargs, now=lambda: clock[0]))
    monkeypatch.setattr(app_module, "build_agent", lambda config, source: (SimpleNamespace(), SkillRegistry()))
    repository = SessionRepository(tmp_path / "cooking.db")
    app = app_module.create_app(PlaygroundConfig(sessions_database=repository.path), service=CookingSource(),
                                repository=repository)
    session_id = repository.create()["id"]
    uri = f"/api/sessions/{session_id}"
    domain = app.state.workspace.service(session_id)
    approved = [{"recipe_id": "eggs", "data_snapshot": SNAPSHOT}]
    selected = domain.execute_tool("select_recipes", {"recipe_ids": ["eggs"], "data_snapshot": SNAPSHOT,
        "expected_revision": 0}, approved_recipes=approved, operation_id="select")
    assert selected["status"] == "ok"
    assert domain.execute_tool("advance_cooking_stage", {"stage": "prep"}, operation_id="prep")["status"] == "ok"
    prep = domain.execute_tool("show_prep_checklist", {}, operation_id="prep-view")
    with TestClient(app) as client:
        def workspace():
            response = client.get(uri + "/workspace")
            assert response.status_code == 200
            return response.json()

        def action(kind, payload, operation_id=None):
            response = client.post(uri + "/actions", json={"type": kind, "payload": payload,
                "action_id": operation_id or str(uuid4()), "base_revision": workspace()["ui_snapshot"]["revision"]})
            assert response.status_code == 200
            return response.json()

        for entry in next(view["data"]["items"] for view in prep["views"] if view["kind"] == "prep_checklist"):
            assert action("check_prep_item", {"item_id": entry["id"], "status": "ready"})["status"] == "ok"
        assert domain.execute_tool("advance_cooking_stage", {"stage": "cooking"}, operation_id="cooking")["status"] == "ok"
        assert domain.execute_tool("show_cooking_guide", {"recipe_id": "eggs"}, operation_id="guide")["status"] == "ok"
        yield SimpleNamespace(client=client, uri=uri, session_id=session_id, repository=repository,
                              clock=clock, workspace=workspace, action=action)


def timers(response):
    return next(view["data"]["timers"] for view in response["views"] if view["kind"] == "timers")


def test_step_start_is_idempotent_and_conflicting_duration_never_replaces_the_clock(kitchen_http):
    kitchen = kitchen_http
    payload = {"recipe_id": "eggs", "step_id": "eggs:step:0", "seconds": 30, "label": "滑蛋"}
    first = kitchen.action("start_timer", payload)
    timer = timers(first)[0]
    kitchen.clock[0] += timedelta(seconds=7)
    duplicate = kitchen.action("start_timer", payload)
    assert duplicate["status"] == "ok" and len(timers(duplicate)) == 1
    assert timers(duplicate)[0]["id"] == timer["id"] and timers(duplicate)[0]["deadline_at"] == timer["deadline_at"]
    conflict = kitchen.action("start_timer", {**payload, "seconds": 60})
    assert conflict["status"] == "timer_already_exists"
    assert timers(conflict)[0]["seconds"] == 30 and timers(conflict)[0]["deadline_at"] == timer["deadline_at"]
    guide = next(view for view in duplicate["views"] if view["kind"] == "cooking_guide")["data"]
    assert guide["timers"][0]["id"] == timer["id"] and guide["server_now"]


def test_pause_resume_expiry_and_terminal_cancel_survive_http_reads_without_hidden_writes(kitchen_http):
    kitchen = kitchen_http
    first = kitchen.action("start_timer", {"seconds": 3, "label": "三秒", "recipe_id": "eggs", "step_id": "eggs:step:0"})
    timer_id = timers(first)[0]["id"]
    kitchen.clock[0] += timedelta(seconds=1)
    paused = kitchen.action("pause_timer", {"timer_id": timer_id})
    assert paused["status"] == "ok" and timers(paused)[0]["remaining_seconds"] == 2
    kitchen.clock[0] += timedelta(seconds=100)
    restored = timers(kitchen.workspace())[0]
    assert restored["status"] == "paused" and restored["remaining_seconds"] == 2 and restored["deadline_at"] is None
    resumed = kitchen.action("resume_timer", {"timer_id": timer_id})
    deadline = timers(resumed)[0]["deadline_at"]
    assert deadline == (kitchen.clock[0] + timedelta(seconds=2)).isoformat()
    kitchen.clock[0] += timedelta(seconds=3)
    before_read = kitchen.repository.get(kitchen.session_id)
    expired = kitchen.workspace()
    assert timers(expired)[0]["status"] == "finished" and timers(expired)[0]["remaining_seconds"] == 0
    assert kitchen.repository.get(kitchen.session_id) == before_read
    assert before_read["workspace"]["timers"][0]["status"] == "running"
    ended = kitchen.action("cancel_timer", {"timer_id": timer_id})
    assert ended["status"] == "ok" and timers(ended)[0]["status"] == "finished"
    repeated = kitchen.action("cancel_timer", {"timer_id": timer_id})
    assert repeated["status"] == "ok" and timers(repeated)[0]["status"] == "finished"
    assert kitchen.repository.get(kitchen.session_id)["workspace"]["timers"][0]["status"] == "finished"


def test_short_timer_history_cannot_evict_an_older_running_timer(kitchen_http):
    kitchen = kitchen_http
    first = kitchen.action("start_timer", {"seconds": 3600, "label": "长汤锅"})
    long_timer = timers(first)[0]
    for index in range(32):
        result = kitchen.action("start_timer", {"seconds": 1, "label": f"短计时{index}"})
        assert result["status"] == "ok"
        kitchen.clock[0] += timedelta(seconds=2)
    current = kitchen.workspace()
    active = next(timer for timer in timers(current) if timer["id"] == long_timer["id"])
    assert active["status"] == "running" and active["deadline_at"] == long_timer["deadline_at"]
    assert len(timers(current)) <= 30
    raw = kitchen.repository.get(kitchen.session_id)["workspace"]["timers"]
    assert any(timer["id"] == long_timer["id"] and timer["status"] == "running" for timer in raw)
