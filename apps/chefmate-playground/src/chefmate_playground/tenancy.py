"""Authenticated ASGI boundary and isolated workspaces sharing one model policy.

The account app owns identity and administrative control. Every business API is
dispatched into a workspace constructed with that account's repositories. An
opaque conversation ID, a header or a query parameter can never select a user.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request as ASGIRequest

from .accounts import AccountError, AccountsRepository, COOKIE_NAME
from .config import APP_ROOT
from .database import Database
from .frontend import mount_frontend
from .http_errors import STORAGE_ERRORS, install_storage_handlers
from .routes.accounts import build_router
from .settings import PERSONAL_PERSISTED, load_overrides, public_settings
from .versioning import current_version


class ResetDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm_username: str = Field(min_length=3, max_length=32)


class WorkspacePool:
    def __init__(self, config, *, service=None, agent=None):
        from .app import create_workspace_app

        self.config, self.service, self.injected_agent = config, service, agent
        self.legacy_data_present = config.sessions_database.is_file()
        self.accounts = AccountsRepository(Database(config.sessions_database.parent / "accounts.db",
            url=config.app_database_url, schema=config.app_database_schema + "_accounts"),
            default_cost_limit_micros=config.new_user_cost_limit_micros)
        self.accounts.recover_reservations()
        if bool(config.admin_username) != bool(config.admin_password):
            raise ValueError("管理员初始化需要同时设置 CHEFMATE_ADMIN_USERNAME 和 CHEFMATE_ADMIN_PASSWORD。")
        if config.admin_username:
            self.accounts.bootstrap_admin(config.admin_username, config.admin_password)
        self.control = create_workspace_app(replace(config, auth_enabled=False,
            app_database_schema=config.app_database_schema + "_control"), service=service, agent=agent)
        self.workspaces = {}
        self.revisions = {}
        self.inflight = {}
        self.lock = asyncio.Lock()

    def revision(self):
        config = self.control.state.context.config
        values = {key: value for key, value in public_settings(config).items()
                  if key not in {"revision", "ui_preferences", "interaction", "cooking"}}
        # Include a private fingerprint: changing the secret alone must refresh
        # all user agents even though public_settings never includes that secret.
        import hashlib
        return hashlib.sha256((json.dumps(values, sort_keys=True) + config.api_key).encode()).hexdigest()

    async def workspace(self, user):
        from .app import create_workspace_app
        from .settings import build_agent

        user_id = user["id"]
        async with self.lock:
            revision = self.revision()
            app = self.workspaces.get(user_id)
            if app is None:
                base = self.control.state.context.config
                config = load_overrides(replace(base, auth_enabled=False, managed_user=True,
                    sessions_database=self.accounts.tenant_database(user_id), ui_preferences={},
                    app_database_schema=self.config.app_database_schema + "_user_" + user_id))
                profile = user.get("profile") or {}
                if not config.ui_preferences and profile.get("theme"):
                    config = replace(config, ui_preferences={"theme": profile["theme"]})
                # Bounded resident cache; persisted content is not evicted.
                if len(self.workspaces) >= 64:
                    idle = next((key for key, value in self.workspaces.items()
                                 if not value.state.manager.active and not self.inflight.get(key)), None)
                    if idle is not None:
                        old = self.workspaces.pop(idle)
                        self.revisions.pop(idle, None)
                        await old.state.manager.close()
                    else:
                        raise HTTPException(503, "当前工作区繁忙，请稍后重试。")
                app = create_workspace_app(config, service=self.service, agent=self.injected_agent)
                app.state.manager.account_usage = AccountUsage(self.accounts, user_id, app.state.context)
                self.workspaces[user_id] = app
                self.revisions[user_id] = revision
            elif self.revisions[user_id] != revision:
                context = app.state.context
                async with context.manager._lock:
                    # A global model update is refused while any turn is active.
                    # This fallback also keeps a running turn's bindings stable.
                    if not context.manager.active:
                        personal = {key: getattr(context.config, key) for key in PERSONAL_PERSISTED}
                        config = replace(self.control.state.context.config, **personal,
                            auth_enabled=False, managed_user=True, sessions_database=context.config.sessions_database,
                            app_database_schema=context.config.app_database_schema)
                        replacement, registry = (self.injected_agent, context.components) if self.injected_agent else build_agent(
                            config, context.retrieval, memory=context.memory, cookbook=context.cookbook,
                            journal=context.journal, preferences=context.preferences)
                        context.config, context.agent, context.components = config, replacement, registry
                        context.manager.agent = replacement
                        context.manager.debug_secrets = (config.api_key, config.database_url, config.app_database_url)
                        context.setup_error = None
                        app.state.config, app.state.components = config, registry
                        self.revisions[user_id] = revision
            profile = user.get("profile") or {}
            kitchen_profile = ({"display_name": user["display_name"], **{
                key: profile[key] for key in ("cooking_level", "household_size", "preferences") if key in profile}}
                if profile.get("use_for_ai") is True else {})
            app.state.memory.sync_kitchen_profile(kitchen_profile, source_revision=profile.get("revision"))
            self.inflight[user_id] = self.inflight.get(user_id, 0) + 1
            return app

    def busy(self):
        return any(self.inflight.values()) or any(app.state.manager.active for app in self.workspaces.values())

    async def close(self):
        await asyncio.gather(self.control.state.manager.close(),
                             *(app.state.manager.close() for app in self.workspaces.values()))

    def data_overview(self):
        items = []
        for user in self.accounts.list_users():
            path = self.accounts.tenant_database(user["id"])
            counts = {"sessions": 0, "recipes": 0, "journal": 0, "memory": 0}
            database = Database(path, url=self.config.app_database_url,
                                schema=self.config.app_database_schema + "_user_" + user["id"])
            if self.config.app_database_url or path.is_file():
                with database.connect() as db:
                    tables = set(database.tables(connection=db))
                    for label, table in (("sessions", "sessions"), ("recipes", "personal_recipes"),
                                         ("journal", "food_journal"), ("memory", "personal_memory")):
                        if table in tables:
                            counts[label] = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            size = sum(file.stat().st_size for file in path.parent.rglob("*") if file.is_file()) if path.parent.is_dir() else 0
            active = bool(self.inflight.get(user["id"]) or self.workspaces.get(user["id"]) and self.workspaces[user["id"]].state.manager.active)
            items.append({"user_id": user["id"], "username": user["username"], "counts": counts,
                          "bytes": size, "active": active})
        return {"items": items, "legacy_data_present": self.legacy_data_present,
                "storage": "postgresql" if self.config.app_database_url else "sqlite",
                "notice": "重置前会归档个人数据；账号和用量账本保留。旧单用户数据不会自动归属新账号。"}

    async def reset_data(self, actor, user_id, confirm_username):
        user = self.accounts.get_user(user_id)
        if not user:
            raise HTTPException(404, "用户不存在。")
        if confirm_username != user["username"]:
            raise HTTPException(422, "请准确输入待重置用户的账号。")
        async with self.lock:
            app = self.workspaces.get(user_id)
            if self.inflight.get(user_id) or app and app.state.manager.active:
                raise HTTPException(409, "该用户仍有请求运行，请等待结束后再重置。")
            source = self.accounts.tenant_database(user_id).parent
            archive_name = user_id + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
            archive = self.config.sessions_database.parent / "archives" / archive_name
            if app:
                await app.state.manager.close()
            if self.config.app_database_url:
                import psycopg
                from psycopg import sql
                schema = self.config.app_database_schema + "_user_" + user_id
                archived_schema = "chefmate_archive_" + uuid4().hex
                archive.mkdir(parents=True, exist_ok=False)
                moved = False
                try:
                    with psycopg.connect(self.config.app_database_url) as db:
                        exists = db.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (schema,)).fetchone()
                        if exists:
                            db.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(sql.Identifier(schema), sql.Identifier(archived_schema)))
                        if source.is_dir():
                            await asyncio.to_thread(shutil.move, str(source), str(archive / "files"))
                            moved = True
                        (archive / "manifest.json").write_text(json.dumps({"user_id": user_id,
                            "original_schema": schema, "archived_schema": archived_schema if exists else None}, indent=2), encoding="utf-8")
                except Exception:
                    if moved:
                        await asyncio.to_thread(shutil.move, str(archive / "files"), str(source))
                    raise HTTPException(503, "归档未完成，个人数据保留，请检查存储后重试。") from None
            elif source.is_dir():
                archive.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.move, str(source), str(archive))
            if app:
                self.workspaces.pop(user_id, None)
                self.revisions.pop(user_id, None)
            with self.accounts._connect() as db:
                # Re-authentication also removes stale browser drafts and views
                # instead of showing cached records from before the reset.
                db.execute("DELETE FROM account_sessions WHERE user_id=?", (user_id,))
                self.accounts._audit(db, actor["id"], user_id, "archive_reset_data", ["personal_data"], self.accounts._time())
        return {"ok": True, "archive": archive_name, "user_id": user_id}


class AccountUsage:
    """Reserve before model entry and settle against admission-time prices."""
    def __init__(self, accounts, user_id, context):
        self.accounts, self.user_id, self.context = accounts, user_id, context
        self.bounds = {}

    def begin(self, turn):
        self.accounts.reserve_usage(self.user_id, turn.id)
        self.bounds[turn.id] = {"tokens": 0, "cost_micros": 0, "rounds": {}}

    def before_model(self, turn, measurement):
        from decimal import Decimal, ROUND_CEILING
        from chefmate_agent.boundary import _AgentFailure

        bound = self.bounds[turn.id]
        round_number = measurement.get("round", len(bound["rounds"]) + 1)
        if round_number in bound["rounds"]:
            return
        user = self.accounts.get_user(self.user_id)
        if not user or user["disabled"]:
            raise _AgentFailure("account_disabled", "账号已停用，本轮已停止。")
        if getattr(turn, "kitchen_profile", None):
            profile = user.get("profile") or {}
            latest = {"display_name": user["display_name"], **{key: profile[key] for key in
                ("cooking_level", "household_size", "preferences") if key in profile}}
            memory = self.context.memory.snapshot()
            if (profile.get("use_for_ai") is not True or not memory["enabled"]
                    or memory.get("kitchen_profile") != turn.kitchen_profile or latest != turn.kitchen_profile):
                raise _AgentFailure("profile_consent_changed", "厨房档案或授权已改变，本轮已停止；可按新设置重新发送。")
        # Byte-based conservative admission estimate includes a framing allowance;
        # provider tokenization/image billing still varies, so it is never
        # represented as measured usage or a guaranteed invoice ceiling.
        inputs = max(1, int(measurement.get("utf8_bytes", int(measurement.get("chars", 0)) * 4))) + 2048 + int(measurement.get("images", 0)) * 32768
        output = getattr(getattr(self.context.agent, "config", None), "max_output_tokens", 4096)
        retries = 1 + getattr(getattr(self.context.agent, "config", None), "max_model_retries", 0)
        prices = self.accounts.reservation_pricing(self.user_id, turn.id)
        single_cost = int((Decimal(inputs) * max(Decimal(prices["input_per_million"]), Decimal(prices["cached_input_per_million"]))
                    + Decimal(output) * Decimal(prices["output_per_million"])).to_integral_value(rounding=ROUND_CEILING))
        single_tokens = inputs + output
        cost, tokens = single_cost * retries, single_tokens * retries
        try:
            self.accounts.resize_usage(self.user_id, turn.id, tokens=bound["tokens"] + tokens,
                                       cost_micros=bound["cost_micros"] + cost)
        except AccountError as error:
            raise _AgentFailure("quota_exceeded", error.detail) from None
        bound["tokens"] += tokens
        bound["cost_micros"] += cost
        bound["rounds"][round_number] = {"tokens": tokens, "cost_micros": cost, "observed": False,
            "single_tokens": single_tokens, "single_cost_micros": single_cost, "max_attempts": retries, "attempts": None,
            "reported_attempts": None, "known_tokens": None, "known_cost_micros": None}

    def observe(self, turn, usage):
        """Replace a completed call's conservative bound with reported usage.

        Unknown calls retain their full bound. This releases unused headroom
        before later tool rounds without treating a missing report as zero.
        """
        bound = self.bounds.get(turn.id)
        if not bound:
            return
        prices = self.accounts.reservation_pricing(self.user_id, turn.id)
        for call in usage.get("calls", []):
            if not isinstance(call, dict) or call.get("round") not in bound["rounds"]:
                continue
            values, _, incomplete = self.accounts._usage({"calls": [call]})
            cost, cost_incomplete = self.accounts.calculate_cost(values, prices)
            current = bound["rounds"][call["round"]]
            reported_attempts = call.get("provider_attempts")
            if type(reported_attempts) is int and reported_attempts > 0:
                current["reported_attempts"] = max(current["reported_attempts"] or 0, reported_attempts)
            attempts = current["reported_attempts"] or current["max_attempts"]
            current["attempts"] = attempts
            if not incomplete and not cost_incomplete:
                current["known_tokens"] = max(current["known_tokens"] or 0, values["total_tokens"])
                current["known_cost_micros"] = max(current["known_cost_micros"] or 0, cost)
            if current["known_tokens"] is None:
                current.update(tokens=max(values["total_tokens"] or 0, current["single_tokens"] * attempts),
                    cost_micros=max(cost, current["single_cost_micros"] * attempts))
            else:
                # Only the final SDK response reports tokens. Actual preceding
                # retries keep their own unknown bounds; absent SDK attempt
                # metadata retains the configured upper bound transparently.
                current.update(tokens=current["known_tokens"] + current["single_tokens"] * (attempts - 1),
                    cost_micros=current["known_cost_micros"] + current["single_cost_micros"] * (attempts - 1), observed=True)
        bound["tokens"] = sum(item["tokens"] for item in bound["rounds"].values())
        bound["cost_micros"] = sum(item["cost_micros"] for item in bound["rounds"].values())
        try:
            self.accounts.resize_usage(self.user_id, turn.id, tokens=max(1, bound["tokens"]),
                cost_micros=bound["cost_micros"], observed=True)
        except AccountError:
            # Disabling an account or changing a cap cannot discard a response
            # that was already billed. Final settlement remains authoritative.
            pass

    def finish(self, turn, usage, *, model, status):
        bound = self.bounds.pop(turn.id, {})
        usage = dict(usage)
        if bound.get("rounds"):
            # A refused model round never enters the provider. Only admitted
            # requests can consume an unknown-usage fallback.
            usage["attempted_calls"] = sum(item["attempts"] or item["max_attempts"] for item in bound["rounds"].values())
        # An injected/offline agent can report usage without request telemetry.
        if not bound.get("rounds") and not usage.get("calls"):
            self.accounts.release_usage(self.user_id, turn.id)
            return
        self.accounts.settle_usage(self.user_id, turn.id, usage, session_id=turn.session_id, model=model, status=status)

    def abort(self, turn):
        self.bounds.pop(turn.id, None)
        self.accounts.release_usage(self.user_id, turn.id)


class TenantBoundary:
    def __init__(self, app, *, pool):
        self.app, self.pool = app, pool

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = ASGIRequest(scope, receive)
        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})(scope, receive, send)
        try:
            hostname = urlsplit("http://" + request.headers.get("host", "")).hostname
        except ValueError:
            hostname = None
        if hostname not in self.pool.config.allowed_hosts and "*" not in self.pool.config.allowed_hosts:
            return await reject(403, "访问地址未被允许，请检查 CHEFMATE_ALLOWED_HOSTS。")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            try:
                parsed = urlsplit(origin) if origin else None
            except ValueError:
                return await reject(403, "仅接受同源页面请求。")
            if (parsed and (parsed.netloc != request.headers.get("host") or parsed.scheme != scope.get("scheme", "http")
                            or parsed.path or parsed.query or parsed.fragment)) or request.headers.get("sec-fetch-site") == "cross-site":
                return await reject(403, "仅接受同源页面请求。")
        path = scope["path"]
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return await reject(400, "请求长度无效。")
        body_limit = 16384 if path.startswith("/api/auth/") else 10_000_000
        if length < 0 or length > body_limit:
            return await reject(413, "请求内容过大。")
        original_receive, received_bytes = receive, 0
        async def bounded_receive():
            nonlocal received_bytes
            message = await original_receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > body_limit:
                    raise HTTPException(413, "请求内容过大。")
            return message
        receive = bounded_receive
        request = ASGIRequest(scope, receive)
        user = None
        if path.startswith("/api/"):
            try:
                user = await asyncio.wait_for(asyncio.to_thread(
                    self.pool.accounts.authenticate_session, request.cookies.get(COOKIE_NAME)), timeout=3)
            except Exception:
                return await reject(503, "应用数据服务暂时不可用，请稍后重试。")
        scope.setdefault("state", {})["user"] = user
        expected = request.headers.get("x-chefmate-account")
        if (user is not None and expected and expected != user["id"] and path.startswith("/api/")
                and path not in {"/api/auth/me", "/api/auth/login", "/api/auth/register"}):
            return await reject(409, {"code": "account_changed", "message": "当前账号已经切换，请同步后重试。"})
        target = self.app
        leased_user = None
        if path.startswith("/api/") and not path.startswith("/api/auth/"):
            if user is None:
                return await reject(401, "请先登录 ChefMate。")
            admin = user["role"] == "admin"
            if path.startswith("/api/admin/"):
                if not admin:
                    return await reject(403, "需要管理员权限。")
            else:
                if not user["onboarding_completed"]:
                    return await reject(403, "请先完成欢迎引导。")
                developer_path = path.startswith(("/api/debug/", "/api/tools/")) or path.endswith("/debug")
                if developer_path and not (admin or user["developer_allowed"]):
                    return await reject(403, "该账号未获开发者模式权限。")
                if path == "/api/settings/test-model":
                    return await reject(403, "请在管理员平台配置模型。")
                if path == "/api/settings" and request.method == "PATCH":
                    try:
                        raw = await request.body()
                    except HTTPException as error:
                        return await reject(error.status_code, error.detail)
                    try:
                        body = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        return await reject(422, "设置请求无效。")
                    if not isinstance(body, dict) or body.get("section") not in {"cooking", "interaction"}:
                        return await reject(403, "系统配置仅可在管理员平台修改。")
                    original = receive
                    sent = False
                    async def replay():
                        nonlocal sent
                        if not sent:
                            sent = True
                            return {"type": "http.request", "body": raw, "more_body": False}
                        return await original()
                    receive = replay
                try:
                    target = await self.pool.workspace(user)
                    leased_user = user["id"]
                except HTTPException as error:
                    return await reject(error.status_code, error.detail)
                except STORAGE_ERRORS:
                    return await reject(503, "应用数据服务暂时不可用，请稍后重试。")

        async def secured(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                for name, value in ((b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                                    (b"referrer-policy", b"same-origin"), (b"x-frame-options", b"DENY")):
                    if name not in existing:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)
        try:
            await target(scope, receive, secured)
        finally:
            if leased_user is not None:
                remaining = self.pool.inflight.get(leased_user, 1) - 1
                if remaining > 0:
                    self.pool.inflight[leased_user] = remaining
                else:
                    self.pool.inflight.pop(leased_user, None)


def create_account_app(config, *, service=None, agent=None):
    pool = WorkspacePool(config, service=service, agent=agent)
    @asynccontextmanager
    async def lifespan(app):
        yield
        await pool.close()
    app = FastAPI(title="ChefMate", version=current_version(APP_ROOT), lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    install_storage_handlers(app)
    app.state.accounts, app.state.pool = pool.accounts, pool
    app.include_router(build_router(pool.accounts, secure_cookie=config.cookie_secure))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"detail": "请求无效，请检查输入内容。"}, status_code=422)

    @app.get("/healthz")
    async def health():
        def check_database():
            database = pool.accounts.database
            if not database.is_postgres and not database.path.is_file():
                return False
            with database.connect(timeout=1) as connection:
                connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
            return True
        try:
            ready = await asyncio.wait_for(asyncio.to_thread(check_database), timeout=2)
        except Exception:
            ready = False
        if not ready:
            return JSONResponse({"status": "unavailable", "service": "chefmate", "authentication": True}, status_code=503)
        return {"status": "ok", "service": "chefmate", "authentication": True}

    @app.api_route("/api/admin/settings", methods=["GET", "PATCH"])
    async def admin_settings(request: Request):
        scope = dict(request.scope)
        scope["path"] = request.url.path.replace("/api/admin/settings", "/api/settings", 1)
        scope["raw_path"] = scope["path"].encode()
        # Route the concrete request through the existing settings validation,
        # revision/CAS and secret handling instead of duplicating that policy.
        messages = []
        async def collect(message):
            messages.append(message)
        if request.method == "GET":
            await pool.control(scope, request.receive, collect)
        else:
            async with pool.lock:
                if pool.busy():
                    raise HTTPException(409, "还有用户的请求正在运行，请等待结束后再更新模型与运行配置。")
                await pool.control(scope, request.receive, collect)
        from starlette.responses import Response
        start = next(message for message in messages if message["type"] == "http.response.start")
        response = Response(b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body"),
                            status_code=start["status"])
        response.raw_headers = start["headers"]
        return response

    @app.post("/api/admin/settings/test-model")
    async def admin_probe(values: dict, request: Request):
        from decimal import Decimal, ROUND_CEILING
        from .settings import probe_model, resolve_model
        try:
            candidate = resolve_model(pool.control.state.context.config, values)
        except ValueError:
            raise HTTPException(422, "请填写有效的模型、端点和专用密钥。") from None
        user_id, turn_id = request.state.user["id"], "probe-" + uuid4().hex
        try:
            reservation = pool.accounts.reserve_usage(user_id, turn_id)
            price = reservation["pricing"]
            # Two small native-tool requests, each capped at 256 output tokens.
            cost = int((Decimal(8192) * max(Decimal(price["input_per_million"]), Decimal(price["cached_input_per_million"]))
                + Decimal(512) * Decimal(price["output_per_million"])).to_integral_value(rounding=ROUND_CEILING))
            pool.accounts.resize_usage(user_id, turn_id, tokens=8704, cost_micros=cost)
        except AccountError as error:
            pool.accounts.release_usage(user_id, turn_id)
            raise HTTPException(error.status_code, error.detail) from None
        observed = {"attempted_calls": 0, "calls": []}
        def observe(usage):
            nonlocal observed
            observed = usage
        result = None
        try:
            result = await probe_model(candidate, on_usage=observe)
            return result
        finally:
            pool.accounts.settle_usage(user_id, turn_id, observed, session_id="model-connection-test",
                model=candidate.model, status="completed" if result and result.get("ok") else "error")

    @app.get("/api/admin/data")
    async def data():
        async with pool.lock:
            return await asyncio.to_thread(pool.data_overview)

    @app.post("/api/admin/users/{user_id}/reset-data")
    async def reset_data(user_id: str, body: ResetDataRequest, request: Request):
        return await pool.reset_data(request.state.user, user_id, body.confirm_username)

    mount_frontend(app, APP_ROOT / "web")
    app.add_middleware(TenantBoundary, pool=pool)
    return app
