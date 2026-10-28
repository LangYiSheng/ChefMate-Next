"""Returning to planning after a finished meal starts a fresh independent batch."""

import copy
from datetime import timedelta

import pytest

import test_workflow
from test_workflow import action, choose, prepare, tool

kitchen = test_workflow.kitchen


@pytest.mark.parametrize("diners", [None, 5])
@pytest.mark.parametrize("portion_mode", ["shared", "per_dish"])
def test_finished_meal_return_to_planning_keeps_frozen_portions_but_not_execution(kitchen, diners, portion_mode):
    service, host, retrieval, clock = kitchen
    assert tool(kitchen, "select_recipes", {"recipe_ids": ["r1", "r2"], "data_snapshot": retrieval.snapshot,
        "expected_revision": 0, "diners": diners, "portion_mode": portion_mode, "servings": {"r1": 4}})["status"] == "ok"
    assert tool(kitchen, "advance_cooking_stage", {"stage": "prep"})["status"] == "ok"
    for view in service.get_workspace()["views"]:
        if view["kind"] == "prep_checklist":
            for item in view["data"]["items"]:
                assert action(kitchen, "check_prep_item", {"item_id": item["id"], "status": "ready"})["status"] == "ok"
    assert tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})["status"] == "ok"
    assert action(kitchen, "start_timer", {"seconds": 60, "label": "这餐的钟"})["status"] == "ok"
    for recipe_id in ("r1", "r2"):
        for ordinal in (0, 1):
            assert action(kitchen, "complete_step", {"recipe_id": recipe_id, "step_id": f"{recipe_id}:step:{ordinal}"})["status"] == "ok"
    assert action(kitchen, "complete_meal", {})["status"] == "ok"
    finished = copy.deepcopy(host.state)
    read_count = len(retrieval.calls)
    # New preferences and a reimported source cannot silently rewrite the
    # already chosen quantities or frozen source when restarting this menu.
    assert action(kitchen, "update_preferences", {"values": {"servings": 8}})["status"] == "ok"
    retrieval.recipes["r1"]["name"] = "后来重新导入的不同菜名"
    clock[0] += timedelta(days=1)
    result = action(kitchen, "change_stage", {"stage": "planning"}, action_id="restart-finished")
    assert result["status"] == "ok"
    assert host.state["meal_id"] != finished["meal_id"]
    assert host.state["meal_plan"] == finished["meal_plan"]
    assert host.state["meal_diners"] == diners and host.state["portion_mode"] == portion_mode
    assert host.state["preferences"]["servings"] == 8
    assert host.state["started_at"] is None and host.state["completed_at"] is None
    assert host.state["prep_status"] == {} and host.state["step_status"] == {} and host.state["timers"] == []
    assert service.meal_record() is None and len(retrieval.calls) == read_count
    identity = host.state["meal_id"]
    assert action(kitchen, "change_stage", {"stage": "planning"}, action_id="restart-finished")["status"] == "ok"
    assert action(kitchen, "change_stage", {"stage": "planning"})["status"] == "ok"
    assert host.state["meal_id"] == identity
    assert tool(kitchen, "advance_cooking_stage", {"stage": "prep"})["status"] == "ok"
    assert tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})["status"] == "prep_incomplete"


def test_return_to_planning_during_an_unfinished_meal_preserves_its_identity_and_progress(kitchen):
    service, host, _, _ = kitchen
    prepare(kitchen)
    assert action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": "r1:step:0"})["status"] == "ok"
    assert action(kitchen, "start_timer", {"seconds": 60, "label": "仍在做的这一餐"})["status"] == "ok"
    before = copy.deepcopy(host.state)
    assert tool(kitchen, "advance_cooking_stage", {"stage": "prep"})["status"] == "ok"
    assert tool(kitchen, "advance_cooking_stage", {"stage": "planning"})["status"] == "ok"
    choose(kitchen)
    for key in ("meal_id", "meal_plan", "meal_diners", "portion_mode", "started_at", "completed_at", "prep_status", "step_status", "timers"):
        assert host.state[key] == before[key], key
    assert service.meal_record()["status"] == "attempted"
