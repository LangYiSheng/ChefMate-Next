"""Domain confirmation tools must preserve the exact ask_user stopping boundary."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessageChunk

from chefmate_agent import InjectedToolSpec
from test_agent import answer, tool
from test_harness import EmptyInput, QUESTION, make_core


def test_domain_wait_stops_at_host_question_and_preserves_confirmation_receipt():
    receipt = {"confirmation_id": "host-owned-token"}
    spec = InjectedToolSpec("request_recipe_deletion", "确认删除菜谱", EmptyInput,
        lambda request, context: {"status": "ok", "pending_question": QUESTION, "receipt": receipt}, effect="wait")
    core, model = make_core([tool(spec.name, {}), answer("收到你的选择。")], [spec])
    first = asyncio.run(core.run_turn([{"role": "user", "content": "删除我的菜谱"}]))
    assert first.status == "completed" and first.awaiting_user
    assert first.pending_question == QUESTION and first.assistant_text == QUESTION["question"]
    assert len(model.histories) == 1
    assert json.loads(first.messages[-1]["content"])["receipt"] == receipt
    resumed = asyncio.run(core.run_turn([*first.messages, {"role": "user", "content": "我选择保留。"}]))
    assert resumed.status == "completed" and not resumed.awaiting_user and len(model.histories) == 2
    assert "host-owned-token" in str(model.histories[-1])


@pytest.mark.parametrize("response", [{"status": "revision_conflict"}, {"status": "ok"},
    {"status": "ok", "pending_question": {"question": "确认吗？"}},
    {"status": "ok", "pending_question": QUESTION, "receipt": {"oversized": "x" * 1001}}])
def test_domain_wait_never_pretends_an_unsaved_question_is_ready(response):
    spec = InjectedToolSpec("request_recipe_deletion", "确认删除菜谱", EmptyInput,
        lambda request, context: response, effect="wait")
    core, model = make_core([tool(spec.name, {})], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "删除菜谱"}]))
    assert result.status == "error" and result.error.code == "question_not_saved"
    assert not result.awaiting_user and result.pending_question is None and len(model.histories) == 1


def test_domain_wait_rejects_batch_before_any_operation_runs():
    executed = []
    spec = InjectedToolSpec("request_recipe_deletion", "确认删除菜谱", EmptyInput,
        lambda request, context: executed.append(True), effect="wait")
    response = [AIMessageChunk(content="", tool_call_chunks=[
        {"name": spec.name, "args": "{}", "id": "confirm", "index": 0},
        {"name": "get_search_options", "args": "{}", "id": "read", "index": 1}])]
    core, _ = make_core([response], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "删除菜谱"}]))
    assert result.error.code == "invalid_tool_batch" and not executed and not core.service.calls


@pytest.mark.parametrize("name,effect", [("request_recipe_deletion", "wait"), ("confirm_recipe_deletion", "write")])
def test_disabled_authoring_skill_blocks_both_confirmation_operations(name, effect):
    executed = []
    spec = InjectedToolSpec(name, "管理个人菜谱", EmptyInput,
        lambda request, context: executed.append(True), required_skill="recipe-authoring", effect=effect)
    core, model = make_core([tool(name, {}), answer("个人菜谱技能已关闭。")], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "删除菜谱"}]))
    assert not executed and result.tool_calls[0].result["status"] == "skill_disabled"
    assert all(name not in {item["function"]["name"] for item in definitions} for definitions in model.bind_history)
    assert not next(item for item in core.tool_catalog() if item["name"] == name)["available"]
    if effect == "wait":
        assert result.error.code == "question_not_saved" and not result.awaiting_user
    else:
        assert result.status == "completed"
