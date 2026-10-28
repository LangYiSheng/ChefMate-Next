"""Raw provider fixtures: no external clients, settings or model requests."""
import asyncio
import copy
import json
from types import SimpleNamespace as NS

import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from openai.types.responses import Response, ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText, ResponseUsage

from chefmate_agent.provider import ArgumentIntegrity, IntegrityChatOpenAI, ProviderIntegrityError

_UNREPORTED = object()


def item(index=0, raw='{"value":1}', *, call_id=None, name="echo"):
    return ResponseFunctionToolCall(type="function_call", id=f"fc_{index}", call_id=call_id or f"call_{index}",
                                    name=name, arguments=raw, status="completed")


def added(index=0, raw="", **kwargs):
    value = item(index, raw, **kwargs)
    value.status = "in_progress"
    return NS(type="response.output_item.added", output_index=index, item=value)


def delta(index, raw):
    return NS(type="response.function_call_arguments.delta", output_index=index, item_id=f"fc_{index}", delta=raw)


def done(index=0, raw='{"value":1}'):
    return NS(type="response.function_call_arguments.done", output_index=index, item_id=f"fc_{index}", arguments=raw)


def response(items=None, status="completed"):
    return Response.model_construct(id="resp_fixture", object="response", created_at=0, model="fixture",
                                    status=status, output=items if items is not None else [item()],
                                    error=None, usage=None, service_tier=None)


def completed(items=None):
    return NS(type="response.completed", response=response(items))


class Stream:
    def __init__(self, events):
        self.events = events
        self.closes = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closes += 1
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closes += 1
        return False

    def __iter__(self):
        yield from self.events

    async def __aiter__(self):
        for event in self.events:
            await asyncio.sleep(0)
            yield event


class Endpoint:
    def __init__(self, scripted, asynchronous=False, retries=_UNREPORTED):
        self.scripted = iter(scripted)
        self.asynchronous = asynchronous
        self.requests = []
        self.streams = []
        self.retries = iter(retries) if isinstance(retries, list) else None
        self.retry_count = retries
        self.with_raw_response = NS(create=self.raw_create, parse=self.raw_create)

    def _run(self, kwargs, raw):
        self.requests.append(copy.deepcopy(kwargs))
        value = next(self.scripted)
        if kwargs.get("stream"):
            value = value if isinstance(value, Stream) else Stream(value)
            self.streams.append(value)
        if not raw:
            return value
        result = NS(parse=lambda: value, headers={})
        retries = next(self.retries) if self.retries is not None else self.retry_count
        if retries is not _UNREPORTED:
            result.retries_taken = retries
        return result

    def _invoke(self, kwargs, raw=False):
        if not self.asynchronous:
            return self._run(kwargs, raw)

        async def result():
            return self._run(kwargs, raw)
        return result()

    def create(self, **kwargs):
        return self._invoke(kwargs)

    def raw_create(self, **kwargs):
        return self._invoke(kwargs, True)


def model(scripted, *, responses=True, streaming=True, retries=_UNREPORTED):
    sync, asynchronous = Endpoint(scripted, retries=retries), Endpoint(scripted, True, retries=retries)
    root = NS(responses=sync, chat=NS(completions=sync), with_raw_response=NS(responses=sync.with_raw_response))
    async_root = NS(responses=asynchronous, chat=NS(completions=asynchronous),
                    with_raw_response=NS(responses=asynchronous.with_raw_response))
    result = IntegrityChatOpenAI(model="fixture", api_key="fixture", base_url="http://127.0.0.1:1/v1",
                                 streaming=streaming, stream_usage=False, use_responses_api=responses,
                                 client=sync, async_client=asynchronous, root_client=root, root_async_client=async_root)
    return result, sync, asynchronous


def test_actual_langchain_adapter_recovers_done_only_and_preserves_tool_binding():
    events = [added(), done(), NS(type="response.output_item.done", output_index=0, item=item()), completed()]
    llm, _, endpoint = model([events])
    tool = {"type": "function", "function": {"name": "echo", "description": "fixture",
             "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}}}
    bound = llm.bind_tools([tool])
    reply = asyncio.run(bound.ainvoke([HumanMessage("fixture")]))
    assert reply.tool_calls == [{"name": "echo", "args": {"value": 1}, "id": "call_0", "type": "tool_call"}]
    assert not reply.invalid_tool_calls
    diagnostic = reply.response_metadata["tool_argument_integrity"]
    assert diagnostic["status"] == "verified" and diagnostic["calls"][0]["status"] == "recovered_end"
    assert "tools" in endpoint.requests[0] and endpoint.requests[0]["stream"] is True
    assert llm._integrity_active is False


def test_interleaved_multiple_calls_are_correlated_by_index_and_call_id():
    events = [added(0), added(1), delta(1, '{"value":'), delta(0, '{"value":'),
              delta(1, "2}"), delta(0, "1}"), done(1, '{"value":2}'), done(),
              completed([item(), item(1, '{"value":2}')])]
    llm, _, _ = model([events])
    reply = asyncio.run(llm.ainvoke("fixture"))
    assert [(call["id"], call["args"]) for call in reply.tool_calls] == [
        ("call_0", {"value": 1}), ("call_1", {"value": 2}),
    ]
    assert all(call["status"] == "verified" for call in reply.response_metadata["tool_argument_integrity"]["calls"])


@pytest.mark.parametrize("events,code", [
    ([added(), delta(0, '{"value":1')], "provider_response_incomplete"),
    ([added(), delta(0, '{"value":1}'), done()], "provider_response_incomplete"),
    ([added(), delta(0, '{"value":1'), completed([item(raw='{"value":1')])], "provider_tool_arguments_incomplete"),
    ([added(), delta(0, '{"value":2}'), done(), completed()], "provider_tool_arguments_conflict"),
    ([added(), done(raw='{"value":2}'), completed()], "provider_tool_arguments_conflict"),
    ([added(), added()], "provider_tool_identity_conflict"),
    ([added(), added(1, call_id="call_0")], "provider_tool_identity_conflict"),
    ([added(), completed([item(call_id="different")])], "provider_tool_identity_conflict"),
    ([added(), done(), NS(type="response.incomplete", response=response(status="incomplete"))], "provider_response_incomplete"),
])
def test_response_corruption_fails_before_a_final_tool_message(events, code):
    llm, _, _ = model([events])
    with pytest.raises(ProviderIntegrityError) as caught:
        asyncio.run(llm.ainvoke("fixture"))
    assert caught.value.code == code
    assert caught.value.diagnostics["status"] == code
    assert '{"value"' not in json.dumps(caught.value.diagnostics)


def test_completed_is_authoritative_when_individual_done_events_are_absent():
    llm, _, _ = model([[added(), delta(0, '{"value":'), completed()]])
    reply = asyncio.run(llm.ainvoke("fixture"))
    assert reply.tool_calls[0]["args"] == {"value": 1}
    assert reply.response_metadata["tool_argument_integrity"]["calls"][0]["status"] == "recovered_end"


def chat(delta=None, finish=None):
    return {"id": "chat_fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish, "logprobs": None}]}


def chat_call(index=0, raw="", *, call_id=None, name="echo"):
    return {"index": index, "id": call_id or f"call_{index}", "type": "function",
            "function": {"name": name, "arguments": raw}}


@pytest.mark.parametrize("raw", ['{"value":1', "", "[]", '{"value":1,"value":2}', '{"value":NaN}', '{"value":1e999}'])
def test_chat_stream_does_not_execute_partially_repaired_json(raw):
    events = [chat({"role": "assistant", "tool_calls": [chat_call(raw=raw)]}), chat(finish="tool_calls")]
    llm, _, _ = model([events], responses=False)
    with pytest.raises(ProviderIntegrityError):
        asyncio.run(llm.ainvoke("fixture"))


def test_chat_stream_complete_json_and_missing_terminal():
    events = [chat({"role": "assistant", "tool_calls": [chat_call(raw='{"value":1}')]})]
    llm, _, _ = model([events + [chat(finish="tool_calls")]], responses=False)
    reply = asyncio.run(llm.ainvoke("fixture"))
    assert reply.tool_calls[0]["args"] == {"value": 1}
    assert reply.response_metadata["tool_argument_integrity"]["status"] == "verified"
    llm, _, _ = model([events], responses=False)
    with pytest.raises(ProviderIntegrityError, match="integrity"):
        asyncio.run(llm.ainvoke("fixture"))


@pytest.mark.parametrize("responses", [True, False])
def test_nonstreaming_sync_and_async_also_validate_raw_json(responses):
    def make_response(raw):
        if responses:
            return response([item(raw=raw)])
        return {"id": "chat_fixture", "model": "fixture", "object": "chat.completion", "created": 0,
                "choices": [{"index": 0, "finish_reason": "tool_calls", "logprobs": None,
                             "message": {"role": "assistant", "content": None,
                                         "tool_calls": [{"id": "call_0", "type": "function",
                                                         "function": {"name": "echo", "arguments": raw}}]}}]}
    for async_mode in (False, True):
        llm, _, _ = model([make_response('{"value":1}')], responses=responses, streaming=False)
        reply = asyncio.run(llm.ainvoke("fixture")) if async_mode else llm.invoke("fixture")
        assert reply.tool_calls[0]["args"] == {"value": 1}
        assert reply.response_metadata["tool_argument_integrity"]["status"] == "verified"
        llm, _, _ = model([make_response('{"value":1')], responses=responses, streaming=False)
        with pytest.raises(ProviderIntegrityError):
            if async_mode:
                asyncio.run(llm.ainvoke("fixture"))
            else:
                llm.invoke("fixture")


def test_concurrent_invocations_of_one_model_keep_call_buffers_isolated():
    llm, _, _ = model([[added(), done(), completed()],
                       [added(raw='{"value":2}'), completed([item(raw='{"value":2}')])]])

    async def run():
        return await asyncio.gather(llm.ainvoke("one"), llm.ainvoke("two"))

    replies = asyncio.run(run())
    assert [r.tool_calls[0]["args"] for r in replies] == [{"value": 1}, {"value": 2}]
    summaries = [r.response_metadata["tool_argument_integrity"]["calls"][0] for r in replies]
    assert summaries[0]["raw_length"] == 0 and summaries[1]["raw_length"] > 0


def test_diagnostics_never_include_argument_values_images_or_reasoning():
    raw = json.dumps({"api_key": "secret-fixture", "image": "data:image/png;base64,fixture", "reasoning": "hidden-fixture"})
    guard = ArgumentIntegrity("responses")
    guard.responses_event(added())
    guard.responses_event(completed([item(raw=raw)]))
    guard.finish()
    dumped = json.dumps(guard.diagnostics())
    assert all(secret not in dumped for secret in ["secret-fixture", "data:image", "hidden-fixture"])
    assert guard.diagnostics()["calls"][0]["fields"] == ["api_key", "image", "reasoning"]


def test_sync_responses_stream_uses_the_same_integrity_adapter():
    llm, _, _ = model([[added(), done(), completed()]])
    reply = llm.invoke("fixture")
    assert reply.tool_calls[0]["args"] == {"value": 1}
    assert reply.response_metadata["tool_argument_integrity"]["status"] == "verified"


@pytest.mark.parametrize("responses", [True, False])
def test_stream_raw_header_path_preserves_usage_and_metadata(responses):
    if responses:
        last = completed()
        last.response.usage = ResponseUsage(input_tokens=10, output_tokens=4, total_tokens=14,
                                            input_tokens_details={"cached_tokens": 2, "cache_write_tokens": 0},
                                            output_tokens_details={"reasoning_tokens": 1})
        events = [added(), done(), last]
    else:
        usage = {"id": "chat_fixture", "model": "fixture", "choices": [],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
        events = [chat({"role": "assistant", "tool_calls": [chat_call(raw='{"value":1}')]}),
                  chat(finish="tool_calls"), usage]
    llm, _, _ = model([events], responses=responses)
    llm.include_response_headers = True
    reply = asyncio.run(llm.ainvoke("fixture"))
    assert reply.usage_metadata["input_tokens"] == 10 and reply.usage_metadata["output_tokens"] == 4
    assert reply.response_metadata["tool_argument_integrity"]["status"] == "verified"


def final_events(responses):
    if not responses:
        return [chat({"role": "assistant", "content": "ready"}), chat(finish="stop")]
    output = ResponseOutputMessage(type="message", id="msg_fixture", role="assistant", status="completed",
                                   content=[ResponseOutputText(type="output_text", text="ready", annotations=[])])
    return [NS(type="response.output_item.added", output_index=0, item=output),
            NS(type="response.output_text.delta", output_index=0, content_index=0, delta="ready"),
            completed([output])]


@pytest.mark.parametrize("responses", [True, False])
def test_standard_create_agent_runs_validated_tool_once_and_never_runs_corrupt_tool(responses):
    effects = []
    tool = StructuredTool.from_function(lambda value: effects.append(value) or "saved", name="echo",
                                        description="Save fixture value.")
    valid = ([added(), done(), completed()] if responses else
             [chat({"role": "assistant", "tool_calls": [chat_call(raw='{"value":1}')]}), chat(finish="tool_calls")])
    llm, _, _ = model([valid, final_events(responses)], responses=responses)
    agent = create_agent(llm, [tool])
    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("fixture")]}))
    assert effects == [1]
    assert [message.type for message in result["messages"]] == ["human", "ai", "tool", "ai"]
    assert result["messages"][-1].text == "ready"

    effects.clear()
    corrupt = ([added(), delta(0, '{"value":1'), completed([item(raw='{"value":1')])] if responses else
               [chat({"role": "assistant", "tool_calls": [chat_call(raw='{"value":1')]}), chat(finish="tool_calls")])
    llm, _, _ = model([corrupt], responses=responses)
    agent = create_agent(llm, [tool])
    with pytest.raises(ProviderIntegrityError):
        asyncio.run(agent.ainvoke({"messages": [HumanMessage("fixture")]}))
    assert effects == []


@pytest.mark.parametrize("status", ["in_progress", "incomplete"])
@pytest.mark.parametrize("terminal", ["output_item.done", "response.completed", "nonstream"])
def test_explicit_unfinished_function_status_cannot_hide_in_completed_response(status, terminal):
    unfinished = item().model_copy(update={"status": status})
    if terminal == "nonstream":
        llm, _, endpoint = model([response([unfinished])], streaming=False)
    else:
        end = (NS(type="response.output_item.done", output_index=0, item=unfinished)
               if terminal == "output_item.done" else completed([unfinished]))
        llm, _, endpoint = model([[added(), end, completed()]])
    with pytest.raises(ProviderIntegrityError) as caught:
        asyncio.run(llm.ainvoke("fixture"))
    assert caught.value.code == "provider_response_incomplete"
    if terminal != "nonstream":
        assert endpoint.streams[0].closes == 1


def test_unspecified_function_status_is_compatible_when_response_completed():
    llm, _, _ = model([[added(), completed([item().model_copy(update={"status": None})])]])
    assert asyncio.run(llm.ainvoke("fixture")).tool_calls[0]["args"] == {"value": 1}


def test_cancelling_async_stream_closes_the_actual_sdk_resource_once():
    async def exercise():
        started, blocked = asyncio.Event(), asyncio.Event()

        class PausedStream(Stream):
            async def __aiter__(self):
                yield added()
                started.set()
                await blocked.wait()
                yield completed()

        stream = PausedStream([])
        llm, _, _ = model([stream])
        task = asyncio.create_task(llm.ainvoke("fixture"))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closes == 1 and llm._integrity_active is False

    asyncio.run(exercise())


@pytest.mark.parametrize("text_first", [True, False])
def test_mixed_text_and_function_indices_preserve_both_payloads(text_first):
    text_index, call_index = (0, 1) if text_first else (1, 0)
    text = ResponseOutputMessage(type="message", id="msg_mixed", role="assistant", status="completed",
                                 content=[ResponseOutputText(type="output_text", text="ready", annotations=[])])
    outputs = [text, item(call_index)] if text_first else [item(call_index), text]
    llm, _, _ = model([[added(call_index),
        NS(type="response.output_item.added", output_index=text_index, item=text),
        NS(type="response.output_text.delta", output_index=text_index, content_index=0, delta="ready"),
        delta(call_index, '{"value":1}'), completed(outputs)]])
    reply = asyncio.run(llm.ainvoke("fixture"))
    assert reply.text == "ready" and reply.tool_calls[0]["args"] == {"value": 1}
    assert reply.tool_calls[0]["id"] == f"call_{call_index}"
    assert reply.response_metadata["tool_argument_integrity"]["calls"][0]["index"] == call_index


@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.parametrize("model_kwargs", [True, False])
def test_chat_structured_stream_is_rejected_before_requesting_beta_events(asynchronous, model_kwargs):
    llm, sync, async_endpoint = model([], responses=False)
    kwargs = {"response_format": {"type": "json_object"}}
    if model_kwargs:
        llm.model_kwargs = kwargs
        kwargs = {}
    with pytest.raises(ProviderIntegrityError) as caught:
        if asynchronous:
            asyncio.run(llm.ainvoke("fixture", **kwargs))
        else:
            llm.invoke("fixture", **kwargs)
    assert caught.value.code == "provider_stream_format_unsupported"
    assert not sync.requests and not async_endpoint.requests


@pytest.mark.parametrize("responses", [True, False])
@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("asynchronous", [True, False])
@pytest.mark.parametrize("retries", [0, 2])
def test_raw_sdk_retry_count_is_request_local_and_preserved_in_final_message(
        responses, streaming, asynchronous, retries):
    scripted = final_events(responses) if streaming else (
        response([]) if responses else {"id": "chat_fixture", "model": "fixture", "object": "chat.completion",
        "created": 0, "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                                   "message": {"role": "assistant", "content": "ready"}}]})
    llm, _, _ = model([scripted], responses=responses, streaming=streaming, retries=retries)
    # Exercise the adapter's already-supported raw streaming path. Do not change
    # default transport behavior merely to gain retry metadata.
    if streaming:
        llm.include_response_headers = True
    reply = asyncio.run(llm.ainvoke("fixture")) if asynchronous else llm.invoke("fixture")
    assert reply.response_metadata["provider_attempts"] == retries + 1
    assert reply.response_metadata["tool_argument_integrity"]["status"] == "verified"
    assert llm._integrity_active is False


@pytest.mark.parametrize("retries", [None, -1, True, 1.5, "2", _UNREPORTED])
def test_invalid_or_unavailable_raw_sdk_retry_count_stays_unknown(retries):
    llm, _, _ = model([response([])], streaming=False, retries=retries)
    reply = asyncio.run(llm.ainvoke("fixture"))
    assert reply.response_metadata["provider_attempts"] is None


@pytest.mark.parametrize("responses", [True, False])
def test_default_stream_does_not_invent_an_attempt_count_without_a_raw_sdk_response(responses):
    llm, _, _ = model([final_events(responses)], responses=responses, retries=0)
    reply = asyncio.run(llm.ainvoke("fixture"))
    assert reply.response_metadata["provider_attempts"] is None


def test_concurrent_raw_stream_attempt_counts_do_not_leak_across_requests():
    llm, _, _ = model([final_events(True), final_events(True)], retries=[0, 2])
    llm.include_response_headers = True

    async def exercise():
        return await asyncio.gather(llm.ainvoke("one"), llm.ainvoke("two"))

    replies = asyncio.run(exercise())
    assert [reply.response_metadata["provider_attempts"] for reply in replies] == [1, 3]


@pytest.mark.parametrize("responses", [True, False])
def test_direct_stream_final_metadata_carries_exactly_one_attempt_observation(responses):
    llm, _, _ = model([final_events(responses)], responses=responses, retries=1)
    llm.include_response_headers = True

    async def exercise():
        return [chunk async for chunk in llm.astream("fixture")]

    chunks = asyncio.run(exercise())
    counts = [chunk.response_metadata["provider_attempts"] for chunk in chunks
              if chunk.response_metadata.get("provider_attempts") is not None]
    assert counts == [2]
