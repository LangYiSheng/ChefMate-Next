"""Real confirmed choices and requested suggestions can update metadata without magic wording."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.recipe_drafts import RecipeDraftRequest
from test_app import Agent, Service
from test_cookbook import draft
from test_recipe_review import PublicationFlow


def metadata_recipe(evidence, *, kind="suggested", question_id=None):
    return {**draft(), "servings": 1, "difficulty": "normal", "total_time_min": 30,
            "metadata_provenance": {field: {"kind": kind, "evidence": evidence, "question_id": question_id}
                                    for field in ("servings", "difficulty", "total_time_min")}}


def revise(flow, recipe):
    return flow.tools["revise_recipe_draft"].handler({"recipe_id": flow.record["recipe_id"],
        "revision": flow.record["revision"], "recipe": recipe}, flow.current)


def answer_metadata(flow, label="按建议：普通、1人份、30分钟"):
    question = flow.current.host_context.execute("ask_user", {"question": "这三项需要怎样设置？",
        "options": [{"id": "suggested", "label": label}], "multiple": False, "allow_custom": True}, flow.current)
    assert question["status"] == "ok"
    identity = question["pending_question"]["id"]
    flow.repository.mutate(flow.session_id, lambda state: state.update(running=False, active_turn=None))
    service = flow.binding.service(flow.session_id)
    answered = service.apply_action({"type": "answer_question", "action_id": str(uuid4()),
        "base_revision": service.model_snapshot()["revision"], "payload": {"question_id": identity, "option_ids": ["suggested"]}})
    assert answered["status"] == "ok"
    flow.current = flow.start("metadata-answer-turn")
    flow.current = replace(flow.current, messages=[{"role": "user", "content": f"我的回答是：{label}。请按这个答案继续。"}])
    flow.read()
    return identity


def test_requested_metadata_suggestions_save_once_and_remain_explicitly_suggested(tmp_path):
    flow = PublicationFlow(tmp_path)
    request = "难度份数总耗时也要更新一下"
    flow.current = replace(flow.current, messages=[{"role": "user", "content": request}])
    flow.read()
    recipe = metadata_recipe(request)
    result = revise(flow, recipe)
    assert result["status"] == "ok"
    record = result["draft"]
    assert record["revision"] == 2 and record["status"] == "draft"
    assert record["source_text"] == draft()["source_text"]
    assert record["document"]["servings"] == 1 and record["document"]["difficulty"] == "normal"
    assert record["document"]["total_time_min"] == 30
    assert any("非原文或实测" in warning and "总耗时30分钟" in warning for warning in record["document"]["warnings"])
    assert record["metadata_provenance"]["servings"]["kind"] == "suggested"
    assert record["nutrition"]["estimated_servings"] is True
    assert flow.cookbook.normalized(record)["estimated_servings"] is True
    assert flow.repository.get(flow.session_id)["workspace"].get("pending_question") is None
    assert record["document"]["ingredients"][1]["grams"] is None
    # A later unrelated edit can preserve the exact accepted values and provenance.
    flow.record = record
    flow.current = replace(flow.current, messages=[{"role": "user", "content": "菜名改成家常炒蛋"}])
    flow.read()
    assert revise(flow, {**recipe, "name": "家常炒蛋"})["status"] == "ok"


@pytest.mark.parametrize("label", ["按建议：普通、1人份、30分钟", "确认：难度普通，总耗时30分钟，1人份"])
def test_host_saved_grouped_answer_updates_metadata_without_rewording_or_reconfirmation(tmp_path, label):
    flow = PublicationFlow(tmp_path)
    identity = answer_metadata(flow, label)
    result = revise(flow, metadata_recipe(label, kind="user_confirmed", question_id=identity))
    assert result["status"] == "ok"
    record = result["draft"]
    assert record["document"]["difficulty"] == "normal" and record["document"]["total_time_min"] == 30
    assert record["source_text"] == draft()["source_text"]
    assert "用户确认" in record["time_evidence"]
    assert flow.repository.get(flow.session_id)["workspace"]["pending_question"] is None
    # Metadata answers still never publish; publication keeps its own exact revision-bound confirmation.
    flow.record = record
    flow.read()
    flow.request()
    assert flow.confirm()["status"] == "confirmation_required"
    assert flow.cookbook.get(record["recipe_id"])["status"] == "draft"


@pytest.mark.parametrize("fault", ["question", "answer", "current_user"])
def test_forged_or_stale_metadata_answers_do_not_change_the_draft(tmp_path, fault):
    flow = PublicationFlow(tmp_path)
    identity = answer_metadata(flow)
    recipe = metadata_recipe("按建议：普通、1人份、30分钟", kind="user_confirmed", question_id=identity)
    if fault == "question":
        for basis in recipe["metadata_provenance"].values():
            basis["question_id"] = "another-question"
    elif fault == "answer":
        flow.repository.mutate(flow.session_id, lambda state: state["workspace"]["last_answer"].update(labels=["继续修改"]))
    else:
        flow.current = replace(flow.current, messages=[*flow.current.messages, {"role": "user", "content": "先给这道菜改个名字"}])
    assert revise(flow, recipe)["status"] == "invalid_recipe_provenance"
    assert flow.cookbook.get(flow.record["recipe_id"])["revision"] == 1


def test_confirmed_metadata_requires_matching_values_and_a_step_timer_is_not_a_recipe_total(tmp_path):
    recipe = metadata_recipe("确认：难度普通，总耗时30分钟，1人份", kind="user_confirmed")
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate({**recipe, "difficulty": "advanced"})
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate({**metadata_recipe("请补充建议"), "time_evidence": "原文没有出现的时间依据"})
    flow = PublicationFlow(tmp_path)
    text = "这一步焖10分钟"
    flow.current = replace(flow.current, messages=[{"role": "user", "content": text}])
    flow.read()
    values = {**draft(), "total_time_min": 10, "metadata_provenance": {
        "total_time_min": {"kind": "user_confirmed", "evidence": text}}}
    assert revise(flow, values)["status"] == "invalid_recipe_provenance"
    grouped_text = "难度普通，1人份，第二步焖10分钟"
    flow.current = replace(flow.current, messages=[*flow.current.messages, {"role": "user", "content": grouped_text}])
    grouped = metadata_recipe(grouped_text, kind="user_confirmed")
    grouped["total_time_min"] = 10
    assert revise(flow, grouped)["status"] == "invalid_recipe_provenance"
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate({**draft(), "total_time_min": 10, "time_evidence": text,
                                          "source_text": draft()["source_text"] + text})


def test_repeated_ingredient_name_does_not_discard_explicit_amount_or_invent_weight(tmp_path):
    flow = PublicationFlow(tmp_path)
    values = draft()
    values["ingredients"][0]["amount"] = "番茄 200 g"
    values["ingredients"][1]["amount"] = "鸡蛋 2 个"
    record = flow.cookbook.create_draft(values)
    tomato, egg = record["document"]["ingredients"]
    assert (tomato["raw_amount"], tomato["raw_unit"]) == ("200", "g")
    assert (egg["raw_amount"], egg["raw_unit"], egg["grams"]) == ("2", "个", None)


def test_raw_http_cannot_invent_conversation_provenance_or_relabel_an_old_value(tmp_path):
    flow = PublicationFlow(tmp_path)
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=flow.repository.path), service=Service(), agent=Agent())
    uri = "/api/cookbook/" + flow.record["recipe_id"]
    with TestClient(app) as client:
        forged = metadata_recipe("普通、1人份、30分钟", kind="user_confirmed", question_id="not-a-real-question")
        assert client.put(uri, json={"revision": 1, "recipe": forged}).status_code == 409
        assert client.get(uri).json()["revision"] == 1
    # Repository callers without a real tool context cannot bypass the HTTP rule either.
    with pytest.raises(ValueError, match="当前对话"):
        flow.cookbook.create_draft(forged)


def test_manual_edit_can_retain_authentic_unchanged_provenance_but_cannot_reuse_it_for_new_numbers(tmp_path):
    flow = PublicationFlow(tmp_path)
    request = "请给难度人数总时间一些合理建议"
    flow.current = replace(flow.current, messages=[{"role": "user", "content": request}])
    flow.read()
    values = metadata_recipe(request)
    flow.record = revise(flow, values)["draft"]
    kept = flow.cookbook.update_draft(flow.record["recipe_id"], {**values, "name": "家常番茄炒蛋"}, 2)
    assert kept["revision"] == 3 and kept["metadata_provenance"] == flow.record["metadata_provenance"]
    with pytest.raises(ValueError, match="当前对话"):
        flow.cookbook.update_draft(flow.record["recipe_id"], {**values, "total_time_min": 60}, 3)
    assert flow.cookbook.get(flow.record["recipe_id"])["document"]["total_time_min"] == 30


def test_answer_is_checked_again_inside_storage_transaction_before_committing(tmp_path, monkeypatch):
    flow = PublicationFlow(tmp_path)
    identity = answer_metadata(flow)
    values = metadata_recipe("按建议：普通、1人份、30分钟", kind="user_confirmed", question_id=identity)
    update = flow.cookbook.update_draft

    def change_answer_before_storage(*args, **kwargs):
        flow.repository.mutate(flow.session_id, lambda state: state["workspace"]["last_answer"].update(
            question_id="new-question", labels=["保留原值"]))
        return update(*args, **kwargs)

    monkeypatch.setattr(flow.cookbook, "update_draft", change_answer_before_storage)
    result = revise(flow, values)
    assert result["status"] != "ok"
    assert flow.cookbook.get(flow.record["recipe_id"])["revision"] == 1
