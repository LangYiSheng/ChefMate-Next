"""Recommendations share the normal run and never execute user actions."""

import asyncio

import pytest

from chefmate_agent import AgentConfig, ChefMateAgent
from chefmate_agent.suggestions import PANTRY_REPLY, finalize_reply_suggestions
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool

REPLIES = [
    {"title": "换种做法", "text": "我想看看不同做法，可以按现有条件重新找找吗？"},
    {"title": "看看用量", "text": "请解释一下当前用量和份量的关系。"},
    {"title": "调整时间", "text": "我想调整做饭的时间限制，请先问我具体要求。"},
]


def make_agent(responses, enabled=True, count=3):
    model = ScriptedModel(responses)
    return ChefMateAgent(Service(), AgentConfig(model="test", reply_suggestions=enabled, reply_suggestion_count=count),
                         model=model, request_models=REQUESTS), model


@pytest.mark.parametrize("count", [1, 3, 6])
def test_selected_count_controls_tool_schema_model_prompt_and_fallback(count):
    from chefmate_agent.suggestions import make_reply_suggestions_tool

    replies = [{"title": f"建议{index}", "text": f"我想聊聊做饭的第{index}个话题。"} for index in range(count)]
    spec = make_reply_suggestions_tool(count)
    limits = spec.args_schema.model_json_schema()["properties"]["suggested_replies"]
    assert limits["minItems"] == limits["maxItems"] == count
    core, model = make_agent([tool("suggest_replies", {"suggested_replies": replies}), answer("继续聊。")], count=count)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "继续"}]))
    assert result.status == "completed"
    assert [item.model_dump() for item in result.suggested_replies] == replies
    assert f"提供{count}个" in model.histories[0][0].content
    fallback = finalize_reply_suggestions(None, count=count)
    assert len(fallback) == count and len({item["text"] for item in fallback}) == count
    context = {"stage": "prep", "components": [{"kind": "prep_checklist", "data": {"items": [{"status": "pending"}]}}]}
    prep = finalize_reply_suggestions(replies, context, count=count)
    assert len(prep) == count and prep[0] == PANTRY_REPLY
    assert finalize_reply_suggestions(prep, context, count=count) == prep


@pytest.mark.parametrize("count", [0, 7, True, "3", 1.5])
def test_invalid_reply_count_is_rejected(count):
    with pytest.raises(ValueError):
        AgentConfig(model="test", reply_suggestion_count=count)


def test_model_suggestions_are_typed_complete_and_use_only_the_normal_run():
    core, model = make_agent([tool("suggest_replies", {"suggested_replies": REPLIES}), answer("可以继续调整。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "接下来呢"}]))
    assert result.status == "completed"
    assert [item.model_dump() for item in result.suggested_replies] == REPLIES
    assert len(model.histories) == 2 and result.usage["model_calls"] == 2
    assert result.actions == []
    assert "最多尝试一次" in model.histories[0][0].content
    assert "直接完成主回答" in model.histories[0][0].content


def test_missing_suggestions_use_trusted_stage_without_another_model_call():
    core, model = make_agent([answer("你好，可以告诉我你的需求。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "你好"}], ui_context={"stage": "prep"}))
    assert result.status == "completed" and len(model.histories) == 1
    assert len(result.suggested_replies) == 3
    assert result.suggested_replies[0].title == "安排备菜顺序"
    assert all("已完成" not in item.text for item in result.suggested_replies)


def test_pantry_shortcut_survives_model_replies_and_requires_live_pending_preparation():
    context = {"stage": "prep", "components": [{"kind": "prep_checklist", "data": {"items": [
        {"id": "salt", "status": "pending"}, {"id": "egg", "status": "pending"},
    ]}}]}
    replies = finalize_reply_suggestions(REPLIES, context)
    assert len(replies) == 3 and replies[0] == PANTRY_REPLY
    assert "不把未说明的食材标为备齐" in replies[0]["text"]
    assert context["components"][0]["data"]["items"][0]["status"] == "pending"
    # The host finalizes the agent result again using the newest authoritative snapshot.
    assert finalize_reply_suggestions(replies, context) == replies
    for item in context["components"][0]["data"]["items"]:
        item["status"] = "ready"
    ready_replies = finalize_reply_suggestions(replies, context)
    assert len(ready_replies) == 3 and PANTRY_REPLY not in ready_replies
    context["components"][0]["data"]["items"][0]["status"] = "pending"
    context["stage"] = "cooking"
    assert PANTRY_REPLY not in finalize_reply_suggestions(replies, context)


def test_pantry_shortcut_deduplicates_model_suggestion_without_hiding_other_choices():
    context = {"stage": "prep", "components": [{"kind": "prep_checklist", "data": {"items": [
        {"id": "salt", "status": "pending"},
    ]}}]}
    replies = finalize_reply_suggestions([REPLIES[0], PANTRY_REPLY, REPLIES[1]], context)
    assert replies == [PANTRY_REPLY, REPLIES[0], REPLIES[1]]


@pytest.mark.parametrize("items,offer_check", [
    ([{"status": "pending", "availability": "missing"}, {"status": "ready", "availability": "available"}], False),
    ([{"status": "pending", "availability": "missing"}, {"status": "pending", "availability": "unknown"}], True),
    ([{"status": "pending"}], True),
    ([{"status": "ready"}, {"status": "skipped", "availability": "unknown"}], False),
])
def test_availability_checks_only_target_unknown_pending_stock(items, offer_check):
    context = {"stage": "prep", "components": [{"kind": "prep_checklist", "data": {"items": items}}]}
    replies = finalize_reply_suggestions(REPLIES, context)
    assert (PANTRY_REPLY in replies) is offer_check
    assert all("小料我都已有" not in reply["text"] for reply in replies)
    assert finalize_reply_suggestions(replies, context) == replies


def test_old_ownership_shortcut_is_removed_when_only_explicit_missing_stock_remains():
    context = {"stage": "prep", "components": [{"kind": "prep_checklist", "data": {"items": [
        {"status": "pending", "availability": "missing"},
    ]}}]}
    old = {"title": "已拥有所有小料，帮我勾选掉", "text": "当前清单里的调味小料我都已有，请帮我勾选为已备齐；主料保持原来的状态，不确定属于小料的项目先问我。"}
    replies = finalize_reply_suggestions([old, *REPLIES[:2]], context)
    assert old not in replies and PANTRY_REPLY not in replies and len(replies) == 3


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
