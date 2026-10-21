"""Interactive waiting and the actual, safe model-request inspection boundary."""

import asyncio
import json

from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from pydantic import BaseModel, ConfigDict

from chefmate_agent import AgentConfig, ChefMateAgent, InjectedToolSpec
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool


class QuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


QUESTION = {"id": "question-1", "question": "想用哪种厨具？", "title": "确认厨具",
            "options": [{"id": "pot", "label": "汤锅"}], "allow_custom": True, "multiple": False}


def make_core(responses, specs=(), **config):
    model = ScriptedModel(responses)
    core = ChefMateAgent(Service(), AgentConfig(model="test", **config), model=model,
                        request_models=REQUESTS, extra_tools=specs)
    return core, model


def test_question_stops_after_its_closed_tool_result_and_answer_continues_normally():
    spec = InjectedToolSpec("ask_user", "等待用户回答", QuestionInput,
                            lambda request, context: {"status": "ok", "pending_question": QUESTION})
    core, model = make_core([tool("ask_user", {"question": QUESTION["question"]}), answer("收到，使用汤锅。")], [spec])
    first = asyncio.run(core.run_turn([{"role": "user", "content": "用手头的厨具做吧。"}]))
    assert first.status == "completed" and first.awaiting_user and first.pending_question == QUESTION
    assert first.assistant_text == QUESTION["question"] and first.suggested_replies == []
    assert len(model.histories) == 1
    assert [message["role"] for message in first.messages] == ["user", "assistant", "tool"]
    assert first.messages[-1]["tool_call_id"] == first.messages[-2]["tool_calls"][0]["id"]
    resumed = asyncio.run(core.run_turn([*first.messages, {"role": "user", "content": "我选择汤锅。"}],
                                       ui_context={"last_answer": {"question_id": QUESTION["id"], "option_ids": ["pot"]}}))
    assert resumed.status == "completed" and not resumed.awaiting_user and len(model.histories) == 2
    assert any(isinstance(message, ToolMessage) and message.name == "ask_user" for message in model.histories[-1])


def test_question_batch_is_rejected_before_any_sibling_operation_runs():
    effects = []
    spec = InjectedToolSpec("ask_user", "提问", QuestionInput,
                            lambda request, context: effects.append(request))
    response = [AIMessageChunk(content="", tool_call_chunks=[
        {"name": "ask_user", "args": json.dumps({"question": "可以吗？"}), "id": "q", "index": 0},
        {"name": "get_search_options", "args": "{}", "id": "r", "index": 1},
    ])]
    core, model = make_core([response], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "开始"}]))
    assert result.error.code == "invalid_tool_batch" and not effects and not core.service.calls
    assert len(model.histories) == 1


def test_unsaved_question_does_not_pretend_to_wait_for_an_answer():
    spec = InjectedToolSpec("ask_user", "提问", QuestionInput,
                            lambda request, context: {"status": "revision_conflict"})
    core, model = make_core([tool("ask_user", {"question": "可以吗？"})], [spec])
    result = asyncio.run(core.run_turn([{"role": "user", "content": "开始"}]))
    assert result.status == "error" and result.error.code == "question_not_saved"
    assert not result.awaiting_user and result.pending_question is None
    assert len(result.debug_trace) == len(model.histories) == 1


def test_saved_question_waits_even_when_workspace_exceeds_the_tool_output_budget():
    snapshot = {"revision": 7, "stage": "prep", "pending_question": QUESTION,
                "components": [{"id": f"large-{index}", "data": {f"field-{field}": "备菜说明" * 75
                               for field in range(8)}} for index in range(8)]}
    assert len(json.dumps(snapshot, ensure_ascii=False)) > 2000
    spec = InjectedToolSpec("ask_user", "提问", QuestionInput,
                            lambda request, context: {"status": "ok", "pending_question": QUESTION,
                                                       "ui_snapshot": snapshot})
    core, model = make_core([tool("ask_user", {"question": QUESTION["question"]})], [spec],
                            max_tool_result_chars=2000)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "你问问我。"}]))
    assert result.status == "completed" and result.awaiting_user and result.pending_question == QUESTION
    assert len(model.histories) == 1
    receipt = json.loads(result.messages[-1]["content"])
    assert receipt == {"status": "ok", "question_id": QUESTION["id"], "revision": 7}
    assert result.messages[-1]["tool_call_id"] == result.messages[-2]["tool_calls"][0]["id"]
    assert result.tool_calls[-1].result == receipt


def test_real_injected_preference_patch_preserves_unsupplied_domain_values():
    import copy
    from chefmate_workflow import UIAction, UISnapshot, WorkflowService
    from chefmate_workflow.models import UpdatePreferencesInput

    state = {}
    domain = WorkflowService(Service(), lambda: copy.deepcopy(state), lambda operation: operation(state))
    seeded = domain.apply_action({"action_id": "seed", "base_revision": 0, "type": "update_preferences",
                                  "payload": {"values": {"servings": 1, "taste_tags": ["light"],
                                                         "allow_estimated": False, "dietary_notes": "不吃香菜"}}})
    assert seeded["status"] == "ok"
    before = domain.model_snapshot()
    seen = []

    def update(request, context):
        seen.append(request)
        return domain.execute_tool("update_preferences", request, operation_id=context.call_id,
                                   user_text="改成两人份，半小时以内。")

    model = ScriptedModel([tool("update_preferences", {"expected_revision": before["revision"],
                            "changed_fields": ["servings", "max_total_time_min"],
                            "values": {"servings": 2, "max_total_time_min": 30}}), answer("已更新人数和时间。")])
    core = ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS,
                        extra_tools=[InjectedToolSpec("update_preferences", "填写条件", UpdatePreferencesInput, update)],
                        ui_context_model=UISnapshot, action_model=UIAction)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "改成两人份，半小时以内。"}], ui_context=before))
    assert result.status == "completed" and result.tool_calls[0].result["status"] == "ok"
    assert seen == [{"expected_revision": before["revision"], "changed_fields": ["servings", "max_total_time_min"],
                     "values": {"servings": 2.0, "max_total_time_min": 30.0}}]
    after = domain.model_snapshot()["preferences"]
    assert after["servings"] == 2 and after["max_total_time_min"] == 30
    assert after["taste_tags"] == ["light"] and after["allow_estimated"] is False
    assert after["dietary_notes"] == "不吃香菜"


def test_trace_matches_the_final_system_projection_and_bound_tool_schemas():
    snapshot = {"revision": 2, "stage": "prep", "components": [], "pending_question": None}
    spec = InjectedToolSpec("get_workspace", "核对进度", EmptyInput,
                            lambda request, context: {"status": "ok", "ui_snapshot": snapshot},
                            allowed_stages=("prep",))
    core, model = make_core([tool("get_workspace", {}), answer("按最新状态继续。")], [spec])
    events = []
    result = asyncio.run(core.run_turn([{"role": "user", "content": "继续备菜"}], events.append,
                                      ui_context={"revision": 1, "stage": "prep", "components": []}))
    assert result.status == "completed" and len(result.debug_trace) == 2
    for captured, actual in zip(result.debug_trace, model.histories):
        assert captured["system"] == actual[0].content
        assert [message["content"] for message in captured["messages"]] == [message.content for message in actual[1:]]
        assert {item["name"] for item in captured["tools"]} == {item["function"]["name"] for item in model.tools}
    human = result.debug_trace[0]["messages"][0]
    assert human["user_text"] == "继续备菜" and human["ui_context"]["revision"] == 1
    assert result.debug_trace[1]["messages"][-1]["result"]["ui_snapshot"]["snapshot_kind"] == "delta"
    assert len([event for event in events if event["type"] == "debug_context"]) == 2
    assert next(item for item in core.tool_catalog({"stage": "idea"}) if item["name"] == "get_workspace")["available"] is False
    assert next(item for item in core.tool_catalog({"stage": "prep"}) if item["name"] == "get_workspace")["available"] is True


def test_failed_request_diagnostics_remove_image_bytes_credentials_and_hidden_fields():
    from uuid import uuid4

    reference = {"id": str(uuid4()), "name": "test.jpg", "mime_type": "image/jpeg", "width": 20,
                 "height": 10, "byte_size": 10}
    secret = "sk-private-credential-value"
    core, model = make_core([RuntimeError("private provider failure")], supports_images=True, api_key=secret)
    result = asyncio.run(core.run_turn([{"role": "user", "content": f"Bearer token-value {secret}", "images": [reference]}],
                                      image_loader=lambda image: "data:image/jpeg;base64,/9j/test"))
    assert result.status == "error" and len(result.debug_trace) == 1
    trace = result.debug_trace[0]
    serialized = json.dumps(trace, ensure_ascii=False)
    assert "base64" not in serialized and secret not in serialized and "token-value" not in serialized
    assert trace["native_images_count"] == 1 and trace["messages"][0]["images"] == [reference]
    assert isinstance(model.histories[0][1], HumanMessage) and isinstance(model.histories[0][1].content, list)


def test_debug_tool_result_and_skill_log_are_safe_and_remain_visible_on_failure(tmp_path):
    skill = tmp_path / "safe-guide"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: safe-guide\ndescription: 备菜指引\n---\n先核对食材。", encoding="utf-8")
    from chefmate_agent import SkillRegistry

    model = ScriptedModel([tool("load_skill", {"name": "safe-guide"}), RuntimeError("unavailable")])
    core = ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS,
                        skills=SkillRegistry([skill]))
    result = asyncio.run(core.run_turn([{"role": "user", "content": "按指引备菜"}]))
    assert result.status == "error" and len(result.debug_trace) == 2
    assert result.skill_events[0]["name"] == "safe-guide" and result.skill_events[0]["status"] == "ok"
    assert "先核对食材。" in result.debug_trace[1]["messages"][-1]["result"]["content"]
    from chefmate_agent.debug import redact_debug

    safe = redact_debug({"content": '{"api_key":"private","reasoning":"hidden","status":"ok"}',
                         "reasoning": "hidden", "password": "private"})
    assert json.loads(safe["content"]) == {"status": "ok"}
    assert "hidden" not in json.dumps(safe) and "private" not in json.dumps(safe)


def test_stop_keeps_only_the_already_sent_safe_request_diagnostics():
    async def exercise():
        entered, cancel = asyncio.Event(), asyncio.Event()

        async def stalled():
            entered.set()
            await asyncio.sleep(10)
            yield AIMessageChunk(content="late")

        core, model = make_core([stalled])
        task = asyncio.create_task(core.run_turn([{"role": "user", "content": "等待测试"}], cancel_event=cancel))
        await asyncio.wait_for(entered.wait(), timeout=2)
        cancel.set()
        result = await asyncio.wait_for(task, timeout=2)
        assert result.status == "cancelled" and len(result.debug_trace) == 1
        assert result.messages == [{"role": "user", "content": "等待测试"}]
        assert model.closed == 1

    asyncio.run(exercise())


def test_removed_shopping_tool_remains_readable_only_as_a_closed_historical_call():
    core, model = make_core([answer("现在统一核对备菜清单。")])
    history = [
        {"role": "user", "content": "显示采购清单"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "legacy", "type": "function",
            "function": {"name": "show_shopping_list", "arguments": "{}"}}]},
        {"role": "tool", "name": "show_shopping_list", "tool_call_id": "legacy",
         "content": '{"status":"ok","ui_snapshot":{"stage":"shopping"}}'},
        {"role": "assistant", "content": "已显示。"},
        {"role": "user", "content": "继续备菜"},
    ]
    result = asyncio.run(core.run_turn(history, ui_context={"revision": 3, "stage": "prep"}))
    assert result.status == "completed"
    assert any(isinstance(message, ToolMessage) and message.name == "show_shopping_list" for message in model.histories[0])
    assert "show_shopping_list" not in {item["name"] for item in result.debug_trace[0]["tools"]}
