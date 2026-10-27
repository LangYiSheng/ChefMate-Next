"""System HTTP endpoints; domain work stays in application services."""

import asyncio
from dataclasses import replace
from fastapi import APIRouter, HTTPException
from chefmate_workflow import CookingSettings
from ..components import DEFAULT_COMPONENTS
from ..config import APP_ROOT
from ..settings import (
    ContextSettings,
    InteractionSettings,
    RuntimeSettings,
    build_agent,
    persist_settings,
    probe_model,
    public_settings,
    resolve_model,
)
from ..versioning import current_commit, current_version, release_history
from ..http_models import SettingsRequest
from ..app_context import AppContext
from ..ui_preferences import PreferencesUpdateRequest, PreferencesChanged


def build_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings")
    async def get_settings():
        return public_settings(context.config, components=context.components)

    @router.get("/api/ui-preferences")
    async def get_ui_preferences():
        return context.preferences.read()

    @router.patch("/api/ui-preferences")
    async def update_ui_preferences(body: PreferencesUpdateRequest):
        try:
            result = await context.preferences.update(body.revision, body.changes)
            return result["preferences"]
        except PreferencesChanged:
            raise HTTPException(409, {"message": "外观偏好已变化，请保留当前草稿并复核。",
                                      "preferences": context.preferences.read()}) from None
        except OSError:
            raise HTTPException(503, "应用偏好未保存，请检查本地配置目录。") from None

    @router.post("/api/settings/test-model")
    async def test_model(values: dict):
        try:
            candidate = resolve_model(context.config, values)
        except ValueError:
            raise HTTPException(422, "请填写有效的端点、模型和专用 API Key；换供应商不能沿用旧密钥。") from None
        return await probe_model(candidate)

    @router.patch("/api/settings")
    async def update_settings(body: SettingsRequest):
        async with context.manager._lock:
            if context.manager.active and body.section != "cooking":
                raise HTTPException(409, "还有对话正在运行，请等它完成后再保存设置。")
            if body.revision != public_settings(context.config, components=context.components)["revision"]:
                raise HTTPException(409, "设置已在另一页面更新；当前草稿保留，请重新读取后复核。")
            try:
                if body.section == "model":
                    candidate = resolve_model(context.config, body.values)
                elif body.section == "runtime":
                    candidate = replace(context.config, **RuntimeSettings.model_validate(body.values).model_dump())
                elif body.section == "context":
                    candidate = replace(context.config, **ContextSettings.model_validate(body.values).model_dump())
                elif body.section == "interaction":
                    candidate = replace(context.config, **InteractionSettings.model_validate(body.values).model_dump())
                elif body.section == "cooking":
                    candidate = replace(context.config, **CookingSettings.model_validate(body.values).model_dump())
                elif body.section == "components":
                    names = body.values.get("enabled_components")
                    if (
                        set(body.values) != {"enabled_components"}
                        or not isinstance(names, list)
                        or any(not isinstance(name, str) or name not in DEFAULT_COMPONENTS for name in names)
                        or len(names) != len(set(names))
                    ):
                        raise ValueError("无效系统组件")
                    candidate = replace(context.config, enabled_components=tuple(names))
                else:
                    raise ValueError("无效设置分类")
                replacement, registry = (
                    (context.agent, context.components)
                    if body.section == "cooking"
                    else build_agent(
                        candidate,
                        context.retrieval,
                        memory=context.memory,
                        cookbook=context.cookbook,
                        journal=context.journal,
                        preferences=context.preferences,
                    )
                )
                persist_settings(candidate)
            except ValueError:
                raise HTTPException(422, "设置值无效；更换供应商时必须填写专用密钥。") from None
            except OSError:
                raise HTTPException(503, "设置未保存，请检查本地配置目录。") from None
            context.config, context.agent, context.components = candidate, replacement, registry
            context.manager.agent = context.agent
            context.manager.reply_suggestions = context.config.reply_suggestions
            context.manager.reply_suggestion_count = context.config.reply_suggestion_count
            context.manager.debug_secrets = (context.config.api_key, context.config.database_url, context.config.app_database_url)
            context.app.state.config, context.app.state.components = context.config, context.components
            if body.section != "cooking":
                context.setup_error = None
            return public_settings(context.config, components=context.components)

    @router.get("/api/status")
    async def status():
        database = {"ready": False, "recipe_count": 0}
        if context.retrieval is not None:
            try:
                options = await asyncio.to_thread(context.retrieval.get_search_options)
                if options.get("status", "ok") == "ok":
                    database.update(ready=True, data_version=(options.get("snapshot") or {}).get("data_version"))
                    if getattr(context.retrieval, "store", None) is not None:
                        count = await asyncio.to_thread(context.retrieval.store.search, limit=1)
                        database["recipe_count"] = count["total"]
                    else:
                        database["recipe_count"] = options.get("recipe_count", 0)
            except Exception:
                database["error"] = "数据库暂时不可用，请检查连接。"
        else:
            database["error"] = context.setup_error or "请先配置 CHEFMATE_DATABASE_URL。"
        pricing_configured, billing_ready = True, True
        if context.manager.account_usage is not None:
            meter = context.manager.account_usage
            user = meter.accounts.get_user(meter.user_id)
            pricing_configured = bool(meter.accounts.pricing().get("configured"))
            billing_ready = bool(user and (user["role"] == "admin" or user["quota"]["cost_limit_micros"] is None or pricing_configured))
        return {
            "ready": bool(database["ready"] and context.agent is not None and billing_ready),
            "pricing_configured": pricing_configured,
            "billing_ready": billing_ready,
            "configured": context.agent is not None,
            "model_configured": context.config.model_configured,
            "personal_storage": "postgresql" if context.config.app_database_url else "sqlite",
            "model": context.config.model or None,
            "database": database,
            "supports_images": context.config.supports_images,
            "max_images": context.config.max_images,
            "reply_suggestions": context.config.reply_suggestions,
            "reply_suggestion_count": context.config.reply_suggestion_count,
            "active_sessions": len(context.manager.active),
            "error": context.setup_error,
            "enabled_components": list(context.config.enabled_components),
        }

    @router.get("/api/usage")
    async def usage_statistics(days: str = "30"):
        try:
            if days != "all" and (not days.isdigit() or not 1 <= int(days) <= 366):
                raise ValueError("无效统计范围")
            return context.manager.usage.summary(days=None if days == "all" else int(days))
        except ValueError:
            raise HTTPException(422, "请选择有效统计范围。") from None

    @router.get("/api/about")
    async def about():
        current_status = await status()
        return {
            "name": "ChefMate",
            "subtitle": "您的个性化烹饪助手",
            "version": current_version(APP_ROOT),
            "commit": await asyncio.to_thread(current_commit, APP_ROOT),
            "runtime": "账号工作台" if context.config.managed_user else "本地工作台",
            "recipe_count": current_status["database"]["recipe_count"],
            "component_count": len(context.components.metadata()) if context.components else 0,
        }

    @router.get("/api/changelog")
    async def changelog():
        return release_history(APP_ROOT)

    @router.get("/api/components")
    async def components():
        return {"items": context.components.metadata() if context.components is not None else []}

    return router
