"""Journal persistence uses observed lifecycle times and source-marked nutrition."""

import copy
from datetime import datetime, timezone

import pytest

from chefmate_playground.journal import JournalRepository
from chefmate_playground.sessions import SessionRepository

NUTRIENTS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")
NOW = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)


def cooking_state(*, completed=False, unknown=False):
    totals = dict.fromkeys(NUTRIENTS, 10.0)
    if unknown:
        totals["sodium_mg"] = None
    recipe = {"recipe_id": "r1", "name": "测试番茄汤", "servings": 2.0, "estimated_servings": False,
              "ingredients": [{"ingredient_id": "tomato", "canonical_name": "番茄", "grams": 200.0,
                               "raw_amount": "200", "raw_unit": "g", "scalable": True,
                               "estimated_grams": False, "nutrition": {"contribution": totals}}],
              "steps": ["煮熟"], "nutrition": {"total": totals, "per_serving": {key: value / 2 if value is not None else None
                  for key, value in totals.items()}, "estimated": True, "estimated_total_fields": ["calories_kcal"]}}
    workspace = {"meal_id": "meal1", "stage": "completed" if completed else "cooking", "meal_diners": 5.0,
                 "started_at": "2026-10-20T16:05:00+00:00", "completed_at": "2026-10-21T00:20:00+00:00" if completed else None,
                 "meal_plan": [{"recipe_id": "r1", "recipe": recipe, "target_servings": 2.5,
                                "data_snapshot": {"data_version": "fixture", "imported_at": "2026-10-01T00:00:00+00:00"}}]}
    return {"id": "s1", "workspace": workspace}


def test_observed_start_upgrade_completion_and_idempotency(tmp_path):
    repo = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    assert repo.sync_session(cooking_state())
    assert not repo.sync_session(cooking_state())
    assert repo.entries()[0]["status"] == "attempted"
    assert repo.sync_session(cooking_state(completed=True))
    assert not repo.sync_session(cooking_state(completed=True))
    entries = repo.entries(date="2026-10-21")
    assert len(entries) == 1 and entries[0]["status"] == "completed"
    assert entries[0]["date"] == "2026-10-21"  # 16:05 UTC is the next local day.
    assert entries[0]["recipes"][0]["ingredients"][0]["grams"] == 250
    assert entries[0]["nutrition"]["meal_total"]["calories_kcal"] == 12.5
    assert entries[0]["nutrition"]["per_diner_reference"]["calories_kcal"] == 2.5
    assert entries[0]["nutrition"]["estimated"]


def test_legacy_states_do_not_invent_cooking_dates(tmp_path):
    repo = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    state = cooking_state(completed=True)
    del state["workspace"]["meal_id"]
    assert not repo.sync_session(state)
    state["workspace"]["meal_id"] = "old"
    del state["workspace"]["started_at"]
    assert not repo.sync_session(state)
    assert repo.entries() == []


def test_unknown_nutrition_is_not_zero_and_attempts_are_not_intake(tmp_path):
    repo = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    repo.sync_session(cooking_state(unknown=True))
    summary = repo.summary(days=7)
    assert summary["completed_meals"] == 0 and summary["attempted_meals"] == 1
    assert summary["nutrition"]["meal_total"]["calories_kcal"] is None
    repo.sync_session(cooking_state(completed=True, unknown=True))
    summary = repo.summary(days=7)
    assert summary["nutrition"]["meal_total"]["sodium_mg"] is None
    assert summary["nutrition"]["per_diner_reference"]["sodium_mg"] is None
    assert summary["nutrition"]["coverage"]["sodium_mg"] == {"known": 0, "total": 1}
    assert summary["completed_dishes"] == 1
    assert summary["days"][-1]["dish_count"] == 1
    assert "不代表个人实际摄入" in summary["notice"]


def test_completed_snapshot_and_feedback_survive_session_deletion(tmp_path):
    path = tmp_path / "sessions.db"
    sessions = SessionRepository(path)
    repo = JournalRepository(path, now=lambda: NOW)
    state = cooking_state(completed=True)
    sessions.save({**state, "updated_at": NOW.isoformat()})
    repo.sync_session(state)
    entry = repo.entries()[0]
    repo.feedback(entry["id"], rating=5, notes="下次少放盐")
    changed = copy.deepcopy(state)
    changed["workspace"]["meal_plan"][0]["recipe"]["name"] = "后来的源菜谱"
    assert not repo.sync_session(changed)
    sessions.delete("s1")
    saved = JournalRepository(path, now=lambda: NOW).get(entry["id"])
    assert saved["recipes"][0]["name"] == "测试番茄汤"
    assert saved["rating"] == 5 and saved["notes"] == "下次少放盐"
    assert repo.search("番茄")[0]["status"] == "completed"
    assert repo.search("%") == []


def test_missing_diner_count_keeps_personal_reference_unknown(tmp_path):
    repo = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    state = cooking_state(completed=True)
    state["workspace"]["meal_diners"] = None
    repo.sync_session(state)
    nutrition = repo.summary()["nutrition"]
    assert nutrition["meal_total"]["calories_kcal"] == 12.5
    assert nutrition["per_diner_reference"]["calories_kcal"] is None


def test_feedback_and_range_validation(tmp_path):
    repo = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    with pytest.raises(ValueError):
        repo.feedback("missing", rating=True)
    with pytest.raises(KeyError):
        repo.feedback("missing", rating=3)
    with pytest.raises(ValueError):
        repo.entries(date="tomorrow")
    with pytest.raises(ValueError):
        repo.summary(days=0)
