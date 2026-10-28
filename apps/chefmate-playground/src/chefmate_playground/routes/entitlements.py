"""Member allowance and permanent-credit administration."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from ..accounts import COOKIE_NAME
from .accounts import _Body, _SafeAccountRoute, _call, _same_origin


class CreditGrant(_Body):
    amount_micros: int = Field(ge=1, le=10**15)
    label: str = Field(default="额外充值额度", min_length=1, max_length=80)
    request_id: str = Field(min_length=1, max_length=100)


class MembershipGrant(_Body):
    label: str = Field(default="ChefMate 会员", min_length=1, max_length=80)
    duration_days: int = Field(default=30, ge=1, le=3660)
    weekly_limit_micros: int = Field(default=10_000_000, ge=0, le=10**15)
    five_hour_limit_micros: int = Field(default=2_000_000, ge=0, le=10**15)
    request_id: str = Field(min_length=1, max_length=100)


class MembershipRevoke(_Body):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


def build_router(accounts):
    router = APIRouter(route_class=_SafeAccountRoute, dependencies=[Depends(_same_origin)])

    def current(request):
        user = getattr(request.state, "user", None) or accounts.authenticate_session(request.cookies.get(COOKIE_NAME))
        if not user:
            raise HTTPException(401, "请先登录 ChefMate。")
        return user

    def administrator(request):
        user = current(request)
        if user["role"] != "admin":
            raise HTTPException(403, "需要管理员权限。")
        return user

    @router.get("/api/auth/entitlements")
    def own(request: Request):
        return _call(accounts.get_entitlements, current(request)["id"])

    @router.get("/api/admin/users/{user_id}/entitlements")
    def details(user_id: str, request: Request):
        administrator(request)
        return _call(accounts.get_entitlements, user_id)

    @router.post("/api/admin/users/{user_id}/credit-grants")
    def grant_credit(user_id: str, body: CreditGrant, request: Request):
        return _call(accounts.grant_credit, administrator(request)["id"], user_id, **body.model_dump())

    @router.post("/api/admin/users/{user_id}/memberships")
    def grant_membership(user_id: str, body: MembershipGrant, request: Request):
        return _call(accounts.grant_membership, administrator(request)["id"], user_id, **body.model_dump())

    @router.post("/api/admin/users/{user_id}/memberships/{membership_id}/revoke")
    def revoke(user_id: str, membership_id: str, body: MembershipRevoke, request: Request):
        return _call(accounts.revoke_membership, administrator(request)["id"], user_id, membership_id, **body.model_dump())

    return router
