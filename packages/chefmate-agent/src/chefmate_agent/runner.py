"""LangChain middleware: execution policy and transient context, without an agent loop."""

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from .models import ToolCallRecord


@dataclass
class TurnRuntimeContext:
    on_event: Any = None
    host_context: Any = None
    ui_context: dict = field(default_factory=dict)
    records: list[ToolCallRecord] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    view_state: dict = field(default_factory=lambda: {"recipes": [], "selected_recipe": None, "snapshot": None})
    model_rounds: int = 0
    tool_calls: int = 0
    skill_chars: int = 0
    loaded_skills: set[str] = field(default_factory=set)


class ChefMateMiddleware(AgentMiddleware):
    def __init__(self, owner):
        self.owner = owner

    async def abefore_model(self, state, runtime):
        from .core import _AgentFailure, _dump

        context = runtime.context
        if context.model_rounds >= self.owner.config.max_model_rounds:
            raise _AgentFailure("step_budget_exceeded", "本轮推理达到步数上限，请缩小需求后重试。")
        if len(_dump(self.owner._serialize_messages(state["messages"]))) > self.owner.config.max_history_chars:
            raise _AgentFailure("history_limit", "本轮工具信息超过上下文预算，请缩小查询范围。")
        if context.model_rounds:
            await self.owner._emit(context.on_event, {
                "type": "status", "status": "thinking", "message": "正在分析查询结果",
            })
        context.model_rounds += 1

    async def awrap_model_call(self, request, handler):
        context = request.runtime.context
        stage = context.ui_context.get("stage")
        tools = [tool for tool in request.tools if (
            tool.name not in self.owner._extra_tools
            or self.owner._extra_tools[tool.name].allowed_stages is None
            or stage in self.owner._extra_tools[tool.name].allowed_stages
        )]
        prompt = self.owner._context_prompt(context, request.state["messages"])
        return await handler(request.override(system_message=SystemMessage(content=prompt), tools=tools))

    async def aafter_model(self, state, runtime):
        from .core import _AgentFailure

        response = state["messages"][-1]
        if not isinstance(response, AIMessage):
            raise _AgentFailure("invalid_response", "模型返回了不支持的消息格式。", True)
        self.owner._validate_response(response)
        calls = self.owner._validate_tool_batch(response)
        context = runtime.context
        if context.tool_calls + len(calls) > self.owner.config.max_tool_calls:
            raise _AgentFailure("tool_budget_exceeded", "本轮工具调用达到上限，请缩小需求后重试。")
        context.tool_calls += len(calls)

    async def awrap_tool_call(self, request, handler):
        from .core import _dump

        call = request.tool_call
        context = request.runtime.context
        await self.owner._emit(context.on_event, {
            "type": "tool_start", "call_id": call["id"], "name": call["name"], "arguments": call["args"],
        })
        try:
            # Validate the original JSON before ToolNode can turn schema failures into prose errors.
            self.owner._requests[call["name"]].model_validate(call["args"])
            # Bind this call's explicit runtime into the standard tool handler; no shared session context.
            scoped_tool = self.owner._make_tool(call["name"], context, request.state["messages"], call["id"])
            response = await handler(request.override(tool=scoped_tool))
        except ValidationError as error:
            result = {
                "status": "invalid_arguments", "message": "参数不符合工具约束，请按schema修正；没有执行查询。",
                "errors": [{"loc": list(item["loc"]), "type": item["type"]} for item in error.errors()[:12]],
            }
            response = ToolMessage(content=_dump(result), tool_call_id=call["id"], name=call["name"])
        if not isinstance(response, ToolMessage):
            from .core import _AgentFailure

            raise _AgentFailure("invalid_tool_result", "工具必须返回结构化JSON结果。")
        result = self.owner._parse_tool_result(response.content)
        record = ToolCallRecord(call_id=call["id"], name=call["name"], arguments=call["args"], result=result)
        context.records.append(record)
        self.owner._collect_result(call["name"], result, context.view_state)
        await self.owner._emit(context.on_event, {"type": "tool_end", **record.model_dump(mode="json")})
        return response
