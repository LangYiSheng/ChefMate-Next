"""Native completion accepts ordinary user wording without a magic-phrase filter."""

import asyncio
import copy

import pytest

from chefmate_agent import AgentConfig, ChefMateAgent, InjectedToolSpec, SkillRegistry
from chefmate_workflow import UIAction, UISnapshot, WorkflowService
from chefmate_workflow.models import CompleteCookingStepInput
from test_agent import REQUESTS, SNAPSHOT, ScriptedModel, Service, answer, tool


@pytest.mark.parametrize("statement", ["当前这一步我已经做完了，帮我标记完成再到下一步", "这步搞定啦", "关于这一步是否完成，我的回答是：是。"])
def test_native_spoken_completion_records_one_step_and_emits_the_next_card(tmp_path, statement):
    state = {"revision": 7, "stage": "cooking", "active_recipe_id": "r1", "active_step_id": "r1:step:0",
             "step_status": {"r1:step:0": "current"}, "meal_plan": [{"recipe_id": "r1", "data_snapshot": SNAPSHOT,
             "recipe": {"recipe_id": "r1", "name": "测试汤", "steps": ["清洗食材", "加水煮开", "盛盘"],
                        "ingredients": [{"ingredient_id": "water", "canonical_name": "水"}]}}]}
    workflow = WorkflowService(Service(), lambda: copy.deepcopy(state), lambda operation: operation(state))
    received = []
    skill = tmp_path / "cooking-guide"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: cooking-guide\ndescription: Cooking guidance\n---\nRecord confirmed completion.", encoding="utf-8")

    def handler(arguments, context):
        return workflow.execute_tool("complete_cooking_step", arguments, operation_id=context.call_id,
                                     user_text=context.messages[-1]["content"])

    model = ScriptedModel([tool("complete_cooking_step", {"expected_revision": 7}), answer("已完成这一步，现在加水煮开。")])
    agent = ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS,
                         skills=SkillRegistry([skill]), ui_context_model=UISnapshot, action_model=UIAction,
                         extra_tools=[InjectedToolSpec("complete_cooking_step", "记录单步完成", CompleteCookingStepInput,
                                                       handler, effect="write", allowed_stages=("cooking",),
                                                       required_skill="cooking-guide")])
    result = asyncio.run(agent.run_turn([{"role": "user", "content": statement}], on_event=received.append,
                                       ui_context=workflow.model_snapshot()))
    assert result.status == "completed" and result.tool_calls[0].result["status"] == "ok"
    display = next(event["action"] for event in received if event["type"] == "ui_action")
    assert display["component"]["data"]["active_step_id"] == "r1:step:1"
    assert state["step_status"]["r1:step:0"] == "done"
    assert state["step_status"]["r1:step:1"] == "current" and state["stage"] == "cooking"
