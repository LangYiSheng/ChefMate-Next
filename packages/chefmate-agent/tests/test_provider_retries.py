"""Real SDK retry accounting through an in-memory HTTP transport, no network."""
import asyncio
import json

import httpx
import pytest

from chefmate_agent.provider import IntegrityChatOpenAI
from chefmate_agent.telemetry import usage_record


@pytest.mark.parametrize("responses", [True, False])
@pytest.mark.parametrize("asynchronous", [True, False])
def test_sdk_500_then_success_reports_two_attempts_without_retaining_headers(responses, asynchronous):
    requests = []

    def transport(request):
        requests.append(request.url.path)
        if len(requests) == 1:
            return httpx.Response(500, headers={"retry-after-ms": "1"},
                                  json={"error": {"message": "fixture transient error", "type": "server_error"}})
        if responses:
            body = {"id": "resp_retry_fixture", "object": "response", "created_at": 0, "model": "fixture",
                    "status": "completed", "error": None, "output": [{"type": "message", "id": "msg_fixture",
                    "role": "assistant", "status": "completed", "content": [{"type": "output_text",
                    "text": "ready", "annotations": []}]}], "usage": {"input_tokens": 6, "output_tokens": 1,
                    "total_tokens": 7, "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0}}, "retries_taken": 99}
        else:
            body = {"id": "chat_retry_fixture", "object": "chat.completion", "created": 0, "model": "fixture",
                    "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                    "message": {"role": "assistant", "content": "ready"}}],
                    "usage": {"prompt_tokens": 6, "completion_tokens": 1, "total_tokens": 7}, "retries_taken": 99}
        return httpx.Response(200, headers={"x-fixture-secret": "never-retain-fixture-header"}, json=body)

    async def exercise():
        with httpx.Client(transport=httpx.MockTransport(transport)) as sync_client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as async_client:
                llm = IntegrityChatOpenAI(model="fixture", api_key="never-retain-fixture-key",
                    base_url="http://sdk-fixture.invalid/v1", max_retries=1, streaming=False,
                    use_responses_api=responses, http_client=sync_client, http_async_client=async_client)
                return await llm.ainvoke("fixture") if asynchronous else llm.invoke("fixture")

    reply = asyncio.run(exercise())
    assert len(requests) == 2
    assert all(path == ("/v1/responses" if responses else "/v1/chat/completions") for path in requests)
    assert reply.response_metadata["provider_attempts"] == 2
    assert reply.response_metadata["tool_argument_integrity"]["status"] == "verified"
    assert "headers" not in reply.response_metadata
    record = usage_record(reply, 1)
    assert record["provider_attempts"] == 2 and record["total_tokens"] == 7
    encoded = json.dumps(record)
    assert "never-retain-fixture-header" not in encoded and "never-retain-fixture-key" not in encoded
