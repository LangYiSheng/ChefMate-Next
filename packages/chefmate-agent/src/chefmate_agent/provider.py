"""Provider argument integrity at the raw SDK boundary.

The SDK clients are wrapped on a request-local model copy. LangChain still owns
request construction, message conversion, callbacks, tool binding and agents.
Only function-call events are held until a successful response terminates. No
argument fragments are added to diagnostics or retained on the shared model.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

INTEGRITY_METADATA = "tool_argument_integrity"
PROVIDER_ATTEMPTS_METADATA = "provider_attempts"
_LIMIT = 256_000
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _safe(value):
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else "[invalid identifier]"


def _hash(value):
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _object(raw):
    if not isinstance(raw, str) or len(raw) > _LIMIT:
        raise ValueError("invalid argument length/type")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-finite JSON number")

    parsed = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(parsed, dict):
        raise ValueError("arguments must be an object")
    # json.loads accepts overflowing numeric literals such as 1e999 as inf.
    json.dumps(parsed, allow_nan=False)
    return parsed


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class ProviderIntegrityError(ValueError):
    """Safe, stable failure; diagnostics contain summaries, never arguments."""

    def __init__(self, code, diagnostics):
        self.code = code
        self.diagnostics = copy.deepcopy(diagnostics)
        super().__init__("Provider tool arguments failed integrity validation.")


@dataclass
class _Call:
    index: int
    call_id: str | None = None
    item_id: str | None = None
    name: str | None = None
    raw: str = ""
    final: str | None = None
    parsed: dict | None = None
    endings: set[str] = field(default_factory=set)
    added: bool = False
    status: str = "pending"

    def summary(self):
        result = {
            "index": self.index, "call_id": _safe(self.call_id), "name": _safe(self.name),
            "status": self.status, "raw_length": len(self.raw), "raw_sha256": _hash(self.raw),
            "endings": sorted(self.endings),
        }
        if self.final is not None:
            result.update(final_length=len(self.final), final_sha256=_hash(self.final))
        if self.parsed is not None:
            result["fields"] = [_safe(key) for key in sorted(self.parsed)[:100]]
        return result


class ArgumentIntegrity:
    """One SDK response only. Public for offline replay and contract tests."""

    def __init__(self, protocol):
        self.protocol = protocol
        self.calls: dict[int, _Call] = {}
        self.ids: dict[str, int] = {}
        self.item_ids: dict[str, int] = {}
        self.other_indices: set[int] = set()
        self.terminal = False
        self.completed_event = None
        self.finish_reason = None
        self.status = "pending"
        self.provider_attempts: int | None = None

    def observe_sdk_response(self, response):
        """Retain only the SDK's public raw-response retry counter.

        Parsed provider JSON and transport headers are not evidence of SDK
        retries. An absent or invalid counter remains unknown, including zero
        retries on SDK/client paths that do not expose a raw response.
        """
        retries = _get(response, "retries_taken")
        self.provider_attempts = retries + 1 if type(retries) is int and retries >= 0 else None

    def diagnostics(self):
        return {"protocol": self.protocol, "status": self.status,
                "calls": [call.summary() for _, call in sorted(self.calls.items())]}

    def fail(self, code, call=None):
        self.status = code
        if call is not None:
            call.status = code
        raise ProviderIntegrityError(code, self.diagnostics())

    def _call(self, index, *, call_id=None, item_id=None, name=None):
        if (type(index) is not int or index < 0 or index in self.other_indices
                or index not in self.calls and len(self.calls) >= 128):
            self.fail("provider_tool_identity_conflict")
        call = self.calls.setdefault(index, _Call(index))
        for attr, value, identities in (("call_id", call_id, self.ids), ("item_id", item_id, self.item_ids)):
            if value is not None:
                if not isinstance(value, str) or not value or len(value) > 200:
                    self.fail("provider_tool_identity_conflict", call)
                if getattr(call, attr) not in (None, value) or identities.get(value, index) != index:
                    self.fail("provider_tool_identity_conflict", call)
                setattr(call, attr, value)
                identities[value] = index
        if name is not None:
            if not isinstance(name, str) or not name or len(name) > 200 or call.name not in (None, name):
                self.fail("provider_tool_identity_conflict", call)
            call.name = name
        return call

    def _response_item(self, index, item, *, terminal=False):
        if not _get(item, "call_id") or not _get(item, "name"):
            self.fail("provider_tool_identity_conflict")
        call = self._call(index, call_id=_get(item, "call_id"), item_id=_get(item, "id"), name=_get(item, "name"))
        if terminal and _get(item, "status") not in (None, "completed"):
            self.fail("provider_response_incomplete", call)
        return call

    def _append(self, call, fragment):
        if not isinstance(fragment, str) or len(call.raw) + len(fragment) > _LIMIT:
            self.fail("provider_tool_arguments_incomplete", call)
        call.raw += fragment

    def _end(self, call, raw, source):
        # Full end-of-call values are authoritative only when mutually consistent.
        if isinstance(raw, str) and len(raw) <= _LIMIT and call.final is None:
            call.final = raw
        try:
            parsed = _object(raw)
        except (TypeError, ValueError, RecursionError):
            self.fail("provider_tool_arguments_incomplete", call)
        if call.parsed is not None and _canonical(parsed) != _canonical(call.parsed):
            self.fail("provider_tool_arguments_conflict", call)
        call.final, call.parsed = raw, parsed
        call.endings.add(source)

    def _verify(self, call):
        if not call.call_id or not call.name:
            self.fail("provider_tool_identity_conflict", call)
        if call.final is None:
            self.fail("provider_tool_arguments_incomplete", call)
        if not call.raw.strip():
            call.status = "recovered_end"
            return
        try:
            parsed = _object(call.raw)
        except (TypeError, ValueError, RecursionError):
            if not call.final.lstrip().startswith(call.raw.lstrip()):
                self.fail("provider_tool_arguments_conflict", call)
            call.status = "recovered_end"
        else:
            if _canonical(parsed) != _canonical(call.parsed):
                self.fail("provider_tool_arguments_conflict", call)
            call.status = "verified"

    def responses_event(self, event):
        kind = _get(event, "type", "")
        if self.terminal:
            self.fail("provider_response_sequence_invalid")
        if kind in {"response.failed", "response.incomplete", "error"}:
            self.fail("provider_response_incomplete")
        if kind == "response.completed":
            self._responses_complete(_get(event, "response"))
            self.completed_event = event
            return []
        item = _get(event, "item")
        if kind in {"response.output_item.added", "response.output_item.done"}:
            index = _get(event, "output_index")
            if _get(item, "type") != "function_call":
                if index in self.calls:
                    self.fail("provider_tool_identity_conflict")
                self.other_indices.add(index)
                return [event]
            call = self._response_item(index, item, terminal=kind.endswith("done"))
            if kind.endswith("added"):
                if call.added:
                    self.fail("provider_tool_identity_conflict", call)
                call.added = True
                self._append(call, _get(item, "arguments", ""))
            else:
                self._end(call, _get(item, "arguments"), "output_item.done")
            return []
        if kind in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
            call = self._call(_get(event, "output_index"), item_id=_get(event, "item_id"))
            if kind.endswith("delta"):
                self._append(call, _get(event, "delta"))
            else:
                self._end(call, _get(event, "arguments"), "arguments.done")
            return []
        return [event]

    def _responses_complete(self, response):
        if _get(response, "status") != "completed" or _get(response, "error"):
            self.fail("provider_response_incomplete")
        output = _get(response, "output")
        if not isinstance(output, list):
            self.fail("provider_response_incomplete")
        final_indices = set()
        for index, item in enumerate(output):
            if _get(item, "type") != "function_call":
                if index in self.calls:
                    self.fail("provider_tool_identity_conflict")
                continue
            call = self._response_item(index, item, terminal=True)
            final_indices.add(index)
            self._end(call, _get(item, "arguments"), "response.completed")
        if final_indices != set(self.calls):
            self.fail("provider_tool_identity_conflict")
        for call in self.calls.values():
            self._verify(call)
        self.terminal = True

    def chat_chunk(self, chunk):
        for choice in _get(chunk, "choices", []):
            if _get(choice, "index", 0) != 0 or self.terminal:
                self.fail("provider_response_sequence_invalid")
            delta = _get(choice, "delta") or {}
            if _get(delta, "function_call"):
                self.fail("provider_tool_identity_conflict")
            for entry in _get(delta, "tool_calls", []) or []:
                function = _get(entry, "function") or {}
                call = self._call(_get(entry, "index"), call_id=_get(entry, "id"), name=_get(function, "name"))
                if _get(function, "arguments") is not None:
                    self._append(call, _get(function, "arguments"))
            finish = _get(choice, "finish_reason")
            if finish is not None:
                self.finish_reason = finish
                if finish not in {"stop", "tool_calls"} or bool(self.calls) != (finish == "tool_calls"):
                    self.fail("provider_response_incomplete")
                for call in self.calls.values():
                    self._end(call, call.raw, "finish_reason")
                    self._verify(call)
                self.terminal = True
        return [chunk]

    def response(self, response):
        """Validate a non-streaming SDK response before LangChain parses it."""
        if self.protocol == "responses":
            self._responses_complete(response)
        else:
            choices = _get(response, "choices", [])
            if len(choices) != 1:
                self.fail("provider_response_incomplete")
            choice = choices[0]
            message = _get(choice, "message")
            entries = _get(message, "tool_calls", []) or []
            if _get(message, "function_call"):
                self.fail("provider_tool_identity_conflict")
            finish = _get(choice, "finish_reason")
            if finish not in {"stop", "tool_calls"} or bool(entries) != (finish == "tool_calls"):
                self.fail("provider_response_incomplete")
            for index, entry in enumerate(entries):
                function = _get(entry, "function") or {}
                call = self._call(index, call_id=_get(entry, "id"), name=_get(function, "name"))
                self._append(call, _get(function, "arguments"))
                self._end(call, call.raw, "response")
                self._verify(call)
            self.terminal = True
        self.status = "verified"
        return response

    def finish(self):
        if not self.terminal:
            self.fail("provider_response_incomplete")
        self.status = "verified"
        if self.protocol != "responses":
            return []
        events = []
        for index, call in sorted(self.calls.items()):
            # A single complete added event prevents partial-JSON repair in the
            # installed adapter. The real completed event still supplies usage.
            item = SimpleNamespace(type="function_call", id=call.item_id or call.call_id,
                                   call_id=call.call_id, name=call.name, arguments=call.final)
            events.append(SimpleNamespace(type="response.output_item.added", output_index=index, item=item))
        events.append(self.completed_event)
        return events


class _GuardedStream:
    def __init__(self, stream, guard):
        self.stream, self.source, self.guard = stream, stream, guard

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        self.source = self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    async def __aenter__(self):
        self.source = await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)

    def _feed(self, event):
        return self.guard.responses_event(event) if self.guard.protocol == "responses" else self.guard.chat_chunk(event)

    def __iter__(self):
        for event in self.source:
            yield from self._feed(event)
        yield from self.guard.finish()

    async def __aiter__(self):
        async for event in self.source:
            for normalized in self._feed(event):
                yield normalized
        for normalized in self.guard.finish():
            yield normalized


class _RawResponse:
    def __init__(self, raw, guard, streaming):
        self.raw, self.guard, self.streaming = raw, guard, streaming
        guard.observe_sdk_response(raw)

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def parse(self, *args, **kwargs):
        parsed = self.raw.parse(*args, **kwargs)
        return _GuardedStream(parsed, self.guard) if self.streaming else self.guard.response(parsed)


class _Client:
    """Delegate SDK requests unchanged, intercepting their returned objects."""

    def __init__(self, client, guard, asynchronous=False, raw=False):
        self.client, self.guard, self.asynchronous, self.raw = client, guard, asynchronous, raw

    def __getattr__(self, name):
        value = getattr(self.client, name)
        if name in {"responses", "chat", "completions", "beta", "with_raw_response"}:
            return _Client(value, self.guard, self.asynchronous, self.raw or name == "with_raw_response")
        if name not in {"create", "parse", "stream"}:
            return value

        def wrap(result, kwargs):
            streaming = kwargs.get("stream", False) or name == "stream"
            if self.raw:
                return _RawResponse(result, self.guard, streaming)
            return _GuardedStream(result, self.guard) if streaming else self.guard.response(result)

        if self.asynchronous:
            async def invoke(*args, **kwargs):
                result = value(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return wrap(result, kwargs)
        else:
            def invoke(*args, **kwargs):
                return wrap(value(*args, **kwargs), kwargs)
        return invoke


class IntegrityChatOpenAI(ChatOpenAI):
    """Drop-in ChatOpenAI with complete, unambiguous native tool arguments."""

    _integrity_active: bool = PrivateAttr(default=False)

    def _request_copy(self, kwargs, *, streaming=False):
        protocol = "responses" if self._use_responses_api({**self.model_kwargs, **kwargs}) else "chat_completions"
        guard = ArgumentIntegrity(protocol)
        # Chat structured streaming returns SDK beta typed events rather than
        # raw completion chunks. Do not silently bypass the native-call guard.
        if streaming and protocol == "chat_completions" and (
                "response_format" in kwargs or "response_format" in self.model_kwargs):
            guard.fail("provider_stream_format_unsupported")
        model = self.model_copy()
        model._integrity_active = True
        for name, asynchronous in (("client", False), ("async_client", True),
                                   ("root_client", False), ("root_async_client", True)):
            client = getattr(self, name, None)
            if client is not None:
                setattr(model, name, _Client(client, guard, asynchronous))
        return model, guard

    @staticmethod
    def _tag(result, guard):
        for generation in result.generations:
            generation.message.response_metadata[INTEGRITY_METADATA] = guard.diagnostics()
            generation.message.response_metadata[PROVIDER_ATTEMPTS_METADATA] = guard.provider_attempts
        return result

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self._integrity_active:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        model, guard = self._request_copy(kwargs)
        result = super(IntegrityChatOpenAI, model)._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return self._tag(result, guard)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self._integrity_active:
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        model, guard = self._request_copy(kwargs)
        result = await super(IntegrityChatOpenAI, model)._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return self._tag(result, guard)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        if self._integrity_active:
            yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
            return
        model, guard = self._request_copy(kwargs, streaming=True)
        yield from super(IntegrityChatOpenAI, model)._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
        yield ChatGenerationChunk(message=AIMessageChunk(content="", response_metadata={
            INTEGRITY_METADATA: guard.diagnostics(), PROVIDER_ATTEMPTS_METADATA: guard.provider_attempts}))

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        if self._integrity_active:
            async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                yield chunk
            return
        model, guard = self._request_copy(kwargs, streaming=True)
        async for chunk in super(IntegrityChatOpenAI, model)._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk
        yield ChatGenerationChunk(message=AIMessageChunk(content="", response_metadata={
            INTEGRITY_METADATA: guard.diagnostics(), PROVIDER_ATTEMPTS_METADATA: guard.provider_attempts}))
