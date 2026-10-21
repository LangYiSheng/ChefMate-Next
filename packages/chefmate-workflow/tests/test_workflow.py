"""Kitchen commands exercised through real-shaped retrieval data and atomic host callbacks."""

import copy
import json
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

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
    return service.apply_action({"type": kind, "payload": payload, "action_id": action_id or str(uuid4()),
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


def test_preparation_retains_dish_identity_shape_and_amounts(kitchen):
    choose(kitchen, ids=["r1", "r2", "r3"])
    result = tool(kitchen, "show_prep_checklist")
    items = result["actions"][0]["component"]["data"]["items"]
    shrimp = [item for item in items if item["ingredient_id"] == "shrimp"]
    assert len(shrimp) == 3
    peeled = [item for item in shrimp if item["canonical_name"] == "虾仁"]
    assert all(item["amount_value"] == 100 for item in peeled)
    assert {item["recipe_id"] for item in peeled} == {"r1", "r2"}


def test_unknown_quantities_remain_unknown_and_separate(kitchen):
    _, _, retrieval, _ = kitchen
    retrieval.recipes["r1"] = recipe(unknown=True)
    retrieval.recipes["r2"] = recipe("r2", unknown=True)
    choose(kitchen, ids=["r1", "r2"], servings={"r1": 4})
    result = tool(kitchen, "show_prep_checklist")
    shrimp = [item for item in result["actions"][0]["component"]["data"]["items"] if item["ingredient_id"] == "shrimp"]
    assert len(shrimp) == 2
    assert all(item["amount_value"] is None for item in shrimp)
    assert all(item["estimated"] for item in shrimp)


def test_single_dish_preparation_preserves_other_dish_confirmations(kitchen):
    choose(kitchen, ids=["r1", "r2"], servings={"r1": 4})
    result = tool(kitchen, "show_prep_checklist", {"recipe_ids": ["r1"]})
    view = result["actions"][0]["component"]
    shrimp = next(item for item in view["data"]["items"] if item["ingredient_id"] == "shrimp")
    assert shrimp["amount_value"] == 200
    assert shrimp["recipe_id"] == "r1"
    assert action(kitchen, "check_prep_item", {"item_id": shrimp["id"], "status": "ready"}, view_id=view["id"])["status"] == "ok"
    result = action(kitchen, "set_checklist_scope", {"recipe_ids": None}, view_id=view["id"])
    all_items = result["actions"][0]["component"]["data"]["items"]
    assert next(item for item in all_items if item["id"] == "r1:ingredient:0")["status"] == "ready"
    assert next(item for item in all_items if item["id"] == "r2:ingredient:0")["status"] == "pending"
    result = action(kitchen, "set_checklist_scope", {"recipe_ids": ["r2"]}, view_id=view["id"])
    single = next(item for item in result["actions"][0]["component"]["data"]["items"] if item["ingredient_id"] == "shrimp")
    assert single["amount_value"] == 100 and single["status"] == "pending"
    assert action(kitchen, "check_prep_item", {"item_id": single["id"], "status": "ready"}, view_id=view["id"])["status"] == "ok"
    restored = action(kitchen, "set_checklist_scope", {"recipe_ids": ["r1"]}, view_id=view["id"])
    assert next(item for item in restored["actions"][0]["component"]["data"]["items"] if item["ingredient_id"] == "shrimp")["status"] == "ready"


def test_adapted_guidance_keeps_source_quantities_and_confirmed_progress(kitchen):
    choose(kitchen)
    tool(kitchen,"advance_cooking_stage",{"stage":"prep"})
    action(kitchen,"check_prep_item",{"item_id":"r1:ingredient:0","status":"ready"})
    _,host,_,_=kitchen
    before=copy.deepcopy(host.state)
    result=tool(kitchen,"update_cooking_guidance",{"recipe_id":"r1","step_id":"r1:step:0",
                "instruction":"按用户指定的小锅分批加热。","reason":"用户只有小锅。"})
    assert result["status"]=="ok"
    guide=next(view for view in result["views"] if view["kind"]=="cooking_guide")
    assert guide["data"]["steps"][0]["adapted_instruction"]=="按用户指定的小锅分批加热。"
    assert guide["data"]["steps"][0]["instruction"]=="加入食材煮5分钟"
    assert host.state["meal_plan"]==before["meal_plan"]
    assert host.state["prep_status"]==before["prep_status"] and host.state["step_status"]==before["step_status"]
    assert host.state["stage"]==before["stage"]
    assert tool(kitchen,"update_cooking_guidance",{"recipe_id":"r1","step_id":"r2:step:0",
                "instruction":"不应写入","reason":"非法步骤"})["status"]=="invalid_reference"


def test_scoped_checklists_reject_unselected_recipes_and_foreign_rows(kitchen):
    choose(kitchen, ids=["r1", "r2"])
    assert tool(kitchen, "show_prep_checklist", {"recipe_ids": ["r3"]})["status"] == "invalid_reference"
    result = tool(kitchen, "show_prep_checklist", {"recipe_ids": ["r1"]})
    view = result["actions"][0]["component"]
    assert all(item["recipe_id"] == "r1" for item in view["data"]["items"])
    assert action(kitchen, "set_checklist_scope", {"recipe_ids": ["r3"]}, view_id=view["id"])["status"] == "invalid_reference"
    action(kitchen, "change_stage", {"stage": "prep"})
    # Refresh a scoped view after stage advancement, which deliberately resets the stage's default view.
    view = tool(kitchen, "show_prep_checklist", {"recipe_ids": ["r1"]})["actions"][0]["component"]
    assert action(kitchen, "check_prep_item", {"item_id": "r2:ingredient:0", "status": "ready"}, view_id=view["id"])["status"] == "invalid_reference"


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


def test_step_timer_duplicate_does_not_restart_or_replace_a_paused_timer(kitchen):
    _, host, _, clock = kitchen
    prepare(kitchen)
    payload = {"seconds": 60, "label": "当前步骤", "recipe_id": "r1", "step_id": "r1:step:0"}
    first = action(kitchen, "start_timer", payload, action_id="start-1")
    timer = first["actions"][0]["component"]["data"]["timers"][0]
    clock[0] += timedelta(seconds=15)
    again = action(kitchen, "start_timer", payload, action_id="start-2")
    current = again["actions"][0]["component"]["data"]["timers"][0]
    assert current["id"] == timer["id"] and current["deadline_at"] == timer["deadline_at"]
    assert current["remaining_seconds"] == 45 and len(host.state["timers"]) == 1
    assert action(kitchen, "start_timer", {**payload, "seconds": 90})["status"] == "timer_already_exists"
    assert action(kitchen, "pause_timer", {"timer_id": timer["id"]})["status"] == "ok"
    paused = copy.deepcopy(host.state["timers"][0])
    clock[0] += timedelta(seconds=100)
    assert action(kitchen, "start_timer", payload)["status"] == "ok"
    assert host.state["timers"][0] == paused
    assert action(kitchen, "cancel_timer", {"timer_id": timer["id"]})["status"] == "ok"
    restarted = action(kitchen, "start_timer", payload)
    assert restarted["status"] == "ok"
    assert len(host.state["timers"]) == 2 and host.state["timers"][-1]["id"] != timer["id"]
    assert host.state["timers"][-1]["remaining_seconds"] == 60


def test_cooking_projection_scopes_timers_to_recipe_and_expiry_never_completes_steps(kitchen):
    service, host, retrieval, clock = kitchen
    choose(kitchen, ids=["r1", "r2"])
    action(kitchen, "change_stage", {"stage": "prep"})
    items = service._prep_items(WorkspaceState.model_validate(host.state))
    tool(kitchen, "update_prep_items", {"expected_revision": host.state["revision"],
         "items": [{"item_id": item.id, "status": "ready"} for item in items]})
    action(kitchen, "change_stage", {"stage": "cooking"})
    for identity, seconds in (("r1", 60), ("r2", 90)):
        action(kitchen, "start_timer", {"seconds": seconds, "label": identity,
               "recipe_id": identity, "step_id": f"{identity}:step:0"})
    action(kitchen, "start_timer", {"seconds": 60, "label": "厨房提醒"})
    views = service.get_workspace()["views"]
    guide = next(view["data"] for view in views if view["kind"] == "cooking_guide")
    assert len(guide["timers"]) == 1 and guide["timers"][0]["recipe_id"] == "r1"
    assert guide["server_now"] == clock[0].isoformat()
    timers = next(view["data"] for view in views if view["kind"] == "timers")
    assert len(timers["timers"]) == 3 and timers["server_now"] == guide["server_now"]
    action(kitchen, "set_active_recipe", {"recipe_id": "r2"})
    clock[0] += timedelta(seconds=91)
    before = copy.deepcopy(host.state)
    restored = WorkflowService(retrieval, host.get, host.mutate, now=lambda: clock[0])
    snapshot = restored.get_workspace()
    expired = next(view["data"] for view in snapshot["views"] if view["kind"] == "cooking_guide")
    assert expired["timers"][0]["recipe_id"] == "r2" and expired["timers"][0]["status"] == "finished"
    assert expired["timers"][0]["remaining_seconds"] == 0 and expired["timers"][0]["deadline_at"] is None
    assert host.state == before and not any(status == "done" for status in host.state["step_status"].values())
    timer_id = expired["timers"][0]["id"]
    assert action(kitchen, "pause_timer", {"timer_id": timer_id})["status"] == "timer_state_conflict"
    assert action(kitchen, "resume_timer", {"timer_id": timer_id})["status"] == "timer_state_conflict"
    assert action(kitchen, "cancel_timer", {"timer_id": timer_id})["status"] == "ok"
    assert next(timer for timer in host.state["timers"] if timer["id"] == timer_id)["status"] == "finished"
    assert host.state["meal_plan"][0]["recipe"]["steps"] == retrieval.recipes["r1"]["steps"]


def test_terminal_history_never_evicts_an_active_timer(kitchen):
    _, host, _, clock = kitchen
    prepare(kitchen)
    action(kitchen, "start_timer", {"seconds": 3600, "label": "长计时", "recipe_id": "r1"})
    long_timer = copy.deepcopy(host.state["timers"][0])
    for index in range(32):
        clock[0] += timedelta(seconds=2)
        action(kitchen, "start_timer", {"seconds": 1, "label": f"短计时{index}"})
    assert len(host.state["timers"]) == 30
    retained = next(timer for timer in host.state["timers"] if timer["id"] == long_timer["id"])
    assert retained["status"] == "running" and retained["deadline_at"] == long_timer["deadline_at"]


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
    for name in ("show_meal_plan", "show_prep_checklist", "show_cooking_guide", "show_timers"):
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


def test_user_buttons_select_prepare_and_cook_without_extra_model_tools(kitchen):
    choices = tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT})
    result = action(kitchen, "select_recipes", {"recipe_ids": ["r1"]},
                    view_id=choices["actions"][0]["component"]["id"])
    assert result["actions"][0]["component"]["kind"] == "meal_plan"
    progress = next(view for view in result["views"] if view["kind"] == "task_progress")
    assert progress["data"]["stage"] == "planning"
    for stage, kind in (("prep", "prep_checklist"),):
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


def test_model_preference_patch_shows_saved_values_and_rejects_stale_revision(kitchen):
    _, host, _, _ = kitchen
    action(kitchen, "update_preferences", {"values": {"servings": 2, "taste_tags": ["light"]}})
    result = tool(kitchen, "update_preferences", {"expected_revision": 1, "changed_fields": ["ingredient_terms"],
                                                 "values": {"ingredient_terms": ["虾"]}})
    assert result["status"] == "ok"
    form = result["actions"][0]["component"]
    assert form["kind"] == "preference_form"
    assert form["data"]["values"]["servings"] == 2 and form["data"]["values"]["taste_tags"] == ["light"]
    assert form["data"]["fields"][0]["value"] == ["虾"]
    before = copy.deepcopy(host.state)
    assert tool(kitchen, "update_preferences", {"expected_revision": 1, "changed_fields": ["servings"],
                                               "values": {"servings": 8}})["status"] == "revision_conflict"
    assert host.state == before


def test_model_selection_uses_current_real_candidates_and_keeps_execution_guard(kitchen):
    _, host, _, _ = kitchen
    tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1", "r2"], "data_snapshot": SNAPSHOT})
    result = tool(kitchen, "select_recipes", {"recipe_ids": ["r2"], "expected_revision": 1})
    assert result["status"] == "ok" and result["ui_snapshot"]["selected_recipe_ids"] == ["r2"]
    before = copy.deepcopy(host.state)
    assert tool(kitchen, "select_recipes", {"recipe_ids": ["invented"], "expected_revision": 2})["status"] == "invalid_reference"
    assert host.state == before
    action(kitchen, "change_stage", {"stage": "prep"})
    assert tool(kitchen, "select_recipes", {"recipe_ids": ["r1"], "expected_revision": 3})["status"] == "stage_conflict"
    assert host.state["meal_plan"][0]["recipe_id"] == "r2"


def test_model_bulk_preparation_changes_only_explicit_real_ids_atomically(kitchen):
    _, host, _, _ = kitchen
    choose(kitchen, ids=["r1", "r2"])
    result = tool(kitchen, "update_prep_items", {"expected_revision": 1,
        "items": [{"item_id": "r1:ingredient:1", "status": "ready"}, {"item_id": "r2:ingredient:1", "status": "ready"}]})
    assert result["status"] == "ok"
    assert host.state["prep_status"] == {"r1:ingredient:1": "ready", "r2:ingredient:1": "ready"}
    assert result["actions"][0]["component"]["kind"] == "prep_checklist"
    before = copy.deepcopy(host.state)
    invalid = {"expected_revision": 2, "items": [{"item_id": "r1:ingredient:0", "status": "ready"},
                                                {"item_id": "foreign", "status": "ready"}]}
    assert tool(kitchen, "update_prep_items", invalid)["status"] == "invalid_reference"
    assert host.state == before
    invalid["expected_revision"] = 1
    assert tool(kitchen, "update_prep_items", invalid)["status"] == "revision_conflict"
    assert host.state == before


def test_question_waits_persists_answer_and_idempotent_submission(kitchen):
    service, host, _, _ = kitchen
    request = {"question": "今晚想吃什么口味？", "options": [{"id": "light", "label": "清淡"}, {"id": "spicy", "label": "微辣"}]}
    result = tool(kitchen, "ask_user", request, operation_id="question-1")
    question = result["pending_question"]
    assert question == service.model_snapshot()["pending_question"]
    assert result["actions"] == [] and question["allow_custom"] is True
    again = tool(kitchen, "ask_user", request, operation_id="question-1")
    assert again["pending_question"]["id"] == question["id"] and host.state["revision"] == 1
    assert tool(kitchen, "ask_user", {"question": "替换问题"})["status"] == "pending_question_exists"
    invalid = action(kitchen, "answer_question", {"question_id": question["id"], "option_ids": ["unknown"]})
    assert invalid["status"] == "invalid_answer" and host.state["pending_question"]["id"] == question["id"]
    payload = {"question_id": question["id"], "option_ids": ["light"], "text": "尽量少盐"}
    answer = action(kitchen, "answer_question", payload, action_id="answer-1")
    assert answer["status"] == "ok" and answer["pending_question"] is None
    assert answer["last_answer"]["labels"] == ["清淡"] and answer["last_answer"]["text"] == "尽量少盐"
    assert action(kitchen, "answer_question", payload, action_id="answer-1")["status"] == "ok"
    assert host.state["revision"] == 2
    assert action(kitchen, "answer_question", payload, action_id="answer-2")["status"] == "question_already_answered"


def test_question_answer_checks_current_revision_and_question_rules(kitchen):
    _, host, _, _ = kitchen
    result = tool(kitchen, "ask_user", {"question": "选一种厨具", "allow_custom": False,
                                       "options": [{"id": "pan", "label": "平底锅"}, {"id": "pot", "label": "汤锅"}]})
    question_id = result["pending_question"]["id"]
    for payload in ({"question_id": question_id, "text": "自填被禁用"},
                    {"question_id": question_id, "option_ids": ["pan", "pot"]},
                    {"question_id": question_id}, {"question_id": "old", "option_ids": ["pan"]}):
        before = copy.deepcopy(host.state)
        assert action(kitchen, "answer_question", payload)["status"] in {"invalid_answer", "stale_question"}
        assert host.state == before
    action(kitchen, "update_preferences", {"values": {"servings": 3}})
    assert action(kitchen, "answer_question", {"question_id": question_id, "option_ids": ["pan"]},
                  base_revision=1)["status"] == "revision_conflict"
    assert host.state["pending_question"] is not None


@pytest.mark.parametrize("name,tool_request", [
    ("ask_user", {"question": "<img src=x>"}),
    ("ask_user", {"question": "无法回答", "allow_custom": False}),
    ("ask_user", {"question": "重复", "options": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]}),
    ("ask_user", {"question": "过长", "options": [{"id": "a", "label": "a" * 81}]}),
    ("update_preferences", {"values": {"servings": 2}}),
    ("update_prep_items", {"expected_revision": 0, "items": [{"item_id": "x", "status": "ready"}, {"item_id": "x", "status": "pending"}]}),
])
def test_harness_tools_reject_unbounded_questions_and_implicit_mutations(kitchen, name, tool_request):
    _, host, _, _ = kitchen
    result = tool(kitchen, name, tool_request)
    assert result["status"] == "invalid_arguments" and result["errors"] and result["recovery"]
    assert host.state == {}


def test_legacy_purchases_migrate_to_single_preparation_without_guessing_other_dishes(kitchen):
    from chefmate_workflow import UISnapshot
    service, host, _, _ = kitchen
    choose(kitchen, ids=["r1", "r2"])
    host.state.update(schema_version=1, stage="shopping", shopping_checked=["r1:ingredient:0", "legacy-salt"])
    host.state["components"].append({"id": "old-shopping", "kind": "shopping_list", "title": "采购", "revision": 1,
        "data": {"recipe_ids": None, "items": [{"id": "legacy-salt", "source_item_ids": ["r1:ingredient:1"],
                  "ingredient_id": "salt", "canonical_name": "盐", "unit": "g", "amount_value": 1, "checked": False}]}})
    before = copy.deepcopy(host.state)
    loaded = WorkspaceState.model_validate(host.state)
    assert loaded.schema_version == 2 and loaded.stage == "prep"
    assert loaded.prep_status == {"r1:ingredient:0": "ready", "r1:ingredient:1": "ready"}
    assert host.state == before
    snapshot = service.model_snapshot()
    assert snapshot["stage"] == "prep" and all(view["kind"] != "shopping_list" for view in snapshot["components"])
    prep = next(view for view in snapshot["components"] if view["kind"] == "prep_checklist")
    assert next(item for item in prep["data"]["items"] if item["id"] == "r2:ingredient:0")["status"] == "pending"
    historical = UISnapshot.model_validate({"revision": 1, "stage": "shopping", "components": [
        {"id": "old", "kind": "shopping_list", "title": "采购", "revision": 1, "editable": True, "data": {}}]})
    assert historical.stage == "prep" and historical.components == []
    action(kitchen, "check_prep_item", {"item_id": "r2:ingredient:0", "status": "ready"})
    assert host.state["schema_version"] == 2 and "shopping_checked" not in host.state


def test_preference_contract_preserves_omitted_and_strict_filler_fields_and_supports_clear(kitchen):
    from chefmate_workflow import TOOL_REQUEST_MODELS
    _, host, _, _ = kitchen
    action(kitchen, "update_preferences", {"values": {"taste_tags": ["light"], "ingredient_terms": ["虾"],
        "exclude_ingredient_terms": ["香菜"], "dietary_notes": "少盐", "available_tools": ["锅"], "allow_estimated": False}})
    # A strict gateway may fill every values field: the explicit mask protects unrelated saved values.
    request = {"expected_revision": 1, "changed_fields": ["servings", "max_total_time_min"], "values": {
        "servings": 1, "max_total_time_min": 20, "taste_tags": [], "ingredient_terms": [],
        "exclude_ingredient_terms": [], "dietary_notes": "", "available_tools": None, "allow_estimated": True}}
    result = tool(kitchen, "update_preferences", request)
    assert result["status"] == "ok"
    saved = host.state["preferences"]
    assert saved["servings"] == 1 and saved["max_total_time_min"] == 20
    assert saved["taste_tags"] == ["light"] and saved["ingredient_terms"] == ["虾"]
    assert saved["exclude_ingredient_terms"] == ["香菜"] and saved["dietary_notes"] == "少盐"
    assert saved["available_tools"] == ["锅"] and saved["allow_estimated"] is False
    assert {field["name"] for field in result["actions"][0]["component"]["data"]["fields"]} == {"servings", "max_total_time_min"}
    clear = {"servings": None, "max_total_time_min": None, "available_tools": None,
             "taste_tags": [], "ingredient_terms": [], "exclude_ingredient_terms": [], "dietary_notes": ""}
    cleared = tool(kitchen, "update_preferences", {"expected_revision": 2, "changed_fields": list(clear), "values": clear})
    assert cleared["status"] == "ok" and all(host.state["preferences"][key] == value for key, value in clear.items())
    for field in ("taste_tags", "ingredient_terms", "exclude_ingredient_terms", "dietary_notes", "allow_estimated"):
        invalid = {"expected_revision": 3, "changed_fields": [field], "values": {field: None}}
        before = copy.deepcopy(host.state)
        with pytest.raises(ValidationError):
            TOOL_REQUEST_MODELS["update_preferences"].model_validate(invalid)
        rejected = tool(kitchen, "update_preferences", invalid)
        assert rejected["status"] == "invalid_arguments" and host.state == before
        assert rejected["errors"][0]["loc"] == ["values", field] and rejected["errors"][0]["hint"]
        assert all("input" not in error and "ctx" not in error for error in rejected["errors"])
    before = copy.deepcopy(host.state)
    rejected = tool(kitchen, "update_preferences", {"expected_revision": 3, "changed_fields": ["servings"],
                                                   "values": {"servings": "never-echo-this-private-input"}})
    assert "never-echo" not in json.dumps(rejected, ensure_ascii=False) and host.state == before
    assert tool(kitchen, "update_preferences", {"expected_revision": 3, "values": {"servings": 2}})["status"] == "invalid_arguments"
    before = copy.deepcopy(host.state)
    ui_rejected = action(kitchen, "update_preferences", {"values": {"taste_tags": None}})
    assert ui_rejected["status"] == "invalid_action" and ui_rejected["errors"] and host.state == before


def test_presentation_revision_does_not_conflict_with_business_write_but_replaced_view_is_stale(kitchen):
    service, host, _, _ = kitchen
    first = tool(kitchen, "show_preference_form")
    first_id = first["actions"][0]["component"]["id"]
    assert first["ui_snapshot"]["revision"] == 0 and first["ui_snapshot"]["presentation_revision"] == 1
    second = tool(kitchen, "show_preference_form")
    assert second["ui_snapshot"]["revision"] == 0 and second["ui_snapshot"]["presentation_revision"] == 2
    assert action(kitchen, "update_preferences", {"values": {"servings": 2}}, view_id=first_id)["status"] == "stale_view"
    saved = tool(kitchen, "update_preferences", {"expected_revision": 0, "changed_fields": ["servings"], "values": {"servings": 2}})
    assert saved["status"] == "ok" and saved["ui_snapshot"]["revision"] == 1
    before_read = copy.deepcopy(host.state)
    assert service.model_snapshot()["presentation_revision"] == 3 and host.state == before_read
    action(kitchen, "update_preferences", {"values": {"servings": 4}})
    stale = tool(kitchen, "update_preferences", {"expected_revision": 1, "changed_fields": ["servings"], "values": {"servings": 1}})
    assert stale["status"] == "revision_conflict" and host.state["preferences"]["servings"] == 4


def test_select_direct_approved_reference_matches_display_then_select_and_legacy_entry(kitchen):
    service, host, retrieval, clock = kitchen
    direct = tool(kitchen, "select_recipes", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT,
                                            "expected_revision": 0, "servings": {"r1": 3}})
    assert direct["status"] == "ok" and direct["ui_snapshot"]["stage"] == "planning"
    direct_plan = copy.deepcopy(host.state["meal_plan"])
    for use_display, legacy in ((True, False), (False, True)):
        other_host = Host()
        other_service = WorkflowService(retrieval, other_host.get, other_host.mutate, now=lambda: clock[0])
        other = other_service, other_host, retrieval, clock
        if use_display:
            tool(other, "show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT})
        request = {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT, "servings": {"r1": 3}}
        if not legacy:
            request["expected_revision"] = other_host.state.get("revision", 0)
        assert tool(other, "set_meal_plan" if legacy else "select_recipes", request)["status"] == "ok"
        assert other_host.state["meal_plan"] == direct_plan
    action(kitchen, "check_prep_item", {"item_id": "r1:ingredient:1", "status": "ready"})
    host.state["step_status"]["r1:step:0"] = "done"
    before = copy.deepcopy(host.state)
    selected = tool(kitchen, "select_recipes", {"recipe_ids": ["r1"], "expected_revision": before["revision"]})
    assert selected["status"] == "ok" and host.state["revision"] == before["revision"]
    assert host.state["prep_status"] == before["prep_status"] and host.state["step_status"] == before["step_status"]
    assert host.state["meal_plan"] == direct_plan
    # Re-showing an already registered candidate is presentation-only; new candidates are business state.
    tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT})
    revision = host.state["revision"]
    tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT})
    assert host.state["revision"] == revision
    tool(kitchen, "show_recipe_choices", {"recipe_ids": ["r1", "r2"], "data_snapshot": SNAPSHOT})
    assert host.state["revision"] == revision + 1
    assert service.model_snapshot()["current_actions"]["set_meal_plan"]["available"] is False


def test_select_requires_unambiguous_approved_reference_and_rejects_concurrent_write(kitchen):
    service, host, retrieval, _ = kitchen
    newer = {"data_version": "new", "imported_at": "2026-10-21T00:00:00+00:00"}
    refs = [{"recipe_id": "r1", "data_snapshot": snapshot} for snapshot in (SNAPSHOT, newer)]
    assert service.execute_tool("select_recipes", {"recipe_ids": ["r1"], "expected_revision": 0},
                                approved_recipes=refs)["status"] == "snapshot_required"
    assert host.state == {} and not retrieval.calls
    retrieval.after_read = lambda: action(kitchen, "update_preferences", {"values": {"servings": 4}})
    raced = service.execute_tool("select_recipes", {"recipe_ids": ["r1"], "expected_revision": 0,
                                                   "data_snapshot": SNAPSHOT}, approved_recipes=refs)
    assert raced["status"] == "revision_conflict" and not host.state["meal_plan"]
    assert host.state["preferences"]["servings"] == 4


def test_show_existing_views_only_changes_presentation_but_new_source_and_active_step_are_business(kitchen):
    _, host, _, _ = kitchen
    choose(kitchen, ids=["r1", "r2"])
    revision = host.state["revision"]
    for name, request in (("show_meal_plan", {}), ("show_prep_checklist", {}), ("show_timers", {}),
                          ("show_recipe_details", {"recipe_id": "r1"}), ("show_cooking_guide", {})):
        before_presentation = host.state["presentation_revision"]
        result = tool(kitchen, name, request)
        assert result["status"] == "ok" and host.state["revision"] == revision
        assert host.state["presentation_revision"] == before_presentation + 1
    tool(kitchen, "show_cooking_guide", {"recipe_id": "r2"})
    assert host.state["revision"] == revision + 1 and host.state["active_recipe_id"] == "r2"
    tool(kitchen, "show_cooking_guide", {"recipe_id": "r2"})
    assert host.state["revision"] == revision + 1
    tool(kitchen, "show_recipe_details", {"recipe_id": "r3", "data_snapshot": SNAPSHOT})
    assert host.state["revision"] == revision + 2
    tool(kitchen, "show_recipe_details", {"recipe_id": "r3"})
    assert host.state["revision"] == revision + 2


def test_registry_and_current_capabilities_share_stage_and_transition_guards(kitchen):
    from chefmate_workflow import TOOL_CONTRACTS, TOOL_REQUEST_MODELS
    service, host, _, _ = kitchen
    assert set(TOOL_CONTRACTS) == set(TOOL_REQUEST_MODELS)
    for model in TOOL_REQUEST_MODELS.values():
        assert "base_revision" not in model.model_json_schema()["properties"]
    empty = service.model_snapshot()
    assert not empty["allowed_transitions"]
    assert {item["code"] for item in empty["blockers"]["planning"]} == {"plan_required"}
    assert tool(kitchen, "advance_cooking_stage", {"stage": "planning"})["status"] == "plan_required"
    choose(kitchen)
    plan = service.model_snapshot()
    assert set(plan["allowed_transitions"]) == {"idea", "prep"}
    assert plan["current_actions"]["update_prep_items"]["available"] is True
    assert plan["current_actions"]["set_cooking_timer"]["available"] is False
    action(kitchen, "change_stage", {"stage": "prep"})
    prep = service.model_snapshot()
    assert prep["allowed_transitions"] == ["planning"]
    assert prep["blockers"]["cooking"][0]["code"] == "prep_incomplete"
    assert tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})["status"] == "prep_incomplete"
    for item in service._prep_items(WorkspaceState.model_validate(host.state)):
        action(kitchen, "check_prep_item", {"item_id": item.id, "status": "ready"})
    assert "cooking" in service.model_snapshot()["allowed_transitions"]
    tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})
    cooking = service.model_snapshot()
    assert cooking["current_actions"]["select_recipes"]["available"] is False
    assert cooking["current_actions"]["update_prep_items"]["available"] is False
    assert cooking["current_actions"]["set_cooking_timer"]["available"] is True
    assert cooking["blockers"]["completed"][0]["code"] == "steps_incomplete"
    assert tool(kitchen, "advance_cooking_stage", {"stage": "completed"})["status"] == "steps_incomplete"
    for ordinal in (0, 1):
        action(kitchen, "complete_step", {"recipe_id": "r1", "step_id": f"r1:step:{ordinal}", "completed": True})
    assert "completed" in service.model_snapshot()["allowed_transitions"]
    tool(kitchen, "advance_cooking_stage", {"stage": "completed"})
    assert service.model_snapshot()["current_actions"]["set_cooking_timer"]["available"] is False
    question = tool(kitchen, "ask_user", {"question": "要开始下一餐吗？"})
    pending = question["ui_snapshot"]
    assert not pending["allowed_transitions"] and pending["current_actions"]["get_cooking_workspace"]["available"]
    assert all(not item["available"] for name, item in pending["current_actions"].items() if name != "get_cooking_workspace")
