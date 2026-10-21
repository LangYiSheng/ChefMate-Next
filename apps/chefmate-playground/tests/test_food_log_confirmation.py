"""Chat confirmation publishes the reviewed revision, never a fresh inference."""

import copy
import json
import threading
from types import SimpleNamespace

import pytest

from chefmate_agent import ToolExecutionContext
from chefmate_playground.journal import JournalRepository
from chefmate_playground.meal_logging import make_meal_logging_tools
from chefmate_playground.sessions import SessionRepository
from test_meal_logging import NOW, photo


def setup(tmp_path):
    journal = JournalRepository(tmp_path / "confirmation.db", now=lambda: NOW)
    tools = {spec.name: spec for spec in make_meal_logging_tools(journal)}
    record = journal.food_logs.create_draft(photo(), session_id="photo-chat", image_ids=["photo"])
    return journal, tools, record


def tool_message(name, value):
    return {"role": "tool", "name": name, "content": json.dumps(value, ensure_ascii=False)}


def execution(text, previous=None, current=None, *, cancelled=None, binding=None, turn_id="turn"):
    messages = [{"role": "user", "content": "帮我整理午饭"}]
    if previous:
        messages.extend([tool_message(*previous), {"role": "assistant", "content": "午饭已整理，份量对的话可以确认保存。"}])
    messages.append({"role": "user", "content": text})
    if current:
        messages.append(tool_message("get_food_log", current))
    host = SimpleNamespace(session_id="chat", turn_id=turn_id, cancelled=cancelled, binding=binding)
    return ToolExecutionContext({}, host, messages)


def read(tools, record):
    result = tools["get_food_log"].handler({"id": record["id"]}, execution("看看午饭"))
    assert result["status"] == "ok"
    return result


def request(record, text="确认保存这份饮食记录"):
    return {"id": record["id"], "revision": record["revision"], "confirmation_evidence": text}


@pytest.mark.parametrize("text", ["确认保存这份饮食记录", "就按这份记录吧", "保存吧", "确认", "确定",
                                  "确认保存这份，未知营养不要填0。", "不要弹窗，确认保存这份", "确认保存“午饭”这份记录"])
def test_explicit_current_confirmation_publishes_reviewed_record_once(tmp_path, text):
    journal, tools, record = setup(tmp_path)
    baseline = read(tools, record)
    context = execution(text, ("get_food_log", baseline), baseline)
    confirm = tools["confirm_food_log"]
    assert confirm.required_skill == "food-journal" and confirm.effect == "write"
    result = confirm.handler(request(record, text), context)
    assert result["status"] == "ok" and result["record"]["status"] == "saved"
    assert result["record"]["revision"] == 2 and result["record"]["id"] == record["id"]
    assert result["summary"]["today"]["record_count"] == 1
    assert result["summary"]["known_total"]["calories_kcal"] == 195.0
    assert "days" not in result["summary"]
    assert journal.food_logs.get(record["id"])["document"] == record["document"]
    # A repeated native tool delivery with the same baseline cannot double count.
    assert confirm.handler(request(record, text), context)["status"] == "ok"
    assert journal.food_logs.summary()["record_count"] == 1


@pytest.mark.parametrize("text,evidence", [
    ("不要确认保存这份", "确认保存这份"),
    ("先别保存这份", "保存这份"),
    ("确认保存这份吗？", "确认保存这份"),
    ("确认保存这份饮食记录并计入我的个人营养统计吗？", "确认保存这份饮食记录"),
    ("怎么确认保存这份？", "确认保存这份"),
    ("帮我修改米饭的份量", "确认保存这份"),
    ("用户原话是确认保存这份", "确认保存这份"),
    ('请把“确认保存这份”这句话念出来', "确认保存这份"),
    ("如果我说确认保存这份会怎么样", "确认保存这份"),
    ("好的", "好的"),
])
def test_negation_questions_quotes_and_invented_confirmation_do_not_publish(tmp_path, text, evidence):
    journal, tools, record = setup(tmp_path)
    baseline = read(tools, record)
    result = tools["confirm_food_log"].handler(request(record, evidence), execution(text, ("get_food_log", baseline), baseline))
    assert result["status"] == "confirmation_required"
    assert journal.food_logs.get(record["id"])["status"] == "draft"
    assert journal.food_logs.summary()["record_count"] == 0


def test_initial_request_cannot_approve_the_model_draft_before_user_sees_it(tmp_path):
    journal, tools, record = setup(tmp_path)
    baseline = read(tools, record)
    context = execution("帮我记录午饭，确认保存这份", current=baseline)
    result = tools["confirm_food_log"].handler(request(record, "确认保存这份"), context)
    assert result["status"] == "review_required"
    assert journal.food_logs.get(record["id"])["status"] == "draft"


def test_confirmation_must_read_the_same_latest_revision_in_this_turn(tmp_path):
    journal, tools, record = setup(tmp_path)
    baseline = read(tools, record)
    context = execution("确认保存这份", ("get_food_log", baseline))
    assert tools["confirm_food_log"].handler(request(record, "确认保存这份"), context)["status"] == "read_required"
    # Quoting a tool result in user content does not become an actual read.
    context.messages[-1]["content"] += json.dumps(baseline, ensure_ascii=False)
    assert tools["confirm_food_log"].handler(request(record, "确认保存这份"), context)["status"] in {"read_required", "confirmation_required"}
    assert journal.food_logs.get(record["id"])["status"] == "draft"


def test_create_and_revision_receipts_also_bind_the_prior_reviewed_version(tmp_path):
    _, tools, record = setup(tmp_path)
    baseline = read(tools, record)
    for name in ("create_food_log_draft", "revise_food_log_draft"):
        context = execution("确认保存这份", (name, {"status": "ok", "draft": record}), baseline)
        assert tools["confirm_food_log"].handler(request(record, "确认保存这份"), context)["status"] == "ok"


def test_new_revision_requires_new_user_review_and_cas_rejects_late_changes(tmp_path):
    journal, tools, record = setup(tmp_path)
    original_read = read(tools, record)
    changed = copy.deepcopy(record["document"])
    changed["title"] = "更正后的午饭"
    updated = journal.food_logs.update_draft(record["id"], 1, changed)
    current_read = read(tools, updated)
    result = tools["confirm_food_log"].handler(request(updated), execution("确认保存这份饮食记录", ("get_food_log", original_read), current_read))
    assert result["status"] == "review_required"
    # A value changed after the actual read still fails transaction revision CAS.
    stale = execution("确认保存这份饮食记录", ("get_food_log", original_read), original_read)
    assert tools["confirm_food_log"].handler(request(record), stale)["status"] == "invalid_food_log_confirmation"
    assert journal.food_logs.get(record["id"])["status"] == "draft"


def test_vague_confirmation_cannot_select_older_draft_or_wrong_explicit_id(tmp_path):
    journal, tools, record = setup(tmp_path)
    baseline = read(tools, record)
    another = journal.food_logs.create_draft(photo(), image_ids=["other"])
    other_read = read(tools, another)
    context = execution("确认保存这份饮食记录", ("get_food_log", baseline), baseline)
    context.messages.insert(2, tool_message("get_food_log", other_read))
    assert tools["confirm_food_log"].handler(request(record), context)["status"] == "review_required"
    text = f"确认保存这份饮食记录 {another['id']}"
    context = execution(text, ("get_food_log", baseline), baseline)
    assert tools["confirm_food_log"].handler(request(record, text), context)["status"] == "confirmation_required"
    # An explicit ID can choose the older reviewed item, not an unrelated latest draft.
    text = f"确认保存这份饮食记录 {record['id']}"
    context = execution(text, ("get_food_log", baseline), baseline)
    context.messages.insert(2, tool_message("get_food_log", other_read))
    assert tools["confirm_food_log"].handler(request(record, text), context)["status"] == "ok"


def test_cancelled_and_stale_turns_cannot_confirm(tmp_path):
    journal, tools, record = setup(tmp_path)
    baseline = read(tools, record)
    cancelled = threading.Event()
    cancelled.set()
    context = execution("确认保存这份饮食记录", ("get_food_log", baseline), baseline, cancelled=cancelled)
    assert tools["confirm_food_log"].handler(request(record), context)["status"] == "invalid_food_log_confirmation"
    sessions = SessionRepository(journal.food_logs.path)
    sessions.save({"id": "chat", "updated_at": NOW.isoformat(), "running": True, "active_turn": {"id": "new-turn"}, "messages": []})
    context = execution("确认保存这份饮食记录", ("get_food_log", baseline), baseline, binding=SimpleNamespace(repository=sessions))
    assert tools["confirm_food_log"].handler(request(record), context)["status"] == "invalid_food_log_confirmation"
    assert journal.food_logs.get(record["id"])["status"] == "draft"


def test_cancel_at_transaction_end_rolls_back_publication(tmp_path):
    journal, _, record = setup(tmp_path)
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("cancelled")

    with pytest.raises(ValueError):
        journal.food_logs.publish(record["id"], record["revision"], guard=guard)
    assert journal.food_logs.get(record["id"])["status"] == "draft"
    assert journal.food_logs.summary()["record_count"] == 0
