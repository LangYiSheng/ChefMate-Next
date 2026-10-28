"""Confirming the same batch size cannot erase preparation or cooking progress."""

import copy

import pytest

import test_workflow
from test_workflow import action, choose, prepare

kitchen = test_workflow.kitchen


@pytest.mark.parametrize("explicit_portion", [False, True])
def test_same_servings_preserve_the_batch_after_returning_to_prep(kitchen, explicit_portion):
    _, host, _, _ = kitchen
    if explicit_portion:
        choose(kitchen, servings={"r1": 2})
    prepare(kitchen)
    action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": "r1:step:0"})
    action(kitchen, "start_timer", {"seconds": 60, "label": "还在焖", "recipe_id": "r1"})
    assert action(kitchen, "change_stage", {"stage": "prep"})["status"] == "ok"
    before = copy.deepcopy(host.state)
    confirmed = action(kitchen, "change_servings", {"recipe_id": "r1", "servings": 2})
    assert confirmed["status"] == "ok"
    for key in ("prep_status", "prep_availability", "step_status", "active_recipe_id", "active_step_id", "timers", "meal_id", "started_at"):
        assert host.state[key] == before[key], key
    assert host.state["meal_plan"][0]["portion_override"] is True
    assert host.state["meal_plan"][0]["target_servings"] == 2
    revision = host.state["revision"]
    assert action(kitchen, "change_servings", {"recipe_id": "r1", "servings": 2})["status"] == "ok"
    assert host.state["revision"] == revision
    assert host.state["step_status"] == before["step_status"]
    assert host.state["timers"] == before["timers"]
