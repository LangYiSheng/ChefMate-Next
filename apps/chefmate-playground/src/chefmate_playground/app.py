"""Application assembly, lifecycle, and local HTTP boundary.

Route groups own HTTP translation. Repositories and agent/workflow packages own
business behavior; create_app keeps the injection point used by tests and hosts.
"""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from chefmate_workflow import CookingSettings

from .app_context import AppContext
from .config import APP_ROOT, PlaygroundConfig
from .cookbook import CookbookRepository, PersonalRecipeRetrieval
from .images import SessionImages
from .frontend import mount_frontend
from .interactions import WorkspaceBinding
from .journal import JournalRepository
from .memory import MemoryRepository
from .routes import mount_routes
from .runtime import TurnManager
from .sessions import SessionRepository
from .settings import build_agent
from .versioning import current_version


def create_app(
    config: PlaygroundConfig | None = None, *, service=None, agent=None, repository: SessionRepository | None = None
) -> FastAPI:
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

    @asynccontextmanager
    async def lifespan(running_app):
        yield
        await running_app.state.manager.close()

    app = FastAPI(title="ChefMate Playground", version=current_version(APP_ROOT), lifespan=lifespan)
    context = AppContext(app, settings, repo, cookbook, journal, memory, retrieval=service)
    workspace_binding = (
        WorkspaceBinding(
            repo,
            service,
            cooking_settings=lambda: {key: getattr(context.config, key) for key in CookingSettings.model_fields},
            journal=journal,
            intake_enabled=lambda: "food-journal" in context.config.enabled_components,
        )
        if service is not None
        else None
    )
    component_registry = None
    try:
        configured_agent, component_registry = build_agent(
            settings, service, memory=memory, cookbook=cookbook, journal=journal
        )
        agent = agent if agent is not None else configured_agent
    except (ValueError, OSError):
        setup_error = "模型或系统组件配置无效，请打开设置检查。"
    images = SessionImages(settings.sessions_database.parent / "images", repo)
    manager = TurnManager(
        repo,
        agent,
        workspace_binding,
        images,
        reply_suggestions=settings.reply_suggestions,
        reply_suggestion_count=settings.reply_suggestion_count,
        debug_secrets=(settings.api_key, settings.database_url),
        memory=memory,
        memory_enabled=lambda: "personal-memory" in context.config.enabled_components,
    )

    app.state.config = settings
    app.state.manager = manager
    app.state.usage = manager.usage
    app.state.service = service
    app.state.workspace = workspace_binding
    app.state.components = component_registry
    app.state.cookbook, app.state.journal, app.state.memory = cookbook, journal, memory

    context.app, context.agent, context.components = app, agent, component_registry
    context.setup_error, context.workspace = setup_error, workspace_binding
    context.images, context.manager = images, manager
    app.state.context = context

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

    mount_routes(app, context)

    mount_frontend(app, APP_ROOT / "web")
    return app
