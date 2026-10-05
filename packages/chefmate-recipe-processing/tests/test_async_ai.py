import asyncio
import json

import httpx
import pytest
from openai import OpenAI, AsyncOpenAI

from chefmate_contracts import default_vocabulary
from chefmate_recipe_processing import AIClient, AsyncAIClient, RequestContext, aextract_recipe


def completion(document, model="synthetic"):
    return httpx.Response(200, json={
        "id": "synthetic", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": document.model_dump_json()}}],
    })


def test_native_async_concurrency_isolates_configuration_logs_and_progress(recipe):
    async def scenario():
        bodies, timeouts = [], []
        both = asyncio.Event()

        async def handler(request):
            body = json.loads(request.content)
            bodies.append(body)
            timeouts.append(request.extensions["timeout"]["read"])
            if len(bodies) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 3)
            return completion(recipe, body["model"])

        async with AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler))) as sdk:
            ai = AsyncAIClient({"model": "unchanged", "api_mode": "deepseek_json", "retries": 0}, sdk)
            logs = {"a": [], "b": []}
            progress = {"a": [], "b": []}
            results = await asyncio.gather(*[
                aextract_recipe({"content": "土豆汤", "source_id": key}, ai=ai,
                                vocabulary=default_vocabulary(), context=RequestContext(
                                    settings={"model": key, "timeout": timeout, "max_output_tokens": tokens},
                                    on_call=lambda *args, key=key: logs[key].append(args),
                                    progress=progress[key].append))
                for key, timeout, tokens in [("a", 11, 101), ("b", 22, 202)]
            ])
            assert all(r.name == recipe.name for r in results)
            assert {(b["model"], b["max_tokens"]) for b in bodies} == {("a", 101), ("b", 202)}
            assert sorted(timeouts) == [11, 22]
            for key in logs:
                assert len(logs[key]) == 1 and logs[key][0][2] == key
                assert logs[key][0][4]["model"] == key and progress[key]
            assert ai.settings["model"] == "unchanged" and ai.settings["timeout"] == 90
            await ai.aclose()
            assert not sdk.is_closed()  # Injected SDK is caller-owned.
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "unauthorized"])
def test_async_retry_policy_and_nonblocking_sleep(recipe, failure):
    async def scenario():
        calls, delays = [], []

        async def handler(request):
            calls.append(request)
            if failure == "unauthorized":
                return httpx.Response(401, json={"error": {"message": "synthetic", "type": "invalid_request_error"}})
            if len(calls) == 1:
                if failure == "timeout":
                    raise httpx.ReadTimeout("synthetic", request=request)
                return completion(type("Invalid", (), {"model_dump_json": lambda self: "{}"})())
            return completion(recipe)

        async def sleeper(delay):
            delays.append(delay)
            await asyncio.sleep(0)

        async with AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler))) as sdk:
            ai = AsyncAIClient({"model": "test", "api_mode": "deepseek_json", "retries": 2}, sdk, sleeper=sleeper)
            invocation = aextract_recipe("土豆汤", ai=ai, vocabulary=default_vocabulary())
            if failure == "unauthorized":
                with pytest.raises(ValueError, match="401"):
                    await invocation
                assert len(calls) == 1 and not delays
            else:
                assert (await invocation).name == recipe.name
                assert len(calls) == 2 and delays == [1]
    asyncio.run(scenario())


def test_native_async_responses_parse(recipe):
    async def scenario():
        async def handler(request):
            assert request.url.path == "/v1/responses"
            body = json.loads(request.content)
            assert body["text"]["format"]["type"] == "json_schema"
            return httpx.Response(200, json={
                "id": "resp_test", "object": "response", "created_at": 0, "model": "test",
                "status": "completed", "error": None, "incomplete_details": None,
                "output": [{"id": "msg_test", "type": "message", "status": "completed", "role": "assistant",
                            "content": [{"type": "output_text", "text": recipe.model_dump_json(), "annotations": []}]}],
            })
        async with AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler))) as sdk:
            result = await aextract_recipe("土豆汤", ai=AsyncAIClient({"model": "test", "retries": 0}, sdk),
                                           vocabulary=default_vocabulary())
            assert result.model_dump() == recipe.model_dump()
    asyncio.run(scenario())


def test_owned_clients_are_closed_and_injected_sync_client_is_not():
    with AIClient({"openai_key": "test", "model": "test"}) as ai:
        owned = ai._get_client(ai.settings)
        assert not owned.is_closed()
    assert owned.is_closed()
    with OpenAI(api_key="test") as sdk:
        with AIClient({"model": "test"}, sdk):
            pass
        assert not sdk.is_closed()

    async def scenario():
        async with AsyncAIClient({"openai_key": "test", "model": "test"}) as ai:
            owned = ai._get_client(ai.settings)
            assert not owned.is_closed()
        assert owned.is_closed()
    asyncio.run(scenario())


def test_native_async_cancel_does_not_retry(recipe):
    async def scenario():
        started = asyncio.Event()
        calls = []
        async def handler(request):
            calls.append(request)
            started.set()
            await asyncio.Event().wait()
        async with AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler))) as sdk:
            ai = AsyncAIClient({"model": "test", "api_mode": "deepseek_json", "retries": 2}, sdk)
            task = asyncio.create_task(aextract_recipe("土豆汤", ai=ai, vocabulary=default_vocabulary()))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(calls) == 1
    asyncio.run(scenario())
