"""Diagnostics HTTP endpoints; domain work stays in application services."""

from fastapi import APIRouter, HTTPException, Request
from ..debug import redact_debug
from ..app_context import AppContext


def build_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    def require_developer(request: Request):
        # An explicit local opt-in, not an authentication boundary.
        if request.headers.get("X-ChefMate-Developer") != "1":
            raise HTTPException(404, "请先开启开发者模式。")

    def catalog(snapshot=None):
        tools = (
            context.agent.tool_catalog(snapshot or {})
            if context.agent is not None and hasattr(context.agent, "tool_catalog")
            else []
        )
        return redact_debug(
            {
                "tools": tools,
                "components": context.components.metadata() if context.components else [],
                "stage": (snapshot or {}).get("stage", "idea"),
            },
            (context.config.api_key, context.config.database_url, context.config.app_database_url),
        )

    @router.get("/api/debug/catalog")
    async def developer_catalog(request: Request):
        require_developer(request)
        return catalog()

    @router.get("/api/sessions/{session_id}/debug")
    async def developer_session(session_id: str, request: Request, turn_id: str | None = None):
        require_developer(request)
        state = context.require_session(session_id)
        snapshot = context.workspace.service(session_id).model_snapshot() if context.workspace else {}
        runs = context.repository.debug_runs(session_id)
        chosen = turn_id or (runs[0]["turn_id"] if runs else None)
        capture = context.repository.debug_run(session_id, chosen) if chosen else None
        if turn_id and capture is None:
            raise HTTPException(404, "这轮没有留存的上下文记录。")
        # Legacy turns can show their public execution log, but are never labeled exact captures.
        logs = [
            {
                "id": message.get("turn_id", message.get("id")),
                "text": message.get("content", ""),
                "created_at": message.get("created_at"),
                "tool_calls": message.get("tool_calls", []),
            }
            for message in state.get("messages", [])
            if message.get("role") == "assistant"
        ]
        return redact_debug(
            {
                **catalog(snapshot),
                "runs": runs,
                "capture": capture,
                "logs": logs,
                "retained_turns": 20,
                "running": state.get("running", False),
            },
            (context.config.api_key, context.config.database_url, context.config.app_database_url),
        )

    return router
