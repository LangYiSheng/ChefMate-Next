"""Turn lifecycle, durable final results, and disposable SSE subscribers."""

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

from chefmate_agent.suggestions import REPLY_SUGGESTIONS_TOOL, finalize_reply_suggestions

from .sessions import SessionRepository, now


class TurnConflict(ValueError):
    pass


@dataclass
class ActiveTurn:
    id: str
    session_id: str
    request_id: str
    message: str
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    task: asyncio.Task | None = None
    last_persist: float = 0
    started: bool = False
    progress: dict = field(default_factory=dict)
    ui_context: dict | None = None
    host_context: object | None = None
    sequence: int = 0

    def emit(self, event: dict) -> None:
        # A browser can detach while generation continues. Never block the agent on an absent reader.
        if self.events.full():
            self.events.get_nowait()
        self.sequence += 1
        self.events.put_nowait({**copy.deepcopy(event), "session_id": self.session_id,
                               "turn_id": self.id, "sequence": self.sequence})


class TurnManager:
    def __init__(self, repository: SessionRepository, agent, workspace_binding=None, images=None, *, reply_suggestions=True):
        self.repository = repository
        self.agent = agent
        self.workspace_binding = workspace_binding
        self.images = images
        self.reply_suggestions = reply_suggestions
        self.active: dict[str, ActiveTurn] = {}
        self._lock = asyncio.Lock()

    async def begin(self, session_id: str, message: str, *, retry: bool = False,
                    request_id: str | None = None, ui_snapshot: dict | None = None,
                    image_ids: list[str] | None = None) -> tuple[ActiveTurn | None, dict | None]:
        image_ids = image_ids or []
        async with self._lock:
            state = self.repository.get(session_id)
            if state is None:
                raise KeyError(session_id)
            if session_id in self.active:
                raise TurnConflict("这段对话仍在运行，请先停止或等待完成。")
            last = state.get("last_run") or {}
            if request_id and last.get("request_id") == request_id and last.get("status") == "completed":
                previous_message = last.get("message")
                if previous_message is None and len(state["messages"]) >= 2:
                    previous_message = state["messages"][-2].get("content")
                if previous_message != message or last.get("image_ids", []) != image_ids:
                    raise TurnConflict("请求标识已经用于另一条消息，请重新发送。")
                replay = copy.deepcopy(last["result"])
                if not self.reply_suggestions:
                    replay["suggested_replies"] = []
                return None, replay
            turn = ActiveTurn(str(uuid4()), session_id, request_id or str(uuid4()), message)
            def prepare(current):
                references = self.images.references(current, image_ids) if self.images else []
                # This snapshot is rebuilt from the authoritative aggregate, not client recipe/form JSON.
                snapshot = self.workspace_binding.service(session_id).model_snapshot() if self.workspace_binding else None
                if ui_snapshot is not None and snapshot is not None and ui_snapshot["revision"] != snapshot["revision"]:
                    raise TurnConflict("界面已更新，请同步最新任务状态后再发送；你的文字没有丢失。")
                tail = current["messages"][-1] if current["messages"] else None
                reuse = (retry and current.get("last_status") in {"error", "cancelled", "interrupted"}
                         and tail and tail.get("role") == "user" and tail.get("content") == message
                         and [image["id"] for image in tail.get("images", [])] == image_ids)
                if retry and not reuse:
                    raise TurnConflict("上一轮已改变，无法重试这条消息；请重新发送。")
                if not reuse:
                    user = {"id": str(uuid4()), "role": "user", "content": message, "created_at": now()}
                    if references:
                        user["images"] = references
                    if snapshot is not None:
                        user["ui_context"] = snapshot
                    current["messages"].append(user)
                elif snapshot is not None:
                    tail["ui_context"] = snapshot
                if current["title"] == "新对话":
                    current["title"] = message[:32] or "图片对话"
                current.update(running=True, last_status="running", last_error=None, updated_at=now(),
                               active_turn={"id": turn.id, "request_id": turn.request_id, "message": message,
                                            "status": "starting", "text": "", "process": [],
                                            "tool_calls": [], "actions": [], "ui_context": snapshot})
                turn.progress = copy.deepcopy(current["active_turn"])
                last_assistant = next((i for i in range(len(current["messages"]) - 1, -1, -1)
                                       if current["messages"][i].get("role") == "assistant"), -1)
                pending = [{key: item[key] for key in ("role", "content", "ui_context", "images") if key in item}
                           for item in current["messages"][last_assistant + 1:] if item.get("role") == "user"]
                return copy.deepcopy([*current["history"], *pending]), snapshot
            history, turn.ui_context = self.repository.mutate(session_id, prepare)
            if self.workspace_binding:
                turn.host_context = self.workspace_binding.for_turn(session_id, turn.id, turn.cancel,
                                                                   turn.ui_context["revision"])
            self.active[session_id] = turn
            turn.task = asyncio.create_task(self._run(turn, history), name=f"chefmate-turn-{turn.id}")
            return turn, None

    async def _run(self, turn: ActiveTurn, history: list[dict]) -> None:
        turn.started = True
        async def on_event(event):
            payload = copy.deepcopy(event)
            if payload.get("type") in {"tool_start", "tool_end"} and payload.get("name") == REPLY_SUGGESTIONS_TOOL:
                # Draft suggestions belong to the finished answer, never to intermediate UI/log events.
                return
            turn.emit(payload)
            progress = turn.progress
            kind = payload.get("type")
            if kind == "assistant_delta":
                progress["text"] += payload.get("text", payload.get("delta", ""))
            elif kind == "status":
                progress["status"] = payload.get("status", payload.get("message", "running"))
            elif kind in {"tool_start", "tool_end"}:
                if kind == "tool_start" and progress["text"]:
                    progress["process"].append(progress["text"])
                    progress["text"] = ""
                call_id = payload.get("call_id")
                existing = next((x for x in progress["tool_calls"] if x.get("call_id") == call_id), None)
                if existing is None:
                    existing = {"call_id": call_id}
                    progress["tool_calls"].append(existing)
                existing.update({k: v for k, v in payload.items() if k != "type"})
                existing["status"] = "completed" if kind == "tool_end" else "running"
            elif kind == "ui_action":
                progress["actions"].append(payload["action"])
            elif kind == "model_usage":
                progress["usage"] = payload["usage"]
            instant = time.monotonic()
            if kind != "assistant_delta" or instant - turn.last_persist >= 0.4:
                def persist(state):
                    if (state.get("active_turn") or {}).get("id") == turn.id:
                        state["active_turn"] = copy.deepcopy(progress)
                self.repository.mutate(turn.session_id, persist)
                turn.last_persist = instant

        try:
            if turn.cancel.is_set():
                raise asyncio.CancelledError
            arguments = {"on_event": on_event, "cancel_event": turn.cancel}
            if self.images and any(message.get("images") for message in history):
                arguments["image_loader"] = lambda reference: self.images.data_url(turn.session_id, reference)
            if turn.ui_context is not None:
                arguments.update(ui_context=turn.ui_context, tool_context=turn.host_context)
            result = await self.agent.run_turn(history, **arguments)
            data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        except asyncio.CancelledError:
            data = {"status": "cancelled", "assistant_text": "", "messages": history,
                    "recipes": [], "tool_calls": [], "selected_recipe": None, "snapshot": None, "error": None}
        except Exception:
            # Provider/DB exceptions may contain connection details; never expose their repr to the browser.
            data = {"status": "error", "assistant_text": "", "messages": history, "recipes": [],
                    "tool_calls": [], "selected_recipe": None, "snapshot": None,
                    "error": {"code": "turn_failed", "message": "这一轮未完成，请重试或检查服务配置。", "retryable": True}}
        status = data.get("status", "error")
        visible = {
            "type": "result", "status": status, "text": data.get("assistant_text", ""),
            "recipes": data.get("recipes", []), "selected_recipe": data.get("selected_recipe"),
            "tool_calls": [item for item in data.get("tool_calls", []) if item.get("name") != REPLY_SUGGESTIONS_TOOL],
            "snapshot": data.get("snapshot"),
            "actions": data.get("actions", []), "process": turn.progress.get("process", []),
            "usage": data.get("usage", {}), "suggested_replies": [],
        }
        async with self._lock:
            def finish(state):
                if (state.get("active_turn") or {}).get("id") != turn.id:
                    return
                if self.workspace_binding:
                    visible["workspace"] = self.workspace_binding.service(turn.session_id).model_snapshot()
                if status == "completed":
                    if self.reply_suggestions:
                        visible["suggested_replies"] = finalize_reply_suggestions(
                            data.get("suggested_replies"), visible.get("workspace") or turn.ui_context)
                    state["history"] = data["messages"]
                    state["messages"].append({
                        "id": str(uuid4()), "turn_id": turn.id, "role": "assistant", "content": visible["text"],
                        "created_at": now(), "actions": visible["actions"], "process": visible["process"],
                        "recipes": visible["recipes"], "selected_recipe": visible["selected_recipe"],
                        "tool_calls": visible["tool_calls"], "snapshot": visible["snapshot"],
                        "usage": visible["usage"], "suggested_replies": visible["suggested_replies"],
                    })
                state.update(running=False, active_turn=None, updated_at=now(), last_status=status,
                             last_error=data.get("error"),
                             last_run={"id": turn.id, "request_id": turn.request_id, "status": status,
                                       "message": turn.message, "image_ids": image_ids_for_turn(history), "result": visible})
            self.repository.mutate(turn.session_id, finish)
            self.active.pop(turn.session_id, None)
        if status == "error":
            turn.emit({"type": "error", **(data.get("error") or {
                "code": "turn_failed", "message": "这一轮未完成。", "retryable": True,
            })})
        elif status == "completed":
            turn.emit(visible)
        else:
            turn.emit({"type": "status", "status": "cancelled", "message": "已停止生成。"})
        turn.emit({"type": "done", "status": status})

    async def cancel(self, session_id: str) -> bool:
        async with self._lock:
            turn = self.active.get(session_id)
            if turn is None:
                return False
            turn.cancel.set()
            # The provider await is interruptible even if it never produces a stream chunk.
            if turn.task and turn.started:
                turn.task.cancel()
            return True

    async def close(self) -> None:
        turns = list(self.active.values())
        for turn in turns:
            turn.cancel.set()
            if turn.task and turn.started:
                turn.task.cancel()
        if turns:
            await asyncio.gather(*(t.task for t in turns if t.task), return_exceptions=True)


def encode_event(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n\n"


def image_ids_for_turn(history):
    last_user = next((message for message in reversed(history) if message.get("role") == "user"), {})
    return [image["id"] for image in last_user.get("images", [])]
