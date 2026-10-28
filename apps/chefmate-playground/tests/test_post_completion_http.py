"""A second cooking batch must not reuse the first journal or intake identity."""

from datetime import timedelta

import test_cooking_timers
from chefmate_playground.journal import LOCAL_ZONE
from chefmate_workflow.models import TOOL_REQUEST_MODELS

kitchen_http = test_cooking_timers.kitchen_http


def test_restart_preserves_old_journal_and_intake_while_recording_a_new_meal(kitchen_http):
    kitchen = kitchen_http
    domain = kitchen.client.app.state.workspace.service(kitchen.session_id)

    def command(name, **values):
        if "expected_revision" in TOOL_REQUEST_MODELS[name].model_fields:
            values = {"expected_revision": domain.model_snapshot()["revision"], **values}
        result = domain.execute_tool(name, values)
        assert result["status"] == "ok", result["status"]
        return result

    def ready():
        prep = next(view for view in kitchen.workspace()["views"] if view["kind"] == "prep_checklist")
        for item in prep["data"]["items"]:
            assert kitchen.action("check_prep_item", {"item_id": item["id"], "status": "ready"})["status"] == "ok"

    def complete():
        for ordinal in (0, 1):
            assert kitchen.action("complete_step", {"recipe_id": "eggs", "step_id": f"eggs:step:{ordinal}"})["status"] == "ok"
        result = kitchen.action("complete_meal", {})
        assert result["status"] == "ok"
        assert result["cooking_intake"]["intake_status"] == "saved"
        return result["cooking_intake"]["record"]

    # Establish a real single-diner plan through the domain commands so each
    # actual completion creates its own personal intake record automatically.
    command("advance_cooking_stage", stage="prep")
    command("advance_cooking_stage", stage="planning")
    command("update_preferences", changed_fields=["servings"], values={"servings": 1})
    command("select_recipes", recipe_ids=["eggs"], diners=1)
    command("advance_cooking_stage", stage="prep")
    ready()
    command("advance_cooking_stage", stage="cooking")
    first_log = complete()
    first_day = kitchen.clock[0].astimezone(LOCAL_ZONE).date().isoformat()
    first_entries = kitchen.client.get("/api/journal", params={"date": first_day}).json()["entries"]
    assert len(first_entries) == 1
    first_entry = first_entries[0]

    kitchen.clock[0] += timedelta(days=1)
    command("advance_cooking_stage", stage="planning")
    assert kitchen.repository.get(kitchen.session_id)["workspace"]["meal_id"] != first_entry["meal_id"]
    assert domain.meal_record() is None
    assert kitchen.client.get("/api/journal", params={"date": first_day}).json()["entries"] == first_entries
    assert kitchen.client.get("/api/food-logs/" + first_log["id"]).json() == first_log
    command("advance_cooking_stage", stage="prep")
    assert domain.execute_tool("advance_cooking_stage", {"stage": "cooking"})["status"] == "prep_incomplete"
    ready()
    command("advance_cooking_stage", stage="cooking")
    assert domain.meal_record()["status"] == "attempted"
    second_log = complete()
    second_day = kitchen.clock[0].astimezone(LOCAL_ZONE).date().isoformat()
    second_entries = kitchen.client.get("/api/journal", params={"date": second_day}).json()["entries"]
    assert len(second_entries) == 1 and second_entries[0]["status"] == "completed"
    assert second_entries[0]["meal_id"] != first_entry["meal_id"]
    assert second_log["id"] != first_log["id"]
    assert kitchen.client.get("/api/journal", params={"date": first_day}).json()["entries"] == first_entries
    assert kitchen.client.get("/api/food-logs/" + first_log["id"]).json() == first_log
