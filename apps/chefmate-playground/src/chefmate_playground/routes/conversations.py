"""Conversations HTTP endpoints; domain work stays in application services."""

import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from chefmate_workflow import WorkspaceState
from ..http_models import SessionUpdate, TurnRequest
from ..images import ImageError
from ..runtime import TurnConflict, encode_event
from ..sessions import public_session
from ..app_context import AppContext
from ..accounts import AccountError


def build_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sessions")
    async def sessions(
        limit: int = Query(default=20, ge=1, le=100), cursor: str | None = Query(default=None, max_length=1024)
    ):
        try:
            states, next_cursor = await asyncio.to_thread(context.repository.page, limit=limit, cursor=cursor)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {
            "items": [
                {
                    key: value
                    for key, value in public_session(state).items()
                    if key
                    in {
                        "id",
                        "title",
                        "pinned",
                        "updated_at",
                        "running",
                        "last_status",
                        "conversation_state",
                        "awaiting_user",
                        "pending_question",
                    }
                }
                for state in states
            ],
            "next_cursor": next_cursor,
        }

    @router.get("/api/timer-sessions")
    async def timer_sessions():
        """Discover background reminders without activating or mutating a chat.

        Finished timer acknowledgements belong to the browser. Include recently
        touched finished timers so a refreshed page can apply its saved ack set;
        updated_at limits discovery only and is not presented as an expiry time.
        """
        recent_since = datetime.now(timezone.utc) - timedelta(hours=24)
        items = []
        for state in await asyncio.to_thread(context.repository.list):
            try:
                workspace = WorkspaceState.model_validate(state.get("workspace") or {})
            except (ValueError, TypeError):
                continue
            has_active = any(timer.status in {"running", "paused"} for timer in workspace.timers)
            has_recent_finished = False
            if any(timer.status == "finished" for timer in workspace.timers):
                try:
                    updated = datetime.fromisoformat(state["updated_at"])
                    has_recent_finished = updated.tzinfo is not None and updated >= recent_since
                except (KeyError, ValueError, TypeError):
                    pass
            if has_active or has_recent_finished:
                items.append({"id": state["id"], "title": state.get("title") or "厨房计时"})
        return {"items": items}

    @router.post("/api/sessions")
    async def new_session():
        return public_session(context.repository.create(), reply_suggestions=context.config.reply_suggestions)

    @router.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        return public_session(context.require_session(session_id), reply_suggestions=context.config.reply_suggestions)

    @router.patch("/api/sessions/{session_id}")
    async def edit_session(session_id: str, payload: SessionUpdate):
        try:
            saved = await asyncio.to_thread(
                context.repository.update_metadata, session_id, title=payload.title, pinned=payload.pinned
            )
        except KeyError:
            raise HTTPException(404, "对话不存在。") from None
        return {
            key: saved.get(key, False if key == "pinned" else None) for key in ("id", "title", "title_custom", "pinned")
        }

    @router.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        state = context.require_session(session_id)
        if session_id in context.manager.active:
            raise HTTPException(409, "请先停止这段对话。")
        if context.workspace and not await asyncio.to_thread(context.workspace.record_journal, session_id):
            raise HTTPException(503, "饮食记录暂未同步，请稍后再删除这段对话。")
        removed = context.repository.delete(session_id)
        await asyncio.to_thread(context.images.remove_session_files, removed or state)
        return {"deleted": True}

    @router.post("/api/sessions/{session_id}/turn")
    async def turn(session_id: str, body: TurnRequest):
        context.require_session(session_id)
        if context.agent is None:
            raise HTTPException(503, "智能体尚未配置，请检查应用 .env 中的数据库、模型和 API Key。")
        if body.images and not context.config.supports_images:
            raise HTTPException(422, "当前模型没有开启图片能力。")
        if len(body.images) > context.config.max_images:
            raise HTTPException(422, f"当前模型每次请求最多接受 {context.config.max_images} 张图片，请移除多余图片。")
        try:
            active, replay = await context.manager.begin(
                session_id,
                body.message,
                retry=body.retry,
                request_id=body.request_id,
                ui_snapshot=body.ui_snapshot.model_dump(mode="json") if body.ui_snapshot is not None else None,
                image_ids=body.images,
            )
        except ImageError:
            raise HTTPException(422, "图片未上传或不属于当前会话，请重新上传。") from None
        except TurnConflict as error:
            raise HTTPException(409, str(error)) from None
        except AccountError as error:
            raise HTTPException(error.status_code, error.detail) from None

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

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
        )

    @router.post("/api/sessions/{session_id}/cancel")
    async def cancel(session_id: str):
        context.require_session(session_id)
        return {"cancelled": await context.manager.cancel(session_id)}

    return router
