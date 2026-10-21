"""Serializable configuration, history and results; no framework objects escape the API."""

from typing import Any, Literal
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    base_url: str | None = Field(default=None, exclude=True, repr=False)
    model_kwargs: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    temperature: float | None = Field(default=None, ge=0, le=2)
    stream_tool_calls: bool = Field(
        default=False,
        description="仅在实际验证供应商支持标准流式tool_calls后启用；默认使用完整工具响应。",
    )
    use_responses_api: bool = False
    supports_images: bool = False
    max_images: int = Field(default=3, ge=1, le=12, strict=True)
    reply_suggestions: bool = Field(default=True, strict=True)
    max_output_tokens: int = Field(default=4096, ge=128, le=16384)
    model_timeout_s: float = Field(default=120, gt=0, le=300)
    max_model_retries: int = Field(default=0, ge=0, le=2)
    turn_timeout_s: float = Field(default=300, gt=0, le=900)
    max_model_rounds: int = Field(default=10, ge=1, le=30)
    max_tool_calls: int = Field(default=16, ge=1, le=50)
    max_history_chars: int = Field(default=400000, ge=4000, le=2000000)
    max_user_chars: int = Field(default=8000, ge=100, le=32000)
    max_tool_result_chars: int = Field(default=60000, ge=2000, le=200000)
    max_ui_context_chars: int = Field(default=24000, ge=1000, le=24000)
    max_skill_chars: int = Field(default=24000, ge=1000, le=64000)
    max_memory_chars: int = Field(default=12000, ge=1000, le=24000)

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must not be blank")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("model_kwargs")
    @classmethod
    def safe_model_kwargs(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        reserved = {
            "model", "api_key", "openai_api_key", "base_url", "openai_api_base", "timeout", "max_retries",
            "streaming", "disable_streaming", "stream_usage", "tools", "tool_choice", "callbacks", "max_tokens",
            "max_completion_tokens",
            "use_responses_api",
        }
        if reserved.intersection(value):
            raise ValueError("model_kwargs contains a reserved connection or execution option")
        try:
            json.dumps(value, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("model_kwargs must be finite JSON values") from exc
        return value


class AgentError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ImageReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    name: str = Field(max_length=100)
    mime_type: Literal["image/jpeg"] = "image/jpeg"
    width: int = Field(ge=1, le=1600)
    height: int = Field(ge=1, le=1600)
    byte_size: int = Field(ge=1, le=2000000)


class ToolCallRecord(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ReplySuggestion(BaseModel):
    """A draft the user may edit; it never represents an already executed action."""

    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=500)

    @field_validator("title", "text")
    @classmethod
    def plain_text(cls, value: str, info) -> str:
        value = value.strip()
        if (not value or "<" in value or ">" in value
                or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value)
                or info.field_name == "title" and any(char in value for char in "\r\n\t")):
            raise ValueError("Reply suggestions must contain plain text")
        return value


class AgentTurnResult(BaseModel):
    status: Literal["completed", "cancelled", "error"]
    assistant_text: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    recipes: list[dict[str, Any]] = Field(default_factory=list)
    selected_recipe: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    context_usage: dict[str, Any] = Field(default_factory=dict)
    suggested_replies: list[ReplySuggestion] = Field(default_factory=list, max_length=3)
    awaiting_user: bool = False
    pending_question: dict[str, Any] | None = None
    debug_trace: list[dict[str, Any]] = Field(default_factory=list)
    skill_events: list[dict[str, Any]] = Field(default_factory=list)
    error: AgentError | None = None
