"""Only module allowed to instantiate/call OpenAI. Every result is validated."""

import json
import time
from collections.abc import Callable
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from pathlib import Path
from .redaction import redact

T = TypeVar("T", bound=BaseModel)


class AIClient:
    def __init__(self, settings: dict, client=None, sleeper: Callable = time.sleep,
                 *, on_call=None, on_error=None, prompt_dir=None, progress=None):
        self.settings = {"openai_key": "", "model": "", "base_url": "https://api.openai.com/v1",
                         "api_mode": "responses", "retries": 2, "timeout": 90,
                         "max_output_tokens": 12000, **settings}
        self.client = client
        self.sleeper = sleeper
        self.on_call = on_call or (lambda *args, **kwargs: None)
        self.on_error = on_error or (lambda *args, **kwargs: None)
        self.prompt_dir = Path(prompt_dir) if prompt_dir else Path(__file__).parent / "prompts"
        self.progress = progress or (lambda message: None)

    def record(self, *args, **kwargs):
        secret = self.settings.get("openai_key", "")
        self.on_call(*[redact(value, secret) for value in args],
                     **{key: redact(value, secret) for key, value in kwargs.items()})

    def request(
        self,
        purpose: str,
        payload: dict,
        schema: type[T],
        entity_id: str,
        validator: Callable[[T], None] | None = None,
        *,
        system_prompt: str | None = None,
    ) -> T:
        if purpose not in ("extract", "match", "tags", "catalog", "usda_match", "usda_query", "convert", "nutrition_estimate"):
            raise ValueError("未知 AI 任务")
        if self.client is None:
            if not self.settings["openai_key"] or not self.settings["model"]:
                raise ValueError("请配置模型 API 密钥和模型名称")
            self.client = OpenAI(
                api_key=self.settings["openai_key"],
                base_url=self.settings["base_url"],
                max_retries=0,
                timeout=self.settings["timeout"],
            )
        prompt = (
            system_prompt
            if system_prompt is not None
            else (self.prompt_dir / f"{purpose}.txt").read_text(encoding="utf-8")
        )
        mode = self.settings["api_mode"]
        if mode not in ("responses", "deepseek_json"):
            raise ValueError("OPENAI_API_MODE 必须为 responses 或 deepseek_json")
        if mode == "deepseek_json":
            prompt += "\n" + (self.prompt_dir / "json_output.txt").read_text(encoding="utf-8")
            prompt += "\nJSON Schema:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        request_log = {"model": self.settings["model"], "prompt": prompt, "payload": payload}
        for attempt in range(self.settings["retries"] + 1):
            response = None
            started = time.monotonic()
            self.progress(f"AI {purpose}：第 {attempt + 1}/{self.settings['retries'] + 1} 次请求，等待响应（单次超时 {self.settings['timeout']} 秒）")
            try:
                if mode == "deepseek_json":
                    response = self.client.chat.completions.create(
                        model=self.settings["model"],
                        messages=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                        response_format={"type": "json_object"},
                        max_tokens=self.settings["max_output_tokens"],
                        stream=False,
                    )
                    choice = response.choices[0]
                    if choice.finish_reason != "stop" or not choice.message.content:
                        raise ValueError("模型输出为空、被截断或未正常结束")
                    parsed = schema.model_validate_json(choice.message.content)
                else:
                    response = self.client.responses.parse(
                        model=self.settings["model"],
                        input=[
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                        text_format=schema,
                        store=False,
                        max_output_tokens=self.settings["max_output_tokens"],
                    )
                    parsed = response.output_parsed
                if parsed is None:
                    raise ValueError("模型拒绝回答或输出不完整")
                self.progress(f"AI 响应已收到（{time.monotonic() - started:.1f} 秒），正在校验字段和枚举")
                result = schema.model_validate(parsed.model_dump() if isinstance(parsed, BaseModel) else parsed)
                if validator:
                    validator(result)
                self.record(
                    "OpenAI",
                    purpose,
                    entity_id,
                    "approved",
                    {**request_log, "attempt": attempt},
                    {
                        "result": result.model_dump(),
                        "usage": response.usage.model_dump() if getattr(response, "usage", None) is not None else None,
                    },
                )
                self.progress(f"AI {purpose}：校验通过")
                return result
            except Exception as exc:
                # Exception messages may contain request headers; retain type/status only.
                error = f"{type(exc).__name__}; HTTP {getattr(exc, 'status_code', 'n/a')}"
                self.progress(f"AI {purpose}：失败（{error}），耗时 {time.monotonic() - started:.1f} 秒")
                raw = (
                    response.model_dump(mode="json")
                    if response is not None and hasattr(response, "model_dump")
                    else None
                )
                self.record(
                    "OpenAI", purpose, entity_id, "failed", {**request_log, "attempt": attempt}, raw, error
                )
                self.on_error("openai_schema_or_request_failure", entity_id, exc)
                if attempt == self.settings["retries"] or getattr(exc, "status_code", None) in (400, 401, 403, 404):
                    raise ValueError(f"OpenAI 处理失败（{error}），请人工处理或查看服务调用记录") from None
                self.progress(f"等待 {min(2**attempt, 4)} 秒后重试")
                self.sleeper(min(2**attempt, 4))
        raise RuntimeError("unreachable")
