"""Builds native tools and enforces skill, stage, argument and result boundaries."""

import asyncio
import copy
import inspect
import json
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import StructuredTool

from .boundary import _AgentFailure, _compact_result, _dump, _plain, _text_content
from .debug import tool_catalog
from .evidence import RetrievalEvidence
from .extensions import ToolExecutionContext

_TOOL_DESCRIPTIONS = {
    "find_ingredients": (
        "查找当前菜谱数据库中的真实食材候选；输入中文、英文、别名或USDA查询词。"
        "返回名称、身份、来源证据、参考关系和数据快照。先查候选再选择，不能编造ID。"
    ),
    "search_recipes": (
        "用真实食材候选和限制条件只读查询菜谱。ingredient_groups组内OR、组间AND。"
        "支持排除食材、耗时、厨具、口味、方法、营养及估算政策、排序和分页；返回匹配证据。"
        "max_total_time_min等限制须显式传入，本工具不会自动读取偏好表单。"
        "传expected_snapshot防止候选来自旧数据。没有结果时不要自行放宽条件。"
    ),
    "get_recipe": "用真实recipe_id和expected_snapshot读取菜谱食材、用量、步骤、营养、原文与warnings。",
    "get_search_options": "读取当前数据库可用的口味、方法、厨具、难度、营养指标及查询选项和快照。",
}


_PROTOCOL_PREFIXES = ("<｜dsml", "</｜dsml", "<|dsml", "</|dsml", "<｜tool▁", "<tool_call", "<function_call")


class AgentTools(RetrievalEvidence):
    """Internal part of ChefMateAgent; the core owns configuration and turn lifecycle."""

    def tool_catalog(self, ui_context=None, *, tools=None) -> list[dict[str, Any]]:
        """The actual bound schemas, with execution-stage availability made explicit."""
        result = tool_catalog(self.tools if tools is None else tools, self._extra_tools, ui_context)
        for item in result:
            binding = self._tool_skills.get(item["name"])
            item["required_skill"] = binding if isinstance(binding, str) else None
            if isinstance(binding, Mapping):
                item["required_skill_by_stage"] = copy.deepcopy(binding)
                item["enabled_target_stages"] = [stage for stage, skill in binding.items() if self.skills.enabled(skill)]
            if not self._tool_skill_available(item["name"]):
                item["available"] = False
                item["blockers"].append({"code": "skill_disabled", "skill": copy.deepcopy(binding)})
        return result

    def _tool_skill_available(self, name):
        binding = self._tool_skills.get(name)
        if isinstance(binding, Mapping):
            return any(self.skills.enabled(skill) for skill in binding.values())
        return not binding or self.skills.enabled(binding)

    def _memory_tool_names(self):
        return {name for name, binding in self._tool_skills.items() if binding == "personal-memory"}

    def _model_tools(self, context):
        """Bind executable operations; keep registered schemas for validating closed history."""
        stage = context.ui_context.get("stage", "idea")
        actions = context.ui_context.get("current_actions")
        result = []
        for tool in self.tools:
            if not self._tool_skill_available(tool.name):
                continue
            if not context.memory_enabled and tool.name in self._memory_tool_names():
                continue
            spec = self._extra_tools.get(tool.name)
            if spec and not spec.model_visible:
                continue
            if spec and spec.allowed_stages is not None and stage not in spec.allowed_stages:
                continue
            if spec and isinstance(actions, dict) and not actions.get(tool.name, {}).get("available", True):
                continue
            result.append(tool)
        return result

    def tool_batch_policy(self, calls, context):
        if len(calls) < 2:
            return None
        effects = [self._extra_tools[call["name"]].effect for call in calls if call["name"] in self._extra_tools]
        if any(effect in {"write", "wait", "presentation", "mixed"} for effect in effects):
            return "涉及任务修改或界面展示的操作请单独调用，等待成功结果和最新版本后再继续；本批未执行。"
        return None

    def _is_wait_tool(self, name: str) -> bool:
        return name == "ask_user" or (name in self._extra_tools and self._extra_tools[name].effect == "wait")

    def _make_tool(self, name: str, context=None, messages=None, call_id: str = "", tool_arguments=None) -> StructuredTool:
        async def execute(**kwargs):
            if context is None:
                raise _AgentFailure("tool_context_required", "工具执行缺少本轮上下文。")
            binding = self._tool_skills.get(name)
            if isinstance(binding, Mapping):
                skill = binding.get(kwargs.get("stage"))
                if skill is None:
                    return {"status": "invalid_arguments", "message": "目标阶段没有对应的技能权限，没有执行操作。"}
                required = [skill]
            else:
                required = [binding]
            if name == "get_recipe" and str(kwargs.get("recipe_id", "")).startswith("custom-"):
                required.append("recipe-authoring")
            for skill in required:
                if skill and not self.skills.enabled(skill):
                    return {"status": "skill_disabled", "skill": skill,
                            "message": "对应厨房技能已关闭，此工具没有执行；可以在设置中开启技能。"}
            if not context.memory_enabled and name in self._memory_tool_names():
                return {"status": "memory_disabled", "message": "个人偏好与长期记忆已关闭，此工具没有执行。"}
            history = self._serialize_messages(messages or [])
            call = {"name": name, "id": call_id, "args": kwargs}
            if name in _TOOL_DESCRIPTIONS:
                arguments = self._requests[name].model_validate(kwargs).model_dump(mode="json")
                fingerprint = json.dumps([name, arguments], sort_keys=True, ensure_ascii=False)
                if fingerprint in context.read_queries:
                    return {"status": "duplicate_query", "previous_call_id": context.read_queries[fingerprint],
                            "message": "本轮已经查询过相同条件，请使用该调用的结果。查某道菜请传name，更多候选请更改offset；不要重复空参数查询。"}
                context.read_queries[fingerprint] = call_id
                output = await self._invoke_tool(call, history)
                snapshot = output.get("snapshot")
                official = not (name == "get_recipe" and str(arguments.get("recipe_id", "")).startswith("custom-"))
                if official and isinstance(snapshot, dict):
                    if context.read_snapshot is not None and context.read_snapshot != snapshot:
                        context.read_queries.clear()
                    context.read_snapshot = copy.deepcopy(snapshot)
                if output.get("status") != "ok":
                    context.read_queries.pop(fingerprint, None)
                else:
                    context.read_queries[fingerprint] = call_id
                return output
            if name == "list_skills":
                return {"status": "ok", "items": self.skills.list_metadata()}
            if name == "load_skill":
                result = await asyncio.to_thread(self.skills.load, kwargs["name"])
                if result.get("status") == "ok" and kwargs["name"] not in context.loaded_skills:
                    size = len(result["content"])
                    if context.skill_chars + size > self.config.max_skill_chars:
                        return {"status": "skill_budget_exceeded", "message": "本轮Skill正文已达到读取预算。"}
                    context.skill_chars += size
                    context.loaded_skills.add(kwargs["name"])
                return result
            spec = self._extra_tools[name]
            if spec.allowed_stages is not None and context.ui_context.get("stage") not in spec.allowed_stages:
                return {"status": "stage_conflict", "message": "此工具当前阶段不可用，请先核对任务阶段。",
                        "stage": context.ui_context.get("stage"), "allowed_stages": list(spec.allowed_stages)}
            availability = context.ui_context.get("current_actions", {}).get(name, {})
            if availability.get("available") is False:
                return {"status": "operation_blocked", "message": "当前任务尚不满足这个操作的前置条件。",
                        "blockers": availability.get("blockers", []),
                        "recovery": "按最新任务状态中的current_actions与blockers选择可执行操作。"}
            previous_ui = copy.deepcopy(context.ui_context)
            execution = ToolExecutionContext(
                ui_context=copy.deepcopy(context.ui_context), host_context=context.host_context,
                messages=history, call_id=call_id,
            )
            # StructuredTool materializes defaults in coroutine kwargs. Revalidate the
            # original model JSON so omitted nested patch fields stay omitted across
            # the application boundary, including explicit null versus absent values.
            supplied = tool_arguments if tool_arguments is not None else kwargs
            request = spec.args_schema.model_validate(supplied).model_dump(mode="json", exclude_unset=True)
            try:
                result = await spec.handler(request, execution) if inspect.iscoroutinefunction(spec.handler) else (
                    await asyncio.to_thread(spec.handler, request, execution)
                )
                if inspect.isawaitable(result):
                    result = await result
                if spec.response_model is not None:
                    result = spec.response_model.model_validate(_plain(result))
                plain = _plain(result)
                if not isinstance(plain, dict):
                    raise ValueError("Tool result must be a mapping")
                if plain.get("status") == "ok":
                    if spec.required_skill == "personal-memory":
                        # Historical search results also enter ToolMessages even
                        # when they do not replace the compact memory context.
                        context.memory_accessed = True
                    if spec.required_skill == "personal-memory" and isinstance(plain.get("memory"), dict):
                        memory = plain["memory"]
                        refreshed = {key: memory[key] for key in
                                     ("revision", "user_notes", "summary", "updated_at") if key in memory}
                        if len(_dump(refreshed)) <= self.config.max_memory_chars:
                            context.memory_context = refreshed
                    actions = plain.get("actions", [])
                    if not isinstance(actions, list) or (actions and self.action_model is None):
                        raise ValueError("UI action schema is required")
                    validated = [self.action_model.model_validate(action).model_dump(mode="json") for action in actions]
                    if plain.get("ui_snapshot") is not None:
                        context.ui_context = self._validate_ui_context(plain["ui_snapshot"])
                    for action in validated:
                        context.actions.append(action)
                        await self._emit(context.on_event, {"type": "ui_action", "action": action})
                    if self._is_wait_tool(name) and isinstance(plain.get("pending_question"), dict):
                        # The question is already committed by the host. Its terminal receipt must
                        # not be replaced by output_too_large merely because the workspace is large.
                        receipt = plain.get("receipt")
                        if receipt is not None and (not isinstance(receipt, dict) or len(_dump(receipt)) > 1000):
                            raise ValueError("Wait receipt must be a compact mapping")
                        context.pending_question = copy.deepcopy(plain["pending_question"])
                        return {"status": "ok", "question_id": context.pending_question.get("id"),
                                "revision": context.ui_context.get("revision"),
                                **({"receipt": copy.deepcopy(receipt)} if receipt is not None else {})}
                # Full typed actions go to the UI separately. The model needs the compact state/receipts only.
                projection = {key: value for key, value in plain.items() if key not in {"workspace", "views", "actions"}}
                if projection.get("ui_snapshot"):
                    projection["ui_snapshot"] = self._snapshot_delta(previous_ui, projection["ui_snapshot"])
                if plain.get("actions"):
                    projection["actions"] = [
                        {key: action[key] for key in ("kind", "action_id", "mode", "view_kind") if key in action}
                        if action.get("kind") == "set_kitchen_display" else
                        {"kind": action.get("kind"), "component": {
                            key: action.get("component", {}).get(key) for key in ("id", "kind", "title")
                            if key in action.get("component", {})
                        }} for action in plain["actions"]]
                return _compact_result(projection, self.config.max_tool_result_chars)
            except Exception:
                return {"status": "tool_error", "message": "业务工具无法完成操作，请查看当前状态后重试。"}

        return StructuredTool.from_function(
            name=name, description=self._descriptions[name], args_schema=self._requests[name], coroutine=execute,
            return_direct=self._is_wait_tool(name),
        )

    def _validate_text(self, text: str) -> None:
        if any(marker in text.casefold() for marker in _PROTOCOL_PREFIXES):
            raise _AgentFailure("tool_protocol_leak", "模型把工具协议当作文本返回，本轮未执行这些调用。", True)
        if len(text) > self.config.max_output_tokens * 8:
            raise _AgentFailure("output_limit", "模型回答超过本轮输出限制。", True)

    def _validate_response(self, response: AIMessage) -> None:
        self._validate_text(_text_content(response))
        finish = response.response_metadata.get("finish_reason")
        if finish in {"length", "content_filter"}:
            raise _AgentFailure("incomplete_response", "模型输出被截断，本轮工具调用未执行。", True)
        raw_calls = response.additional_kwargs.get("tool_calls", [])
        if isinstance(response, AIMessageChunk):
            raw_calls = [{"function": {"arguments": part.get("args", "")}} for part in response.tool_call_chunks]
        for raw_call in raw_calls:
                try:
                    parsed = json.loads(raw_call.get("function", {}).get("arguments", ""))
                except (TypeError, ValueError):
                    raise _AgentFailure("incomplete_tool_batch", "模型工具参数不完整，本批调用未执行。", True) from None
                if not isinstance(parsed, dict):
                    raise _AgentFailure("invalid_tool_batch", "模型工具参数必须是JSON对象，本批调用未执行。", True)

    def _validate_tool_batch(self, response: AIMessage) -> list[dict[str, Any]]:
        if response.invalid_tool_calls:
            raise _AgentFailure("invalid_tool_batch", "模型工具参数无效，本批调用未执行。", True)
        calls = response.tool_calls
        if len(calls) > 1 and any(self._is_wait_tool(call.get("name")) for call in calls):
            raise _AgentFailure("invalid_tool_batch", "提问工具必须单独调用；本批没有执行任何操作。", True)
        if response.response_metadata.get("finish_reason") == "tool_calls" and not calls:
            raise _AgentFailure("incomplete_tool_batch", "模型声明了工具调用但没有完整调用，本轮未执行。", True)
        ids: set[str] = set()
        for call in calls:
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id or call_id in ids:
                raise _AgentFailure("invalid_tool_batch", "模型工具调用标识无效，本批调用未执行。", True)
            if call.get("name") not in self._requests or not isinstance(call.get("args"), dict):
                raise _AgentFailure("invalid_tool_batch", "模型请求了未知工具或无效参数，本批调用未执行。", True)
            if len(_dump(call["args"])) > 18000:
                raise _AgentFailure("invalid_tool_batch", "模型工具参数超过大小限制，本批调用未执行。")
            ids.add(call_id)
        return calls
