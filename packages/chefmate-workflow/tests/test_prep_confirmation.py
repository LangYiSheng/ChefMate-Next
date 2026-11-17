"""One-click preparation confirms only the visible scope and enters cooking atomically."""

import copy

import test_workflow
from test_workflow import action, choose, tool

kitchen = test_workflow.kitchen


def prep(kitchen, ids=None):
    choose(kitchen, ids=ids)
    result = action(kitchen, "start_prep", {})
    assert result["status"] == "ok"
    return next(view for view in result["views"] if view["kind"] == "prep_checklist")


def test_confirm_all_starts_cooking_once_and_keeps_skipped_items(kitchen):
    view = prep(kitchen)
    skipped = view["data"]["items"][-1]["id"]
    action(kitchen, "check_prep_item", {"item_id": skipped, "status": "skipped"}, view_id=view["id"])
    revision = kitchen[1].state["revision"]
    result = action(kitchen, "confirm_prep", {}, view_id=view["id"], action_id="confirm", base_revision=revision)
    assert result["status"] == "ok"
    assert result["workspace"]["stage"] == "cooking"
    assert result["workspace"]["active_step_id"] == "r1:step:0"
    assert kitchen[1].state["prep_status"][skipped] == "skipped"
    assert kitchen[1].state["revision"] == revision + 1
    saved = copy.deepcopy(kitchen[1].state)
    assert action(kitchen, "confirm_prep", {}, view_id=view["id"], action_id="confirm", base_revision=revision)["status"] == "ok"
    assert kitchen[1].state == saved


def test_missing_item_blocks_entire_confirmation_until_user_repairs_it(kitchen):
    view = prep(kitchen)
    identity = view["data"]["items"][-1]["id"]
    marked = action(kitchen, "check_prep_item", {"item_id": identity, "status": "pending", "availability": "missing"}, view_id=view["id"])
    assert marked["status"] == "ok"
    before = copy.deepcopy(kitchen[1].state)
    assert action(kitchen, "confirm_prep", {}, view_id=view["id"])["status"] == "ingredients_missing"
    assert kitchen[1].state == before
    action(kitchen, "check_prep_item", {"item_id": identity, "status": "ready"}, view_id=view["id"])
    assert action(kitchen, "confirm_prep", {}, view_id=view["id"])["workspace"]["stage"] == "cooking"


def test_per_dish_confirmation_moves_to_next_checklist_without_assuming_its_stock(kitchen):
    view = prep(kitchen, ["r1", "r2"])
    action(kitchen, "set_checklist_scope", {"recipe_ids": ["r1"]}, view_id=view["id"])
    action(kitchen, "check_prep_item", {"item_id": "r2:ingredient:0", "status": "pending", "availability": "missing"})
    result = action(kitchen, "confirm_prep", {}, view_id=view["id"])
    assert result["workspace"]["stage"] == "prep"
    next_view = next(item for item in result["views"] if item["kind"] == "prep_checklist")
    assert next_view["data"]["recipe_ids"] == ["r2"]
    assert all(item["status"] == "pending" for item in next_view["data"]["items"])
    assert kitchen[1].state["prep_availability"]["r2:ingredient:0"] == "missing"
    action(kitchen, "check_prep_item", {"item_id": "r2:ingredient:0", "status": "ready"}, view_id=view["id"])
    assert action(kitchen, "confirm_prep", {}, view_id=view["id"])["workspace"]["stage"] == "cooking"


def test_bulk_confirmation_rejects_stale_scope_and_pending_questions(kitchen):
    view = prep(kitchen)
    revision = kitchen[1].state["revision"]
    action(kitchen, "check_prep_item", {"item_id": "r1:ingredient:0", "status": "ready"}, view_id=view["id"])
    before = copy.deepcopy(kitchen[1].state)
    assert action(kitchen, "confirm_prep", {}, view_id=view["id"], base_revision=revision)["status"] == "revision_conflict"
    assert action(kitchen, "confirm_prep", {})["status"] == "invalid_reference"
    assert kitchen[1].state == before
    tool(kitchen, "ask_user", {"question": "要换一种食材吗？"})
    before = copy.deepcopy(kitchen[1].state)
    assert action(kitchen, "confirm_prep", {}, view_id=view["id"])["status"] == "pending_question_exists"
    assert kitchen[1].state == before


def test_select_and_prepare_is_one_atomic_explicit_action(kitchen):
    choose(kitchen)
    revision = kitchen[1].state["revision"]
    result = action(kitchen, "select_recipes", {"recipe_ids": ["r1"], "start_prep": True})
    assert result["status"] == "ok" and result["workspace"]["stage"] == "prep"
    assert kitchen[1].state["revision"] == revision + 1
    assert all(item["status"] == "pending" for item in next(view for view in result["views"] if view["kind"] == "prep_checklist")["data"]["items"])


def test_completing_dish_can_continue_to_next_without_completing_other_steps(kitchen):
    view = prep(kitchen, ["r1", "r2"])
    action(kitchen, "confirm_prep", {}, view_id=view["id"])
    for index in range(2):
        result = action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": f"r1:step:{index}", "continue_to_next_recipe": True})
        assert result["status"] == "ok"
    assert result["workspace"]["active_recipe_id"] == "r2"
    assert result["workspace"]["active_step_id"] == "r2:step:0"
    assert kitchen[1].state["step_status"].get("r2:step:0") == "current"
    assert result["workspace"]["stage"] == "cooking"
