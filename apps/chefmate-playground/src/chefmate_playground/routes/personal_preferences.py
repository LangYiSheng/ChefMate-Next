"""Personal preferences are distinct from identity and administrative settings."""

from fastapi import APIRouter, HTTPException, Query

from ..accounts import AccountError


def build_router(context):
    router = APIRouter()

    def service():
        result = getattr(context, "personal_preferences", None)
        if result is None:
            raise HTTPException(404, "当前实例未启用账号个人偏好。")
        return result

    def call(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except AccountError as error:
            raise HTTPException(error.status_code, error.detail) from None

    @router.get("/api/personal-preferences")
    def read():
        return call(service().snapshot)

    @router.patch("/api/personal-preferences")
    def update(values: dict):
        values = dict(values)
        revision = values.pop("revision", None)
        return call(service().update, revision, values)

    @router.delete("/api/personal-preferences/summary")
    def clear(revision: str = Query(min_length=64, max_length=64)):
        return call(service().update, revision, {}, clear_summary=True)

    return router
