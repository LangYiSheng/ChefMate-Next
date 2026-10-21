"""Request measurements are content-free and reported usage never fills unknowns."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from chefmate_agent import AgentConfig, ChefMateAgent
from chefmate_agent.debug import UI_CONTEXT_MARKER
from chefmate_agent.telemetry import MEMORY_MARKER, request_measurement, usage_record, usage_summary
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool


def test_measurement_partitions_actual_request_text_and_omits_pixels_and_private_content():
    user = "今天吃什么" + UI_CONTEXT_MARKER + '{"revision":1}' + MEMORY_MARKER + '{"user_notes":"不吃香菜"}'
    messages = [HumanMessage(content=[{"type": "text", "text": user},
                                      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64/SECRET"}}]),
                AIMessage(content="正在核对", tool_calls=[{"id": "x", "name": "read", "args": {}}]),
                ToolMessage(content="指南正文", tool_call_id="g", name="load_skill"),
                ToolMessage(content='{"result":"done"}', tool_call_id="x", name="read")]
    result = request_measurement(2, "系统正文", messages, [], model="fixture")
    categories = {item["key"]: item for item in result["categories"]}
    assert categories["system"]["chars"] == 4
    assert categories["ui_state"]["chars"] == len(UI_CONTEXT_MARKER + '{"revision":1}')
    assert categories["memory"]["chars"] == len(MEMORY_MARKER + '{"user_notes":"不吃香菜"}')
    assert categories["guidance"]["chars"] == 4
    assert result["chars"] == sum(item["chars"] for item in result["categories"])
    assert result["images"] == 1 and result["image_tokens_included"] is False and result["estimated"]
    encoded = json.dumps(result, ensure_ascii=False)
    for private in ("SECRET", "不吃香菜", "今天吃什么", "系统正文", "指南正文"):
        assert private not in encoded


def test_partial_usage_preserves_actual_known_subtotal_and_missing_request():
    response = AIMessage(content="ok", usage_metadata={"input_tokens": 100, "output_tokens": 20,
                         "total_tokens": 120, "input_token_details": {"cache_read": 0}},
                         response_metadata={"model_name": "actual-model"})
    record = usage_record(response, 1, model="configured-model")
    assert record["model"] == "actual-model" and record["cached_tokens"] == 0
    summary = usage_summary([record], attempted_calls=2)
    assert summary["input_tokens"] is None and summary["total_tokens"] is None
    assert summary["known_totals"]["input_tokens"] == 100
    assert summary["coverage"]["input_tokens"] == {"reported": 1, "calls": 2}
    assert summary["cached_tokens"] is None and summary["reasoning_tokens"] is None


def test_actual_model_boundary_counts_schemas_each_round_and_persists_latest_on_failure():
    first = tool("get_search_options", {})
    first[-1].usage_metadata = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    model = ScriptedModel([first, RuntimeError("provider interrupted")])
    agent = ChefMateAgent(Service(), AgentConfig(model="fixture"), model=model, request_models=REQUESTS)
    events = []
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "查选项"}], events.append,
                                       memory_context={"user_notes": "只用电磁炉"}))
    assert result.status == "error" and len(model.histories) == 2
    measurements = [event["context_usage"] for event in events if event["type"] == "context_usage"]
    assert len(measurements) == 2 and result.context_usage == measurements[-1]
    for measurement in measurements:
        assert next(item for item in measurement["categories"] if item["key"] == "tool_schemas")["chars"] > 0
        assert next(item for item in measurement["categories"] if item["key"] == "memory")["chars"] > 0
    assert next(item for item in measurements[-1]["categories"] if item["key"] == "tools")["chars"] > 0
    assert result.usage["model_calls"] == 2 and result.usage["reported_calls"] == 1
    assert result.usage["input_tokens"] is None and result.usage["known_totals"]["input_tokens"] == 100


def test_success_reports_total_without_guessing_cache():
    model = ScriptedModel([[AIMessageChunk(content="完成", usage_metadata={"input_tokens": 10,
                           "output_tokens": 3, "total_tokens": 13})], answer("继续")])
    agent = ChefMateAgent(Service(), AgentConfig(model="fixture"), model=model, request_models=REQUESTS)
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "你好"}]))
    assert result.usage["total_tokens"] == 13 and result.usage["cached_tokens"] is None
    assert result.context_usage["round"] == 1


def test_missing_provider_total_is_exact_same_call_sum_with_explicit_provenance():
    response = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 20}, response_metadata={})
    record = usage_record(response, 1, model="fixture")
    assert record["total_tokens"] == 120 and record["total_tokens_source"] == "input_output_sum"
    summary = usage_summary([record])
    assert summary["total_tokens"] == 120 and summary["known_totals"]["total_tokens"] == 120
    assert summary["total_derived_calls"] == 1 and summary["total_reported_calls"] == 0
    assert summary["coverage"]["total_tokens"] == {"reported": 0, "derived": 1, "known": 1, "calls": 1}
    assert summary["cached_tokens"] is None and summary["reasoning_tokens"] is None
    missing_request = usage_summary([record], attempted_calls=2)
    assert missing_request["total_tokens"] is None
    assert missing_request["coverage"]["total_tokens"]["known"] == 1


def test_historical_call_derivation_never_combines_different_calls_coverage():
    calls = [{"input_tokens": 100, "output_tokens": None, "total_tokens": None},
             {"input_tokens": None, "output_tokens": 20, "total_tokens": None}]
    summary = usage_summary(calls)
    assert summary["known_totals"]["input_tokens"] == 100
    assert summary["known_totals"]["output_tokens"] == 20
    assert summary["known_totals"]["total_tokens"] is None and summary["total_tokens"] is None
    assert summary["total_derived_calls"] == 0
    # Normalization does not rewrite retained historical observations.
    assert "total_tokens_source" not in calls[0]
    complete = usage_summary([{"input_tokens": 100, "output_tokens": 20}])
    assert complete["total_tokens"] == 120 and complete["total_derived_calls"] == 1


@pytest.mark.parametrize("inputs,outputs", [(None, 10), (10, None), (True, 10), (10, False),
                                           (1.5, 10), (10, -1), (-1, 10)])
def test_missing_or_invalid_call_operands_cannot_be_derived(inputs, outputs):
    result = usage_summary([{"input_tokens": inputs, "output_tokens": outputs}])
    assert result["total_tokens"] is None and result["total_derived_calls"] == 0


def test_provider_totals_take_priority_and_zero_operands_are_complete():
    result = usage_summary([{"input_tokens": 100, "output_tokens": 20, "total_tokens": 125},
                            {"input_tokens": 0, "output_tokens": 0}])
    assert result["total_tokens"] == 125
    assert result["calls"][0]["total_tokens_source"] == "reported"
    assert result["calls"][1]["total_tokens_source"] == "input_output_sum"
    assert result["total_reported_calls"] == result["total_derived_calls"] == 1
