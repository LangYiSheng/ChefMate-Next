"""本地设置、密钥边界与原生工具协议连接测试。"""

import asyncio
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from chefmate_workflow import CookingSettings

from .config import APP_ROOT
from .components import ComponentRegistry, DEFAULT_COMPONENTS, RETRIEVAL_TOOLS, component_metadata
PERSISTED = {"model", "base_url", "api_key", "api_format", "stream_tool_calls", "supports_images", "max_images", "enabled_components",
             "turn_timeout_s", "model_timeout_s", "model_max_retries", "max_model_rounds", "max_tool_calls",
             "max_history_chars", "max_tool_result_chars", "reply_suggestions", "reply_suggestion_count", "timer_limit", "clear_timer_on_step_change", "ui_preferences"}
PERSONAL_PERSISTED = {"ui_preferences", "reply_suggestions", "reply_suggestion_count", "timer_limit", "clear_timer_on_step_change"}


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr | None = Field(default=None, repr=False)
    api_format: str = "chat_completions"
    stream_tool_calls: bool = False
    supports_images: bool = False
    max_images: int = Field(default=3, ge=1, le=12)

    @field_validator("model")
    @classmethod
    def model_name(cls, value):
        if not value.strip():
            raise ValueError("请输入模型名称")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in {"https", "http"}
                or parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("请使用 HTTPS 端点或本机 HTTP 地址，不包含账号、参数或片段")
        return value

    @field_validator("api_format")
    @classmethod
    def protocol(cls, value):
        if value not in {"chat_completions", "responses"}:
            raise ValueError("请选择支持的 OpenAI 协议")
        return value


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    turn_timeout_s: float = Field(ge=10, le=300, allow_inf_nan=False)
    model_timeout_s: float = Field(ge=10, le=300, allow_inf_nan=False)
    model_max_retries: int = Field(ge=0, le=2)
    max_model_rounds: int = Field(ge=2, le=30)
    max_tool_calls: int = Field(ge=2, le=50)


class ContextSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_history_chars: int = Field(ge=4000, le=2000000)
    max_tool_result_chars: int = Field(ge=2000, le=200000)


class InteractionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reply_suggestions: bool
    reply_suggestion_count: int = Field(default=3, ge=1, le=6)


def settings_path(config):
    return config.sessions_database.parent / "settings.json"


def load_overrides(config):
    path = settings_path(config)
    if not path.exists():
        return config
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or set(values) - PERSISTED:
        raise ValueError("本地设置文件无效")
    if config.managed_user and set(values) - PERSONAL_PERSISTED:
        raise ValueError("用户设置不能覆盖管理员配置")
    model_values = {key: values.get(key, getattr(config, key)) for key in
                    ("model", "base_url", "api_key", "api_format", "stream_tool_calls", "supports_images", "max_images")}
    model_name = model_values["model"]
    model_values["model"] = model_name or "unconfigured"
    model = ModelSettings.model_validate(model_values)
    updated = model.model_dump(exclude={"api_key"})
    updated["model"] = model_name
    updated["api_key"] = model.api_key.get_secret_value() if model.api_key else ""
    if "enabled_components" in values:
        if (not isinstance(values["enabled_components"], list)
                or any(not isinstance(name, str) or name not in DEFAULT_COMPONENTS for name in values["enabled_components"])
                or len(values["enabled_components"]) != len(set(values["enabled_components"]))):
            raise ValueError("系统组件配置无效")
        updated["enabled_components"] = tuple(values["enabled_components"])
    runtime = RuntimeSettings.model_validate({key: values.get(key, int(getattr(config, key)))
                                             for key in RuntimeSettings.model_fields})
    context = ContextSettings.model_validate({key: values.get(key, getattr(config, key))
                                             for key in ContextSettings.model_fields})
    interaction = InteractionSettings.model_validate({key: values.get(key, getattr(config, key))
                                                      for key in InteractionSettings.model_fields})
    cooking = CookingSettings.model_validate({key: values.get(key, getattr(config, key)) for key in CookingSettings.model_fields})
    from .ui_preferences import AppearancePreferences
    appearance = values.get("ui_preferences", config.ui_preferences)
    AppearancePreferences.model_validate(appearance)
    return replace(config, **updated, **runtime.model_dump(), **context.model_dump(), **interaction.model_dump(), **cooking.model_dump(), ui_preferences=appearance)


def resolve_model(config, value):
    model = ModelSettings.model_validate(value)
    key = model.api_key.get_secret_value().strip() if model.api_key else ""
    # 空密码只在同一端点沿用，避免将旧供应商密钥发送到新站点。
    if not key:
        if urlsplit(model.base_url).netloc != urlsplit(config.base_url).netloc:
            raise ValueError("更换供应商时，请填写这个供应商的专用 API Key。")
        key = config.api_key
    if not key:
        raise ValueError("请填写 API Key。")
    return replace(config, **model.model_dump(exclude={"api_key"}), api_key=key)


def public_settings(config, *, components=None):
    from .ui_preferences import preferences_snapshot
    payload = {name: getattr(config, name) for name in PERSISTED}
    revision = hashlib.sha256(json.dumps(payload, sort_keys=True, default=list).encode()).hexdigest()
    payload.pop("api_key")
    return {"revision": revision,
            "model": {key: payload[key] for key in
                      ("model", "base_url", "api_format", "stream_tool_calls", "supports_images", "max_images")},
            "api_key_set": bool(config.api_key), "enabled_components": list(config.enabled_components),
            "available_components": list(DEFAULT_COMPONENTS),
            "components": components.metadata() if components else component_metadata(config.enabled_components),
            "runtime": {key: payload[key] for key in RuntimeSettings.model_fields},
            "context": {key: payload[key] for key in ContextSettings.model_fields},
            "interaction": {key: payload[key] for key in InteractionSettings.model_fields},
            "cooking": {key: payload[key] for key in CookingSettings.model_fields},
            "ui_preferences": preferences_snapshot(config)}


def persist_settings(config):
    path = settings_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {name: getattr(config, name) for name in (PERSONAL_PERSISTED if config.managed_user else PERSISTED)}
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_agent(config, service, *, memory=None, cookbook=None, journal=None, preferences=None):
    from chefmate_agent import AgentConfig, ChefMateAgent
    from chefmate_workflow import UIAction, UISnapshot

    components = ComponentRegistry(config.enabled_components, instruction_root=APP_ROOT / "skills",
                                   resources={"retrieval": service, "memory": memory, "cookbook": cookbook, "journal": journal, "preferences": preferences})
    if not config.model_configured or service is None:
        return None, components
    extra_tools = components.build_tools()
    registered = [*RETRIEVAL_TOOLS, *(tool.name for tool in extra_tools)]
    agent = ChefMateAgent(service, AgentConfig(
        model=config.model, api_key=config.api_key, base_url=config.base_url,
        use_responses_api=config.api_format == "responses", stream_tool_calls=config.stream_tool_calls,
        supports_images=config.supports_images, max_images=config.max_images,
        reply_suggestions=config.reply_suggestions,
        reply_suggestion_count=config.reply_suggestion_count,
        turn_timeout_s=config.turn_timeout_s, model_timeout_s=config.model_timeout_s,
        max_model_retries=config.model_max_retries, max_model_rounds=config.max_model_rounds,
        max_tool_calls=config.max_tool_calls,
        max_history_chars=config.max_history_chars, max_tool_result_chars=config.max_tool_result_chars,
    ), extra_tools=extra_tools, ui_context_model=UISnapshot, action_model=UIAction,
       skills=components.instructions, tool_skill_bindings=components.tool_bindings(registered))
    return agent, components


async def probe_model(config, *, on_usage=None):
    """验证原生工具请求、结果回传和最终回答；不把文本中的工具指令当成功。"""
    from chefmate_agent.provider import IntegrityChatOpenAI
    from langchain_core.messages import HumanMessage, ToolMessage
    from chefmate_agent.telemetry import usage_record, usage_summary

    model = IntegrityChatOpenAI(model=config.model, api_key=config.api_key, base_url=config.base_url,
                       use_responses_api=config.api_format == "responses", timeout=35, max_retries=0,
                       streaming=config.stream_tool_calls, stream_usage=True, max_tokens=256)
    usage_calls, attempts = [], 0
    def report():
        if on_usage is not None:
            on_usage(usage_summary(usage_calls, attempted_calls=attempts))
    tool = {"type": "function", "function": {"name": "connection_echo", "description": "连接测试，无副作用",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"], "additionalProperties": False}}}
    request = HumanMessage(content="调用connection_echo，value必须是chefmate。得到工具结果后只回复OK。")
    async def run():
        nonlocal attempts
        bound = model.bind_tools([tool], tool_choice="connection_echo")
        attempts += 1
        report()
        if config.stream_tool_calls:
            reply = None
            async for chunk in bound.astream([request]):
                reply = chunk if reply is None else reply + chunk
        else:
            reply = await bound.ainvoke([request])
        if reply is not None:
            usage_calls.append(usage_record(reply, 1, model=config.model))
            report()
        calls = reply.tool_calls if reply is not None else []
        if (len(calls) != 1 or calls[0].get("name") != "connection_echo"
                or calls[0].get("args") != {"value": "chefmate"} or not calls[0].get("id")
                or reply.invalid_tool_calls):
            return {"ok": False, "message": "端点有响应，但没有返回正确的原生工具调用。"}
        attempts += 1
        report()
        final = await model.ainvoke([request, reply, ToolMessage(content="chefmate", tool_call_id=reply.tool_calls[0]["id"])])
        usage_calls.append(usage_record(final, 2, model=config.model))
        report()
        if final.tool_calls or not final.content:
            return {"ok": False, "message": "工具调用成功，但结果回传后的回答未完成。"}
        return {"ok": True, "message": "连接、原生工具调用和结果回传均通过。",
                "api_format": config.api_format, "stream_tool_calls": config.stream_tool_calls}
    try:
        return await asyncio.wait_for(run(), timeout=75)
    except Exception as error:
        code = getattr(error, "status_code", None)
        return {"ok": False, "message": "连接测试未通过，请检查端点、专用密钥、模型和协议。",
                "http_status": code if isinstance(code, int) else None}
    finally:
        report()
