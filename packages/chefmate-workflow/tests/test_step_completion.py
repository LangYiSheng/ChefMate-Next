"""Spoken single-step completion shares the UI transaction, separately from navigation."""

import copy

import pytest

import test_step_navigation
from test_workflow import tool

kitchen = test_step_navigation.kitchen
cooking = test_step_navigation.cooking


def complete(cooking, **arguments):
    return tool(cooking, "complete_cooking_step", {"expected_revision": cooking[1].state["revision"], **arguments})


def test_confirming_current_step_completes_only_it_and_refreshes_the_current_card(cooking):
    service, host, _, _ = cooking
    before = next(view for view in service.get_workspace()["views"] if view["kind"] == "cooking_guide")
    result = complete(cooking)
    card = result["actions"][0]["component"]
    assert result["status"] == "ok" and card["id"] == before["id"]
    assert card["data"]["active_step_id"] == "r1:step:1"
    assert [step["status"] for step in card["data"]["steps"]] == ["done", "current", "pending"]
    assert host.state["stage"] == "cooking" and host.state["revision"] == before["revision"] + 1
    undone = complete(cooking, recipe_id="r1", step_id="r1:step:0", completed=False)
    assert undone["actions"][0]["component"]["data"]["active_step_id"] == "r1:step:0"
    assert not any(status == "done" for status in host.state["step_status"].values())


def test_completion_replay_and_stale_version_cannot_complete_the_following_step(cooking):
    _, host, _, _ = cooking
    arguments = {"expected_revision": host.state["revision"]}
    first = tool(cooking, "complete_cooking_step", arguments, operation_id="finish-one")
    assert first["status"] == "ok"
    before = copy.deepcopy(host.state)
    replay = tool(cooking, "complete_cooking_step", arguments, operation_id="finish-one")
    assert replay["status"] == "ok" and replay["actions"] == [] and host.state == before
    assert tool(cooking, "complete_cooking_step", arguments)["status"] == "revision_conflict"
    assert host.state == before


@pytest.mark.parametrize("arguments,status", [
    ({"recipe_id": "r3", "step_id": "r3:step:0"}, "invalid_reference"),
    ({"recipe_id": "r1", "step_id": "r2:step:0"}, "invalid_reference"),
    ({"recipe_id": "r2"}, "step_required"),
    ({"step_ids": ["r1:step:0", "r1:step:1"]}, "invalid_arguments"),
])
def test_invalid_completion_never_changes_any_progress(cooking, arguments, status):
    _, host, _, _ = cooking
    before = copy.deepcopy(host.state)
    result = complete(cooking, **arguments)
    assert result["status"] == status and result["actions"] == [] and host.state == before


def test_finishing_one_dish_does_not_complete_the_other_or_end_the_meal(cooking):
    _, host, _, _ = cooking
    for _ in range(3):
        assert complete(cooking)["status"] == "ok"
    assert host.state["active_step_id"] is None and host.state["stage"] == "cooking"
    assert complete(cooking)["status"] == "step_required"
    assert not any(key.startswith("r2:") and value == "done" for key, value in host.state["step_status"].items())
    assert tool(cooking, "advance_cooking_stage", {"stage": "completed"})["status"] == "steps_incomplete"
    explicit = complete(cooking, recipe_id="r2", step_id="r2:step:0")
    assert explicit["ui_snapshot"]["active_recipe_id"] == "r2" and explicit["ui_snapshot"]["active_step_id"] == "r2:step:1"


def test_completion_is_not_allowed_before_the_cooking_stage(kitchen):
    from test_workflow import choose
    choose(kitchen)
    assert complete(kitchen)["status"] == "stage_conflict"
