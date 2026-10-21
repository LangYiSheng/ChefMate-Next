"""Application binding for kitchen aggregates and model-driven presentation tools."""

import asyncio
import copy
import json
from typing import Any

from chefmate_workflow import TOOL_REQUEST_MODELS, WorkflowService

from .sessions import SessionRepository, now


class WorkspaceChanged(Exception):
    pass


class TurnClosed(Exception):
    pass


TOOL_DESCRIPTIONS = {
    "show_preference_form": "展示可编辑的食材、耗时、人数、口味和厨具表单；保留用户已同步的值，不替用户填写或覆盖。",
    "show_recipe_choices": "用真实搜索返回的recipe_ids与data_snapshot展示最多6道可选菜谱；用户选菜会直接同步任务。",
    "show_recipe_details": "展示真实菜谱或当前任务冻结的菜谱详情，包含食材用量、步骤、估算与原文。",
    "set_meal_plan": "将用户已选择或明确要求安排的真实菜谱建立为任务计划；最多4道，保留菜谱来源快照。",
    "show_meal_plan": "展示当前任务菜单和目标人数；用户可直接调整人数。",
    "show_prep_checklist": "展示可勾选的备菜清单；recipe_ids选某道菜，null整餐；用户确认后才标为准备好。",
    "show_cooking_guide": "展示当前任务菜谱的原始步骤和已完成进度；浏览步骤不等于确认完成。",
    "set_cooking_timer": "按用户指定秒数或菜谱原文的明确时长创建持久化计时；不猜测时间，不自动标步骤完成。",
    "show_timers": "展示已保存的计时器，用户可暂停、继续或取消，刷新不会丢失截止时间。",
    "get_cooking_workspace": "读取用户最新表单、选菜、备菜勾选、步骤和计时状态；版本冲突后先读取，不覆盖用户修改。",
    "advance_cooking_stage": "按照真实任务状态推进或回退做饭阶段；备菜/完成条件由业务服务强制校验。",
    "update_cooking_guidance": "根据用户明确补充的条件调整当前任务的备菜/步骤指导。recipe_id/step_id来自已选餐单。instruction为建议做法、reason为调整原因；保留原步骤、真实用量和勾选/完成状态，不修改源菜谱，不编造未知量。",
    "update_preferences": "按用户明确表达填写或修改用餐条件，传最新expected_revision；保留未修改字段，可展示已填表单请用户核对。",
    "select_recipes": "按用户明确表达选择真实候选菜品，传recipe_ids与最新expected_revision；展示保存后的选择供核对，不编造菜谱ID。",
    "update_prep_items": "按用户明确说明已有/缺少/跳过的食材更新备菜状态；使用最新清单item_id与expected_revision，可一次修改多项并展示结果。不能自行猜测食材是否已有。",
    "ask_user": "在存在实质歧义或需要用户选择时提出一个问题，可提供少量选项或允许自由填写。本次必须单独调用，随后暂停等待答案；不能替用户回答，不用于无必要的额外确认。",
}

TOOL_STAGES = {
    "set_meal_plan": ("idea", "planning", "completed"),
    "show_meal_plan": ("planning", "prep", "cooking", "completed"),
    "show_prep_checklist": ("planning", "prep", "cooking"),
    "show_cooking_guide": ("planning", "prep", "cooking", "completed"),
    "set_cooking_timer": ("cooking",),
    "update_cooking_guidance": ("planning", "prep", "cooking"),
    "select_recipes": ("idea", "planning", "completed"),
    "update_prep_items": ("planning", "prep"),
}


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
    def __init__(self, repository: SessionRepository, retrieval):
        self.repository = repository
        self.retrieval = retrieval

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

        return WorkflowService(self.retrieval, get_state, mutate)

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
        self.service = WorkflowService(binding.retrieval, self._read, self._mutate)

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
        # Full views belong to HTTP/UI. Model history receives only this tool's presentation and compact snapshot.
        return {key: copy.deepcopy(value) for key, value in result.items()
                if key in {"status", "message", "actions", "ui_snapshot", "pending_question"}}


def make_tool_specs():
    from chefmate_agent import InjectedToolSpec

    specs = []
    for name, request_model in TOOL_REQUEST_MODELS.items():
        def handler(request, context, tool_name=name):
            if not isinstance(context.host_context, ScopedWorkspace):
                return {"status": "workspace_unavailable", "message": "当前调用方没有提供厨房任务服务。"}
            return context.host_context.execute(tool_name, request, context)
        specs.append(InjectedToolSpec(
            name=name, description=TOOL_DESCRIPTIONS[name], args_schema=request_model, handler=handler,
            allowed_stages=TOOL_STAGES.get(name),
        ))
    return specs
