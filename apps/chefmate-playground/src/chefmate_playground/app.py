"""Same-origin local web app with read-only recipe tools and persisted chat sessions."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from chefmate_workflow import UIAction, UISnapshot, UserActionRequest

from .config import APP_ROOT, PlaygroundConfig
from .interactions import WorkspaceBinding, make_tool_specs
from .runtime import TurnConflict, TurnManager, encode_event
from .sessions import SessionRepository, public_session


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
        from chefmate_agent import SkillRegistry
        skill_root = (APP_ROOT / "skills").resolve(strict=True)
        skill_directories = [(skill_root / name).resolve(strict=True) for name in settings.enabled_skills]
        if any(path.parent != skill_root for path in skill_directories):
            raise ValueError("Kitchen skill escaped its enabled directory")
        skill_registry = SkillRegistry(skill_directories)
    except (ValueError, OSError):
        setup_error = "厨房 Skills 配置无效，请检查启用的技能目录。"
    if agent is None and service is not None and settings.model_configured and skill_registry is not None:
        try:
            from chefmate_agent import AgentConfig, ChefMateAgent

            agent = ChefMateAgent(service, AgentConfig(
                model=settings.model, api_key=settings.api_key, base_url=settings.base_url,
                turn_timeout_s=settings.turn_timeout_s, max_model_rounds=settings.max_model_rounds,
                max_tool_calls=settings.max_tool_calls,
                model_timeout_s=settings.model_timeout_s, max_model_retries=settings.model_max_retries,
                stream_tool_calls=settings.stream_tool_calls,
            ), extra_tools=make_tool_specs(), ui_context_model=UISnapshot, action_model=UIAction,
                skills=skill_registry)
        except (ValueError, OSError):
            setup_error = "模型配置无效，请检查应用的 .env。"
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
        return {"items": [{key: value for key, value in state.items()
                           if key in {"id", "title", "updated_at", "running", "last_status"}}
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

    app.mount("/static", StaticFiles(directory=APP_ROOT / "static", check_dir=False), name="static")
    return app
