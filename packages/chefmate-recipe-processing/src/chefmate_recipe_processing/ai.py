"""Validated native sync/async model adapters with invocation-local configuration."""
import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

from openai import OpenAI, AsyncOpenAI
from pydantic import BaseModel

from .execution import Operation, io, run, arun, invocation_context
from .redaction import redact

PURPOSES = {"extract", "match", "tags", "catalog", "usda_match", "usda_query", "convert", "nutrition_estimate"}


class _BoundClient:
    def __init__(self, owner, context):
        self.owner = owner
        self.context = context
        self.settings = dict(context.settings)
        self.progress = context.progress if context.progress is not None else owner.progress

    def bind(self, *, context=None, progress=None):
        from .protocols import RequestContext
        previous = self.context
        context = context or RequestContext()
        combined = RequestContext(
            settings={**previous.settings, **context.settings},
            progress=progress if progress is not None else (
                context.progress if context.progress is not None else previous.progress),
            on_call=context.on_call if context.on_call is not None else previous.on_call,
            on_error=context.on_error if context.on_error is not None else previous.on_error,
        )
        return _BoundClient(self.owner, combined)

    def request(self, *args, **kwargs):
        return self.owner.request(*args, request_context=self.context, **kwargs)

    async def arequest(self, *args, **kwargs):
        if not hasattr(self.owner, "arequest"):
            raise TypeError("异步处理需要 AsyncAIClient 或异步模型适配器")
        return await self.owner.arequest(*args, request_context=self.context, **kwargs)


class AIClient:
    def __init__(self, settings: dict, client=None, sleeper=time.sleep, *, on_call=None,
                 on_error=None, prompt_dir=None, progress=None):
        self.settings = {"openai_key": "", "model": "", "base_url": "https://api.openai.com/v1",
                         "api_mode": "responses", "retries": 2, "timeout": 90,
                         "max_output_tokens": 12000, **settings}
        self.client = client
        self.sleeper = sleeper
        self.on_call = on_call or (lambda *args, **kwargs: None)
        self.on_error = on_error or (lambda *args, **kwargs: None)
        self.progress = progress or (lambda message: None)
        self.prompt_dir = Path(prompt_dir) if prompt_dir else Path(__file__).parent / "prompts"
        self._clients = {}
        self._client_lock = threading.Lock()
        self._closed = False

    def bind(self, *, context=None, progress=None):
        context = invocation_context(dict(self.settings), context, progress)
        context = replace(context,
                          progress=context.progress if context.progress is not None else self.progress,
                          on_call=context.on_call if context.on_call is not None else self.on_call,
                          on_error=context.on_error if context.on_error is not None else self.on_error)
        return _BoundClient(self, context)

    def _get_client(self, settings):
        if self._closed:
            raise ValueError("模型适配器已关闭")
        with self._client_lock:
            if self.client is not None and not self._clients:
                client = self.client  # Externally supplied client, including legacy assignment.
            else:
                if not settings["openai_key"] or not settings["model"]:
                    raise ValueError("请配置模型 API 密钥和模型名称")
                key = (settings["openai_key"], settings["base_url"])
                if key not in self._clients:
                    client_type = AsyncOpenAI if isinstance(self, AsyncAIClient) else OpenAI
                    self._clients[key] = client_type(api_key=key[0], base_url=key[1],
                                                    max_retries=0, timeout=settings["timeout"])
                client = self._clients[key]
                self.client = client
        # SDK clients share their transport; options remain local to this request.
        if isinstance(client, (OpenAI, AsyncOpenAI)):
            client = client.with_options(timeout=settings["timeout"], max_retries=0)
        return client

    def record(self, *args, **kwargs):
        self._record(None, *args, **kwargs)

    def _record(self, context, *args, **kwargs):
        settings = context.settings if context else self.settings
        callback = context.on_call if context and context.on_call is not None else self.on_call
        secret = settings.get("openai_key", "")
        callback(*[redact(value, secret) for value in args],
                 **{key: redact(value, secret) for key, value in kwargs.items()})

    @staticmethod
    def _arguments(settings, mode, payload, prompt, schema):
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        if mode == "deepseek_json":
            return dict(model=settings["model"], messages=messages, response_format={"type": "json_object"},
                        max_tokens=settings["max_output_tokens"], stream=False)
        return dict(model=settings["model"], input=messages, text_format=schema,
                    store=False, max_output_tokens=settings["max_output_tokens"])

    @staticmethod
    def _parse(response, mode, schema, validator):
        if mode == "deepseek_json":
            choice = response.choices[0]
            if choice.finish_reason != "stop" or not choice.message.content:
                raise ValueError("模型输出为空、被截断或未正常结束")
            parsed = schema.model_validate_json(choice.message.content)
        else:
            parsed = response.output_parsed
        if parsed is None:
            raise ValueError("模型拒绝回答或输出不完整")
        result = schema.model_validate(parsed.model_dump() if isinstance(parsed, BaseModel) else parsed)
        if validator:
            validator(result)
        return result

    def _request(self, purpose, payload, schema, entity_id, validator=None, *, system_prompt=None,
                 request_context=None):
        context = invocation_context(dict(self.settings), request_context)
        context = replace(context, on_call=context.on_call if context.on_call is not None else self.on_call)
        settings = context.settings
        report = context.progress if context.progress is not None else self.progress
        on_error = context.on_error if context.on_error is not None else self.on_error
        if purpose not in PURPOSES:
            raise ValueError("未知 AI 任务")
        client = self._get_client(settings)
        prompt = system_prompt if system_prompt is not None else (
            yield io((self.prompt_dir / f"{purpose}.txt").read_text, encoding="utf-8"))
        mode = settings["api_mode"]
        if mode not in ("responses", "deepseek_json"):
            raise ValueError("OPENAI_API_MODE 必须为 responses 或 deepseek_json")
        if mode == "deepseek_json":
            prompt += "\n" + (yield io((self.prompt_dir / "json_output.txt").read_text, encoding="utf-8"))
            prompt += "\nJSON Schema:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        arguments = self._arguments(settings, mode, payload, prompt, schema)
        sender = client.chat.completions.create if mode == "deepseek_json" else client.responses.parse
        request_log = {"model": settings["model"], "prompt": prompt, "payload": payload}
        for attempt in range(settings["retries"] + 1):
            response = None
            started = time.monotonic()
            yield io(report, f"AI {purpose}：第 {attempt + 1}/{settings['retries'] + 1} 次请求，等待响应（单次超时 {settings['timeout']} 秒）")
            try:
                response = yield Operation(sender, kwargs=arguments,
                                           async_function=sender if isinstance(self, AsyncAIClient) else None)
                result = yield io(self._parse, response, mode, schema, validator)
                yield io(self._record, context, "OpenAI", purpose, entity_id, "approved",
                         {**request_log, "attempt": attempt},
                         {"result": result.model_dump(), "usage": response.usage.model_dump()
                          if getattr(response, "usage", None) is not None else None})
                yield io(report, f"AI {purpose}：校验通过（{time.monotonic() - started:.1f} 秒）")
                return result
            except Exception as exc:
                error = f"{type(exc).__name__}; HTTP {getattr(exc, 'status_code', None)}"
                yield io(report, f"AI {purpose}：失败（{error}），耗时 {time.monotonic() - started:.1f} 秒")
                raw = response.model_dump(mode="json") if response is not None and hasattr(response, "model_dump") else None
                yield io(self._record, context, "OpenAI", purpose, entity_id, "failed",
                         {**request_log, "attempt": attempt}, raw, error)
                yield io(on_error, "openai_schema_or_request_failure", entity_id, exc)
                if attempt == settings["retries"] or getattr(exc, "status_code", None) in (400, 401, 403, 404):
                    raise ValueError(f"OpenAI 处理失败（{error}），请人工处理或查看服务调用记录") from None
                delay = min(2**attempt, 4)
                yield io(report, f"等待 {delay} 秒后重试")
                yield Operation(self.sleeper, (delay,), async_function=getattr(self, "async_sleeper", None))
        raise RuntimeError("unreachable")

    def request(self, purpose, payload, schema, entity_id, validator=None, *, system_prompt=None,
                request_context=None):
        return run(self._request(purpose, payload, schema, entity_id, validator,
                                 system_prompt=system_prompt, request_context=request_context))

    def close(self):
        for client in self._clients.values():
            client.close()
        self._clients.clear()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AsyncAIClient(AIClient):
    def __init__(self, settings, client=None, *, sleeper=asyncio.sleep, **kwargs):
        super().__init__(settings, client, **kwargs)
        self.async_sleeper = sleeper

    async def arequest(self, purpose, payload, schema, entity_id, validator=None, *, system_prompt=None,
                       request_context=None):
        return await arun(self._request(purpose, payload, schema, entity_id, validator,
                                       system_prompt=system_prompt, request_context=request_context))

    def request(self, *args, **kwargs):
        raise TypeError("AsyncAIClient 请使用 arequest()")

    async def aclose(self):
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        self._closed = True

    def close(self):
        raise TypeError("AsyncAIClient 请使用 await aclose()")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
