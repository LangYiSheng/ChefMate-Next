"""Identity HTTP API. Workspace access is enforced by the outer tenant boundary."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from ..accounts import AccountError, AccountsRepository, COOKIE_NAME, SESSION_SECONDS


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Credentials(_Body):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(_Body):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class PasswordReset(_Body):
    new_password: str = Field(min_length=10, max_length=128)


class Onboarding(_Body):
    display_name: str = Field(min_length=1, max_length=40)
    cooking_level: str = "beginner"
    household_size: int = Field(default=1, ge=1, le=20)
    preferences: list[str] = Field(default_factory=list, max_length=12)
    theme: str = "system"
    use_for_ai: bool = False


class UserUpdate(_Body):
    disabled: bool | None = None
    developer_allowed: bool | None = None
    role: str | None = None
    cost_limit_micros: int | None = Field(default=None, ge=0, le=10**15)


class PricingUpdate(_Body):
    currency: str = "CNY"
    input_per_million: str | int | float | None = None
    output_per_million: str | int | float | None = None
    cached_input_per_million: str | int | float | None = None
    revision: int | None = None


class PolicyUpdate(_Body):
    revision: int = Field(ge=1)
    registration_enabled: bool = True
    default_cost_limit_micros: int | None = Field(default=None, ge=0, le=10**15)


class ReconcileUsage(_Body):
    revision: int = Field(ge=0)
    final_cost_micros: int = Field(ge=0, le=10**15)
    reason: str = Field(min_length=1, max_length=500)


class _SafeAccountRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                # Validation diagnostics can include rejected plaintext passwords.
                return JSONResponse({"detail": "账号请求无效，请检查输入内容。"}, status_code=422)
        return handler


def _call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except AccountError as error:
        raise HTTPException(error.status_code, error.detail) from None


def _same_origin(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise HTTPException(403, "仅接受同源页面请求。") from None
        if (parsed.scheme not in {"http", "https"} or parsed.scheme != request.url.scheme
                or parsed.netloc != request.headers.get("host")
                or parsed.path or parsed.query or parsed.fragment):
            raise HTTPException(403, "仅接受同源页面请求。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "仅接受同源页面请求。")


def build_router(accounts: AccountsRepository, *, secure_cookie=False, registration_enabled=True):
    router = APIRouter(route_class=_SafeAccountRoute, dependencies=[Depends(_same_origin)])

    def current_user(request):
        user = getattr(request.state, "user", None)
        if user is None:
            user = accounts.authenticate_session(request.cookies.get(COOKIE_NAME))
        if user is None:
            raise HTTPException(401, "请先登录 ChefMate。")
        return user

    def administrator(request):
        user = current_user(request)
        if user["role"] != "admin":
            raise HTTPException(403, "需要管理员权限。")
        return user

    def login_response(request, response, user):
        token = _call(accounts.issue_session, user["id"], expected_revision=user["session_revision"])
        accounts.revoke_session(request.cookies.get(COOKIE_NAME))
        response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=secure_cookie, samesite="strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"user": user}

    @router.get("/api/auth/me")
    def me(request: Request):
        user = getattr(request.state, "user", None) or accounts.authenticate_session(request.cookies.get(COOKIE_NAME))
        return {"user": user, "registration_enabled": bool(registration_enabled and accounts.policy()["registration_enabled"])}

    @router.post("/api/auth/register", status_code=201)
    def register(body: Credentials, request: Request, response: Response):
        if not registration_enabled or not accounts.policy()["registration_enabled"]:
            raise HTTPException(403, "当前暂停开放注册，请联系管理员。")
        client_key = request.client.host if request.client else "unknown"
        _call(accounts.check_rate_limit, "register:" + client_key, count=20, window_seconds=3600)
        user = _call(accounts.register, body.username, body.password)
        return login_response(request, response, user)

    @router.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        client_key = request.client.host if request.client else "unknown"
        user = _call(accounts.login, body.username, body.password, client_key=client_key)
        return login_response(request, response, user)

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        accounts.revoke_session(request.cookies.get(COOKIE_NAME))
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=secure_cookie, samesite="strict")
        return {"ok": True}

    @router.patch("/api/auth/password")
    def password(body: PasswordChange, request: Request, response: Response):
        user = current_user(request)
        _call(accounts.change_password, user["id"], body.current_password, body.new_password)
        refreshed = accounts.get_user(user["id"])
        login_response(request, response, refreshed)
        return {"ok": True, "user": refreshed}

    @router.patch("/api/auth/onboarding")
    def onboarding(body: Onboarding, request: Request):
        user = current_user(request)
        return {"user": _call(accounts.complete_onboarding, user["id"], body.model_dump())}

    @router.get("/api/auth/quota")
    def quota(request: Request):
        user = current_user(request)
        return {"quota": user["quota"], "pricing": accounts.pricing()}

    @router.get("/api/admin/users")
    def users(request: Request):
        administrator(request)
        return {"items": accounts.list_users()}

    @router.patch("/api/admin/users/{user_id}")
    def update_user(user_id: str, body: UserUpdate, request: Request):
        actor = administrator(request)
        return {"user": _call(accounts.update_user, actor["id"], user_id, body.model_dump(exclude_unset=True))}

    @router.post("/api/admin/users/{user_id}/password")
    def reset_password(user_id: str, body: PasswordReset, request: Request):
        actor = administrator(request)
        _call(accounts.reset_password, actor["id"], user_id, body.new_password)
        return {"ok": True}

    @router.get("/api/admin/pricing")
    def pricing(request: Request):
        administrator(request)
        return accounts.pricing()

    @router.get("/api/admin/policy")
    def policy(request: Request):
        administrator(request)
        return accounts.policy()

    @router.patch("/api/admin/policy")
    def update_policy(body: PolicyUpdate, request: Request):
        actor = administrator(request)
        return _call(accounts.update_policy, actor["id"], body.model_dump(exclude_unset=True))

    @router.patch("/api/admin/pricing")
    def update_pricing(body: PricingUpdate, request: Request):
        actor = administrator(request)
        return _call(accounts.update_pricing, actor["id"], body.model_dump(exclude_unset=True))

    @router.get("/api/admin/usage")
    def usage(request: Request, days: int = 30):
        administrator(request)
        return _call(accounts.usage_summary, days=days)

    @router.get("/api/admin/users/{user_id}/usage")
    def user_usage(user_id: str, request: Request):
        administrator(request)
        user = accounts.get_user(user_id)
        if user is None:
            raise HTTPException(404, "用户不存在。")
        return {"user": user, "items": accounts.get_usage(user_id)}

    @router.post("/api/admin/users/{user_id}/usage/{turn_id}/reconcile")
    def reconcile(user_id: str, turn_id: str, body: ReconcileUsage, request: Request):
        actor = administrator(request)
        return _call(accounts.reconcile_usage, actor["id"], user_id, turn_id, **body.model_dump())

    @router.get("/api/admin/users/{user_id}/usage/{turn_id}/reconciliations")
    def reconciliation_history(user_id: str, turn_id: str, request: Request):
        administrator(request)
        return {"items": accounts.reconciliations(user_id, turn_id)}

    @router.get("/api/admin/audit")
    def audit(request: Request):
        administrator(request)
        return {"items": accounts.audit_log()}

    return router
