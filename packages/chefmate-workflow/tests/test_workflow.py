"""Kitchen commands exercised through real-shaped retrieval data and atomic host callbacks."""

import copy
import json
from datetime import datetime, timedelta, timezone
from threading import RLock

import pytest
from pydantic import ValidationError

from chefmate_workflow import UserActionRequest, WorkflowService, WorkspaceState

SNAPSHOT = {"data_version": "fixture", "imported_at": "2026-10-13T00:00:00+00:00"}
NUTRIENTS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")


def recipe(identity="r1", *, shrimp_name="虾仁", unit="g", unknown=False, scalable=True):
    contribution = dict(zip(NUTRIENTS, (100., 20., 2., 1., 0., 100.)))
    salt = dict(zip(NUTRIENTS, (0., 0., 0., 0., 0., 1000.)))
    total = {key: contribution[key] + salt[key] for key in NUTRIENTS}
    return {"recipe_id": identity, "name": "虾仁汤", "description": "清洗后批准的测试菜谱", "servings": 2.,
            "estimated_servings": False, "total_time_min": 20., "difficulty": "beginner",
            "nutrition": {"total": total, "per_serving": {key: value / 2 for key, value in total.items()},
                          "status": "complete", "estimated": False, "estimated_total_fields": [],
                          "estimated_per_serving_fields": [], "estimated_servings": False},
            "warnings": ["虾仁可按原文核对"], "raw_source_json": {"private_source": "must_not_persist"},
            "ingredients": [{"ordinal": 0, "ingredient_id": "shrimp", "canonical_name": shrimp_name,
                             "raw_name": shrimp_name, "raw_text": "虾仁100g", "raw_amount": "适量" if unknown else "100",
                             "raw_unit": unit, "grams": None if unknown else 100., "scalable": scalable,
                             "estimated_grams": unknown, "nutrition": {"contribution": contribution}},
                            {"ordinal": 1, "ingredient_id": "salt", "canonical_name": "盐", "raw_name": "盐",
                             "raw_text": "盐1g", "raw_amount": "1", "raw_unit": "g", "grams": 1.,
                             "scalable": False, "estimated_grams": False, "nutrition": {"contribution": salt}}],
            "steps": ["加入食材煮5分钟", "盛盘"]}


class Host:
    def __init__(self):
        self.state = {}
        self.lock = RLock()

    def get(self):
        return copy.deepcopy(self.state)

    def mutate(self, fn):
        with self.lock:
            return fn(self.state)


class Retrieval:
    def __init__(self):
        self.recipes = {"r1": recipe(), "r2": recipe("r2"), "r3": recipe("r3", shrimp_name="整虾", unit="个")}
        self.snapshot = dict(SNAPSHOT)
        self.calls = []
        self.after_read = None

    def get_recipe(self, request):
        self.calls.append(request)
        if request["expected_snapshot"] != self.snapshot:
            return {"status": "snapshot_changed", "snapshot": self.snapshot}
        source = copy.deepcopy(self.recipes[request["recipe_id"]])
        if self.after_read:
            self.after_read()
        return {"status": "ok", "snapshot": self.snapshot, "recipe": source}


@pytest.fixture
def kitchen():
    host, retrieval = Host(), Retrieval()
    clock = [datetime(2026, 10, 13, tzinfo=timezone.utc)]
    service = WorkflowService(retrieval, host.get, host.mutate, now=lambda: clock[0])
    return service, host, retrieval, clock


def tool(kitchen, name, request=None, **kwargs):
    service, _, retrieval, _ = kitchen
    approved = [{"recipe_id": identity, "data_snapshot": retrieval.snapshot} for identity in retrieval.recipes]
    return service.execute_tool(name, request, approved_recipes=approved, **kwargs)


def action(kitchen, kind, payload, *, action_id=None, base_revision=None, view_id=None):
    service, host, _, _ = kitchen
    return service.apply_action({"type": kind, "payload": payload, "action_id": action_id or f"a-{host.state.get('revision', 0)}",
                                 "base_revision": host.state.get("revision", 0) if base_revision is None else base_revision,
                                 "view_id": view_id})


def choose(kitchen, ids=None, servings=None):
    result = tool(kitchen, "set_meal_plan", {"recipe_ids": ids or ["r1"], "data_snapshot": SNAPSHOT,
                                            "servings": servings or {}})
    assert result["status"] == "ok"
    return result


def prepare(kitchen):
    choose(kitchen)
    assert action(kitchen, "change_stage", {"stage": "prep"})["status"] == "ok"
    result = tool(kitchen, "show_prep_checklist")
    for item in next(view for view in result["views"] if view["kind"] == "prep_checklist")["data"]["items"]:
        assert action(kitchen, "check_prep_item", {"item_id": item["id"], "status": "ready"})["status"] == "ok"
    assert action(kitchen, "change_stage", {"stage": "cooking"})["status"] == "ok"


def test_partial_preference_update_model_instance_keeps_other_values(kitchen):
    service, host, _, _ = kitchen
    action(kitchen, "update_preferences", {"values": {"max_total_time_min": 30, "taste_tags": ["light"]}})
    request = UserActionRequest.model_validate({"type": "update_preferences", "action_id": "second", "base_revision": 1,
                                               "payload": {"values": {"ingredient_terms": ["鸡肉"]}}})
    result = service.apply_action(request)
    assert result["status"] == "ok"
    assert host.state["preferences"]["taste_tags"] == ["light"]
    assert host.state["preferences"]["max_total_time_min"] == 30
    assert host.state["preferences"]["ingredient_terms"] == ["鸡肉"]


@pytest.mark.parametrize("payload", [
    {"type": "arbitrary_html", "payload": {"html": "<script>"}},
    {"type": "update_preferences", "payload": {"values": {"api_key": "secret"}}},
    {"type": "update_preferences", "payload": {"values": {"servings": 0}}},
    {"type": "start_timer", "payload": {"seconds": True, "label": "wrong"}},
    {"type": "change_stage", "payload": {"stage": "unknown"}},
])
def test_closed_actions_reject_unknown_keys_types_and_numbers(payload):
    with pytest.raises(ValidationError):
        UserActionRequest.model_validate({"action_id": "x", "base_revision": 0, **payload})


def test_action_id_idempotency_and_cas_conflict(kitchen):
    _, host, _, _ = kitchen
    payload = {"values": {"ingredient_terms": ["虾"]}}
    first = action(kitchen, "update_preferences", payload, action_id="same", base_revision=0)
    again = action(kitchen, "update_preferences", payload, action_id="same", base_revision=0)
    assert first["status"] == again["status"] == "ok"
    assert host.state["revision"] == 1
    conflict = action(kitchen, "update_preferences", {"values": {"ingredient_terms": ["鸡"]}}, action_id="same", base_revision=1)
    assert conflict["status"] == "action_id_conflict"
    stale = action(kitchen, "update_preferences", payload, action_id="other", base_revision=0)
    assert stale["status"] == "revision_conflict"
    assert stale["workspace"]["revision"] == 1


def test_replaced_view_is_stale_and_reads_do_not_advance_revision(kitchen):
    service, host, _, _ = kitchen
    old = tool(kitchen, "show_preference_form")["actions"][0]["component"]["id"]
    tool(kitchen, "show_preference_form")
    revision = host.state["revision"]
    assert service.get_workspace()["workspace"]["revision"] == revision
    assert action(kitchen, "update_preferences", {"values": {"servings": 3}}, view_id=old)["status"] == "stale_view"
    assert host.state["revision"] == revision


def test_no_recipe_authority_no_mutation_and_source_reimport_rejected(kitchen):
    service, host, retrieval, _ = kitchen
    request = {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT}
    assert service.execute_tool("show_recipe_choices", request)["status"] == "unverified_reference"
    assert host.state == {}
    retrieval.snapshot = {**SNAPSHOT, "imported_at": "2026-10-13T01:00:00+00:00"}
    approved = [{"recipe_id": "r1", "data_snapshot": SNAPSHOT}]
    assert service.execute_tool("set_meal_plan", request, approved_recipes=approved)["status"] == "snapshot_changed"
    assert host.state == {}


def test_frozen_task_survives_source_changes_and_drops_private_original(kitchen):
    _, host, retrieval, _ = kitchen
    choose(kitchen)
    assert "must_not_persist" not in json.dumps(host.state)
    count = len(retrieval.calls)
    retrieval.recipes["r1"]["steps"] = ["changed source"]
    retrieval.snapshot = {**SNAPSHOT, "imported_at": "2026-10-13T01:00:00+00:00"}
    result = tool(kitchen, "show_recipe_details", {"recipe_id": "r1"})
    assert result["actions"][0]["component"]["data"]["recipe"]["steps"][0]["instruction"] == "加入食材煮5分钟"
    assert len(retrieval.calls) == count
    assert WorkspaceState.model_validate(host.state).meal_plan[0].data_snapshot.data_version == "fixture"


def test_source_read_cannot_overwrite_concurrent_human_change(kitchen):
    _, host, retrieval, _ = kitchen
    retrieval.after_read = lambda: action(kitchen, "update_preferences", {"values": {"ingredient_terms": ["鸡"]}}, action_id="concurrent")
    result = tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT})
    assert result["status"] == "revision_conflict"
    assert host.state["preferences"]["ingredient_terms"] == ["鸡"]
    assert host.state["candidates"] == []


def test_form_choices_and_selected_plan_have_real_current_values(kitchen):
    _, host, _, _ = kitchen
    tool(kitchen, "show_preference_form", {"fields": ["ingredient_terms", "servings"]})
    action(kitchen, "update_preferences", {"values": {"ingredient_terms": ["虾"], "servings": 4}})
    tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1", "r2"], "data_snapshot": SNAPSHOT})
    result = action(kitchen, "select_recipes", {"recipe_ids": ["r2"]})
    assert host.state["meal_plan"][0]["recipe_id"] == "r2"
    assert host.state["meal_plan"][0]["target_servings"] == 4
    form = next(view for view in result["views"] if view["kind"] == "preference_form")
    assert form["data"]["values"]["ingredient_terms"] == ["虾"]


def test_portions_respect_non_scalable_ingredients_and_metric_contributions(kitchen):
    choose(kitchen, servings={"r1": 4})
    result = tool(kitchen, "show_recipe_details", {"recipe_id": "r1"})
    view = result["actions"][0]["component"]["data"]["recipe"]
    assert view["ingredients"][0]["grams"] == 200
    assert view["ingredients"][1]["grams"] == 1
    assert view["nutrition"]["total"]["protein_g"] == 40
    assert view["nutrition"]["total"]["sodium_mg"] == 1200
    assert view["nutrition"]["per_serving"]["sodium_mg"] == 300
    result = action(kitchen, "change_stage", {"stage": "prep"})
    snapshot = result["ui_snapshot"]
    plan = next(component for component in snapshot["components"] if component["kind"] == "meal_plan")
    assert plan["data"]["recipes"][0]["original_servings"] == 2
    assert plan["data"]["recipes"][0]["target_servings"] == 4
    prep = next(component for component in snapshot["components"] if component["kind"] == "prep_checklist")
    shrimp, salt = prep["data"]["items"]
    assert {key: shrimp[key] for key in ("amount_text", "amount_value", "unit", "scaled", "estimated")} == {
        "amount_text": "200 g", "amount_value": 200, "unit": "g", "scaled": True, "estimated": False}
    assert salt["amount_value"] == 1
    assert salt["scaled"] is False
    assert len(json.dumps(snapshot, ensure_ascii=False)) < 24000


def test_shopping_merges_only_compatible_identity_shape_unit(kitchen):
    choose(kitchen, ids=["r1", "r2", "r3"])
    result = tool(kitchen, "show_shopping_list")
    items = result["actions"][0]["component"]["data"]["items"]
    shrimp = [item for item in items if item["ingredient_id"] == "shrimp"]
    assert len(shrimp) == 2
    peeled = next(item for item in shrimp if item["canonical_name"] == "虾仁")
    assert peeled["amount_value"] == 200
    assert set(peeled["source_recipe_ids"]) == {"r1", "r2"}


def test_unknown_quantities_remain_unknown_and_separate(kitchen):
    _, _, retrieval, _ = kitchen
    retrieval.recipes["r1"] = recipe(unknown=True)
    retrieval.recipes["r2"] = recipe("r2", unknown=True)
    choose(kitchen, ids=["r1", "r2"], servings={"r1": 4})
    result = tool(kitchen, "show_shopping_list")
    shrimp = [item for item in result["actions"][0]["component"]["data"]["items"] if item["ingredient_id"] == "shrimp"]
    assert len(shrimp) == 2
    assert all(item["amount_value"] is None for item in shrimp)
    assert all(item["estimated"] for item in shrimp)


def test_stage_guards_and_all_steps_done_before_completion(kitchen):
    choose(kitchen)
    assert action(kitchen, "change_stage", {"stage": "cooking"})["status"] == "stage_conflict"
    assert action(kitchen, "change_stage", {"stage": "prep"})["status"] == "ok"
    assert action(kitchen, "change_stage", {"stage": "cooking"})["status"] == "prep_incomplete"
    for identity in ("r1:ingredient:0", "r1:ingredient:1"):
        assert action(kitchen, "check_prep_item", {"item_id": identity, "status": "ready"})["status"] == "ok"
    assert action(kitchen, "change_stage", {"stage": "cooking"})["status"] == "ok"
    assert action(kitchen, "change_stage", {"stage": "completed"})["status"] == "steps_incomplete"
    action(kitchen, "set_active_step", {"recipe_id": "r1", "step_id": "r1:step:1"})
    assert action(kitchen, "change_stage", {"stage": "completed"})["status"] == "steps_incomplete"
    for identity in ("r1:step:0", "r1:step:1"):
        assert action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": identity})["status"] == "ok"
    assert action(kitchen, "change_stage", {"stage": "completed"})["status"] == "ok"


def test_unknown_ids_and_changing_active_task_are_rejected(kitchen):
    tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1", "r2"], "data_snapshot": SNAPSHOT})
    prepare(kitchen)
    assert action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": "made-up"})["status"] == "invalid_reference"
    assert tool(kitchen, "set_meal_plan", {"recipe_ids": ["r2"], "data_snapshot": SNAPSHOT})["status"] == "stage_conflict"
    assert tool(kitchen, "show_recipe_details", {"recipe_id": "r3", "data_snapshot": SNAPSHOT})["status"] == "invalid_reference"
    assert tool(kitchen, "show_recipe_details", {"recipe_id": "r2"})["status"] == "invalid_reference"
    assert action(kitchen, "open_recipe_detail", {"recipe_id": "r2"})["status"] == "invalid_reference"


def test_timer_deadline_pause_restore_cancel_and_expiry_are_persistent(kitchen):
    service, host, retrieval, clock = kitchen
    prepare(kitchen)
    result = action(kitchen, "start_timer", {"seconds": 60, "label": "汤", "recipe_id": "r1", "step_id": "r1:step:0"})
    timer_id = result["actions"][0]["component"]["data"]["timers"][0]["id"]
    clock[0] += timedelta(seconds=20)
    assert action(kitchen, "pause_timer", {"timer_id": timer_id})["status"] == "ok"
    assert host.state["timers"][0]["remaining_seconds"] == 40
    clock[0] += timedelta(seconds=100)
    restored = WorkflowService(retrieval, host.get, host.mutate, now=lambda: clock[0])
    assert restored.get_workspace()["views"][-1]["data"]["timers"][0]["remaining_seconds"] == 40
    assert action(kitchen, "resume_timer", {"timer_id": timer_id})["status"] == "ok"
    clock[0] += timedelta(seconds=41)
    timers = next(view for view in service.get_workspace()["views"] if view["kind"] == "timers")["data"]["timers"]
    assert timers[0]["status"] == "finished"
    assert timers[0]["remaining_seconds"] == 0
    assert action(kitchen, "resume_timer", {"timer_id": timer_id})["status"] == "timer_state_conflict"


def test_tool_timer_duration_requires_source_or_trusted_user_text(kitchen):
    prepare(kitchen)
    params = {"seconds": 999, "label": "guess", "recipe_id": "r1", "step_id": "r1:step:0", "basis": "recipe_text"}
    assert tool(kitchen, "set_cooking_timer", params)["status"] == "unverified_duration"
    assert tool(kitchen, "set_cooking_timer", {**params, "seconds": 300})["status"] == "ok"
    assert tool(kitchen, "set_cooking_timer", {"seconds": 60, "label": "user"})["status"] == "unverified_duration"
    assert tool(kitchen, "set_cooking_timer", {"seconds": 60, "label": "user"}, user_text="计时1分钟")["status"] == "ok"


def test_model_snapshot_compact_and_action_history_bounded(kitchen):
    service, host, _, _ = kitchen
    choose(kitchen, ids=["r1", "r2", "r3"])
    for name in ("show_recipe_choices",):
        tool(kitchen, name, {"recipe_ids": ["r1", "r2", "r3"], "data_snapshot": SNAPSHOT})
    for name in ("show_meal_plan", "show_shopping_list", "show_prep_checklist", "show_cooking_guide", "show_timers"):
        tool(kitchen, name)
    for _ in range(25):
        action(kitchen, "update_preferences", {"values": {"ingredient_terms": ["虾"]}})
    snapshot = service.model_snapshot()
    assert len(snapshot["recent_actions"]) == len(host.state["recent_actions"]) == 20
    assert len(json.dumps(snapshot, ensure_ascii=False)) < 24000
    assert "nutrition" not in json.dumps(snapshot)
    assert "private_source" not in json.dumps(snapshot)


def test_duplicate_tool_operation_does_not_reread_after_source_reimport(kitchen):
    _, host, retrieval, _ = kitchen
    params = {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT}
    first = tool(kitchen, "set_meal_plan", params, operation_id="one-call")
    count = len(retrieval.calls)
    retrieval.snapshot = {**SNAPSHOT, "imported_at": "2026-10-13T01:00:00+00:00"}
    again = tool(kitchen, "set_meal_plan", params, operation_id="one-call")
    assert first["status"] == again["status"] == "ok"
    assert host.state["revision"] == 1
    assert len(retrieval.calls) == count


def test_unknown_source_servings_keeps_target_goal_without_inventing_scaling(kitchen):
    _, _, retrieval, _ = kitchen
    retrieval.recipes["r1"]["servings"] = None
    choose(kitchen, servings={"r1": 4})
    result = tool(kitchen, "show_recipe_details", {"recipe_id": "r1"})
    view = result["actions"][0]["component"]["data"]["recipe"]
    assert view["servings"] is None
    assert view["original_servings"] is None
    assert view["target_servings"] == 4
    assert view["ingredients"][0]["grams"] == 100
    assert any("人数未知" in warning for warning in view["warnings"])


def test_changing_batch_after_rollback_resets_only_that_recipe_progress(kitchen):
    prepare(kitchen)
    action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": "r1:step:0"})
    action(kitchen, "start_timer", {"seconds": 60, "label": "one", "recipe_id": "r1"})
    assert action(kitchen, "change_stage", {"stage": "prep"})["status"] == "ok"
    result = action(kitchen, "change_servings", {"recipe_id": "r1", "servings": 4})
    assert result["status"] == "ok"
    _, host, _, _ = kitchen
    assert "r1:step:0" not in host.state["step_status"]
    assert all(status == "pending" for status in host.state["prep_status"].values())
    assert host.state["timers"][0]["status"] == "cancelled"


def test_current_preferences_available_without_rendered_form_and_long_history_stays_compact(kitchen):
    service, _, _, _ = kitchen
    for number in range(21):
        action(kitchen, "update_preferences", {"values": {"dietary_notes": "d" * 2000,
                                                           "ingredient_terms": [f"food-{number}"]}})
    snapshot = service.model_snapshot()
    assert snapshot["preferences"]["ingredient_terms"] == ["food-20"]
    assert snapshot["preferences"]["dietary_notes"] == "d" * 2000
    assert len(json.dumps(snapshot)) < 24000
    assert all("changed_fields" in record["payload"] for record in snapshot["recent_actions"])


def test_selecting_fresh_candidate_with_same_id_uses_the_displayed_source(kitchen):
    _, host, retrieval, _ = kitchen
    choose(kitchen)
    retrieval.snapshot = {**SNAPSHOT, "imported_at": "2026-10-13T01:00:00+00:00"}
    retrieval.recipes["r1"]["name"] = "新配方虾仁"
    choices = tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": retrieval.snapshot})
    details = action(kitchen, "open_recipe_detail", {"recipe_id": "r1"},
                     view_id=choices["actions"][0]["component"]["id"])
    assert details["actions"][0]["component"]["data"]["recipe"]["name"] == "新配方虾仁"
    assert host.state["meal_plan"][0]["recipe"]["name"] == "虾仁汤"
    assert action(kitchen, "select_recipes", {"recipe_ids": ["r1"]})["status"] == "ok"
    assert host.state["meal_plan"][0]["recipe"]["name"] == "新配方虾仁"
    assert host.state["meal_plan"][0]["data_snapshot"]["imported_at"] == retrieval.snapshot["imported_at"]


def test_unselected_preview_disappears_when_entering_execution(kitchen):
    tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1", "r2"], "data_snapshot": SNAPSHOT})
    action(kitchen, "select_recipes", {"recipe_ids": ["r1"]})
    result = tool(kitchen, "show_recipe_details", {"recipe_id": "r2", "data_snapshot": SNAPSHOT})
    assert any(view["kind"] == "recipe_detail" for view in result["views"])
    result = action(kitchen, "change_stage", {"stage": "prep"})
    assert result["status"] == "ok"
    assert all(view["kind"] != "recipe_detail" for view in result["views"])


def test_user_buttons_select_shop_prepare_and_cook_without_extra_model_tools(kitchen):
    choices = tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT})
    result = action(kitchen, "select_recipes", {"recipe_ids": ["r1"]},
                    view_id=choices["actions"][0]["component"]["id"])
    assert result["actions"][0]["component"]["kind"] == "meal_plan"
    progress = next(view for view in result["views"] if view["kind"] == "task_progress")
    assert progress["data"]["stage"] == "planning"
    for stage, kind in (("shopping", "shopping_list"), ("prep", "prep_checklist")):
        result = action(kitchen, "change_stage", {"stage": stage}, view_id=progress["id"])
        assert result["status"] == "ok"
        assert result["actions"][0]["component"]["kind"] == kind
        assert any(view["kind"] == kind for view in result["views"])
        progress = next(view for view in result["views"] if view["kind"] == "task_progress")
        assert progress["data"]["stage"] == stage
    prep = result["actions"][0]["component"]
    for item in prep["data"]["items"]:
        assert action(kitchen, "check_prep_item", {"item_id": item["id"], "status": "ready"},
                      view_id=prep["id"])["status"] == "ok"
    result = action(kitchen, "change_stage", {"stage": "cooking"}, view_id=progress["id"])
    guide = result["actions"][0]["component"]
    assert guide["kind"] == "cooking_guide"
    assert guide["data"]["steps"][0]["instruction"] == "加入食材煮5分钟"
    assert guide["data"]["active_step_id"] == "r1:step:0"
    assert next(view for view in result["views"] if view["kind"] == "task_progress")["data"]["stage"] == "cooking"
    for step in guide["data"]["steps"]:
        assert action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": step["id"]},
                      view_id=guide["id"])["status"] == "ok"
    result = action(kitchen, "change_stage", {"stage": "completed"}, view_id=progress["id"])
    assert result["status"] == "ok"
    progress = result["actions"][0]["component"]
    assert progress["kind"] == "task_progress" and progress["editable"]
    assert all(not view["editable"] for view in result["views"]
               if view["kind"] in {"meal_plan", "prep_checklist", "cooking_guide"})
    result = action(kitchen, "change_stage", {"stage": "planning"}, view_id=progress["id"])
    assert result["status"] == "ok"
    assert result["actions"][0]["component"]["kind"] == "meal_plan"
    assert result["actions"][0]["component"]["editable"]
    assert action(kitchen, "change_servings", {"recipe_id": "r1", "servings": 4},
                  view_id=result["actions"][0]["component"]["id"])["status"] == "ok"


def test_direct_prep_and_stage_return_preserve_existing_component_ids(kitchen):
    result = choose(kitchen)
    original = {view["kind"]: view["id"] for view in result["views"]}
    result = action(kitchen, "change_stage", {"stage": "prep"})
    prep_id = result["actions"][0]["component"]["id"]
    assert result["actions"][0]["component"]["kind"] == "prep_checklist"
    result = action(kitchen, "change_stage", {"stage": "prep"})
    assert result["actions"][0]["component"]["id"] == prep_id
    result = action(kitchen, "change_stage", {"stage": "planning"})
    assert result["actions"][0]["component"]["kind"] == "meal_plan"
    current = {view["kind"]: view["id"] for view in result["views"]}
    assert current["meal_plan"] == original["meal_plan"]
    assert current["task_progress"] == original["task_progress"]
    assert len(current) == len(result["views"])


@pytest.mark.parametrize("display", ["inline", "workspace", "none"])
def test_model_controls_presentation_without_dropping_task_state(kitchen, display):
    result = tool(kitchen, "show_preference_form", {"display": display})
    assert result["status"] == "ok"
    assert len(result["views"]) == 1
    if display == "none":
        assert result["actions"] == []
    else:
        assert result["actions"][0]["placement"] == display
    read = tool(kitchen, "get_cooking_workspace")
    assert read["actions"] == []
    assert len(read["views"]) == 1
