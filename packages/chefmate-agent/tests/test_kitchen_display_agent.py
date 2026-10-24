"""The native runner validates display actions and honors the preference switch."""

import asyncio
import copy

import pytest

from chefmate_agent import AgentConfig, ChefMateAgent, InjectedToolSpec, KITCHEN_TOOL_SKILLS, SkillRegistry
from chefmate_workflow import UIAction, UISnapshot, WorkflowService
from chefmate_workflow.models import KitchenDisplayInput
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool


@pytest.mark.parametrize("enabled", [False, True])
def test_display_tool_emits_typed_action_only_with_app_preferences_enabled(tmp_path, enabled):
    state, called, received = {}, [], []
    workflow = WorkflowService(Service(), lambda: copy.deepcopy(state), lambda operation: operation(state))
    directories = []
    if enabled:
        skill = tmp_path / "app-preferences"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: app-preferences\ndescription: Display preferences\n---\nUse requested display modes.", encoding="utf-8")
        directories.append(skill)

    def handler(arguments, context):
        called.append(arguments)
        return workflow.execute_tool("set_kitchen_display", arguments, operation_id=context.call_id)

    model = ScriptedModel([tool("set_kitchen_display", {"mode": "conversation"}), answer("继续聊吧。")])
    agent = ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS,
                         skills=SkillRegistry(directories),
                         tool_skill_bindings={"set_kitchen_display": KITCHEN_TOOL_SKILLS["set_kitchen_display"]},
                         ui_context_model=UISnapshot, action_model=UIAction, extra_tools=[
                             InjectedToolSpec("set_kitchen_display", "切换显示模式", KitchenDisplayInput,
                                              handler, effect="presentation")])
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "返回普通聊天"}],
                                       on_event=received.append, ui_context=workflow.model_snapshot()))
    display = [event["action"] for event in received if event["type"] == "ui_action"]
    assert result.status == "completed"
    if enabled:
        assert called == [{"mode": "conversation"}]
        expected = {"kind": "set_kitchen_display", "action_id": "call-1", "mode": "conversation", "view_kind": None}
        assert display == [expected] and result.actions == [expected]
        assert result.tool_calls[0].result["actions"] == [expected]
    else:
        assert called == display == result.actions == []
        assert result.tool_calls[0].result["status"] == "skill_disabled"
    assert all(("set_kitchen_display" in {tool["function"]["name"] for tool in batch}) == enabled
               for batch in model.bind_history)
