"""A native model tool call updates cooking state and emits the refreshed card."""

import asyncio
import copy

from chefmate_agent import AgentConfig, ChefMateAgent, InjectedToolSpec, SkillRegistry
from chefmate_workflow import UIAction, UISnapshot, WorkflowService
from chefmate_workflow.models import NavigateCookingStepInput
from test_agent import REQUESTS, SNAPSHOT, ScriptedModel, Service, answer, tool


def test_native_next_step_call_moves_real_card_without_completing_current_step(tmp_path):
    state = {"revision": 7, "stage": "cooking", "active_recipe_id": "r1", "active_step_id": "r1:step:0",
             "step_status": {"r1:step:0": "current"}, "meal_plan": [{"recipe_id": "r1", "data_snapshot": SNAPSHOT,
             "recipe": {"recipe_id": "r1", "name": "测试汤", "steps": ["清洗食材", "加水煮开", "盛盘"],
                        "ingredients": [{"ingredient_id": "water", "canonical_name": "水"}]}}]}
    workflow = WorkflowService(Service(), lambda: copy.deepcopy(state), lambda operation: operation(state))
    received = []
    skill = tmp_path / "cooking-guide"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: cooking-guide\ndescription: Cooking guidance\n---\nNavigate requested steps.", encoding="utf-8")

    def handler(arguments, context):
        return workflow.execute_tool("navigate_cooking_step", arguments, operation_id=context.call_id)

    model = ScriptedModel([tool("navigate_cooking_step", {"expected_revision": 7, "direction": "next"}),
                           answer("下一步加水煮开。")])
    agent = ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS,
                         skills=SkillRegistry([skill]), ui_context_model=UISnapshot, action_model=UIAction,
                         extra_tools=[InjectedToolSpec("navigate_cooking_step", "导航做菜步骤", NavigateCookingStepInput,
                                                       handler, effect="write", allowed_stages=("cooking",),
                                                       required_skill="cooking-guide")])
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "下一步吧"}], on_event=received.append,
                                       ui_context=workflow.model_snapshot()))
    assert result.status == "completed" and result.tool_calls[0].result["status"] == "ok"
    display = next(event["action"] for event in received if event["type"] == "ui_action")
    assert display["kind"] == "show_component" and display["component"]["kind"] == "cooking_guide"
    assert display["component"]["data"]["active_step_id"] == "r1:step:1"
    assert state["active_step_id"] == "r1:step:1" and state["step_status"]["r1:step:0"] == "pending"
    assert not any(value == "done" for value in state["step_status"].values())
