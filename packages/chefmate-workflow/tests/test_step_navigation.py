"""Model navigation moves the real step without claiming the user completed it."""

import copy

import pytest

from chefmate_workflow import WorkflowService
import test_workflow
from test_workflow import action, choose, tool

kitchen = test_workflow.kitchen


@pytest.fixture
def cooking(kitchen):
    service, host, retrieval, _ = kitchen
    retrieval.recipes["r1"]["steps"] = ["清洗食材", "加入食材煮5分钟", "盛盘"]
    choose(kitchen, ids=["r1", "r2"])
    tool(kitchen, "advance_cooking_stage", {"stage": "prep"})
    for item in service.get_workspace()["views"][-1]["data"]["items"]:
        action(kitchen, "check_prep_item", {"item_id": item["id"], "status": "ready"})
    tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})
    return kitchen


def navigate(cooking, direction="next", **arguments):
    return tool(cooking, "navigate_cooking_step", {
        "expected_revision": cooking[1].state["revision"], "direction": direction, **arguments})


def test_next_refreshes_the_existing_card_while_previous_step_remains_incomplete(cooking):
    service, host, _, _ = cooking
    original = next(view for view in service.get_workspace()["views"] if view["kind"] == "cooking_guide")
    revision = host.state["revision"]
    moved = navigate(cooking)
    card = moved["actions"][0]["component"]
    assert moved["status"] == "ok" and moved["actions"][0]["placement"] == "inline"
    assert card["id"] == original["id"] and card["kind"] == "cooking_guide"
    assert card["revision"] == revision + 1 and card["presentation_revision"] > original["presentation_revision"]
    assert card["data"]["active_step_id"] == "r1:step:1"
    assert [step["status"] for step in card["data"]["steps"]] == ["pending", "current", "pending"]
    assert host.state["stage"] == "cooking"


def test_navigation_can_review_done_steps_without_changing_completion(cooking):
    _, host, _, _ = cooking
    action(cooking, "complete_step", {"recipe_id": "r1", "step_id": "r1:step:0"})
    done = {key for key, value in host.state["step_status"].items() if value == "done"}
    reviewed = navigate(cooking, "previous")
    assert reviewed["actions"][0]["component"]["data"]["active_step_id"] == "r1:step:0"
    assert {key for key, value in host.state["step_status"].items() if value == "done"} == done
    assert navigate(cooking, "step", step_number=3)["actions"][0]["component"]["data"]["active_step_id"] == "r1:step:2"
    assert navigate(cooking, "current")["actions"][0]["component"]["data"]["active_step_id"] == "r1:step:2"


@pytest.mark.parametrize("direction,number,status", [("previous", None, "first_step"), ("step", 4, "invalid_reference")])
def test_navigation_boundaries_do_not_mutate_the_task(cooking, direction, number, status):
    _, host, _, _ = cooking
    before = copy.deepcopy(host.state)
    result = navigate(cooking, direction, **({"step_number": number} if number is not None else {}))
    assert result["status"] == status and result["actions"] == []
    assert host.state == before
    navigate(cooking, "step", step_number=3)
    before = copy.deepcopy(host.state)
    assert navigate(cooking)["status"] == "last_step"
    assert host.state == before and not any(value == "done" for value in host.state["step_status"].values())


def test_navigation_replay_and_stale_version_never_advance_twice(cooking):
    _, host, _, _ = cooking
    request = {"expected_revision": host.state["revision"], "direction": "next"}
    first = tool(cooking, "navigate_cooking_step", request, operation_id="next-once")
    assert first["status"] == "ok"
    before = copy.deepcopy(host.state)
    replay = tool(cooking, "navigate_cooking_step", request, operation_id="next-once")
    assert replay["status"] == "ok" and replay["actions"] == [] and host.state == before
    stale = tool(cooking, "navigate_cooking_step", request, operation_id="stale-next")
    assert stale["status"] == "revision_conflict" and host.state == before


@pytest.mark.parametrize("arguments,status", [
    ({"recipe_id": "r3"}, "invalid_reference"),
    ({"step_number": 2}, "invalid_arguments"),
    ({"direction": "step"}, "invalid_arguments"),
    ({"direction": "step", "step_number": 0}, "invalid_arguments"),
])
def test_invalid_navigation_is_atomic(cooking, arguments, status):
    _, host, _, _ = cooking
    before = copy.deepcopy(host.state)
    result = navigate(cooking, **arguments)
    assert result["status"] == status and result["actions"] == [] and host.state == before


@pytest.mark.parametrize("clear_timers", [False, True])
def test_navigation_respects_existing_explicit_timer_policy(cooking, clear_timers):
    _, host, retrieval, clock = cooking
    service = WorkflowService(retrieval, host.get, host.mutate, now=lambda: clock[0],
                              cooking_settings={"clear_timer_on_step_change": clear_timers})
    timer = service.execute_tool("set_cooking_timer", {"seconds": 60, "label": "本步骤提醒",
                                  "recipe_id": "r1", "step_id": "r1:step:0"}, user_text="计时60秒")
    assert timer["status"] == "ok"
    identity = host.state["timers"][0]["id"]
    result = service.execute_tool("navigate_cooking_step", {"expected_revision": host.state["revision"], "direction": "next"})
    assert result["status"] == "ok"
    assert (identity in {item["id"] for item in host.state["timers"]}) is not clear_timers


def test_navigation_requires_cooking_stage_and_validates_selected_dish(cooking):
    assert navigate(cooking, "step", step_number=2, recipe_id="r2")["ui_snapshot"]["active_step_id"] == "r2:step:1"
    tool(cooking, "advance_cooking_stage", {"stage": "prep"})
    assert navigate(cooking)["status"] == "stage_conflict"
