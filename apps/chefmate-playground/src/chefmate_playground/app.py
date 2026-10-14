"""Same-origin local web app with read-only recipe tools and persisted chat sessions."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from chefmate_workflow import UISnapshot, UserActionRequest

from .config import APP_ROOT, PlaygroundConfig
from .interactions import WorkspaceBinding
from .runtime import TurnConflict, TurnManager, encode_event
from .sessions import SessionRepository, public_session
from .settings import (SKILLS, ContextSettings, RuntimeSettings, build_agent, persist_settings, probe_model,
                       public_settings, resolve_model)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str
    section: str
    values: dict


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=8000)
    retry: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    ui_snapshot: UISnapshot | None = None

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("请输入消息")
        return value.strip()


def create_app(config: PlaygroundConfig | None = None, *, service=None, agent=None,
               repository: SessionRepository | None = None) -> FastAPI:
    settings = config or PlaygroundConfig.load()
    setup_error = None
    repo = repository or SessionRepository(settings.sessions_database)
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
    workspace_binding = WorkspaceBinding(repo, service) if service is not None else None
    skill_registry = None
    try:
        configured_agent, skill_registry = build_agent(settings, service)
        agent = agent if agent is not None else configured_agent
    except (ValueError, OSError):
        setup_error = "模型或厨房技能配置无效，请打开设置检查。"
    manager = TurnManager(repo, agent, workspace_binding)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await manager.close()

    app = FastAPI(title="ChefMate Playground", lifespan=lifespan)
    app.state.config = settings
    app.state.manager = manager
    app.state.service = service
    app.state.workspace = workspace_binding
    app.state.skills = skill_registry

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
        return public_settings(settings)

    @app.post("/api/settings/test-model")
    async def test_model(values: dict):
        try:
            candidate = resolve_model(settings, values)
        except ValueError:
            raise HTTPException(422, "请填写有效的端点、模型和专用 API Key；换供应商不能沿用旧密钥。") from None
        return await probe_model(candidate)

    @app.patch("/api/settings")
    async def update_settings(body: SettingsRequest):
        nonlocal settings, agent, skill_registry, setup_error
        async with manager._lock:
            if manager.active:
                raise HTTPException(409, "还有对话正在运行，请等它完成后再保存设置。")
            if body.revision != public_settings(settings)["revision"]:
                raise HTTPException(409, "设置已在另一页面更新；当前草稿保留，请重新读取后复核。")
            try:
                if body.section == "model":
                    candidate = resolve_model(settings, body.values)
                elif body.section == "runtime":
                    candidate = replace(settings, **RuntimeSettings.model_validate(body.values).model_dump())
                elif body.section == "context":
                    candidate = replace(settings, **ContextSettings.model_validate(body.values).model_dump())
                elif body.section == "skills":
                    names = body.values.get("enabled_skills")
                    if (set(body.values) != {"enabled_skills"} or not isinstance(names, list)
                            or any(name not in SKILLS for name in names)):
                        raise ValueError("无效技能")
                    candidate = replace(settings, enabled_skills=tuple(dict.fromkeys(names)))
                else:
                    raise ValueError("无效设置分类")
                replacement, registry = build_agent(candidate, service)
                persist_settings(candidate)
            except ValueError:
                raise HTTPException(422, "设置值无效；更换供应商时必须填写专用密钥。") from None
            except OSError:
                raise HTTPException(503, "设置未保存，请检查本地配置目录。") from None
            settings, agent, skill_registry = candidate, replacement, registry
            manager.agent = agent
            app.state.config, app.state.skills = settings, skill_registry
            setup_error = None
            return public_settings(settings)

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
            "active_sessions": len(manager.active), "error": setup_error,
        }

    @app.get("/api/sessions")
    async def sessions():
        return {"items": [{key: value for key, value in public_session(state).items()
                           if key in {"id", "title", "updated_at", "running", "last_status", "conversation_state"}}
                          for state in repo.list()]}

    @app.post("/api/sessions")
    async def new_session():
        return public_session(repo.create())

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        return public_session(require_session(session_id))

    @app.get("/api/skills")
    async def skills():
        return {"items": skill_registry.list_metadata() if skill_registry is not None else []}

    @app.get("/api/sessions/{session_id}/workspace")
    async def workspace(session_id: str):
        require_session(session_id)
        if workspace_binding is None:
            raise HTTPException(503, "厨房任务尚未连接菜谱库。")
        try:
            return await asyncio.to_thread(workspace_binding.service(session_id).get_workspace)
        except Exception:
            raise HTTPException(503, "暂时无法读取任务状态。") from None

    @app.post("/api/sessions/{session_id}/actions")
    async def ui_action(session_id: str, body: UserActionRequest):
        require_session(session_id)
        if body.root.type == "change_stage":
            raise HTTPException(403, "会话阶段由助手根据你的需求管理，请通过对话告诉助手下一步想做什么。")
        if workspace_binding is None:
            raise HTTPException(503, "厨房任务尚未连接菜谱库。")
        # Keep partial-form field presence; Pydantic defaults must not overwrite untouched values.
        try:
            return await asyncio.to_thread(workspace_binding.service(session_id).apply_action, body)
        except ValueError:
            raise HTTPException(422, "这项界面操作无法执行，请检查输入。") from None
        except Exception:
            raise HTTPException(503, "操作尚未保存，请刷新任务状态后重试。") from None

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        require_session(session_id)
        if session_id in manager.active:
            raise HTTPException(409, "请先停止这段对话。")
        repo.delete(session_id)
        return {"deleted": True}

    @app.post("/api/sessions/{session_id}/turn")
    async def turn(session_id: str, body: TurnRequest):
        require_session(session_id)
        if agent is None:
            raise HTTPException(503, "智能体尚未配置，请检查应用 .env 中的数据库、模型和 API Key。")
        try:
            active, replay = await manager.begin(session_id, body.message, retry=body.retry, request_id=body.request_id,
                                                 ui_snapshot=body.ui_snapshot.model_dump(mode="json")
                                                 if body.ui_snapshot is not None else None)
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
