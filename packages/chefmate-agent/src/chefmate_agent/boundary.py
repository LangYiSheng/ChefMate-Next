"""Shared JSON, error and event boundaries; independent of the agent runner."""

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from .models import AgentError

EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class _AgentFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.error = AgentError(code=code, message=message, retryable=retryable)
        super().__init__(message)


class _UserCancelled(Exception):
    pass


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _text_content(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(
        block.get("text", "") for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _compact_result(value: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Keep valid JSON and evidence, without dumping giant source blobs or connection secrets."""
    excluded = {
        "source_json", "raw_source", "original_source", "export_snapshot", "raw_payload", "api_key", "password",
        "dsn", "database_url", "connection_string", "authorization", "credentials",
    }

    def clip(item: Any, depth: int, list_limit: int, text_limit: int) -> Any:
        if depth > 10:
            return {"omitted": "deep nested data"}
        if isinstance(item, str):
            return item if len(item) <= text_limit else item[:text_limit] + "…[截短]"
        if isinstance(item, dict):
            return {
                key: clip(data, depth + 1, list_limit, text_limit)
                for key, data in item.items() if str(key).lower() not in excluded
            }
        if isinstance(item, list):
            result = [clip(data, depth + 1, list_limit, text_limit) for data in item[:list_limit]]
            if len(item) > list_limit:
                result.append({"omitted_items": len(item) - list_limit})
            return result
        return item

    for list_limit, text_limit in ((40, 1800), (24, 900), (12, 500), (6, 250)):
        compact = clip(value, 0, list_limit, text_limit)
        if len(_dump(compact)) <= max_chars:
            if compact != value:
                compact["result_trimmed"] = True
            return compact
    return {
        "status": "output_too_large",
        "snapshot": value.get("snapshot"),
        "message": "工具结果超过本轮输出预算，请减少limit或缩小查询范围。",
        "result_trimmed": True,
    }
