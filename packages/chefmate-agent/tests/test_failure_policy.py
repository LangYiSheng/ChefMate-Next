"""Offline execution tests for bounded recovery and closed native tool history."""

import asyncio
import json

from langchain_core.messages import AIMessageChunk, ToolMessage
from pydantic import BaseModel, ConfigDict

from chefmate_agent import AgentConfig, ChefMateAgent, InjectedToolSpec
from chefmate_agent.failure_policy import failure_fingerprint, safe_errors
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool


class RequiredInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def make_agent(responses, specs=(), **config):
    model = ScriptedModel(responses)
    agent = ChefMateAgent(Service(), AgentConfig(model="test", **config), model=model,
                          request_models=REQUESTS, extra_tools=specs)
    return agent, model


def batch(*calls):
    return [AIMessageChunk(content="", tool_call_chunks=[
        {"name": name, "args": json.dumps(arguments), "id": call_id, "index": index}
        for index, (name, arguments, call_id) in enumerate(calls)
    ])]


def assert_closed(messages):
    pending = set()
    for message in messages:
        if message["role"] == "assistant":
            assert not pending
            pending.update(call["id"] for call in message.get("tool_calls", []))
        elif message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
    assert not pending


def test_three_identical_schema_failures_finalize_with_real_candidates_and_closed_history():
    effects = []
    spec = InjectedToolSpec("show_test", "显示", RequiredInput, lambda values, context: effects.append(values))
    core, model = make_agent([tool("search_recipes", {}, "search"), tool("show_test", {}, "bad-1"),
                            tool("show_test", {}, "bad-2"), tool("show_test", {}, "bad-3"),
                            answer("已查到白灼虾；展示操作未成功，先提供查询结果。")], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "找菜并显示"}]))
    assert result.status == "completed" and len(model.histories) == 5 and not effects
    assert result.recipes[0]["recipe_id"] == "white-shrimp"
    failed = result.tool_calls[1:]
    assert [record.result["status"] for record in failed] == ["invalid_arguments"] * 3
    assert [record.result["recovery"]["action"] for record in failed] == ["repair", "repair_or_answer", "finalize"]
    assert failed[0].result["errors"][0]["loc"] == ["value"]
    assert failed[0].result["errors"][0]["type"] == "missing"
    assert failed[0].result["errors"][0]["hint"]
    assert result.debug_trace[-1]["tools"] == [] and "本次请求不提供任何工具" in result.debug_trace[-1]["system"]
    assert isinstance(model.histories[-1][-1], ToolMessage)
    assert_closed(result.messages)


def test_parameter_correction_continues_normally_after_second_failure():
    effects = []

    def save(values, context):
        effects.append(values)
        return {"status": "ok"}

    spec = InjectedToolSpec("save_test", "保存", RequiredInput, save)
    core, model = make_agent([tool("save_test", {}, "bad-1"), tool("save_test", {}, "bad-2"),
                            tool("save_test", {"value": "修正"}, "fixed"), answer("保存成功。")], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "保存"}]))
    assert result.status == "completed" and effects == [{"value": "修正"}]
    assert result.tool_calls[-1].result["status"] == "ok" and len(model.histories) == 4
    assert result.debug_trace[-1]["tools"] and "本次请求不提供任何工具" not in result.debug_trace[-1]["system"]
    assert_closed(result.messages)


def test_recovery_exhaustion_uses_one_finalization_request_and_never_executes_its_tool():
    effects = []
    spec = InjectedToolSpec("save_test", "保存", RequiredInput,
                            lambda values, context: effects.append(values) or {"status": "ok"})
    core, model = make_agent([tool("save_test", {}, "bad-1"), tool("save_test", {}, "bad-2"),
                            tool("save_test", {}, "bad-3"), tool("save_test", {"value": "too late"}, "ignored")], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "保存"}]))
    assert result.status == "error" and result.error.code == "no_progress"
    assert len(model.histories) == len(result.debug_trace) == 4 and len(result.tool_calls) == 3 and not effects
    assert result.debug_trace[-1]["tools"] == []
    assert [message.tool_call_id for message in model.histories[-1] if isinstance(message, ToolMessage)] == [
        "bad-1", "bad-2", "bad-3"]


def test_failed_suggestions_fall_back_without_retrying_or_blocking_the_answer():
    core, model = make_agent([tool("search_recipes", {}, "search"), tool("suggest_replies", {}, "suggest"),
                            answer("查到白灼虾。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "找菜"}]))
    assert result.status == "completed" and result.recipes and len(result.suggested_replies) == 3
    assert result.tool_calls[-1].result["status"] == "invalid_arguments"
    assert result.tool_calls[-1].result["recovery"]["action"] == "answer"
    assert "suggest_replies" not in {item["name"] for item in result.debug_trace[-1]["tools"]}
    assert len(model.histories) == 3
    assert_closed(result.messages)


def test_duplicate_auxiliary_calls_are_not_executed_and_cannot_loop_until_step_budget():
    suggestions = {"suggested_replies": [{"title": str(index), "text": f"下一步{index}"} for index in range(3)]}
    core, model = make_agent([batch(("suggest_replies", suggestions, "first"),
                                  ("suggest_replies", suggestions, "duplicate-1"),
                                  ("suggest_replies", suggestions, "duplicate-2")), answer("完成。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "继续"}]))
    assert result.status == "completed" and len(model.histories) == 2
    assert [record.result["status"] for record in result.tool_calls].count("ok") == 1
    assert [record.result["status"] for record in result.tool_calls].count("auxiliary_skipped") == 2
    assert [item.model_dump() for item in result.suggested_replies] == suggestions["suggested_replies"]
    assert result.debug_trace[-1]["tools"] == []
    assert_closed(result.messages)


def test_dependent_batch_is_rejected_as_a_whole_then_can_be_retried_serially():
    effects = []

    def save(values, context):
        effects.append(values)
        return {"status": "ok"}

    spec = InjectedToolSpec("save_test", "保存", RequiredInput, save, effect="write")
    core, model = make_agent([batch(("save_test", {"value": "one"}, "write"),
                                  ("search_recipes", {}, "read")),
                            tool("save_test", {"value": "one"}, "serial-write"),
                            tool("search_recipes", {}, "serial-read"), answer("已保存并查到白灼虾。")], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "保存后查菜"}]))
    assert result.status == "completed" and len(model.histories) == 4
    assert [record.result["status"] for record in result.tool_calls[:2]] == ["dependent_tool_batch"] * 2
    assert effects == [{"value": "one"}] and len(core.service.calls) == 1
    assert result.tool_calls[2].result["status"] == result.tool_calls[3].result["status"] == "ok"
    assert_closed(result.messages)


def test_failure_fingerprints_normalize_order_but_separate_arguments_and_error_codes():
    arguments = {"values": {"servings": 1, "items": ["a", "b"]}, "revision": 2}
    reordered = {"revision": 2.0, "values": {"items": ["a", "b"], "servings": 1.0}}
    assert failure_fingerprint("save", arguments, {"status": "invalid_arguments"}) == failure_fingerprint(
        "save", reordered, {"status": "invalid_arguments"})
    assert failure_fingerprint("save", {}, {"status": "error", "code": "stale"}) != failure_fingerprint(
        "save", {}, {"status": "error", "code": "invalid"})
    errors = safe_errors([{"loc": ["values", "servings"], "type": "float_type", "input": "sk-secret",
                           "msg": "private exception", "ctx": {"error": ValueError("secret")}, "hint": "untrusted"}])
    assert set(errors[0]) == {"loc", "type", "hint"}
    assert "secret" not in json.dumps(errors) and "untrusted" not in json.dumps(errors)


def test_registered_recipe_validation_codes_keep_only_static_repair_hints():
    errors = safe_errors([{"loc": ["ingredients", 2, "amount_evidence"],
                           "type": "recipe_evidence_not_quoted", "input": "private source",
                           "msg": "private exception", "ctx": {"error": ValueError("private")},
                           "hint": "untrusted replacement"}])
    assert errors[0]["loc"] == ["ingredients", 2, "amount_evidence"]
    assert errors[0]["type"] == "recipe_evidence_not_quoted"
    assert "Markdown" in errors[0]["hint"]
    assert "private" not in json.dumps(errors) and "untrusted" not in json.dumps(errors)
    assert safe_errors(errors) == errors
