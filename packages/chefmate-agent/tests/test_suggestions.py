"""Recommendations share the normal run and never execute user actions."""

import asyncio

import pytest

from chefmate_agent import AgentConfig, ChefMateAgent
from chefmate_agent.suggestions import finalize_reply_suggestions
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool

REPLIES = [
    {"title": "换种做法", "text": "我想看看不同做法，可以按现有条件重新找找吗？"},
    {"title": "看看用量", "text": "请解释一下当前用量和份量的关系。"},
    {"title": "调整时间", "text": "我想调整做饭的时间限制，请先问我具体要求。"},
]


def make_agent(responses, enabled=True):
    model = ScriptedModel(responses)
    return ChefMateAgent(Service(), AgentConfig(model="test", reply_suggestions=enabled),
                         model=model, request_models=REQUESTS), model


def test_model_suggestions_are_typed_complete_and_use_only_the_normal_run():
    core, model = make_agent([tool("suggest_replies", {"suggested_replies": REPLIES}), answer("可以继续调整。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "接下来呢"}]))
    assert result.status == "completed"
    assert [item.model_dump() for item in result.suggested_replies] == REPLIES
    assert len(model.histories) == 2 and result.usage["model_calls"] == 2
    assert result.actions == []
    assert "本轮提供后续回复建议" in model.histories[0][0].content


def test_missing_suggestions_use_trusted_stage_without_another_model_call():
    core, model = make_agent([answer("你好，可以告诉我你的需求。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "你好"}], ui_context={"stage": "prep"}))
    assert result.status == "completed" and len(model.histories) == 1
    assert len(result.suggested_replies) == 3
    assert result.suggested_replies[0].title == "安排备菜顺序"
    assert all("已完成" not in item.text for item in result.suggested_replies)


@pytest.mark.parametrize("bad", [
    REPLIES[:2],
    [*REPLIES, {"title": "多一条", "text": "这条超出数量。"}],
    [{"title": "a" * 21, "text": "超长标题"}, *REPLIES[1:]],
    [{"title": "不安全", "text": "<img src=x onerror=alert(1)>"}, *REPLIES[1:]],
    [{"title": "太长了", "text": "x" * 501}, *REPLIES[1:]],
    [REPLIES[0], REPLIES[0], REPLIES[2]],
])
def test_invalid_tool_batches_are_rejected_and_fall_back_to_safe_drafts(bad):
    core, _ = make_agent([tool("suggest_replies", {"suggested_replies": bad}), answer("请告诉我你的想法。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "继续"}]))
    assert result.status == "completed"
    assert result.tool_calls[0].result["status"] == "invalid_arguments"
    assert [item.model_dump() for item in result.suggested_replies] == finalize_reply_suggestions(None)


def test_disabled_recommendations_allow_existing_history_but_do_not_generate_or_return_drafts():
    enabled, _ = make_agent([tool("suggest_replies", {"suggested_replies": REPLIES}), answer("好的。")])
    first = asyncio.run(enabled.run_turn([{"role": "user", "content": "你好"}]))
    disabled, model = make_agent([answer("继续聊。")], enabled=False)
    assert "suggest_replies" not in {item["function"]["name"] for item in disabled._tool_definitions}
    result = asyncio.run(disabled.run_turn([*first.messages, {"role": "user", "content": "继续"}]))
    assert result.status == "completed" and result.suggested_replies == []
    assert "本轮提供后续回复建议" not in model.histories[0][0].content
    assert len(model.histories) == 1


def test_error_after_saved_suggestions_does_not_expose_them():
    core, _ = make_agent([tool("suggest_replies", {"suggested_replies": REPLIES}), RuntimeError("provider failed")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "继续"}]))
    assert result.status == "error" and result.suggested_replies == []
