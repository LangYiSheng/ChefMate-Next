"""Administrative CRUD and native-tool probes for saved model connections."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..accounts import AccountError, COOKIE_NAME
from ..model_profiles import ModelProfilesRegistry
from .accounts import _SafeAccountRoute, _same_origin


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Pricing(_Body):
    currency: str = "CNY"
    input_per_million: str | int | float = "0"
    cached_input_per_million: str | int | float = "0"
    output_per_million: str | int | float = "0"
    configured: bool = False


class Create(_Body):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr | None = Field(default=None, repr=False)
    api_format: str = "chat_completions"
    stream_tool_calls: bool = False
    supports_images: bool = False
    max_images: int = Field(default=3, ge=1, le=12)
    pricing: Pricing = Field(default_factory=Pricing)


class Patch(_Body):
    revision: int = Field(ge=1)
    values: dict


class Revision(_Body):
    revision: int = Field(ge=1)


class Test(_Body):
    revision: int | None = Field(default=None, ge=1)


class _Route(_SafeAccountRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except AccountError as error:
                raise HTTPException(error.status_code, error.detail) from None
        return handler


def build_router(pool):
    registry = ModelProfilesRegistry(pool)
    pool.model_profiles = registry
    router = APIRouter(route_class=_Route, dependencies=[Depends(_same_origin)])

    def actor(request):
        user = getattr(request.state, "user", None) or pool.accounts.authenticate_session(request.cookies.get(COOKIE_NAME))
        if user is None:
            raise HTTPException(401, "请先登录 ChefMate。")
        if user["role"] != "admin":
            raise HTTPException(403, "需要管理员权限。")
        return user["id"]

    @router.get("/api/admin/models")
    def list_models(request: Request):
        return registry.list(actor(request))

    @router.post("/api/admin/models", status_code=201)
    async def create_model(body: Create, request: Request):
        values = body.model_dump(exclude={"revision"})
        values["api_key"] = body.api_key.get_secret_value() if body.api_key else None
        return await registry.change(actor(request), body.revision, "create", values=values)

    @router.patch("/api/admin/models/{profile_id}")
    async def update_model(profile_id: str, body: Patch, request: Request):
        return await registry.change(actor(request), body.revision, "update", profile_id, body.values)

    @router.post("/api/admin/models/{profile_id}/activate")
    async def activate_model(profile_id: str, body: Revision, request: Request):
        return await registry.change(actor(request), body.revision, "activate", profile_id)

    @router.delete("/api/admin/models/{profile_id}")
    async def delete_model(profile_id: str, request: Request, revision: int = Query(ge=1)):
        return await registry.change(actor(request), revision, "delete", profile_id)

    @router.post("/api/admin/models/{profile_id}/test")
    async def test_model(profile_id: str, request: Request, body: Test | None = None):
        return await registry.test(actor(request), profile_id, body.revision if body else None)

    return router
