"""Turn-local memory context and disabled-skill tools share real runner boundaries."""

import asyncio
import json

from pydantic import BaseModel, ConfigDict

from chefmate_agent import AgentConfig, ChefMateAgent, InjectedToolSpec, SkillRegistry
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


def registry(tmp_path, name="personal-memory"):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A test skill\n---\nUse only verified data.", encoding="utf-8")
    return SkillRegistry([root])


def test_disabled_skill_is_hidden_and_a_fabricated_call_still_cannot_execute():
    called = []
    spec = InjectedToolSpec("private_read", "private", Empty, lambda request, context: called.append(request),
                            required_skill="personal-memory")
    model = ScriptedModel([tool("private_read", {}), answer("记忆技能已关闭。")])
    agent = ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS, extra_tools=[spec])
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "读取旧偏好"}]))
    assert result.status == "completed" and called == []
    assert result.tool_calls[0].result["status"] == "skill_disabled"
    assert all("private_read" not in {definition["function"]["name"] for definition in binding}
               for binding in model.bind_history)
    catalog = next(item for item in agent.tool_catalog() if item["name"] == "private_read")
    assert not catalog["available"] and catalog["required_skill"] == "personal-memory"


def test_explicit_bindings_disable_retrieval_without_breaking_old_complete_history():
    service = Service()
    model = ScriptedModel([tool("get_search_options", {}), answer("选菜技能已关闭。")])
    agent = ChefMateAgent(service, AgentConfig(model="test"), model=model, request_models=REQUESTS,
                         tool_skill_bindings={"get_search_options": "meal-planning"})
    previous = [{"role": "user", "content": "之前有哪些选项"}, {"role": "assistant", "content": "", "tool_calls": [
        {"id": "old", "type": "function", "function": {"name": "get_search_options", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old", "name": "get_search_options", "content": '{"status":"ok"}'},
        {"role": "assistant", "content": "查过了。"}, {"role": "user", "content": "再查一下"}]
    result = asyncio.run(agent.run_turn(previous))
    assert result.status == "completed" and service.calls == []
    assert result.tool_calls[0].result["status"] == "skill_disabled"


def test_enabled_skill_executes_and_memory_is_current_data_without_persisting_copies(tmp_path):
    spec = InjectedToolSpec("private_read", "private", Empty, lambda request, context: {"status": "ok"},
                            required_skill="personal-memory")
    model = ScriptedModel([tool("private_read", {}), answer("知道啦。")])
    agent = ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS,
                         extra_tools=[spec], skills=registry(tmp_path))
    memory = {"revision": 2, "user_notes": "不吃香菜", "summary": "口味清淡"}
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "今天吃什么"}], memory_context=memory))
    assert result.status == "completed" and result.tool_calls[0].result["status"] == "ok"
    assert all("不吃香菜" in history[1].content for history in model.histories)
    assert "用户自定义内容优先" in model.histories[0][1].content
    assert "当前明确要求优先于历史偏好" in model.histories[0][0].content
    assert result.messages[0]["content"] == "今天吃什么"
    assert "长期记忆（数据" not in json.dumps(result.messages, ensure_ascii=False)
    assert memory["revision"] == 2


def test_memory_counts_against_total_context_and_prunes_complete_old_turns():
    model = ScriptedModel([answer("继续")])
    agent = ChefMateAgent(Service(), AgentConfig(model="test", max_history_chars=4000), model=model, request_models=REQUESTS)
    source = [{"role": "user", "content": "旧话题" * 400}, {"role": "assistant", "content": "旧回答" * 400},
              {"role": "user", "content": "现在这顿"}]
    result = asyncio.run(agent.run_turn(source, memory_context={"summary": "长期偏好" * 500}))
    assert result.status == "completed"
    assert all("旧话题" not in str(message.content) for message in model.histories[0])
    assert result.messages[0]["content"] == "现在这顿"


def test_oversized_memory_is_rejected_before_model_request():
    model = ScriptedModel([])
    agent = ChefMateAgent(Service(), AgentConfig(model="test", max_memory_chars=1000), model=model, request_models=REQUESTS)
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "开始"}], memory_context={"summary": "a" * 1001}))
    assert result.status == "error" and result.error.code == "memory_context_limit"
    assert model.histories == []


def test_custom_recipe_search_snapshot_is_per_recipe_and_does_not_mix_official_sources():
    custom = {"data_version": "custom-egg:v2", "imported_at": "2026-10-21T00:00:00+00:00"}
    history = [{"role": "tool", "name": "search_custom_recipes", "content": json.dumps({
        "status": "ok", "items": [{"recipe_id": "custom-egg", "snapshot": custom}]})}]
    assert ChefMateAgent._verify_references("get_recipe", {"recipe_id": "custom-egg", "expected_snapshot": custom}, history) is None
    wrong = ChefMateAgent._verify_references("get_recipe", {"recipe_id": "custom-egg", "expected_snapshot": {}}, history)
    assert wrong["status"] == "snapshot_required" and wrong["snapshot"] == custom
    guessed = ChefMateAgent._verify_references("get_recipe", {"recipe_id": "custom-unknown", "expected_snapshot": custom}, history)
    assert guessed["status"] == "unverified_reference"


def test_custom_recipe_detail_cannot_bypass_disabled_authoring_skill():
    service = Service()
    model = ScriptedModel([tool("get_recipe", {"recipe_id": "custom-egg"}), answer("个人菜谱技能已关闭。")])
    agent = ChefMateAgent(service, AgentConfig(model="test"), model=model, request_models=REQUESTS)
    result = asyncio.run(agent.run_turn([{"role": "user", "content": "读取我的炒蛋"}]))
    assert result.status == "completed" and service.calls == []
    assert result.tool_calls[0].result["status"] == "skill_disabled"
    assert result.tool_calls[0].result["skill"] == "recipe-authoring"
