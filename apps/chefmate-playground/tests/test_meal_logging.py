"""Photo intake is a source-marked draft, never automatic personal consumption."""

import copy
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from chefmate_agent import ToolExecutionContext
from chefmate_playground.journal import JournalRepository
from chefmate_playground.meal_logging import FoodLogDraftRequest, FoodLogRepository, make_meal_logging_tools
from chefmate_playground.sessions import SessionRepository

NOW = datetime(2026, 10, 21, 8, tzinfo=timezone.utc)


def photo():
    return {"title": "今天的午饭", "source_kind": "image", "meal_type": "lunch", "items": [
        {"name": "米饭", "portion": "约一小碗", "amount_g": 150.0, "portion_basis": "estimated",
         "nutrition": {"calories_kcal": 195.0, "protein_g": 4.0, "fat_g": 0.5, "carbohydrate_g": 43.0},
         "nutrition_basis": "estimated", "evidence": "照片中有一小碗米饭", "assumptions": ["按一小碗约150克熟米饭估算"]}]}


def text_record():
    return {"title": "午饭米饭", "source_kind": "text", "source_text": "我午饭吃了150克米饭，包装标注195kcal。", "items": [
        {"name": "米饭", "portion": "150克", "amount_g": 150.0, "portion_basis": "user_provided",
         "nutrition": {"calories_kcal": 195.0}, "nutrition_basis": "user_provided", "evidence": "包装标注195kcal"}]}


def context(values=None, *, images=None, session_id="chat", turn_id="turn", call_id="call-1", binding=None, cancelled=None):
    host = SimpleNamespace(session_id=session_id, turn_id=turn_id, binding=binding, cancelled=cancelled)
    latest = {"role": "user", "content": (values or {}).get("source_text", "这是今天的午饭，帮我记录一下。"), "images": images or []}
    return ToolExecutionContext({}, host, [latest], call_id=call_id)


def tool(journal, name="create_food_log_draft"):
    return next(item for item in make_meal_logging_tools(journal) if item.name == name)


def test_photo_draft_requires_confirmation_and_preserves_estimate_provenance(tmp_path):
    journal = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    result = tool(journal).handler(photo(), context(images=[{"id": "current-image"}]))
    assert result["status"] == "ok"
    record = result["draft"]
    assert record["status"] == "draft" and record["revision"] == 1
    assert record["date"] == "2026-10-21" and record["record_kind"] == "personal_intake"
    assert record["source_image_ids"] == ["current-image"]
    assert record["nutrition"]["estimated"]
    assert record["nutrition"]["total"]["calories_kcal"] == 195.0
    assert record["nutrition"]["total"]["sodium_mg"] is None
    assert journal.summary()["intake"]["record_count"] == 0
    assert journal.summary()["today"]["intake"]["total"]["calories_kcal"] is None
    saved = journal.food_logs.publish(record["id"], 1)
    assert saved["status"] == "saved" and saved["revision"] == 2
    assert journal.food_logs.publish(record["id"], 1) == saved
    summary = journal.summary()
    assert summary["intake"]["record_count"] == 1 and summary["intake"]["estimated_records"] == 1
    assert summary["today"]["intake"]["total"]["calories_kcal"] == 195.0
    assert summary["nutrition"]["meal_total"]["calories_kcal"] is None  # Cooking production stays separate.
    assert summary["production"] == summary["nutrition"]


def test_missing_portion_stays_unknown_and_cannot_smuggle_nutrition(tmp_path):
    request = {"title": "照片里的食物", "source_kind": "image", "items": [{"name": "疑似炖肉"}]}
    repo = FoodLogRepository(tmp_path / "sessions.db", now=lambda: NOW)
    record = repo.create_draft(request, image_ids=["image"])
    assert all(value is None for value in record["nutrition"]["total"].values())
    for patch in ({"amount_g": 100.0}, {"nutrition": {"calories_kcal": 300.0}, "nutrition_basis": "estimated", "evidence": "照片"}):
        changed = copy.deepcopy(request)
        changed["items"][0].update(patch)
        with pytest.raises(ValidationError):
            FoodLogDraftRequest.model_validate(changed)


@pytest.mark.parametrize("mutation", [
    lambda v: v["items"][0].update(assumptions=[]),
    lambda v: v["items"][0].update(nutrition_basis="unknown"),
    lambda v: v["items"][0].update(evidence=""),
    lambda v: v["items"][0]["nutrition"].update(calories_kcal=float("nan")),
    lambda v: v["items"][0]["nutrition"].update(protein_g=-1.0),
    lambda v: v["items"][0]["nutrition"].update(calories_kcal=True),
    lambda v: v["items"][0].update(nutrition_basis="database"),
])
def test_estimate_contract_rejects_false_precision_and_unvalidated_database_claims(mutation):
    values = photo()
    mutation(values)
    with pytest.raises(ValidationError):
        FoodLogDraftRequest.model_validate(values)


def test_text_sources_and_user_provided_values_must_come_from_current_user(tmp_path):
    journal = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    create = tool(journal).handler
    values = text_record()
    assert create(values, context(values))["status"] == "ok"
    changed = copy.deepcopy(values)
    changed["source_text"] = "模型编写的历史来源"
    assert create(changed, context(values, call_id="2"))["status"] == "invalid_food_log"
    changed = copy.deepcopy(values)
    changed["items"][0]["amount_g"] = 300.0
    assert create(changed, context(values, call_id="3"))["status"] == "invalid_food_log"
    changed = copy.deepcopy(values)
    changed["items"][0]["nutrition"]["calories_kcal"] = 400.0
    assert create(changed, context(values, call_id="4"))["status"] == "invalid_food_log"
    assert journal.food_logs.list(status="all")["total"] == 1


def test_only_current_user_images_can_be_bound_and_no_image_ids_in_model_schema(tmp_path):
    journal = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    execution = context(images=[])
    execution.messages.insert(0, {"role": "user", "content": "旧图片", "images": [{"id": "old-image"}]})
    assert tool(journal).handler(photo(), execution)["status"] == "invalid_food_log"
    assert "source_image_ids" not in FoodLogDraftRequest.model_fields
    assert journal.food_logs.list(status="draft")["total"] == 0


def test_user_provided_evidence_cannot_borrow_another_food_weight_or_swap_macros(tmp_path):
    journal = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    values = text_record()
    values["source_text"] += "另吃肉300克，米饭营养标签蛋白质4克，脂肪0.5克。"
    item = values["items"][0]
    item["amount_g"] = 300.0  # 300 exists in user text, but is not this food's 150克 portion.
    assert tool(journal).handler(values, context(values))["status"] == "invalid_food_log"
    item["amount_g"] = 150.0
    item["evidence"] = "包装标注195kcal。另吃肉300克，米饭营养标签蛋白质4克，脂肪0.5克。"
    item["nutrition"].update(protein_g=0.5, fat_g=4.0)
    assert tool(journal).handler(values, context(values))["status"] == "invalid_food_log"
    item["nutrition"].update(protein_g=4.0, fat_g=0.5)
    assert tool(journal).handler(values, context(values))["status"] == "ok"


def test_revision_compare_and_publish_retry_keep_original_idempotency_fingerprint(tmp_path):
    repo = FoodLogRepository(tmp_path / "sessions.db", now=lambda: NOW)
    record = repo.create_draft(photo(), session_id="chat", image_ids=["i"], operation_id="same-call")
    changed = photo()
    changed["title"] = "我核对过的午饭"
    updated = repo.update_draft(record["id"], 1, changed)
    assert updated["revision"] == 2
    with pytest.raises(ValueError):
        repo.update_draft(record["id"], 1, photo())
    assert repo.create_draft(photo(), session_id="chat", image_ids=["i"], operation_id="same-call")["revision"] == 2
    with pytest.raises(ValueError):
        repo.create_draft(changed, session_id="chat", image_ids=["i"], operation_id="same-call")
    saved = repo.publish(record["id"], 2)
    assert saved["revision"] == 3
    assert repo.publish(record["id"], 2)["revision"] == 3
    with pytest.raises(ValueError):
        repo.update_draft(record["id"], 3, changed)


def test_partial_and_daily_totals_do_not_replace_unknown_with_zero(tmp_path):
    repo = FoodLogRepository(tmp_path / "sessions.db", now=lambda: NOW)
    values = photo()
    values["items"].append({"name": "汤，份量不清楚"})
    record = repo.create_draft(values, image_ids=["image"])
    repo.publish(record["id"], 1)
    summary = repo.summary(7)
    assert summary["total"]["calories_kcal"] is None
    assert summary["known_total"]["calories_kcal"] == 195.0
    assert summary["coverage"]["calories_kcal"] == {"known": 0, "total": 1}
    assert summary["estimated"] and "calories_kcal" in summary["estimated_fields"]
    assert summary["today"]["record_count"] == 1
    assert summary["days"][0]["record_count"] == 0
    assert summary["days"][0]["total"]["calories_kcal"] is None


def test_local_date_explicit_history_evidence_and_future_rejection(tmp_path):
    journal = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    values = text_record()
    values.update(date="2026-10-20", date_evidence="昨天", source_text="我昨天午饭吃了150克米饭，包装标注195kcal。")
    result = tool(journal).handler(values, context(values))
    assert result["status"] == "ok" and result["draft"]["date"] == "2026-10-20"
    values["date"] = "2026-10-19"
    assert tool(journal).handler(values, context(values, call_id="wrong-date"))["status"] == "invalid_food_log"
    values["date"] = "2026-10-22"
    with pytest.raises(ValueError):
        journal.food_logs.create_draft(values)


def test_cancelled_and_stale_active_turn_cannot_create_draft(tmp_path):
    path = tmp_path / "sessions.db"
    sessions = SessionRepository(path)
    state = {"id": "chat", "updated_at": NOW.isoformat(), "running": True, "active_turn": {"id": "turn"}, "messages": []}
    sessions.save(state)
    journal = JournalRepository(path, now=lambda: NOW)
    event = threading.Event()
    event.set()
    result = tool(journal).handler(photo(), context(images=[{"id": "i"}], cancelled=event))
    assert result["status"] == "invalid_food_log"
    binding = SimpleNamespace(repository=sessions)
    result = tool(journal).handler(photo(), context(images=[{"id": "i"}], binding=binding, turn_id="old-turn"))
    assert result["status"] == "invalid_food_log"
    assert journal.food_logs.list(status="all")["total"] == 0


def test_cancellation_at_transaction_end_rolls_back_draft(tmp_path):
    repo = FoodLogRepository(tmp_path / "sessions.db", now=lambda: NOW)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("cancelled")

    with pytest.raises(ValueError):
        repo.create_draft(photo(), image_ids=["i"], guard=guard)
    assert repo.list(status="all")["total"] == 0


def test_intake_survives_chat_deletion_and_soft_delete_can_restore(tmp_path):
    path = tmp_path / "sessions.db"
    sessions = SessionRepository(path)
    sessions.save({"id": "chat", "updated_at": NOW.isoformat(), "messages": []})
    repo = FoodLogRepository(path, now=lambda: NOW)
    draft = repo.create_draft(photo(), session_id="chat", image_ids=["image"])
    saved = repo.publish(draft["id"], 1)
    sessions.delete("chat")
    assert repo.summary()["record_count"] == 1
    deleted = repo.delete(saved["id"], 2)
    assert deleted["status"] == "deleted" and repo.summary()["record_count"] == 0
    assert repo.get(saved["id"]) is None
    restored = repo.restore(saved["id"], 3)
    assert restored["status"] == "saved" and restored["document"] == saved["document"]
    assert repo.summary()["record_count"] == 1


def test_search_is_bounded_literal_and_does_not_count_drafts(tmp_path):
    journal = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    journal.food_logs.create_draft(photo(), image_ids=["i"])
    search = tool(journal, "search_food_logs")
    assert search.required_skill == "food-journal" and search.effect == "read"
    result = search.handler({"status": "draft", "query": "米饭"}, context())
    assert result["total"] == 1 and result["summary"]["record_count"] == 0
    assert "source_text" not in str(result) and "days" not in result["summary"]
    assert search.handler({"status": "all", "query": "%"}, context())["total"] == 0
    for bad in ({"days": True}, {"limit": 1001}, {"offset": -1}):
        with pytest.raises(ValueError):
            journal.food_logs.list(**bad)


def test_write_receipt_is_small_even_when_the_review_document_is_large(tmp_path):
    import json

    journal = JournalRepository(tmp_path / "sessions.db", now=lambda: NOW)
    values = photo()
    values["items"] = [{**copy.deepcopy(values["items"][0]), "assumptions": ["份量与配料需要核对。" * 30] * 12} for _ in range(30)]
    result = tool(journal).handler(values, context(images=[{"id": "i"}]))
    assert result["status"] == "ok" and result["draft"]["food_count"] == 30
    assert len(json.dumps(result, ensure_ascii=False)) < 5000
    assert len(journal.food_logs.get(result["draft"]["id"])["document"]["items"]) == 30
