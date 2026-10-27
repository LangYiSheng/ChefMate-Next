"""Administrative membership-template CRUD with same-origin and revision checks."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from ..accounts import COOKIE_NAME
from ..membership_templates import MembershipTemplates
from .accounts import _Body, _SafeAccountRoute, _call, _same_origin


class Create(_Body):
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    duration_days: int = Field(ge=1, le=3660)
    weekly_limit_micros: int = Field(ge=0, le=10**15)
    five_hour_limit_micros: int = Field(ge=0, le=10**15)


class Patch(_Body):
    revision: int = Field(ge=1)
    values: dict


def build_router(accounts):
    registry = MembershipTemplates(accounts)
    router = APIRouter(route_class=_SafeAccountRoute, dependencies=[Depends(_same_origin)])

    def actor(request):
        user = getattr(request.state, "user", None) or accounts.authenticate_session(request.cookies.get(COOKIE_NAME))
        if user is None:
            raise HTTPException(401, "请先登录 ChefMate。")
        if user["role"] != "admin":
            raise HTTPException(403, "需要管理员权限。")
        return user["id"]

    @router.get("/api/admin/membership-templates")
    def templates(request: Request):
        return _call(registry.list, actor(request))

    @router.post("/api/admin/membership-templates", status_code=201)
    def create(body: Create, request: Request):
        return _call(registry.change, actor(request), body.revision, "create", values=body.model_dump(exclude={"revision"}))

    @router.patch("/api/admin/membership-templates/{template_id}")
    def update(template_id: str, body: Patch, request: Request):
        return _call(registry.change, actor(request), body.revision, "update", template_id, body.values)

    @router.delete("/api/admin/membership-templates/{template_id}")
    def delete(template_id: str, request: Request, revision: int = Query(ge=1)):
        return _call(registry.change, actor(request), revision, "delete", template_id)

    return router
