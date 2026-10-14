"""Offline protocol tests. Scripted responses are injected only by tests."""

import asyncio
import copy
import json

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.messages import message_chunk_to_message
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chefmate_agent import AgentConfig, ChefMateAgent

SNAPSHOT = {"data_version": "test-1", "imported_at": "2026-10-13T00:00:00+00:00"}
SHRIMP = {"ingredient_id": "shrimp", "canonical_name": "虾仁"}


class Lookup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    queries: list[str] = Field(min_length=1)


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ingredient_groups: list[dict] = Field(default_factory=list)
    max_total_time_min: float | None = None
    expected_snapshot: dict | None = None


class Detail(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    recipe_id: str
    expected_snapshot: dict | None = None


REQUESTS = {"find_ingredients": Lookup, "search_recipes": Search, "get_recipe": Detail}


class ScriptedModel(BaseChatModel):
    responses: list = Field(default_factory=list, exclude=True)
    histories: list = Field(default_factory=list, exclude=True)
    tools: list | None = Field(default=None, exclude=True)
    bind_history: list = Field(default_factory=list, exclude=True)
    closed: int = 0

    def __init__(self, responses):
        super().__init__(responses=list(responses))

    @property
    def _llm_type(self):
        return "explicit-scripted-test-model"

    def bind_tools(self, tools, **kwargs):
        self.tools = [convert_to_openai_tool(tool) for tool in tools]
        self.bind_history.append(copy.deepcopy(self.tools))
        return self.bind(tools=self.tools, **kwargs)

    def _generate(self, messages, **kwargs):
        raise NotImplementedError("Tests use the async standard runner")

    async def _agenerate(self, messages, **kwargs):
        aggregate = None
        async for generation in self._astream(messages, **kwargs):
            aggregate = generation if aggregate is None else aggregate + generation
        return ChatResult(generations=[ChatGeneration(message=message_chunk_to_message(aggregate.message))])

    async def _astream(self, messages, **kwargs):
        self.histories.append(copy.deepcopy(messages))
        response = self.responses.pop(0)
        try:
            if isinstance(response, Exception):
                raise response
            if callable(response):
                async for chunk in response():
                    yield ChatGenerationChunk(message=chunk)
            else:
                for chunk in response:
                    if chunk.tool_call_chunks and not chunk.additional_kwargs.get("tool_calls"):
                        chunk.additional_kwargs["tool_calls"] = [{
                            "index": part["index"], "id": part.get("id"), "type": "function",
                            "function": {"name": part.get("name"), "arguments": part.get("args", "")},
                        } for part in chunk.tool_call_chunks]
                    yield ChatGenerationChunk(message=chunk)
        finally:
            self.closed += 1


class Service:
    def __init__(self):
        self.calls = []
        self.search_status = "ok"

    def find_ingredients(self, request):
        self.calls.append(("find_ingredients", request))
        return {"status": "ok", "snapshot": SNAPSHOT, "items": [SHRIMP], "total": 1}

    def search_recipes(self, request):
        self.calls.append(("search_recipes", request))
        return {"status": self.search_status, "snapshot": SNAPSHOT, "total": 1,
                "items": [{"recipe_id": "white-shrimp", "name": "白灼虾", "total_time_min": 15,
                           "warnings": ["营养含估算"]}]}

    def get_recipe(self, request):
        self.calls.append(("get_recipe", request))
        return {"status": "ok", "snapshot": SNAPSHOT, "recipe": {
            "recipe_id": "white-shrimp", "name": "白灼虾", "steps": [{"text": "按数据库步骤煮虾"}],
            "warnings": ["营养含估算"],
        }}

    def get_search_options(self):
        self.calls.append(("get_search_options", {}))
        return {"status": "ok", "snapshot": SNAPSHOT, "difficulties": ["beginner"]}


def tool(name, args, call_id="call-1"):
    return [AIMessageChunk(content="", tool_call_chunks=[{
        "name": name, "args": json.dumps(args, ensure_ascii=False), "id": call_id, "index": 0,
    }])]


def answer(text="查到白灼虾，约15分钟；营养含估算。"):
    return [AIMessageChunk(content=text)]


def agent(responses, service=None, **config):
    model = ScriptedModel(responses)
    service = service or Service()
    return ChefMateAgent(service, AgentConfig(model="test", **config), model=model, request_models=REQUESTS), model, service


def test_lookup_search_events_and_json_history():
    search = {"ingredient_groups": [{"any_of": [SHRIMP]}], "expected_snapshot": SNAPSHOT}
    core, model, service = agent([tool("find_ingredients", {"queries": ["虾"]}),
                                  tool("search_recipes", search, "call-2"), answer()])
    events = []
    result = asyncio.run(core.run_turn([{"role": "user", "content": "我想吃虾"}], events.append))
    assert result.status == "completed"
    assert result.recipes[0]["recipe_id"] == "white-shrimp"
    assert result.snapshot == SNAPSHOT
    assert [name for name, _ in service.calls] == ["find_ingredients", "search_recipes"]
    assert [message["role"] for message in result.messages] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert all(isinstance(message["content"], str) for message in result.messages)
    json.dumps(result.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
    assert sum(event["type"] == "tool_end" for event in events) == 2
    assert any(event["type"] == "assistant_delta" for event in events)
    thinking = [event for event in events if event.get("type") == "status" and event.get("status") == "thinking"]
    assert [event["message"] for event in thinking] == ["正在理解需求", "正在分析查询结果", "正在分析查询结果"]
    assert model.closed == 3


def test_followup_preserves_real_order_and_conditions():
    search = {"ingredient_groups": [{"any_of": [SHRIMP]}], "expected_snapshot": SNAPSHOT}
    core, model, service = agent([tool("find_ingredients", {"queries": ["虾"]}),
                                  tool("search_recipes", search, "call-2"), answer(),
                                  tool("get_recipe", {"recipe_id": "white-shrimp", "expected_snapshot": SNAPSHOT}),
                                  answer("这是数据库记录的完整做法。")])
    first = asyncio.run(core.run_turn([{"role": "user", "content": "我想吃虾"}]))
    second = asyncio.run(core.run_turn(first.messages + [{"role": "user", "content": "第一道怎么做"}]))
    assert second.status == "completed"
    assert second.selected_recipe["recipe_id"] == "white-shrimp"
    assert any(isinstance(message, ToolMessage) for message in model.histories[-1])
    assert any(isinstance(message, HumanMessage) and message.content == "我想吃虾" for message in model.histories[-1])
    assert service.calls[-1][0] == "get_recipe"


@pytest.mark.parametrize("bad_args", ['{"queries":["虾"]', '{"queries":'])
def test_partial_tool_json_never_executes(bad_args):
    response = [AIMessageChunk(content="", tool_call_chunks=[{
        "name": "find_ingredients", "args": bad_args, "id": "partial", "index": 0,
    }])]
    core, _, service = agent([response])
    history = [{"role": "user", "content": "虾"}]
    result = asyncio.run(core.run_turn(history))
    assert result.status == "error"
    assert result.error.code == "incomplete_tool_batch"
    assert service.calls == []
    assert result.messages == history


def test_entire_unknown_tool_batch_rejected_before_any_execution():
    response = [AIMessageChunk(content="", tool_call_chunks=[
        {"name": "find_ingredients", "args": '{"queries":["虾"]}', "id": "good", "index": 0},
        {"name": "execute_sql", "args": '{}', "id": "bad", "index": 1},
    ])]
    core, _, service = agent([response])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.error.code == "invalid_tool_batch"
    assert service.calls == []


def test_schema_errors_are_repairable_and_unknown_fields_never_reach_service():
    core, _, service = agent([tool("find_ingredients", {"queries": ["虾"], "sql": "DROP TABLE recipes"}),
                              tool("find_ingredients", {"queries": ["虾"]}, "repair"), answer()])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.status == "completed"
    assert result.tool_calls[0].result["status"] == "invalid_arguments"
    assert len(service.calls) == 1


def test_unverified_ingredient_and_recipe_references_are_blocked():
    core, _, service = agent([tool("search_recipes", {
        "ingredient_groups": [{"any_of": [SHRIMP]}], "expected_snapshot": SNAPSHOT,
    }), tool("get_recipe", {"recipe_id": "invented", "expected_snapshot": SNAPSHOT}, "recipe"), answer("先查候选。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.status == "completed"
    assert all(record.result["status"] == "unverified_reference" for record in result.tool_calls)
    assert service.calls == []


def test_snapshot_is_mandatory_for_selected_candidate():
    core, _, service = agent([tool("find_ingredients", {"queries": ["虾"]}), tool("search_recipes", {
        "ingredient_groups": [{"any_of": [SHRIMP]}],
    }, "search"), answer("需要使用候选快照。")])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.tool_calls[-1].result["status"] == "snapshot_required"
    assert len(service.calls) == 1


def test_failed_search_not_published_as_recipes():
    service = Service()
    service.search_status = "snapshot_changed"
    core, _, _ = agent([tool("find_ingredients", {"queries": ["虾"]}), tool("search_recipes", {
        "ingredient_groups": [{"any_of": [SHRIMP]}], "expected_snapshot": SNAPSHOT,
    }, "search"), answer("数据库已更新，需要重新查询。")], service)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.status == "completed"
    assert result.recipes == []


def test_budget_checks_entire_batch_before_execution():
    response = [AIMessageChunk(content="", tool_call_chunks=[
        {"name": "find_ingredients", "args": '{"queries":["虾"]}', "id": "one", "index": 0},
        {"name": "get_search_options", "args": '{}', "id": "two", "index": 1},
    ])]
    core, _, service = agent([response], max_tool_calls=1)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.error.code == "tool_budget_exceeded"
    assert service.calls == []


def test_cancellation_closes_stream_and_does_not_commit_partial_history():
    async def exercise():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def wait_forever():
            started.set()
            yield AIMessageChunk(content="我来看看")
            await asyncio.sleep(20)

        core, model, _ = agent([wait_forever])
        history = [{"role": "user", "content": "虾"}]
        task = asyncio.create_task(core.run_turn(history, cancel_event=cancelled))
        await started.wait()
        cancelled.set()
        result = await task
        assert result.status == "cancelled"
        assert result.messages == history
        assert model.closed == 1

    asyncio.run(exercise())


def test_turn_timeout_keeps_history_transactional():
    async def wait_forever():
        yield AIMessageChunk(content="稍等")
        await asyncio.sleep(20)

    core, _, _ = agent([wait_forever], turn_timeout_s=0.03)
    history = [{"role": "user", "content": "虾"}]
    result = asyncio.run(core.run_turn(history))
    assert result.error.code == "turn_timeout"
    assert result.messages == history


def test_provider_errors_and_config_serialization_do_not_leak_credentials():
    core, _, _ = agent([RuntimeError("failed with secret-key at https://private.example")], api_key="secret-key")
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.error.code == "model_error"
    assert "secret-key" not in result.model_dump_json()
    assert "private.example" not in result.model_dump_json()
    assert "api_key" not in core.config.model_dump()


def test_history_rejects_orphan_tool_results_and_does_not_call_model():
    core, model, _ = agent([answer()])
    history = [{"role": "tool", "tool_call_id": "orphan", "content": "{}"},
               {"role": "user", "content": "虾"}]
    result = asyncio.run(core.run_turn(history))
    assert result.error.code == "invalid_history"
    assert model.histories == []


def test_context_pruning_keeps_complete_newest_turn():
    core, model, _ = agent([answer()], max_history_chars=4000)
    history = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答" * 2000},
               {"role": "user", "content": "虾"}]
    result = asyncio.run(core.run_turn(history))
    assert result.status == "completed"
    assert len(result.messages) == 2
    assert len(model.histories[0]) == 2  # owned system prompt + most recent user


def test_production_never_falls_back_to_mock():
    with pytest.raises(ValueError, match="real provider API key"):
        ChefMateAgent(Service(), AgentConfig(model="configured-model"), request_models=REQUESTS)


def test_reserved_model_kwargs_and_bad_urls_rejected():
    with pytest.raises(ValidationError):
        AgentConfig(model="test", model_kwargs={"api_key": "secret"})
    with pytest.raises(ValidationError):
        AgentConfig(model="test", base_url="https://user:secret@example.com/v1")


def test_real_tool_schemas_are_available():
    from chefmate_retrieval import IngredientLookupRequest, RecipeDetailRequest, RecipeSearchRequest

    core = ChefMateAgent(Service(), AgentConfig(model="test"), model=ScriptedModel([answer()]))
    assert core._requests["find_ingredients"] is IngredientLookupRequest
    assert core._requests["search_recipes"] is RecipeSearchRequest
    assert core._requests["get_recipe"] is RecipeDetailRequest
    assert {item["function"]["name"] for item in core._tool_definitions} == {
        "find_ingredients", "search_recipes", "get_recipe", "get_search_options", "list_skills", "load_skill",
    }


def test_streamed_tool_arguments_are_assembled_before_execution():
    response = [
        AIMessageChunk(content="", tool_call_chunks=[{
            "name": "find_ingredients", "args": '{"que', "id": "split", "index": 0,
        }]),
        AIMessageChunk(content="", tool_call_chunks=[{
            "name": None, "args": 'ries":["虾"]}', "id": None, "index": 0,
        }]),
    ]
    core, _, service = agent([response, answer()])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    assert result.status == "completed"
    assert service.calls == [("find_ingredients", {"queries": ["虾"]})]


def test_snapshot_replacement_invalidates_previously_verified_reference():
    old = {"data_version": "old", "imported_at": "2026-10-12T00:00:00+00:00"}
    lookup_args = json.dumps({"queries": ["虾"]})
    history = [
        {"role": "user", "content": "虾"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "lookup", "type": "function",
         "function": {"name": "find_ingredients", "arguments": lookup_args}}]},
        {"role": "tool", "tool_call_id": "lookup", "name": "find_ingredients",
         "content": json.dumps({"status": "ok", "snapshot": old, "items": [SHRIMP]})},
        {"role": "assistant", "content": "已记录候选"},
        {"role": "user", "content": "刷新数据后再查"},
    ]
    core, _, service = agent([tool("get_search_options", {}), tool("search_recipes", {
        "ingredient_groups": [{"any_of": [SHRIMP]}], "expected_snapshot": old,
    }, "stale-search"), answer("数据已更新，需重新查食材。")])
    result = asyncio.run(core.run_turn(history))
    assert result.status == "completed"
    assert result.tool_calls[-1].result["status"] == "unverified_reference"
    assert [name for name, _ in service.calls] == ["get_search_options"]


def test_failing_new_search_clears_previous_recipe_cards():
    service = Service()
    core, _, _ = agent([tool("search_recipes", {}), answer(),
                       tool("search_recipes", {}, "new-search"), answer("数据库已变化，需要重新查询。")], service)
    first = asyncio.run(core.run_turn([{"role": "user", "content": "给我几道菜"}]))
    assert first.recipes
    service.search_status = "snapshot_changed"
    second = asyncio.run(core.run_turn(first.messages + [{"role": "user", "content": "再查一次"}]))
    assert second.recipes == []
    assert second.selected_recipe is None


def test_tool_source_and_secret_fields_are_removed_without_losing_warnings():
    class LargeSourceService(Service):
        def find_ingredients(self, request):
            result = super().find_ingredients(request)
            result.update(source_json="x" * 40000, password="never-return-this",
                          warnings=["原文显示可加可不加"], raw_text="真实食材原文")
            return result

    core, _, _ = agent([tool("find_ingredients", {"queries": ["虾"]}), answer()], LargeSourceService())
    result = asyncio.run(core.run_turn([{"role": "user", "content": "虾"}]))
    output = result.tool_calls[0].result
    assert output["warnings"] == ["原文显示可加可不加"]
    assert output["raw_text"] == "真实食材原文"
    assert "source_json" not in output
    assert "password" not in output
    assert "never-return-this" not in result.model_dump_json()


@pytest.mark.parametrize("parts", [
    ["好的。<｜DSML｜tool_calls><｜DSML｜invoke name=\"find_ingredients\">"],
    ["好的。<｜D", "SML｜tool_calls>", "<｜DSML｜invoke name=\"find_ingredients\">"],
    ["说明文字" * 40, "<|DS", "ML|tool_calls>"],
])
def test_protocol_leaks_never_complete_or_reach_ui(parts):
    core, _, service = agent([[AIMessageChunk(content=part) for part in parts]])
    history = [{"role": "user", "content": "我想吃虾"}]
    events = []
    result = asyncio.run(core.run_turn(history, events.append))
    assert result.status == "error"
    assert result.error.code == "tool_protocol_leak"
    assert result.messages == history
    assert result.assistant_text == ""
    assert service.calls == []
    displayed = "".join(event.get("text", "") for event in events)
    assert "DSML" not in displayed
    assert "<｜D" not in displayed
    assert "<|DS" not in displayed


def test_real_provider_uses_explicit_nonstream_tools_by_default():
    core = ChefMateAgent(Service(), AgentConfig(model="test", api_key="not-a-real-key"), request_models=REQUESTS)
    assert core._base_model.disable_streaming == "tool_calling"
    assert core._base_model.streaming is False
    assert core._base_model.request_timeout == 120
    assert core._base_model.max_retries == 0
    enabled = ChefMateAgent(Service(), AgentConfig(model="test", api_key="not-a-real-key", stream_tool_calls=True),
                           request_models=REQUESTS)
    assert enabled._base_model.disable_streaming is False
    assert enabled._base_model.streaming is True


def test_explicit_model_timeout_and_retry_budget_reach_provider():
    core = ChefMateAgent(Service(), AgentConfig(model="test", api_key="not-a-real-key", model_timeout_s=90,
                                               max_model_retries=1, turn_timeout_s=240), request_models=REQUESTS)
    assert core._base_model.request_timeout == 90
    assert core._base_model.max_retries == 1
    assert core.config.turn_timeout_s == 240


def test_real_production_wire_schemas_are_dereferenced_but_still_strict():
    model = ScriptedModel([answer()])
    core = ChefMateAgent(Service(), AgentConfig(model="test"), model=model)
    serialized = json.dumps(core._tool_definitions)
    assert '"$ref"' not in serialized
    assert '"$defs"' not in serialized
    search = next(tool["function"]["parameters"] for tool in core._tool_definitions
                  if tool["function"]["name"] == "search_recipes")
    selection = search["properties"]["ingredient_groups"]["items"]["properties"]["any_of"]["items"]
    assert selection["additionalProperties"] is False
    assert "ingredient_id" in selection["required"]
    with pytest.raises(ValidationError):
        core._requests["search_recipes"].model_validate({"ingredient_groups": [{"any_of": [{
            "ingredient_id": "shrimp", "unexpected": "never accepted",
        }]}]})


@pytest.mark.parametrize("new_status,empty", [("ok", False), ("ok", True), ("invalid_selection", False)])
def test_new_search_always_invalidates_previously_selected_detail(new_status, empty):
    class MutableSearchService(Service):
        empty = False

        def search_recipes(self, request):
            result = super().search_recipes(request)
            if self.empty:
                result.update(items=[], total=0)
            return result

    service = MutableSearchService()
    core, _, _ = agent([
        tool("search_recipes", {}),
        tool("get_recipe", {"recipe_id": "white-shrimp", "expected_snapshot": SNAPSHOT}, "detail"),
        answer("这道菜的完整做法。"),
        tool("search_recipes", {}, "new-search"),
        answer("新的查询结果。"),
    ], service)
    first = asyncio.run(core.run_turn([{"role": "user", "content": "找菜并看看详情"}]))
    assert first.selected_recipe["recipe_id"] == "white-shrimp"
    service.empty = empty
    service.search_status = new_status
    second = asyncio.run(core.run_turn(first.messages + [{"role": "user", "content": "换一批条件再查询"}]))
    assert second.status == "completed"
    assert second.selected_recipe is None
    assert bool(second.recipes) == (new_status == "ok" and not empty)


def test_native_tool_finish_without_complete_calls_is_rejected():
    core, _, service = agent([[AIMessageChunk(content="正在查询", response_metadata={"finish_reason": "tool_calls"})]])
    history = [{"role": "user", "content": "我想吃虾"}]
    result = asyncio.run(core.run_turn(history))
    assert result.status == "error"
    assert result.error.code == "incomplete_tool_batch"
    assert result.messages == history
    assert result.assistant_text == ""
    assert service.calls == []
