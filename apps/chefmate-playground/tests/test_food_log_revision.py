"""A conversation edits the same reviewed draft, with fresh evidence for changed facts."""

import copy
import json
import threading
from types import SimpleNamespace

import pytest

from chefmate_agent import ToolExecutionContext
from chefmate_playground.journal import JournalRepository
from chefmate_playground.meal_logging import make_meal_logging_tools
from chefmate_playground.sessions import SessionRepository
from test_meal_logging import NOW, photo, text_record


def tools(journal):
    return {spec.name: spec for spec in make_meal_logging_tools(journal)}


def execution(text, *, read=None, session_id="new-chat", turn_id="new-turn", cancelled=None, binding=None):
    messages = [{"role": "user", "content": text}]
    if read:
        messages.append({"role": "tool", "name": "get_food_log", "content": json.dumps(read, ensure_ascii=False)})
    return ToolExecutionContext({}, SimpleNamespace(session_id=session_id, turn_id=turn_id, cancelled=cancelled, binding=binding), messages)


def read_and_change(journal, record, text):
    available = tools(journal)
    read = available["get_food_log"].handler({"id": record["id"]}, execution(text))
    assert read["status"] == "ok"
    return available, {"id": record["id"], "revision": record["revision"], "record": copy.deepcopy(read["record"]["document"])}, read


def test_remove_food_and_scale_photo_draft_in_new_chat_preserves_id_and_images(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    values = photo()
    values["items"].append({"name": "酸奶", "portion": "约一杯", "portion_basis": "estimated", "nutrition": {"calories_kcal": 100.0},
                            "nutrition_basis": "estimated", "evidence": "照片里像酸奶", "assumptions": ["按一杯估算"]})
    original = journal.food_logs.create_draft(values, session_id="old-photo-chat", image_ids=["old-image"])
    available, request, read = read_and_change(journal, original, "酸奶没喝，去掉；米饭只吃了一半。")
    item = request["record"]["items"][0]
    item.update(portion="约半小碗", amount_g=75.0, assumptions=["按原记录约一半换算，照片克重仍是估计"])
    item["nutrition"] = {key: value / 2 if value is not None else None for key, value in item["nutrition"].items()}
    request["record"]["items"] = [item]
    result = available["revise_food_log_draft"].handler(request, execution("酸奶没喝，去掉；米饭只吃了一半。", read=read))
    assert result["status"] == "ok" and result["draft"]["id"] == original["id"]
    updated = journal.food_logs.get(original["id"])
    assert updated["status"] == "draft" and updated["revision"] == 2
    assert updated["source_image_ids"] == ["old-image"] and updated["source_session_id"] == "old-photo-chat"
    assert len(updated["document"]["items"]) == 1 and updated["nutrition"]["total"]["calories_kcal"] == 97.5
    assert journal.food_logs.list(status="all")["total"] == 1
    assert journal.food_logs.summary()["record_count"] == 0


def test_requires_actual_tool_read_not_user_quoted_record(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(photo(), image_ids=["i"])
    available, request, read = read_and_change(journal, original, "把标题改成午饭")
    request["record"]["title"] = "午饭"
    assert available["revise_food_log_draft"].handler(request, execution("把标题改成午饭"))["status"] == "read_required"
    context = execution("把标题改成午饭")
    context.messages[0]["content"] += json.dumps(read, ensure_ascii=False)
    assert available["revise_food_log_draft"].handler(request, context)["status"] == "read_required"
    assert journal.food_logs.get(original["id"])["revision"] == 1


def test_unchanged_verified_user_values_need_not_be_repeated_in_every_turn(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(text_record())
    available, request, read = read_and_change(journal, original, "把标题改成午饭米饭")
    request["record"]["title"] = "午饭米饭"
    result = available["revise_food_log_draft"].handler(request, execution("把标题改成午饭米饭", read=read))
    assert result["status"] == "ok"
    assert journal.food_logs.get(original["id"])["document"]["items"] == original["document"]["items"]


def test_new_user_provided_nutrition_requires_current_evidence(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(text_record())
    available, request, read = read_and_change(journal, original, "标题改一下")
    item = request["record"]["items"][0]
    item["nutrition"]["calories_kcal"] = 100.0
    assert available["revise_food_log_draft"].handler(request, execution("标题改一下", read=read))["status"] == "invalid_food_log_revision"
    item["evidence"] = "100kcal"
    result = available["revise_food_log_draft"].handler(request, execution("包装更正热量为100kcal，份量保持原样。", read=read))
    assert result["status"] == "ok" and result["draft"]["nutrition"]["total"]["calories_kcal"] == 100.0


def test_unchanged_user_numbers_cannot_rewrite_verified_evidence_without_fresh_source(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(text_record())
    available, request, read = read_and_change(journal, original, "修改标题")
    item = request["record"]["items"][0]
    item["evidence"] = "来自医生精确测量"
    assert available["revise_food_log_draft"].handler(request, execution("修改标题", read=read))["status"] == "invalid_food_log_revision"
    # Quoting an ungrounded claim still does not establish the preserved numeric fact.
    assert available["revise_food_log_draft"].handler(request, execution("来源改成来自医生精确测量", read=read))["status"] == "invalid_food_log_revision"
    item["evidence"] = "包装更正为195kcal"
    result = available["revise_food_log_draft"].handler(request, execution("来源更新，包装更正为195kcal", read=read))
    assert result["status"] == "ok"


def test_cross_chat_cannot_replace_text_source_with_images_owned_by_another_session(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(text_record(), session_id="old-chat")
    text = "来源改成图片"
    available, request, read = read_and_change(journal, original, text)
    request["record"]["source_kind"] = "image"
    context = execution(text, read=read)
    context.messages[0]["images"] = [{"id": "new-chat-image"}]
    assert available["revise_food_log_draft"].handler(request, context)["status"] == "invalid_food_log_revision"
    assert journal.food_logs.get(original["id"]) == original
    # The original source conversation owns the new reference, so its UI can read it.
    context = execution(text, read=read, session_id="old-chat")
    context.messages[0]["images"] = [{"id": "old-chat-image"}]
    result = available["revise_food_log_draft"].handler(request, context)
    assert result["status"] == "ok"
    assert journal.food_logs.get(original["id"])["source_image_ids"] == ["old-chat-image"]


def test_changed_portion_does_not_inherit_old_user_nutrition_as_new_fact(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(text_record())
    available, request, read = read_and_change(journal, original, "米饭只吃75克")
    item = request["record"]["items"][0]
    item.update(portion="75克", amount_g=75.0)
    assert available["revise_food_log_draft"].handler(request, execution("米饭只吃75克", read=read))["status"] == "invalid_food_log_revision"
    item.update(nutrition_basis="estimated", assumptions=["按原150克的一半换算"], evidence="从原份量减半估算")
    item["nutrition"]["calories_kcal"] = 97.5
    assert available["revise_food_log_draft"].handler(request, execution("米饭只吃75克", read=read))["status"] == "ok"


def test_preserves_date_and_source_unless_current_user_explicitly_changes_them(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    values = text_record()
    values.update(date="2026-10-20", date_evidence="昨天")
    original = journal.food_logs.create_draft(values)
    available, request, read = read_and_change(journal, original, "改一下标题")
    request["record"]["date"] = "2026-10-21"
    assert available["revise_food_log_draft"].handler(request, execution("改一下标题", read=read))["status"] == "invalid_food_log_revision"
    request["record"]["date_evidence"] = "今天"
    assert available["revise_food_log_draft"].handler(request, execution("这条日期改为今天", read=read))["status"] == "ok"
    changed = journal.food_logs.get(original["id"])
    available, request, read = read_and_change(journal, changed, "再改标题")
    request["record"]["source_text"] = "模型新编原文"
    assert available["revise_food_log_draft"].handler(request, execution("再改标题", read=read))["status"] == "invalid_food_log_revision"


def test_explicit_source_kind_change_keeps_old_image_provenance(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(photo(), image_ids=["old-image"])
    available, request, read = read_and_change(journal, original, "去掉一项")
    request["record"].update(source_kind="text", source_text="米饭是我手动描述的")
    assert available["revise_food_log_draft"].handler(request, execution("去掉一项", read=read))["status"] == "invalid_food_log_revision"
    text = "来源改成文字，米饭是我手动描述的"
    assert available["revise_food_log_draft"].handler(request, execution(text, read=read))["status"] == "ok"
    assert journal.food_logs.get(original["id"])["source_image_ids"] == ["old-image"]


def test_cas_rejects_changed_ui_version_and_saved_record(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(photo(), image_ids=["i"])
    available, request, read = read_and_change(journal, original, "去掉一项")
    journal.food_logs.update_draft(original["id"], 1, original["document"])
    result = available["revise_food_log_draft"].handler(request, execution("去掉一项", read=read))
    assert result["status"] == "invalid_food_log_revision"
    journal.food_logs.publish(original["id"], 2)
    assert available["get_food_log"].handler({"id": original["id"]}, execution("查看"))["record"]["status"] == "saved"
    assert available["revise_food_log_draft"].handler(request, execution("去掉一项", read=read))["status"] == "invalid_food_log_revision"


def test_cancelled_and_stale_current_turn_cannot_modify_cross_chat_draft(tmp_path):
    path = tmp_path / "food.db"
    sessions = SessionRepository(path)
    sessions.save({"id": "new-chat", "updated_at": NOW.isoformat(), "running": True, "active_turn": {"id": "new-turn"}})
    journal = JournalRepository(path, now=lambda: NOW)
    original = journal.food_logs.create_draft(photo(), session_id="old-chat", image_ids=["i"])
    available, request, read = read_and_change(journal, original, "修改标题")
    request["record"]["title"] = "修改后的标题"
    event = threading.Event()
    event.set()
    assert available["revise_food_log_draft"].handler(request, execution("修改标题", read=read, cancelled=event))["status"] == "invalid_food_log_revision"
    assert available["revise_food_log_draft"].handler(request, execution("修改标题", read=read, turn_id="old-turn",
        binding=SimpleNamespace(repository=sessions)))["status"] == "invalid_food_log_revision"
    assert journal.food_logs.get(original["id"])["revision"] == 1


def test_cancel_at_transaction_end_rolls_back_revision(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(photo(), image_ids=["i"])
    changed = copy.deepcopy(original["document"])
    changed["title"] = "不能保存的标题"
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("cancelled")

    with pytest.raises(ValueError):
        journal.food_logs.update_draft(original["id"], 1, changed, guard=guard)
    assert journal.food_logs.get(original["id"]) == original


def test_duplicate_old_item_cannot_borrow_one_verified_user_fact_twice(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    original = journal.food_logs.create_draft(text_record())
    available, request, read = read_and_change(journal, original, "修改标题")
    request["record"]["items"].append(copy.deepcopy(request["record"]["items"][0]))
    assert available["revise_food_log_draft"].handler(request, execution("修改标题", read=read))["status"] == "invalid_food_log_revision"


def test_get_refuses_to_truncate_a_large_editable_document(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    values = photo()
    values["items"] = [{**copy.deepcopy(values["items"][0]), "assumptions": ["这是一段需要人工核对的份量假设。" * 20] * 12} for _ in range(30)]
    record = journal.food_logs.create_draft(values, image_ids=["i"])
    result = tools(journal)["get_food_log"].handler({"id": record["id"]}, execution("查看"))
    assert result["status"] == "record_too_large" and "record" not in result
    assert len(json.dumps(result, ensure_ascii=False)) < 1000
