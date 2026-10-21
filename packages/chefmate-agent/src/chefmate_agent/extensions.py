"""Typed injection points; application packages own UI and cooking-workflow payloads."""

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


@dataclass(frozen=True)
class ToolExecutionContext:
    ui_context: dict[str, Any]
    host_context: Any
    messages: list[dict[str, Any]]
    call_id: str = ""


@dataclass(frozen=True)
class InjectedToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: Callable[[dict[str, Any], ToolExecutionContext], Any]
    response_model: type[BaseModel] | None = None
    allowed_stages: tuple[str, ...] | None = None
    effect: str = "read"
    auto_display: bool = False
    preconditions: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()
    model_visible: bool = True
    required_skill: str | None = None
