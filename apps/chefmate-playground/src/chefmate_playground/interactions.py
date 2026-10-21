"""Application binding for kitchen aggregates and model-driven presentation tools."""

import asyncio
import copy
import json
import logging
import sqlite3
from typing import Any

from chefmate_workflow import TOOL_REQUEST_MODELS, WorkflowService
from chefmate_workflow.contracts import TOOL_CONTRACTS

from .sessions import SessionRepository, now

logger = logging.getLogger(__name__)


class WorkspaceChanged(Exception):
    pass


class TurnClosed(Exception):
    pass




def approved_recipes(messages: list[dict]) -> list[dict]:
    """Source references come from actual tools, never from assistant prose or client JSON."""
    refs: dict[tuple[str, str], dict] = {}
    for message in messages:
        if message.get("role") != "tool" or message.get("name") not in {"search_recipes", "get_recipe"}:
            continue
        try:
            output = json.loads(message["content"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(output, dict) or output.get("status") != "ok" or not output.get("snapshot"):
            continue
        items = output.get("items", []) if message["name"] == "search_recipes" else [output.get("recipe")]
        for item in items:
            if not isinstance(item, dict) or not item.get("recipe_id"):
                continue
            ref = {"recipe_id": item["recipe_id"], "data_snapshot": output["snapshot"]}
            refs[(ref["recipe_id"], json.dumps(ref["data_snapshot"], sort_keys=True))] = ref
    return list(refs.values())


class WorkspaceBinding:
    def __init__(self, repository: SessionRepository, retrieval, *, cooking_settings=None, journal=None, intake_enabled=lambda: True):
        self.repository = repository
        self.retrieval = retrieval
        self.cooking_settings = cooking_settings
        self.journal = journal
        self.intake_enabled = intake_enabled

    def record_journal(self, session_id, *, register_intake=False):
        if self.journal is not None:
            state = self.repository.get(session_id)
            if state:
                try:
                    self.journal.sync_session(state, register_intake=register_intake and self.intake_enabled())
                except (sqlite3.Error, OSError, ValueError):
                    # The workflow has already committed. A journal outage must not
                    # turn a saved action into a reported failure or break SSE done.
                    logger.warning("Cooking journal synchronization pending for session %s", session_id)
                    return False
        return True

    def intake_status(self, session_id):
        if self.journal is None:
            return {"status": "unavailable", "record": None, "message": "营养记录服务尚未连接。"}
        if not self.intake_enabled():
            return {"status": "ok", "intake_status": "disabled", "record": None,
                    "message": "饮食记录组件已关闭，本次仅保留制作历史，没有自动登记个人营养。"}
        from .cooked_intake import entry_identity
        state = self.repository.get(session_id) or {}
        workspace = state.get("workspace") or {}
        if not workspace.get("meal_id") or workspace.get("stage") != "completed":
            return {"status": "meal_not_completed", "record": None}
        try:
            return self.journal.cooked_intake.get(entry_identity(session_id, workspace["meal_id"]))
        except (sqlite3.Error, OSError, ValueError):
            return {"status": "sync_pending", "record": None, "message": "本次制作已完成，营养同步待重试，尚不能声称已登记个人营养。"}

    def service(self, session_id: str) -> WorkflowService:
        def get_state():
            state = self.repository.get(session_id)
            if state is None:
                raise KeyError(session_id)
            return state.get("workspace", {})

        def mutate(operation):
            def update(state):
                result = operation(state.setdefault("workspace", {}))
                state["updated_at"] = now()
                return result
            return self.repository.mutate(session_id, update)

        return WorkflowService(self.retrieval, get_state, mutate, cooking_settings=self.cooking_settings)

    def for_turn(self, session_id: str, turn_id: str, cancelled: asyncio.Event, expected_revision: int):
        return ScopedWorkspace(self, session_id, turn_id, cancelled, expected_revision)


class ScopedWorkspace:
    """One run's optimistic write boundary; tools cannot overwrite concurrent user edits."""
    def __init__(self, binding: WorkspaceBinding, session_id: str, turn_id: str, cancelled: asyncio.Event,
                 expected_revision: int):
        self.binding = binding
        self.session_id = session_id
        self.turn_id = turn_id
        self.cancelled = cancelled
        self.expected_revision = expected_revision
        self.service = WorkflowService(binding.retrieval, self._read, self._mutate, cooking_settings=binding.cooking_settings)

    def _read(self):
        state = self.binding.repository.get(self.session_id)
        if state is None:
            raise KeyError(self.session_id)
        return state.get("workspace", {})

    def _mutate(self, operation):
        def update(state):
            if (self.cancelled.is_set() or not state.get("running")
                    or (state.get("active_turn") or {}).get("id") != self.turn_id):
                raise TurnClosed()
            workspace = state.setdefault("workspace", {})
            if workspace.get("revision", 0) != self.expected_revision:
                raise WorkspaceChanged()
            result = operation(workspace)
            # Synchronous tools may finish after cancellation. Abort the repository
            # transaction before saving their completed mutation in that case.
            if self.cancelled.is_set():
                raise TurnClosed()
            self.expected_revision = workspace.get("revision", self.expected_revision)
            state["updated_at"] = now()
            return result
        return self.binding.repository.mutate(self.session_id, update)

    def model_snapshot(self):
        return self.service.model_snapshot()

    def execute(self, name: str, request: dict, context: Any):
        if self.cancelled.is_set():
            return {"status": "turn_closed", "message": "本轮已停止，没有继续修改任务。"}
        try:
            if name == "get_cooking_workspace":
                self.expected_revision = self.model_snapshot()["revision"]
            result = self.service.execute_tool(
                name, request, approved_recipes=approved_recipes(context.messages),
                operation_id=f"agent:{self.turn_id}:{context.call_id}",
                user_text=next((m["content"] for m in reversed(context.messages) if m.get("role") == "user"), ""),
            )
        except WorkspaceChanged:
            result = self.binding.service(self.session_id).get_workspace()
            result.update(status="revision_conflict", message="用户刚修改了界面，请先读取最新工作区，不要覆盖已确认操作。")
        except TurnClosed:
            return {"status": "turn_closed", "message": "本轮已停止，没有继续修改任务。"}
        completed = name == "advance_cooking_stage" and request.get("stage") == "completed" and result.get("status") == "ok"
        self.binding.record_journal(self.session_id, register_intake=completed)
        if completed:
            result["cooking_intake"] = self.binding.intake_status(self.session_id)
        # Full views belong to HTTP/UI. Model history receives only this tool's presentation and compact snapshot.
        return {key: copy.deepcopy(value) for key, value in result.items()
                if key in {"status", "message", "errors", "recovery", "actions", "ui_snapshot", "pending_question", "cooking_intake"}}


def make_tool_specs():
    from chefmate_agent import InjectedToolSpec

    specs = []
    for name, request_model in TOOL_REQUEST_MODELS.items():
        def handler(request, context, tool_name=name):
            if not isinstance(context.host_context, ScopedWorkspace):
                return {"status": "workspace_unavailable", "message": "当前调用方没有提供厨房任务服务。"}
            return context.host_context.execute(tool_name, request, context)
        specs.append(InjectedToolSpec(
            name=name, description=TOOL_CONTRACTS[name].description, args_schema=request_model, handler=handler,
            allowed_stages=TOOL_CONTRACTS[name].allowed_stages, effect=TOOL_CONTRACTS[name].effect,
            auto_display=TOOL_CONTRACTS[name].auto_display, preconditions=TOOL_CONTRACTS[name].preconditions,
            side_effects=TOOL_CONTRACTS[name].side_effects,
            model_visible=TOOL_CONTRACTS[name].model_visible,
        ))
    return specs
