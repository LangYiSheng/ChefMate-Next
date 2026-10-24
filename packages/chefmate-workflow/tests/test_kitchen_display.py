"""Display requests are typed presentation events, never cooking mutations."""

import copy

import pytest
from pydantic import ValidationError

from chefmate_workflow import UIAction, WorkflowToolResult
import test_workflow
from test_workflow import action, choose, tool

kitchen = test_workflow.kitchen


def test_display_request_uses_current_view_without_changing_cooking_state(kitchen):
    _, host, _, _ = kitchen
    choose(kitchen, ids=["r1", "r2"])
    tool(kitchen, "advance_cooking_stage", {"stage": "prep"})
    scoped = tool(kitchen, "show_prep_checklist", {"recipe_ids": ["r2"]})
    before = copy.deepcopy(host.state)
    result = tool(kitchen, "set_kitchen_display", {"mode": "focus"}, operation_id="display-1")
    display = result["actions"][0]
    assert display == {"kind": "set_kitchen_display", "action_id": "display-1",
                       "mode": "focus", "view_kind": "prep_checklist"}
    assert UIAction.model_validate(display).model_dump(mode="json") == display
    WorkflowToolResult.model_validate(result)
    assert host.state == {**before, "recent_actions": host.state["recent_actions"]}
    assert host.state["recent_actions"][-1]["type"] == "set_kitchen_display"
    view = next(view for view in result["views"] if view["kind"] == "prep_checklist")
    assert view["data"]["recipe_ids"] == ["r2"]
    assert view["data"] == scoped["actions"][0]["component"]["data"]


def test_display_operation_replay_does_not_reemit_or_overwrite(kitchen):
    _, host, _, _ = kitchen
    request = {"mode": "conversation"}
    first = tool(kitchen, "set_kitchen_display", request, operation_id="same")
    before = copy.deepcopy(host.state)
    assert first["actions"][0]["mode"] == "conversation"
    replay = tool(kitchen, "set_kitchen_display", request, operation_id="same")
    assert replay["status"] == "ok" and replay["actions"] == []
    assert host.state == before
    conflict = tool(kitchen, "set_kitchen_display", {"mode": "focus"}, operation_id="same")
    assert conflict["status"] == "action_id_conflict" and conflict["actions"] == []
    assert host.state == before


@pytest.mark.parametrize("arguments,status", [
    ({"mode": "focus"}, "view_unavailable"),
    ({"mode": "focus", "view_kind": "prep_checklist"}, "view_unavailable"),
    ({"mode": "focus", "view_kind": "arbitrary"}, "invalid_arguments"),
    ({"mode": "fullscreen"}, "invalid_arguments"),
    ({"mode": "conversation", "view_kind": "timers"}, "invalid_arguments"),
    ({"mode": "conversation", "script": "alert(1)"}, "invalid_arguments"),
])
def test_unavailable_or_untyped_display_requests_are_atomic(kitchen, arguments, status):
    _, host, _, _ = kitchen
    before = copy.deepcopy(host.state)
    result = tool(kitchen, "set_kitchen_display", arguments)
    assert result["status"] == status and result["actions"] == []
    assert host.state == before


def test_display_cannot_select_a_card_hidden_in_current_stage(kitchen):
    choose(kitchen)
    tool(kitchen, "advance_cooking_stage", {"stage": "prep"})
    for item_id in ("r1:ingredient:0", "r1:ingredient:1"):
        action(kitchen, "check_prep_item", {"item_id": item_id, "status": "ready"})
    tool(kitchen, "advance_cooking_stage", {"stage": "cooking"})
    assert tool(kitchen, "set_kitchen_display", {"mode": "focus", "view_kind": "prep_checklist"})["status"] == "view_unavailable"
    current = tool(kitchen, "set_kitchen_display", {"mode": "focus"})
    assert current["actions"][0]["view_kind"] == "cooking_guide"


def test_action_schema_rejects_fields_from_other_ui_event_kind():
    with pytest.raises(ValidationError):
        UIAction.model_validate({"kind": "set_kitchen_display", "action_id": "a",
                                 "mode": "conversation", "component": {}})
