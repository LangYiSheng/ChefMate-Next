"""Assembles the standard LangChain agent and commits only complete turns."""

import asyncio
import copy
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, ValidationError

from .boundary import EventCallback, _AgentFailure, _dump, _text_content, _UserCancelled
from .extensions import InjectedToolSpec
from .history import HistoryBoundary
from .models import AgentConfig, AgentError, AgentTurnResult
from .prompt import SYSTEM_PROMPT
from .provider import IntegrityChatOpenAI, ProviderIntegrityError
from .runner import ChefMateMiddleware, TurnRuntimeContext
from .skills import SkillListRequest, SkillLoadRequest, SkillRegistry
from .suggestions import REPLY_SUGGESTIONS_TOOL, finalize_reply_suggestions, make_reply_suggestions_tool
from .telemetry import usage_record, usage_summary
from .tool_execution import _TOOL_DESCRIPTIONS, AgentTools


class RetrievalServiceProtocol(Protocol):
    def find_ingredients(self, request: dict[str, Any]) -> Any: ...
    def search_recipes(self, request: dict[str, Any]) -> Any: ...
    def get_recipe(self, request: dict[str, Any]) -> Any: ...
    def get_search_options(self) -> Any: ...


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChefMateAgent(HistoryBoundary, AgentTools):
    """Inject a synchronous read-only retrieval service and optional LangChain-compatible model.

    ``messages`` use OpenAI-style JSON dictionaries. Returned history is only committed
    after a complete assistant reply, so callers can persist it without partial tool batches.
    Instances hold no conversation state and may be shared across independent sessions.
    """

    def __init__(
        self,
        service: RetrievalServiceProtocol,
        config: AgentConfig,
        model: Any | None = None,
        *,
        request_models: Mapping[str, type[BaseModel]] | None = None,
        extra_tools: Sequence[InjectedToolSpec] = (),
        ui_context_model: type[BaseModel] | None = None,
        action_model: type[BaseModel] | None = None,
        skills: SkillRegistry | None = None,
        tool_skill_bindings: Mapping[str, str | Mapping[str, str]] | None = None,
    ):
        self.service = service
        self.config = config
        if request_models is None:
            from chefmate_retrieval import IngredientLookupRequest, RecipeDetailRequest, RecipeSearchRequest

            request_models = {
                "find_ingredients": IngredientLookupRequest,
                "search_recipes": RecipeSearchRequest,
                "get_recipe": RecipeDetailRequest,
            }
        self._requests = {**request_models, "get_search_options": EmptyRequest}
        if set(self._requests) != set(_TOOL_DESCRIPTIONS):
            raise ValueError("request_models must define exactly the three retrieval request models")
        self.skills = skills or SkillRegistry()
        self.ui_context_model = ui_context_model
        self.action_model = action_model
        self._extra_tools: dict[str, InjectedToolSpec] = {}
        self._tool_skills = copy.deepcopy(dict(tool_skill_bindings or {}))
        self._descriptions = {**_TOOL_DESCRIPTIONS,
            "list_skills": "列出显式启用的本地Skills名称与简介；不读取所有正文，不提供shell或任意文件访问。",
            "load_skill": "按已启用名称渐进读取Skill正文；遵循目录allowlist和读取预算，不执行脚本。",
        }
        self._requests.update(list_skills=SkillListRequest, load_skill=SkillLoadRequest)
        suggestion_tools = (make_reply_suggestions_tool(config.reply_suggestion_count),) if config.reply_suggestions else ()
        for spec in (*extra_tools, *suggestion_tools):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", spec.name) or spec.name in self._requests:
                raise ValueError("Injected tool name is invalid or duplicated")
            self._requests[spec.name] = spec.args_schema
            self._descriptions[spec.name] = spec.description + (
                " 可用阶段：" + "、".join(spec.allowed_stages) + "；其他阶段调用会被拒绝。" if spec.allowed_stages else "")
            self._extra_tools[spec.name] = spec
            if spec.required_skill:
                self._tool_skills[spec.name] = spec.required_skill
        def valid_binding(binding):
            def valid_name(value):
                return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value)
            return valid_name(binding) if isinstance(binding, str) else (
                isinstance(binding, Mapping) and bool(binding)
                and all(valid_name(stage) and valid_name(skill) for stage, skill in binding.items()))
        if any(name not in self._requests or not valid_binding(binding)
               for name, binding in self._tool_skills.items()):
            raise ValueError("Tool skill bindings must reference registered tools and valid skill names")
        self._base_model = model if model is not None else self._create_model()
        self.tools = [self._make_tool(name) for name in self._requests]
        self._tool_definitions = [convert_to_openai_tool(tool) for tool in self.tools]
        self.runner = create_agent(
            self._base_model, tools=self.tools, system_prompt=SYSTEM_PROMPT,
            middleware=[ChefMateMiddleware(self)], context_schema=TurnRuntimeContext,
        )

    def _debug_secrets(self):
        return (self.config.api_key.get_secret_value(),) if self.config.api_key else ()

    def _create_model(self) -> ChatOpenAI:
        if self.config.api_key is None or not self.config.api_key.get_secret_value().strip():
            raise ValueError("A real provider API key is required; production has no mock fallback")
        options: dict[str, Any] = {
            "model": self.config.model,
            "api_key": self.config.api_key.get_secret_value(),
            "timeout": self.config.model_timeout_s,
            "max_retries": self.config.max_model_retries,
            "max_tokens": self.config.max_output_tokens,
            "streaming": self.config.stream_tool_calls,
            "disable_streaming": False if self.config.stream_tool_calls else "tool_calling",
            "stream_usage": False,
            "use_responses_api": self.config.use_responses_api,
            "model_kwargs": copy.deepcopy(self.config.model_kwargs),
        }
        if self.config.base_url:
            options["base_url"] = self.config.base_url
        if self.config.temperature is not None:
            options["temperature"] = self.config.temperature
        return IntegrityChatOpenAI(**options)

    def _context_prompt(self, context: TurnRuntimeContext, messages) -> str:
        prompt = SYSTEM_PROMPT
        if self.config.reply_suggestions:
            prompt += (f"\n可选择调用suggest_replies提供{self.config.reply_suggestion_count}个不同方向的用户口吻草稿，最多尝试一次；"
                       "缺少建议或调用失败会由系统提供默认草稿，直接完成主回答，不为此重复调用。"
                       "需要ask_user等待回答时不生成推荐。不要在最终正文重复列出建议。")
        metadata = self.skills.list_metadata()
        if metadata:
            prompt += "\n已启用的能力指引仅列元数据，需要细节时调用load_skill；关闭所属能力会同时禁用其工具：\n" + _dump(metadata)
        return prompt

    @staticmethod
    def _usage_record(response, round_number, *, model=""):
        return usage_record(response, round_number, model=model)

    @staticmethod
    def _usage_summary(context):
        return usage_summary(copy.deepcopy(context.usage), attempted_calls=len(context.debug_trace))

    async def run_turn(
        self,
        messages: Sequence[dict[str, Any]],
        on_event: EventCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        *,
        ui_context: dict[str, Any] | BaseModel | None = None,
        tool_context: Any = None,
        image_loader: Callable | None = None,
        memory_context: dict[str, Any] | None = None,
        memory_enabled: bool = True,
        user_identity: dict[str, str] | None = None,
    ) -> AgentTurnResult:
        original = copy.deepcopy(list(messages))
        context = TurnRuntimeContext(on_event=on_event, host_context=tool_context)
        try:
            if type(memory_enabled) is not bool:
                raise _AgentFailure("invalid_memory_context", "长期记忆授权状态无效。")
            private_tools = self._memory_tool_names()
            context.memory_enabled = memory_enabled and (not private_tools or any(
                self._tool_skill_available(name) for name in private_tools))
            if user_identity is not None:
                if (not isinstance(user_identity, dict) or set(user_identity) != {"display_name"}
                        or not isinstance(user_identity["display_name"], str)
                        or not 1 <= len(user_identity["display_name"]) <= 40
                        or any(ord(char) < 32 for char in user_identity["display_name"])):
                    raise _AgentFailure("invalid_user_identity", "用户称呼格式无效。")
                context.user_identity = dict(user_identity)
            if memory_context is not None and context.memory_enabled:
                if not isinstance(memory_context, dict):
                    raise _AgentFailure("invalid_memory_context", "长期记忆格式无效。")
                try:
                    memory_size = len(_dump(memory_context))
                except (TypeError, ValueError):
                    raise _AgentFailure("invalid_memory_context", "长期记忆必须是有限JSON数据。") from None
                if memory_size > self.config.max_memory_chars:
                    raise _AgentFailure("memory_context_limit", "长期记忆超过本轮读取预算，请精简记忆。")
                context.memory_context = copy.deepcopy(memory_context)
            working = self._prepare_history(original, reserved_chars=(len(_dump(context.memory_context))
                                            if context.memory_context else 0) + (len(_dump(context.user_identity)) if context.user_identity else 0))
            if working[-1].get("images") and not self.config.supports_images:
                raise _AgentFailure("images_not_supported", "当前模型没有开启图片能力，请更换模型或开启支持图片。")
            if self.config.supports_images:
                for message in working:
                    for reference in message.get("images", []):
                        if reference["id"] in context.image_contents:
                            continue
                        if image_loader is None:
                            raise _AgentFailure("image_unavailable", "图片无法读取，请重新上传。")
                        try:
                            value = await asyncio.to_thread(image_loader, reference)
                        except (ValueError, OSError):
                            raise _AgentFailure("image_unavailable", "图片无法读取，请重新上传。") from None
                        if not isinstance(value, str) or not value.startswith("data:image/jpeg;base64,") or len(value) > 2800000:
                            raise _AgentFailure("image_unavailable", "图片格式无效，请重新上传。")
                        context.image_contents[reference["id"]] = value
            context.ui_context = self._validate_ui_context(ui_context)
            if context.ui_context:
                working[-1]["ui_context"] = copy.deepcopy(context.ui_context)
                # Preserve the human's initial state even if generated assistant/tool messages are rolled back.
                original[-1]["ui_context"] = copy.deepcopy(context.ui_context)
            for message in working:
                if message["role"] == "tool":
                    try:
                        previous = json.loads(message["content"])
                    except ValueError:
                        continue
                    if isinstance(previous, dict):
                        self._collect_result(message["name"], previous, context.view_state)
            if cancel_event is not None and cancel_event.is_set():
                raise _UserCancelled()
            await self._emit(on_event, {"type": "status", "status": "thinking", "message": "正在理解需求"})
            async with asyncio.timeout(self.config.turn_timeout_s):
                final_messages = await self._wait_checked(self._stream_runner(working, context), cancel_event)
            pending_question = None
            if final_messages and isinstance(final_messages[-1], ToolMessage) and self._is_wait_tool(final_messages[-1].name):
                question_result = self._parse_tool_result(final_messages[-1].content)
                pending_question = context.pending_question
                if (question_result.get("status") != "ok" or not isinstance(pending_question, dict)
                        or not isinstance(pending_question.get("question"), str) or not pending_question.get("id")):
                    raise _AgentFailure("question_not_saved", "这个问题未能保存，请同步当前状态后重新提问。", True)
                answer = pending_question["question"]
            elif not final_messages or not isinstance(final_messages[-1], AIMessage):
                raise _AgentFailure("invalid_response", "智能体未返回完整回答。", True)
            else:
                answer = _text_content(final_messages[-1]).strip()
                if not answer or final_messages[-1].tool_calls:
                    raise _AgentFailure("empty_response", "模型没有返回完整可用回答。", True)
            complete = self._serialize_messages(final_messages)
            # Revalidate the complete tool batch/history before exposing it for persistence.
            self._prepare_history([*complete, {"role": "user", "content": "history-validation"}])
            await self._emit(on_event, {"type": "status", "status": "completed",
                                        "message": "等待你的回答" if pending_question else "查询完成"})
            return AgentTurnResult(
                status="completed", assistant_text=answer, messages=complete, tool_calls=context.records,
                actions=context.actions, usage=self._usage_summary(context), context_usage=context.context_usage, **context.view_state,
                awaiting_user=bool(pending_question), pending_question=pending_question,
                debug_trace=context.debug_trace, skill_events=context.skill_events,
                suggested_replies=finalize_reply_suggestions(next((
                    record.result.get("suggested_replies") for record in reversed(context.records)
                    if record.name == REPLY_SUGGESTIONS_TOOL and record.result.get("status") == "ok"
                ), None), context.ui_context, count=self.config.reply_suggestion_count) if self.config.reply_suggestions and not pending_question else [],
            )
        except _UserCancelled:
            await self._emit(on_event, {"type": "status", "status": "cancelled", "message": "已停止本轮查询"})
            return AgentTurnResult(status="cancelled", messages=original, tool_calls=context.records,
                                   actions=context.actions, usage=self._usage_summary(context), context_usage=context.context_usage,
                                   debug_trace=context.debug_trace, skill_events=context.skill_events)
        except TimeoutError:
            error = AgentError(code="turn_timeout", message="本轮请求超时，可以重试。", retryable=True)
        except _AgentFailure as exc:
            error = exc.error
        except ValidationError:
            error = AgentError(code="invalid_history", message="对话历史格式不正确。")
        except ProviderIntegrityError as exc:
            if context.debug_trace:
                context.debug_trace[-1]["parameter_integrity"] = copy.deepcopy(exc.diagnostics)
                await self._emit(on_event, {"type": "debug_context", "context": context.debug_trace[-1]})
            error = AgentError(code=exc.code, message="模型返回的工具参数未通过完整性校验，本批操作未执行，可以重试。",
                               retryable=True)
        except Exception:
            # Provider exception strings may contain request URLs, headers or credentials.
            error = AgentError(code="model_error", message="模型服务暂时无法完成请求，请检查配置后重试。", retryable=True)
        await self._emit(on_event, {"type": "status", "status": "error", "message": error.message, "code": error.code})
        return AgentTurnResult(status="error", messages=original, tool_calls=context.records,
                               actions=context.actions, usage=self._usage_summary(context), context_usage=context.context_usage, error=error,
                               debug_trace=context.debug_trace, skill_events=context.skill_events)

    @staticmethod
    async def _emit(callback: EventCallback | None, event: dict[str, Any]) -> None:
        if callback is not None:
            response = callback(copy.deepcopy(event))
            if inspect.isawaitable(response):
                await response

    @staticmethod
    async def _wait_checked(awaitable: Awaitable[Any], cancel_event: asyncio.Event | None) -> Any:
        task = asyncio.create_task(awaitable)
        cancelled = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        try:
            if cancelled is None:
                return await task
            done, _ = await asyncio.wait({task, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                raise _UserCancelled()
            return await task
        finally:
            for pending in (task, cancelled):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(*(pending for pending in (task, cancelled) if pending is not None),
                                 return_exceptions=True)

    async def _stream_runner(self, history: list[dict[str, Any]], context: TurnRuntimeContext) -> list[BaseMessage]:
        current = add_messages([], self._to_langchain(history))
        accumulated: dict[str, str] = {}
        pending: dict[str, str] = {}
        stream = self.runner.astream(
            {"messages": current}, context=context, stream_mode=["messages", "updates"],
            config={"recursion_limit": self.config.max_model_rounds * 5 + 10},
        )
        try:
            async for mode, data in stream:
                if mode == "messages":
                    chunk, metadata = data
                    if not isinstance(chunk, (AIMessage, AIMessageChunk)):
                        continue
                    key = chunk.id or str(metadata.get("langgraph_step"))
                    text = _text_content(chunk)
                    accumulated[key] = accumulated.get(key, "") + text
                    self._validate_text(accumulated[key])
                    pending[key] = pending.get(key, "") + text
                    if len(pending[key]) > 64:
                        safe, pending[key] = pending[key][:-64], pending[key][-64:]
                        await self._emit(context.on_event, {"type": "assistant_delta", "text": safe, "delta": safe})
                elif mode == "updates":
                    for update in data.values():
                        if not isinstance(update, dict) or not update.get("messages"):
                            continue
                        current = add_messages(current, update["messages"])
                        for message in update["messages"]:
                            if isinstance(message, AIMessage):
                                self._validate_response(message)
                                self._validate_tool_batch(message)
                    for key, tail in list(pending.items()):
                        if tail:
                            await self._emit(context.on_event, {"type": "assistant_delta", "text": tail, "delta": tail})
                            pending[key] = ""
        finally:
            closer = getattr(stream, "aclose", None)
            if closer is not None:
                await closer()
        return current
