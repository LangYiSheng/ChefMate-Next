"""Preparation distinguishes unknown stock from explicit missing ingredients."""

import copy

import pytest

from chefmate_workflow import WorkflowService
import test_workflow
from test_workflow import action, choose, tool

kitchen = test_workflow.kitchen


def prep_view(result):
    return next(view for view in result["views"] if view["kind"] == "prep_checklist")


def update(kitchen, items, **extra):
    return tool(kitchen, "update_prep_items", {"expected_revision": kitchen[1].state["revision"], "items": items, **extra})


def test_explicit_confirmation_and_missing_stock_persist_without_blocking_prep(kitchen):
    _, host, retrieval, clock = kitchen
    choose(kitchen, ids=["r1", "r2"])
    entered = tool(kitchen, "advance_cooking_stage", {"stage": "prep"})
    assert entered["status"] == "ok"  # Preparing includes finding and buying missing ingredients.
    recorded = update(kitchen, [{"item_id": "r1:ingredient:0", "status": "ready"},
                               {"item_id": "r1:ingredient:1", "status": "pending", "availability": "missing"}])
    items = {item["id"]: item for item in prep_view(recorded)["data"]["items"]}
    assert (items["r1:ingredient:0"]["status"], items["r1:ingredient:0"]["availability"]) == ("ready", "available")
    assert (items["r1:ingredient:1"]["status"], items["r1:ingredient:1"]["availability"]) == ("pending", "missing")
    assert items["r2:ingredient:0"]["availability"] == "unknown"
    restored = WorkflowService(retrieval, host.get, host.mutate, now=lambda: clock[0]).get_workspace()
    compact = next(view for view in restored["ui_snapshot"]["components"] if view["kind"] == "prep_checklist")
    assert next(item for item in compact["data"]["items"] if item["id"] == "r1:ingredient:1")["availability"] == "missing"
    assert tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})["status"] == "prep_incomplete"


def test_multiselect_answers_keep_unselected_stock_unknown_until_an_explicit_update(kitchen):
    choose(kitchen)
    question = tool(kitchen, "ask_user", {"question": "哪些食材已经有了？", "multiple": True,
        "options": [{"id": "shrimp", "label": "虾仁已经有了"}, {"id": "salt", "label": "盐已经有了"}]})
    answer = action(kitchen, "answer_question", {"question_id": question["pending_question"]["id"], "option_ids": ["shrimp"]})
    assert answer["last_answer"]["labels"] == ["虾仁已经有了"]
    entered = tool(kitchen, "advance_cooking_stage", {"stage": "prep"})
    assert all(item["availability"] == "unknown" for item in prep_view(entered)["data"]["items"])
    recorded = update(kitchen, [{"item_id": "r1:ingredient:0", "status": "ready"}])
    items = prep_view(recorded)["data"]["items"]
    assert items[0]["availability"] == "available" and items[1]["availability"] == "unknown"
    assert recorded["last_answer"]["labels"] == ["虾仁已经有了"]


def test_a_repaired_purchase_clears_missing_and_allows_real_cooking_navigation(kitchen):
    choose(kitchen)
    tool(kitchen, "advance_cooking_stage", {"stage": "prep"})
    missing = update(kitchen, [{"item_id": "r1:ingredient:0", "status": "pending", "availability": "missing"},
                              {"item_id": "r1:ingredient:1", "status": "ready"}])
    stale = missing["ui_snapshot"]["revision"]
    question = tool(kitchen, "ask_user", {"question": "虾仁现在补齐了吗？", "options": [{"id": "yes", "label": "已经买齐"}]})
    action(kitchen, "answer_question", {"question_id": question["pending_question"]["id"], "option_ids": ["yes"]})
    rejected = update(kitchen, [{"item_id": "r1:ingredient:0", "status": "ready"}], expected_revision=stale)
    assert rejected["status"] == "revision_conflict"
    assert prep_view(rejected)["data"]["items"][0]["availability"] == "missing"
    repaired = update(kitchen, [{"item_id": "r1:ingredient:0", "status": "ready"}])
    assert all(item["availability"] == "available" for item in prep_view(repaired)["data"]["items"])
    assert tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})["status"] == "ok"
    current = tool(kitchen, "navigate_cooking_step", {"expected_revision": kitchen[1].state["revision"], "direction": "current"})
    assert current["ui_snapshot"]["active_step_id"] == "r1:step:0"
    following = tool(kitchen, "navigate_cooking_step", {"expected_revision": kitchen[1].state["revision"], "direction": "next"})
    assert following["ui_snapshot"]["active_step_id"] == "r1:step:1"
    assert not any(value == "done" for value in kitchen[1].state["step_status"].values())


@pytest.mark.parametrize("invalid", [
    {"item_id": "r1:ingredient:1", "status": "ready", "availability": "missing"},
    {"item_id": "r1:ingredient:1", "status": "pending", "availability": "inferred_missing"},
    {"item_id": "r3:ingredient:0", "status": "pending", "availability": "missing"},
])
def test_invalid_availability_batches_never_record_partial_readiness(kitchen, invalid):
    choose(kitchen)
    before = copy.deepcopy(kitchen[1].state)
    result = update(kitchen, [{"item_id": "r1:ingredient:0", "status": "ready"}, invalid])
    assert result["status"] in {"invalid_arguments", "invalid_reference"}
    assert kitchen[1].state == before


def test_user_checking_or_resetting_item_removes_stale_missing_fact(kitchen):
    choose(kitchen)
    missing = update(kitchen, [{"item_id": "r1:ingredient:0", "status": "pending", "availability": "missing"}])
    view_id = prep_view(missing)["id"]
    ready = action(kitchen, "check_prep_item", {"item_id": "r1:ingredient:0", "status": "ready"}, view_id=view_id)
    assert prep_view(ready)["data"]["items"][0]["availability"] == "available"
    reset = action(kitchen, "check_prep_item", {"item_id": "r1:ingredient:0", "status": "pending"}, view_id=view_id)
    assert prep_view(reset)["data"]["items"][0]["availability"] == "unknown"
    update(kitchen, [{"item_id": "r1:ingredient:0", "status": "pending", "availability": "missing"}])
    choose(kitchen, ids=["r2"])
    assert kitchen[1].state["prep_availability"] == {}
