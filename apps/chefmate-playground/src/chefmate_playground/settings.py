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

SKILLS = ("meal-planning", "pantry-preparation", "cooking-guide")
PERSISTED = {"model", "base_url", "api_key", "api_format", "stream_tool_calls", "supports_images", "max_images", "enabled_skills",
             "turn_timeout_s", "model_timeout_s", "model_max_retries", "max_model_rounds", "max_tool_calls",
             "max_history_chars", "max_tool_result_chars", "reply_suggestions", "timer_limit", "clear_timer_on_step_change"}


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


def settings_path(config):
    return config.sessions_database.parent / "settings.json"


def load_overrides(config):
    path = settings_path(config)
    if not path.exists():
        return config
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or set(values) - PERSISTED:
        raise ValueError("本地设置文件无效")
    model_values = {key: values.get(key, getattr(config, key)) for key in
                    ("model", "base_url", "api_key", "api_format", "stream_tool_calls", "supports_images", "max_images")}
    model_name = model_values["model"]
    model_values["model"] = model_name or "unconfigured"
    model = ModelSettings.model_validate(model_values)
    updated = model.model_dump(exclude={"api_key"})
    updated["model"] = model_name
    updated["api_key"] = model.api_key.get_secret_value() if model.api_key else ""
    if "enabled_skills" in values:
        if not isinstance(values["enabled_skills"], list) or any(name not in SKILLS for name in values["enabled_skills"]):
            raise ValueError("厨房技能配置无效")
        updated["enabled_skills"] = tuple(dict.fromkeys(values["enabled_skills"]))
    runtime = RuntimeSettings.model_validate({key: values.get(key, int(getattr(config, key)))
                                             for key in RuntimeSettings.model_fields})
    context = ContextSettings.model_validate({key: values.get(key, getattr(config, key))
                                             for key in ContextSettings.model_fields})
    interaction = InteractionSettings.model_validate({key: values.get(key, getattr(config, key))
                                                      for key in InteractionSettings.model_fields})
    cooking = CookingSettings.model_validate({key: values.get(key, getattr(config, key)) for key in CookingSettings.model_fields})
    return replace(config, **updated, **runtime.model_dump(), **context.model_dump(), **interaction.model_dump(), **cooking.model_dump())


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


def public_settings(config):
    payload = {name: getattr(config, name) for name in PERSISTED}
    revision = hashlib.sha256(json.dumps(payload, sort_keys=True, default=list).encode()).hexdigest()
    payload.pop("api_key")
    return {"revision": revision,
            "model": {key: payload[key] for key in
                      ("model", "base_url", "api_format", "stream_tool_calls", "supports_images", "max_images")},
            "api_key_set": bool(config.api_key), "enabled_skills": list(config.enabled_skills),
            "available_skills": list(SKILLS),
            "runtime": {key: payload[key] for key in RuntimeSettings.model_fields},
            "context": {key: payload[key] for key in ContextSettings.model_fields},
            "interaction": {key: payload[key] for key in InteractionSettings.model_fields},
            "cooking": {key: payload[key] for key in CookingSettings.model_fields}}


def persist_settings(config):
    path = settings_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {name: getattr(config, name) for name in PERSISTED}
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


def build_agent(config, service):
    from chefmate_agent import AgentConfig, ChefMateAgent, SkillRegistry
    from chefmate_workflow import UIAction, UISnapshot
    from .interactions import make_tool_specs

    root = (APP_ROOT / "skills").resolve(strict=True)
    if any(name not in SKILLS for name in config.enabled_skills):
        raise ValueError("无效技能目录")
    directories = [(root / name).resolve(strict=True) for name in config.enabled_skills]
    if any(path.parent != root for path in directories):
        raise ValueError("技能目录超出应用范围")
    skills = SkillRegistry(directories)
    if not config.model_configured or service is None:
        return None, skills
    agent = ChefMateAgent(service, AgentConfig(
        model=config.model, api_key=config.api_key, base_url=config.base_url,
        use_responses_api=config.api_format == "responses", stream_tool_calls=config.stream_tool_calls,
        supports_images=config.supports_images, max_images=config.max_images,
        reply_suggestions=config.reply_suggestions,
        turn_timeout_s=config.turn_timeout_s, model_timeout_s=config.model_timeout_s,
        max_model_retries=config.model_max_retries, max_model_rounds=config.max_model_rounds,
        max_tool_calls=config.max_tool_calls,
        max_history_chars=config.max_history_chars, max_tool_result_chars=config.max_tool_result_chars,
    ), extra_tools=make_tool_specs(), ui_context_model=UISnapshot, action_model=UIAction, skills=skills)
    return agent, skills


async def probe_model(config):
    """验证原生工具请求、结果回传和最终回答；不把文本中的工具指令当成功。"""
    from chefmate_agent.provider import IntegrityChatOpenAI
    from langchain_core.messages import HumanMessage, ToolMessage

    model = IntegrityChatOpenAI(model=config.model, api_key=config.api_key, base_url=config.base_url,
                       use_responses_api=config.api_format == "responses", timeout=35, max_retries=0,
                       streaming=config.stream_tool_calls, stream_usage=False)
    tool = {"type": "function", "function": {"name": "connection_echo", "description": "连接测试，无副作用",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"], "additionalProperties": False}}}
    request = HumanMessage(content="调用connection_echo，value必须是chefmate。得到工具结果后只回复OK。")
    async def run():
        bound = model.bind_tools([tool], tool_choice="connection_echo")
        if config.stream_tool_calls:
            reply = None
            async for chunk in bound.astream([request]):
                reply = chunk if reply is None else reply + chunk
        else:
            reply = await bound.ainvoke([request])
        calls = reply.tool_calls if reply is not None else []
        if (len(calls) != 1 or calls[0].get("name") != "connection_echo"
                or calls[0].get("args") != {"value": "chefmate"} or not calls[0].get("id")
                or reply.invalid_tool_calls):
            return {"ok": False, "message": "端点有响应，但没有返回正确的原生工具调用。"}
        final = await model.ainvoke([request, reply, ToolMessage(content="chefmate", tool_call_id=calls[0]["id"])])
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
