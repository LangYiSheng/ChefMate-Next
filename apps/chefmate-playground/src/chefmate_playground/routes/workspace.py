"""Workspace HTTP endpoints; domain work stays in application services."""

import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from chefmate_workflow import UserActionRequest
from ..images import ImageError, MAX_UPLOAD_BYTES
from ..app_context import AppContext


def build_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sessions/{session_id}/workspace")
    async def workspace(session_id: str):
        context.require_session(session_id)
        if context.workspace is None:
            raise HTTPException(503, "厨房任务尚未连接菜谱库。")
        try:
            return await asyncio.to_thread(context.workspace.service(session_id).get_workspace)
        except Exception:
            raise HTTPException(503, "暂时无法读取任务状态。") from None

    @router.post("/api/sessions/{session_id}/images")
    async def upload_image(session_id: str, request: Request):
        context.require_session(session_id)
        if not context.config.supports_images:
            raise HTTPException(422, "当前模型未开启图片能力，请在模型连接中配置。")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "图片过大，请压缩后再上传。")
        try:
            return await asyncio.to_thread(
                context.images.save, session_id, bytes(data), request.query_params.get("name", "照片.jpg")
            )
        except (ImageError, ValueError, KeyError):
            raise HTTPException(422, "图片未保存，请选择有效且尺寸合适的 JPG、PNG 或 WebP。") from None

    @router.get("/api/sessions/{session_id}/images/{image_id}")
    async def image(session_id: str, image_id: str):
        state = context.require_session(session_id)
        try:
            context.images.references(state, [image_id])
            path = context.images.path(session_id, image_id)
        except (ValueError, ImageError):
            raise HTTPException(404, "图片不存在。") from None
        return FileResponse(path, media_type="image/jpeg")

    @router.delete("/api/sessions/{session_id}/images/{image_id}")
    async def remove_image(session_id: str, image_id: str):
        context.require_session(session_id)
        try:
            await asyncio.to_thread(context.images.remove, session_id, image_id)
        except (ValueError, ImageError):
            raise HTTPException(409, "图片已经发送或已移除，请刷新当前会话。") from None
        return {"removed": True}

    @router.post("/api/sessions/{session_id}/actions")
    async def ui_action(session_id: str, body: UserActionRequest):
        context.require_session(session_id)
        if body.root.type == "change_stage":
            raise HTTPException(403, "会话阶段由助手根据你的需求管理，请通过对话告诉助手下一步想做什么。")
        if context.workspace is None:
            raise HTTPException(503, "厨房任务尚未连接菜谱库。")
        # Keep partial-form field presence; Pydantic defaults must not overwrite untouched values.
        try:
            result = await asyncio.to_thread(context.workspace.service(session_id).apply_action, body)
            completed = body.root.type == "complete_meal" and result.get("status") == "ok"
            await asyncio.to_thread(context.workspace.record_journal, session_id, register_intake=completed)
            if completed:
                result["cooking_intake"] = await asyncio.to_thread(context.workspace.intake_status, session_id)
            return result
        except ValueError:
            raise HTTPException(422, "这项界面操作无法执行，请检查输入。") from None
        except Exception:
            raise HTTPException(503, "操作尚未保存，请刷新任务状态后重试。") from None

    return router
