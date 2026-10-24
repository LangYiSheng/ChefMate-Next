"""The agent's entire application-settings boundary: ordinary, typed preferences only."""

import hashlib
import json
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppearancePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    theme: Literal["system", "light", "dark"] = "light"
    light_palette: Literal["ink", "sage", "oat", "clay", "blue", "rose", "lavender", "olive"] = "ink"
    dark_palette: Literal["graphite", "night", "deep-blue", "cocoa"] = "graphite"
    font_pairing: Literal["mixed", "sans", "serif", "wenkai"] = "mixed"
    font_size: Literal["small", "medium", "large"] = "medium"
    density: Literal["comfortable", "compact"] = "comfortable"
    inline_limit: Literal[1, 2] = 2
    show_estimates: bool = True

    @model_validator(mode="before")
    @classmethod
    def exact_integer(cls, values):
        if isinstance(values, dict) and "inline_limit" in values and type(values["inline_limit"]) is not int:
            raise ValueError("inline_limit必须是整数1或2")
        return values


class AppPreferences(AppearancePreferences):
    reply_suggestions: bool = True
    reply_suggestion_count: int = Field(default=3, ge=1, le=6)


class PreferencesReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    changes: AppPreferences

    @model_validator(mode="after")
    def whitelist(self):
        if not self.changes.model_fields_set:
            raise ValueError("请选择要修改的普通偏好")
        return self


def preferences_snapshot(config):
    values = AppPreferences.model_validate({
        **config.ui_preferences,
        "reply_suggestions": config.reply_suggestions,
        "reply_suggestion_count": config.reply_suggestion_count,
    }).model_dump()
    initialized = bool(config.ui_preferences)
    revision = hashlib.sha256(json.dumps([initialized, values], sort_keys=True).encode()).hexdigest()
    return {"revision": revision, "initialized": initialized, "values": values}


class PreferencesChanged(ValueError):
    pass


class AppPreferencesService:
    """HTTP and native tools share CAS, persistence, and the live interaction owner."""

    def __init__(self, context):
        self.context = context

    def read(self):
        return preferences_snapshot(self.context.config)

    async def update(self, revision, changes, *, host=None):
        from .settings import build_agent, persist_settings

        request = PreferencesUpdateRequest.model_validate({"revision": revision, "changes": changes})
        patch = request.changes.model_dump(exclude_unset=True)
        context = self.context
        async with context.manager._lock:
            if host is not None:
                state = context.repository.get(host.session_id)
                if (host.cancelled.is_set() or not state or not state.get("running")
                        or (state.get("active_turn") or {}).get("id") != host.turn_id):
                    raise ValueError("本轮已经结束，没有修改应用偏好")
            before = self.read()
            if before["revision"] != request.revision:
                raise PreferencesChanged("应用偏好已经更新，请读取当前版本后复核修改")
            values = AppPreferences.model_validate({**before["values"], **patch}).model_dump()
            appearance = {key: values[key] for key in AppearancePreferences.model_fields}
            appearance_values = appearance if any(key in AppearancePreferences.model_fields for key in patch) else dict(context.config.ui_preferences)
            candidate = replace(context.config, ui_preferences=appearance_values,
                                reply_suggestions=values["reply_suggestions"],
                                reply_suggestion_count=values["reply_suggestion_count"])
            interaction_changed = any(key in patch for key in ("reply_suggestions", "reply_suggestion_count"))
            replacement, registry = (build_agent(candidate, context.retrieval, memory=context.memory,
                                                 cookbook=context.cookbook, journal=context.journal,
                                                 preferences=self) if interaction_changed
                                     else (context.agent, context.components))
            persist_settings(candidate)
            context.config = candidate
            context.agent, context.components = replacement, registry
            context.app.state.config = candidate
            context.app.state.components = registry
            context.manager.agent = replacement
            context.manager.reply_suggestions = candidate.reply_suggestions
            context.manager.reply_suggestion_count = candidate.reply_suggestion_count
            # The currently awaited arun retains its binding; future turns use the new reply policy.
            changed = {key: value for key, value in patch.items() if before["values"][key] != value}
            return {"status": "ok", "changed": changed, "preferences": self.read()}


def make_preferences_tools(service):
    from chefmate_agent import InjectedToolSpec
    from .interactions import ScopedWorkspace

    def read(values, context):
        PreferencesReadRequest.model_validate(values)
        return {"status": "ok", "preferences": service.read()}

    async def update(values, context):
        request = PreferencesUpdateRequest.model_validate(values)
        host = context.host_context
        if not isinstance(host, ScopedWorkspace) or host.binding is not service.context.workspace:
            return {"status": "session_required", "message": "请在当前对话中修改普通应用偏好。"}
        try:
            return await service.update(request.revision, request.changes, host=host)
        except PreferencesChanged:
            return {"status": "preferences_changed", "preferences": service.read(),
                    "message": "偏好已在其他页面更新；没有覆盖，请读取当前值并保留未提交的修改。"}
        except ValueError:
            return {"status": "turn_closed", "message": "本轮已经结束，没有修改偏好。"}
        except OSError:
            return {"status": "save_failed", "message": "应用偏好尚未保存，请稍后重试。"}

    return [
        InjectedToolSpec("get_app_preferences", "只读普通应用偏好：主题、配色、字体、字号、密度、内联数量、估算标识和推荐回复；不返回模型或开发者配置。",
                         PreferencesReadRequest, read, required_skill="app-preferences"),
        InjectedToolSpec("update_app_preferences", "按用户本次明确要求修改普通应用偏好。先读取revision，changes仅传实际修改的白名单字段；不支持任何模型、开发者、系统、密钥设置。",
                         PreferencesUpdateRequest, update, required_skill="app-preferences", effect="write",
                         side_effects=("保存白名单普通应用偏好并同步工作台",)),
    ]
