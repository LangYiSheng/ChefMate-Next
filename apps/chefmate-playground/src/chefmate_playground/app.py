"""Same-origin local web app with read-only recipe tools and persisted chat sessions."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from chefmate_workflow import CookingSettings, UISnapshot, UserActionRequest, WorkspaceState

from .config import APP_ROOT, PlaygroundConfig
from .interactions import WorkspaceBinding
from .images import ImageError, MAX_UPLOAD_BYTES, SessionImages
from .debug import redact_debug
from .runtime import TurnConflict, TurnManager, encode_event
from .sessions import SessionRepository, public_session
from .cookbook import CookbookRepository, PersonalRecipeRetrieval, RecipeDraftRequest
from .journal import JournalRepository
from .memory import MemoryRepository, MemoryConflict
from .components import DEFAULT_COMPONENTS
from .settings import (ContextSettings, InteractionSettings, RuntimeSettings, build_agent, persist_settings, probe_model,
                       public_settings, resolve_model)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str
    section: str
    values: dict


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(default="", max_length=8000)
    images: list[str] = Field(default_factory=list, max_length=12)
    retry: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    ui_snapshot: UISnapshot | None = None

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def content_present(self):
        if not self.message and not self.images:
            raise ValueError("请输入消息或上传图片")
        if len(set(self.images)) != len(self.images):
            raise ValueError("图片不能重复")
        return self


def create_app(config: PlaygroundConfig | None = None, *, service=None, agent=None,
               repository: SessionRepository | None = None) -> FastAPI:
    settings = config or PlaygroundConfig.load()
    setup_error = None
    repo = repository or SessionRepository(settings.sessions_database)
    cookbook = CookbookRepository(settings.sessions_database)
    journal = JournalRepository(settings.sessions_database)
    memory = MemoryRepository(settings.sessions_database, journal=journal)
    if service is None and settings.database_url:
        try:
            from chefmate_food_data import LocalFoodData
            from chefmate_retrieval import RetrievalService
            from chefmate_storage import RecipeStore

            store = RecipeStore(settings.database_url, schema=settings.database_schema)
            foods = LocalFoodData(settings.usda_database) if settings.usda_database else None
            service = RetrievalService(store, foods=foods)
        except (ValueError, OSError):
            setup_error = "数据库或本地 USDA 配置无效。"
    if service is not None:
        service = PersonalRecipeRetrieval(service, cookbook)
    workspace_binding = WorkspaceBinding(repo, service, cooking_settings=lambda: {
        key: getattr(settings, key) for key in CookingSettings.model_fields}, journal=journal) if service is not None else None
    component_registry = None
    try:
        configured_agent, component_registry = build_agent(settings, service, memory=memory, cookbook=cookbook, journal=journal)
        agent = agent if agent is not None else configured_agent
    except (ValueError, OSError):
        setup_error = "模型或系统组件配置无效，请打开设置检查。"
    images = SessionImages(settings.sessions_database.parent / "images", repo)
    manager = TurnManager(repo, agent, workspace_binding, images, reply_suggestions=settings.reply_suggestions,
                          debug_secrets=(settings.api_key, settings.database_url), memory=memory,
                          memory_enabled=lambda: "personal-memory" in settings.enabled_components)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await manager.close()

    app = FastAPI(title="ChefMate Playground", lifespan=lifespan)
    app.state.config = settings
    app.state.manager = manager
    app.state.usage = manager.usage
    app.state.service = service
    app.state.workspace = workspace_binding
    app.state.components = component_registry
    app.state.cookbook, app.state.journal, app.state.memory = cookbook, journal, memory

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/api/settings"):
            return JSONResponse({"detail": "设置请求无效，请检查字段和配置版本。"}, status_code=422)
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        try:
            hostname = urlsplit("http://" + request.headers.get("host", "")).hostname
        except ValueError:
            hostname = None
        if hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "仅通过本机地址访问工作台。"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host") or parsed.scheme not in {"http", "https"}:
                return JSONResponse({"detail": "仅接受同源页面请求。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_session(session_id: str):
        state = repo.get(session_id)
        if state is None:
            raise HTTPException(404, "对话不存在。")
        return state

    @app.get("/api/settings")
    async def get_settings():
        return public_settings(settings, components=component_registry)

    @app.post("/api/settings/test-model")
    async def test_model(values: dict):
        try:
            candidate = resolve_model(settings, values)
        except ValueError:
            raise HTTPException(422, "请填写有效的端点、模型和专用 API Key；换供应商不能沿用旧密钥。") from None
        return await probe_model(candidate)

    @app.patch("/api/settings")
    async def update_settings(body: SettingsRequest):
        nonlocal settings, agent, component_registry, setup_error
        async with manager._lock:
            if manager.active and body.section != "cooking":
                raise HTTPException(409, "还有对话正在运行，请等它完成后再保存设置。")
            if body.revision != public_settings(settings, components=component_registry)["revision"]:
                raise HTTPException(409, "设置已在另一页面更新；当前草稿保留，请重新读取后复核。")
            try:
                if body.section == "model":
                    candidate = resolve_model(settings, body.values)
                elif body.section == "runtime":
                    candidate = replace(settings, **RuntimeSettings.model_validate(body.values).model_dump())
                elif body.section == "context":
                    candidate = replace(settings, **ContextSettings.model_validate(body.values).model_dump())
                elif body.section == "interaction":
                    candidate = replace(settings, **InteractionSettings.model_validate(body.values).model_dump())
                elif body.section == "cooking":
                    candidate = replace(settings, **CookingSettings.model_validate(body.values).model_dump())
                elif body.section == "components":
                    names = body.values.get("enabled_components")
                    if (set(body.values) != {"enabled_components"} or not isinstance(names, list)
                            or any(not isinstance(name, str) or name not in DEFAULT_COMPONENTS for name in names)
                            or len(names) != len(set(names))):
                        raise ValueError("无效系统组件")
                    candidate = replace(settings, enabled_components=tuple(names))
                else:
                    raise ValueError("无效设置分类")
                replacement, registry = (agent, component_registry) if body.section == "cooking" else build_agent(candidate, service, memory=memory, cookbook=cookbook, journal=journal)
                persist_settings(candidate)
            except ValueError:
                raise HTTPException(422, "设置值无效；更换供应商时必须填写专用密钥。") from None
            except OSError:
                raise HTTPException(503, "设置未保存，请检查本地配置目录。") from None
            settings, agent, component_registry = candidate, replacement, registry
            manager.agent = agent
            manager.reply_suggestions = settings.reply_suggestions
            manager.debug_secrets = (settings.api_key, settings.database_url)
            app.state.config, app.state.components = settings, component_registry
            if body.section != "cooking":
                setup_error = None
            return public_settings(settings, components=component_registry)

    @app.get("/api/status")
    async def status():
        database = {"ready": False, "recipe_count": 0}
        if service is not None:
            try:
                options = await asyncio.to_thread(service.get_search_options)
                if options.get("status", "ok") == "ok":
                    database.update(ready=True, data_version=(options.get("snapshot") or {}).get("data_version"))
                    if getattr(service, "store", None) is not None:
                        count = await asyncio.to_thread(service.store.search, limit=1)
                        database["recipe_count"] = count["total"]
                    else:
                        database["recipe_count"] = options.get("recipe_count", 0)
            except Exception:
                database["error"] = "数据库暂时不可用，请检查连接。"
        else:
            database["error"] = setup_error or "请先配置 CHEFMATE_DATABASE_URL。"
        return {
            "ready": bool(database["ready"] and agent is not None), "configured": agent is not None,
            "model": settings.model or None, "database": database,
            "supports_images": settings.supports_images,
            "max_images": settings.max_images,
            "reply_suggestions": settings.reply_suggestions,
            "active_sessions": len(manager.active), "error": setup_error,
            "enabled_components": list(settings.enabled_components),
        }

    @app.get("/api/sessions")
    async def sessions():
        return {"items": [{key: value for key, value in public_session(state).items()
                           if key in {"id", "title", "updated_at", "running", "last_status", "conversation_state", "awaiting_user", "pending_question"}}
                          for state in repo.list() if state.get("messages")]}

    @app.get("/api/timer-sessions")
    async def timer_sessions():
        """Discover background reminders without activating or mutating a chat.

        Finished timer acknowledgements belong to the browser. Include recently
        touched finished timers so a refreshed page can apply its saved ack set;
        updated_at limits discovery only and is not presented as an expiry time.
        """
        recent_since = datetime.now(timezone.utc) - timedelta(hours=24)
        items = []
        for state in await asyncio.to_thread(repo.list):
            try:
                workspace = WorkspaceState.model_validate(state.get("workspace") or {})
            except (ValueError, TypeError):
                continue
            has_active = any(timer.status in {"running", "paused"} for timer in workspace.timers)
            has_recent_finished = False
            if any(timer.status == "finished" for timer in workspace.timers):
                try:
                    updated = datetime.fromisoformat(state["updated_at"])
                    has_recent_finished = updated.tzinfo is not None and updated >= recent_since
                except (KeyError, ValueError, TypeError):
                    pass
            if has_active or has_recent_finished:
                items.append({"id": state["id"], "title": state.get("title") or "厨房计时"})
        return {"items": items}

    @app.get("/api/memory")
    async def get_memory():
        return memory.snapshot()

    @app.patch("/api/memory")
    async def update_memory(values: dict):
        if (set(values) - {"user_notes", "revision", "enabled"} or type(values.get("revision")) is not int
                or not isinstance(values.get("user_notes"), str)
                or "enabled" in values and type(values["enabled"]) is not bool):
            raise HTTPException(422, "记忆设置无效。")
        try:
            return memory.update_user(values["user_notes"], values["revision"], enabled=values.get("enabled"))
        except MemoryConflict:
            raise HTTPException(409, "记忆已更新，你的草稿仍保留；请重新读取并核对。") from None
        except ValueError:
            raise HTTPException(422, "记忆内容过长或无效。") from None

    @app.delete("/api/memory")
    async def clear_memory(revision: int = Query(ge=0)):
        try:
            return memory.clear(revision)
        except MemoryConflict:
            raise HTTPException(409, "记忆已更新，请重新读取后再清空。") from None

    @app.get("/api/journal")
    async def food_journal(days: int = Query(default=7, ge=1, le=366), date: str | None = None):
        try:
            if workspace_binding:
                for state in repo.list():
                    if (state.get("workspace") or {}).get("meal_id"):
                        await asyncio.to_thread(workspace_binding.record_journal, state["id"])
            return {"summary": journal.summary(days=days), "entries": journal.entries(date, days=days),
                    "food_logs": journal.food_logs.list(status="saved", days=days),
                    "drafts": journal.food_logs.list(status="draft", days=days),
                    "deleted_food_logs": journal.food_logs.list(status="deleted", days=days)}
        except ValueError:
            raise HTTPException(422, "请选择有效日期。") from None

    @app.get("/api/usage")
    async def usage_statistics(days: str = "30"):
        try:
            if days != "all" and (not days.isdigit() or not 1 <= int(days) <= 366):
                raise ValueError("无效统计范围")
            return manager.usage.summary(days=None if days == "all" else int(days))
        except ValueError:
            raise HTTPException(422, "请选择有效统计范围。") from None

    @app.get("/api/about")
    async def about():
        import tomllib
        version = tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        current_status = await status()
        return {"name": "ChefMate", "subtitle": "您的个性化烹饪助手", "version": version,
                "runtime": "本地工作台", "recipe_count": current_status["database"]["recipe_count"],
                "component_count": len(component_registry.metadata()) if component_registry else 0}

    @app.get("/api/food-logs")
    async def food_logs(status: str = Query(default="saved", pattern="^(draft|saved|all|deleted)$"),
                        days: int = Query(default=30, ge=1, le=366), offset: int = Query(default=0, ge=0, le=100000),
                        limit: int = Query(default=100, ge=1, le=100), query: str = Query(default="", max_length=200)):
        return journal.food_logs.list(status=status, days=days, limit=limit, offset=offset, query=query)

    @app.get("/api/food-logs/{identity}")
    async def food_log(identity: str):
        record = journal.food_logs.get(identity)
        if record is None:
            raise HTTPException(404, "饮食记录不存在。")
        return record

    @app.put("/api/food-logs/{identity}")
    async def edit_food_log(identity: str, values: dict):
        if set(values) != {"revision", "record"} or type(values["revision"]) is not int or not isinstance(values["record"], dict):
            raise HTTPException(422, "饮食草稿字段无效。")
        try:
            return journal.food_logs.update_draft(identity, values["revision"], values["record"])
        except KeyError:
            raise HTTPException(404, "饮食草稿不存在。") from None
        except ValueError as error:
            if "版本" in str(error) or "已更新" in str(error):
                raise HTTPException(409, "草稿已更新，请重新读取后核对。") from None
            raise HTTPException(422, "请核对份量、营养依据与记录日期。") from None

    @app.post("/api/food-logs/{identity}/publish")
    async def publish_food_log(identity: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int or values["revision"] < 1:
            raise HTTPException(422, "请核对记录版本。")
        try:
            return journal.food_logs.publish(identity, values["revision"])
        except KeyError:
            raise HTTPException(404, "饮食草稿不存在。") from None
        except ValueError:
            raise HTTPException(409, "记录已更新，请刷新后核对。") from None

    @app.delete("/api/food-logs/{identity}")
    async def delete_food_log(identity: str, revision: int = Query(ge=1)):
        try:
            return journal.food_logs.delete(identity, revision)
        except KeyError:
            raise HTTPException(404, "饮食记录不存在。") from None
        except ValueError:
            raise HTTPException(409, "记录已更新，请刷新后核对。") from None

    @app.post("/api/food-logs/{identity}/restore")
    async def restore_food_log(identity: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int or values["revision"] < 1:
            raise HTTPException(422, "请核对记录版本。")
        try:
            return journal.food_logs.restore(identity, values["revision"])
        except KeyError:
            raise HTTPException(404, "饮食记录不存在。") from None
        except ValueError:
            raise HTTPException(409, "记录已更新，请刷新后核对。") from None

    @app.patch("/api/journal/{entry_id}")
    async def journal_feedback(entry_id: str, values: dict):
        if set(values) - {"rating", "notes"}:
            raise HTTPException(422, "反馈字段无效。")
        try:
            previous = journal.get(entry_id)
            if previous is None:
                raise KeyError(entry_id)
            return journal.feedback(entry_id, rating=values.get("rating", previous["rating"]), notes=values.get("notes", previous["notes"]))
        except KeyError:
            raise HTTPException(404, "饮食记录不存在。") from None
        except ValueError:
            raise HTTPException(422, "请填写有效的评分和笔记。") from None

    @app.get("/api/cookbook")
    async def recipe_center(query: str = Query(default="", max_length=200),
                            source: str = Query(default="all", pattern="^(all|user|favorites|drafts|deleted)$"),
                            offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=12, ge=1, le=30)):
        favorite_ids = cookbook.favorites()
        personal = cookbook.list(query=query, status="draft" if source == "drafts" else "saved", limit=100)
        def card(record):
            return {"recipe_id": record["recipe_id"], "name": record["document"]["name"], "origin": "user",
                    "status": record["status"], "revision": record["revision"],
                    **({key: record[key] for key in ("deleted_from", "deleted_at")} if record["status"] == "deleted" else {}),
                    "servings": record["document"]["servings"], "total_time_min": record["document"]["total_time_min"],
                    "description": record["document"]["description"], "favorite": record["recipe_id"] in favorite_ids}
        if source in {"user", "drafts", "deleted"}:
            status = {"user": "saved", "drafts": "draft", "deleted": "deleted"}[source]
            personal = cookbook.list(query=query, status=status, limit=limit, offset=offset)
            return {**personal, "items": [card(item) for item in personal["items"]]}
        if source == "favorites":
            items = []
            for identity in favorite_ids:
                result = await asyncio.to_thread(service.get_recipe, {"recipe_id": identity}) if service else cookbook.retrieve(identity)
                recipe = result.get("recipe")
                if result.get("status") == "ok" and recipe and (not query or query.casefold() in recipe["name"].casefold()):
                    if identity.startswith("custom-"):
                        record = cookbook.get(identity)
                        if record and record["status"] == "saved":
                            items.append(card(record))
                        continue
                    items.append({**{key: recipe.get(key) for key in ("recipe_id", "name", "servings", "total_time_min", "description")},
                                  "origin": recipe.get("origin", "system"), "favorite": True, "status": "saved"})
            return {"items": items[offset:offset+limit], "total": len(items), "offset": offset, "limit": limit}
        # Page personal records first, then the official catalog without copying its dataset.
        personal_count = personal["total"]
        personal_page = cookbook.list(query=query, limit=limit, offset=offset)["items"]
        remaining = limit - len(personal_page)
        official_offset = max(0, offset-personal_count)
        try:
            official = await asyncio.to_thread(service.search_recipes,
                {"name": query or None, "ready_for_recommendation": None, "limit": max(1, remaining), "offset": official_offset}) if service else {"items": [], "total": 0}
        except Exception:
            raise HTTPException(503, "菜谱库暂时无法读取，请稍后重试。") from None
        items = [card(item) for item in personal_page]
        items.extend({**{key: item.get(key) for key in ("recipe_id", "name", "servings", "total_time_min", "description")},
                      "origin": "system", "favorite": item["recipe_id"] in favorite_ids, "status": "saved"}
                     for item in official.get("items", [])[:remaining])
        return {"items": items, "total": personal_count+official.get("total", 0), "offset": offset, "limit": limit}

    @app.get("/api/cookbook/{recipe_id}")
    async def personal_recipe(recipe_id: str):
        record = cookbook.get(recipe_id)
        if record is None:
            raise HTTPException(404, "个人菜谱不存在。")
        return record

    @app.delete("/api/cookbook/{recipe_id}")
    async def delete_personal_recipe(recipe_id: str, revision: int = Query(ge=1)):
        try:
            return cookbook.delete(recipe_id, revision)
        except KeyError:
            raise HTTPException(404, "个人菜谱不存在，基础菜谱不能删除。") from None
        except ValueError:
            raise HTTPException(409, "菜谱版本已变化，请刷新后重新确认删除。") from None

    @app.post("/api/cookbook/{recipe_id}/restore")
    async def restore_personal_recipe(recipe_id: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int or values["revision"] < 1:
            raise HTTPException(422, "请核对菜谱版本。")
        try:
            return cookbook.restore(recipe_id, values["revision"])
        except KeyError:
            raise HTTPException(404, "个人菜谱不存在。") from None
        except ValueError:
            raise HTTPException(409, "菜谱状态已变化，请刷新后重试。") from None

    @app.put("/api/cookbook/{recipe_id}")
    async def edit_recipe_draft(recipe_id: str, values: dict):
        if set(values) != {"revision", "recipe"} or type(values["revision"]) is not int:
            raise HTTPException(422, "草稿请求无效。")
        try:
            return cookbook.update_draft(recipe_id, RecipeDraftRequest.model_validate(values["recipe"]), values["revision"])
        except ValueError:
            raise HTTPException(409, "草稿未保存，请检查填写内容和版本。") from None

    @app.post("/api/cookbook/{recipe_id}/publish")
    async def publish_recipe(recipe_id: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int:
            raise HTTPException(422, "请核对草稿版本。")
        try:
            return cookbook.publish(recipe_id, values["revision"])
        except KeyError:
            raise HTTPException(404, "草稿不存在。") from None
        except ValueError:
            raise HTTPException(409, "草稿已更新，请重新核对。") from None

    @app.put("/api/favorites/{recipe_id}")
    async def favorite_recipe(recipe_id: str, values: dict):
        if set(values) != {"favorite"} or type(values["favorite"]) is not bool:
            raise HTTPException(422, "收藏设置无效。")
        if values["favorite"]:
            result = await asyncio.to_thread(service.get_recipe, {"recipe_id": recipe_id}) if service else cookbook.retrieve(recipe_id)
            if result.get("status") != "ok":
                raise HTTPException(404, "菜谱不存在，不能收藏。")
        try:
            return cookbook.favorite(recipe_id, values["favorite"])
        except ValueError:
            raise HTTPException(409, "菜谱已删除或尚未保存，不能收藏。") from None

    @app.post("/api/sessions")
    async def new_session():
        return public_session(repo.create(), reply_suggestions=settings.reply_suggestions)

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        return public_session(require_session(session_id), reply_suggestions=settings.reply_suggestions)

    @app.get("/api/components")
    async def components():
        return {"items": component_registry.metadata() if component_registry is not None else []}

    def require_developer(request: Request):
        # An explicit local opt-in, not an authentication boundary.
        if request.headers.get("X-ChefMate-Developer") != "1":
            raise HTTPException(404, "请先开启开发者模式。")

    def catalog(snapshot=None):
        tools = agent.tool_catalog(snapshot or {}) if agent is not None and hasattr(agent, "tool_catalog") else []
        return redact_debug({"tools": tools, "components": component_registry.metadata() if component_registry else [],
                             "stage": (snapshot or {}).get("stage", "idea")}, (settings.api_key, settings.database_url))

    @app.get("/api/debug/catalog")
    async def developer_catalog(request: Request):
        require_developer(request)
        return catalog()

    @app.get("/api/sessions/{session_id}/debug")
    async def developer_session(session_id: str, request: Request, turn_id: str | None = None):
        require_developer(request)
        state = require_session(session_id)
        snapshot = workspace_binding.service(session_id).model_snapshot() if workspace_binding else {}
        runs = repo.debug_runs(session_id)
        chosen = turn_id or (runs[0]["turn_id"] if runs else None)
        capture = repo.debug_run(session_id, chosen) if chosen else None
        if turn_id and capture is None:
            raise HTTPException(404, "这轮没有留存的上下文记录。")
        # Legacy turns can show their public execution log, but are never labeled exact captures.
        logs = [{"id": message.get("turn_id", message.get("id")), "text": message.get("content", ""),
                 "created_at": message.get("created_at"), "tool_calls": message.get("tool_calls", [])}
                for message in state.get("messages", []) if message.get("role") == "assistant"]
        return redact_debug({**catalog(snapshot), "runs": runs, "capture": capture, "logs": logs,
                             "retained_turns": 20, "running": state.get("running", False)},
                            (settings.api_key, settings.database_url))

    @app.get("/api/sessions/{session_id}/workspace")
    async def workspace(session_id: str):
        require_session(session_id)
        if workspace_binding is None:
            raise HTTPException(503, "厨房任务尚未连接菜谱库。")
        try:
            return await asyncio.to_thread(workspace_binding.service(session_id).get_workspace)
        except Exception:
            raise HTTPException(503, "暂时无法读取任务状态。") from None

    @app.post("/api/sessions/{session_id}/images")
    async def upload_image(session_id: str, request: Request):
        require_session(session_id)
        if not settings.supports_images:
            raise HTTPException(422, "当前模型未开启图片能力，请在模型连接中配置。")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "图片过大，请压缩后再上传。")
        try:
            return await asyncio.to_thread(images.save, session_id, bytes(data), request.query_params.get("name", "照片.jpg"))
        except (ImageError, ValueError, KeyError):
            raise HTTPException(422, "图片未保存，请选择有效且尺寸合适的 JPG、PNG 或 WebP。") from None

    @app.get("/api/sessions/{session_id}/images/{image_id}")
    async def image(session_id: str, image_id: str):
        state = require_session(session_id)
        try:
            images.references(state, [image_id])
            path = images.path(session_id, image_id)
        except (ValueError, ImageError):
            raise HTTPException(404, "图片不存在。") from None
        return FileResponse(path, media_type="image/jpeg")

    @app.delete("/api/sessions/{session_id}/images/{image_id}")
    async def remove_image(session_id: str, image_id: str):
        require_session(session_id)
        try:
            await asyncio.to_thread(images.remove, session_id, image_id)
        except (ValueError, ImageError):
            raise HTTPException(409, "图片已经发送或已移除，请刷新当前会话。") from None
        return {"removed": True}

    @app.post("/api/sessions/{session_id}/actions")
    async def ui_action(session_id: str, body: UserActionRequest):
        require_session(session_id)
        if body.root.type == "change_stage":
            raise HTTPException(403, "会话阶段由助手根据你的需求管理，请通过对话告诉助手下一步想做什么。")
        if workspace_binding is None:
            raise HTTPException(503, "厨房任务尚未连接菜谱库。")
        # Keep partial-form field presence; Pydantic defaults must not overwrite untouched values.
        try:
            result = await asyncio.to_thread(workspace_binding.service(session_id).apply_action, body)
            await asyncio.to_thread(workspace_binding.record_journal, session_id)
            return result
        except ValueError:
            raise HTTPException(422, "这项界面操作无法执行，请检查输入。") from None
        except Exception:
            raise HTTPException(503, "操作尚未保存，请刷新任务状态后重试。") from None

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        state = require_session(session_id)
        if session_id in manager.active:
            raise HTTPException(409, "请先停止这段对话。")
        if workspace_binding and not await asyncio.to_thread(workspace_binding.record_journal, session_id):
            raise HTTPException(503, "饮食记录暂未同步，请稍后再删除这段对话。")
        removed = repo.delete(session_id)
        await asyncio.to_thread(images.remove_session_files, removed or state)
        return {"deleted": True}

    @app.post("/api/sessions/{session_id}/turn")
    async def turn(session_id: str, body: TurnRequest):
        require_session(session_id)
        if agent is None:
            raise HTTPException(503, "智能体尚未配置，请检查应用 .env 中的数据库、模型和 API Key。")
        if body.images and not settings.supports_images:
            raise HTTPException(422, "当前模型没有开启图片能力。")
        if len(body.images) > settings.max_images:
            raise HTTPException(422, f"当前模型每次请求最多接受 {settings.max_images} 张图片，请移除多余图片。")
        try:
            active, replay = await manager.begin(session_id, body.message, retry=body.retry, request_id=body.request_id,
                                                 ui_snapshot=body.ui_snapshot.model_dump(mode="json")
                                                 if body.ui_snapshot is not None else None, image_ids=body.images)
        except ImageError:
            raise HTTPException(422, "图片未上传或不属于当前会话，请重新上传。") from None
        except TurnConflict as error:
            raise HTTPException(409, str(error)) from None

        async def stream():
            if replay is not None:
                yield encode_event(replay)
                yield encode_event({"type": "done", "status": "completed"})
                return
            # Detaching a view only detaches SSE. The job finishes, persists, and can be reopened.
            while True:
                try:
                    event = await asyncio.wait_for(active.events.get(), timeout=10)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield encode_event(event)
                if event.get("type") == "done":
                    break

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel(session_id: str):
        require_session(session_id)
        return {"cancelled": await manager.cancel(session_id)}

    @app.get("/api/recipes/{recipe_id}")
    async def recipe(recipe_id: str):
        if service is None:
            raise HTTPException(503, "数据库尚未配置。")
        try:
            result = await asyncio.to_thread(service.get_recipe, {"recipe_id": recipe_id})
        except Exception:
            raise HTTPException(503, "暂时无法读取菜谱。") from None
        if result.get("status") == "not_found":
            raise HTTPException(404, "菜谱不存在或数据集已更新。")
        return result

    @app.post("/api/tools/{tool_name}")
    async def tool(tool_name: str, payload: dict):
        if service is None:
            raise HTTPException(503, "数据库尚未配置。")
        calls = {
            "find_ingredients": service.find_ingredients, "search_recipes": service.search_recipes,
            "get_recipe": service.get_recipe, "get_search_options": service.get_search_options,
        }
        function = calls.get(tool_name)
        if function is None:
            raise HTTPException(404, "工具不存在。")
        try:
            if tool_name == "get_search_options":
                if payload:
                    raise ValueError("这个工具不接受参数。")
                return await asyncio.to_thread(function)
            return await asyncio.to_thread(function, payload)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except Exception:
            raise HTTPException(503, "查询未完成，请检查数据库连接。") from None

    @app.get("/")
    async def index():
        return FileResponse(APP_ROOT / "static" / "index.html")

    @app.get("/settings")
    async def settings_page():
        return FileResponse(APP_ROOT / "static" / "settings.html")

    app.mount("/static", StaticFiles(directory=APP_ROOT / "static", check_dir=False), name="static")
    return app
