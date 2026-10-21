"""Standard runner, scoped UI context/actions, and progressive Skill boundaries."""

import asyncio
import json
from typing import Literal

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field

from chefmate_agent import AgentConfig, ChefMateAgent, InjectedToolSpec, SkillRegistry
from test_agent import REQUESTS, ScriptedModel, Service, answer, tool
from uuid import uuid4


class UISnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int
    stage: str
    components: list[dict] = Field(default_factory=list)
    recent_actions: list[dict] = Field(default_factory=list)


class UIAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["show_component"]
    component: dict


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def make_core(responses, **kwargs):
    model = ScriptedModel(responses)
    return ChefMateAgent(Service(), AgentConfig(model="test"), model=model, request_models=REQUESTS, **kwargs), model


def image_ref():
    return {"id":str(uuid4()),"name":"test.jpg","mime_type":"image/jpeg","width":20,"height":10,"byte_size":10}


def test_images_are_native_request_blocks_and_only_metadata_is_persisted():
    reference=image_ref()
    model=ScriptedModel([answer("看见了图片。")])
    core=ChefMateAgent(Service(),AgentConfig(model="test",supports_images=True),model=model,request_models=REQUESTS)
    result=asyncio.run(core.run_turn([{"role":"user","content":"","images":[reference]}],
                                   image_loader=lambda ref:"data:image/jpeg;base64,/9j/test"))
    assert result.status=="completed"
    message=model.histories[0][1]
    assert message.content[1]["type"]=="image_url"
    assert message.content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert result.messages[0]["images"]==[reference] and result.messages[0]["content"]==""
    assert "base64" not in json.dumps(result.model_dump(mode="json"))
    disabled,plain=make_core([answer("无法看图片。")])
    rejected=asyncio.run(disabled.run_turn([{"role":"user","content":"查看","images":[reference]}]))
    assert rejected.error.code=="images_not_supported" and plain.histories==[]
    replay=asyncio.run(disabled.run_turn([*result.messages,{"role":"user","content":"继续聊"}]))
    assert replay.status=="completed" and isinstance(plain.histories[0][1].content,str)
    assert "当前模型未开启图片能力" in plain.histories[0][1].content


def test_image_history_budget_prunes_complete_old_turns():
    core=ChefMateAgent(Service(),AgentConfig(model="test",supports_images=True,max_images=6),
                       model=ScriptedModel([]),request_models=REQUESTS)
    source=[]
    for label in ("one","two","three"):
        source.append({"role":"user","content":label,"images":[image_ref() for _ in range(3)]})
        if label!="three":
            source.append({"role":"assistant","content":"收到"})
    prepared=core._prepare_history(source)
    assert prepared[0]["content"]=="two"
    assert sum(len(item.get("images",[])) for item in prepared)==6


def test_image_limit_counts_history_and_preserves_visible_source_metadata():
    model = ScriptedModel([answer("收到新图片。")])
    core = ChefMateAgent(Service(), AgentConfig(model="test", supports_images=True, max_images=5),
                        model=model, request_models=REQUESTS)
    source = [{"role": "user", "content": "旧图片", "images": [image_ref() for _ in range(4)]},
              {"role": "assistant", "content": "旧回答"},
              {"role": "user", "content": "新图片", "images": [image_ref() for _ in range(4)]}]
    result = asyncio.run(core.run_turn(source, image_loader=lambda ref: "data:image/jpeg;base64,/9j/test"))
    assert result.status == "completed"
    assert len(source) == 3 and len(source[0]["images"]) == 4
    users = [message for message in model.histories[0] if isinstance(message, HumanMessage)]
    assert len(users) == 1
    assert sum(item.get("type") == "image_url" for item in users[0].content) == 4


def test_image_limit_rejects_oversized_latest_turn_before_provider_request():
    model = ScriptedModel([])
    core = ChefMateAgent(Service(), AgentConfig(model="test", supports_images=True, max_images=1),
                        model=model, request_models=REQUESTS)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "看图", "images": [image_ref(), image_ref()]}]))
    assert result.error.code == "image_limit"
    assert model.histories == []


def test_missing_image_is_reported_before_any_provider_request():
    model=ScriptedModel([])
    core=ChefMateAgent(Service(),AgentConfig(model="test",supports_images=True),model=model,request_models=REQUESTS)
    def unavailable(reference):
        raise FileNotFoundError('removed')
    result=asyncio.run(core.run_turn([{"role":"user","content":"查看","images":[image_ref()]}],image_loader=unavailable))
    assert result.error.code=='image_unavailable'
    assert model.histories==[]


def test_stable_catalog_still_denies_stage_restricted_execution():
    calls = []
    spec = InjectedToolSpec("prep_tool", "备菜", EmptyInput, lambda request, context: calls.append(request),
                            allowed_stages=("prep",))
    core, _ = make_core([tool("prep_tool", {}), answer("请先确认餐单。")], extra_tools=[spec], ui_context_model=UISnapshot)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "开始"}],
                                      ui_context={"revision": 1, "stage": "search"}))
    assert result.status == "completed" and calls == []
    assert result.tool_calls[0].result["status"] == "stage_conflict"


def test_snapshot_delta_retains_quantities_and_removes_stale_components():
    before = {"revision": 1, "stage": "prep", "components": [
        {"id": "list", "kind": "prep_checklist", "revision": 1, "data": {"amount_value": 100, "checked": False}},
        {"id": "old", "kind": "recipe_detail", "revision": 1, "data": {"name": "旧菜"}}], "preferences": {"servings": 2}}
    after = {**before, "revision": 2, "components": [{**before["components"][0], "revision": 2}]}
    delta = ChefMateAgent._snapshot_delta(before, after)
    assert "components" not in delta and delta["removed_component_ids"] == ["old"]
    restored = ChefMateAgent._merge_snapshot(before, delta)
    assert restored["components"][0]["data"]["amount_value"] == 100
    assert restored["preferences"]["servings"] == 2
    assert len(json.dumps(delta)) < len(json.dumps(after))


def test_usage_reports_actual_cache_and_unknown_metrics_without_guessing():
    reported = [AIMessageChunk(content="完成", usage_metadata={"input_tokens": 1200, "output_tokens": 80,
        "total_tokens": 1280, "input_token_details": {"cache_read": 1024}, "output_token_details": {"reasoning": 10}})]
    core, _ = make_core([reported, answer("继续。")])
    events = []
    first = asyncio.run(core.run_turn([{"role": "user", "content": "测试"}], events.append))
    assert first.usage["input_tokens"] == 1200 and first.usage["cached_tokens"] == 1024
    assert first.usage["reasoning_tokens"] == 10
    assert any(event["type"] == "model_usage" for event in events)
    second = asyncio.run(core.run_turn([*first.messages, {"role": "user", "content": "继续"}]))
    assert second.usage["input_tokens"] is None and second.usage["cached_tokens"] is None


def test_standard_create_agent_owns_execution_and_history():
    core, _ = make_core([tool("get_search_options", {}), answer("可用选项已读取。")])
    assert isinstance(core.runner, CompiledStateGraph)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "有哪些选项"}]))
    assert result.status == "completed"
    assert [message["role"] for message in result.messages] == ["user", "assistant", "tool", "assistant"]


def test_scoped_host_context_actions_and_original_user_snapshot():
    host = object()
    captured = []
    component = {"id": "view-1", "kind": "test", "title": "偏好", "data": "LARGE_UI_RENDER_DATA" * 1000}

    def handler(request, context):
        captured.append(context)
        return {"status": "ok", "ui_snapshot": {"revision": 2, "stage": "prep"},
                "actions": [{"kind": "show_component", "component": component}],
                "views": [component], "workspace": {"full_data": component}}

    spec = InjectedToolSpec("show_form", "展示偏好表单", EmptyInput, handler)
    core, model = make_core([tool("show_form", {}), answer("请在表单中选择。")],
                            extra_tools=[spec], ui_context_model=UISnapshot, action_model=UIAction)
    events = []
    snapshot = {"revision": 1, "stage": "search"}
    result = asyncio.run(core.run_turn([{"role": "user", "content": "我想调整偏好"}], events.append,
                                      ui_context=snapshot, tool_context=host))
    assert result.status == "completed"
    assert captured[0].host_context is host
    assert captured[0].call_id == "call-1"
    assert captured[0].messages[-1]["tool_calls"][0]["function"]["name"] == "show_form"
    assert result.messages[0]["content"] == "我想调整偏好"
    assert result.messages[0]["ui_context"]["revision"] == 1
    assert result.actions[0]["component"]["data"] == component["data"]
    assert any(event["type"] == "ui_action" for event in events)
    assert "LARGE_UI_RENDER_DATA" not in result.messages[2]["content"]
    assert "views" not in result.tool_calls[0].result
    assert any(isinstance(message, ToolMessage) and '"stage":"prep"' in message.content
               for message in model.histories[-1])
    assert model.histories[0][0].content == model.histories[-1][0].content
    assert snapshot == {"revision": 1, "stage": "search"}
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_tool_catalog_stays_stable_after_latest_snapshot():
    def move(request, context):
        return {"status": "ok", "ui_snapshot": {"revision": 2, "stage": "prep"}, "actions": []}

    first = InjectedToolSpec("show_form", "查询阶段表单", EmptyInput, move, allowed_stages=("search",))
    second = InjectedToolSpec("prep_tool", "备菜阶段工具", EmptyInput, move, allowed_stages=("prep",))
    core, model = make_core([tool("show_form", {}), answer("准备备菜。")],
                            extra_tools=[first, second], ui_context_model=UISnapshot, action_model=UIAction)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "开始"}],
                                      ui_context={"revision": 1, "stage": "search"}))
    assert result.status == "completed"
    names = [{item["function"]["name"] for item in definitions} for definitions in model.bind_history]
    assert "show_form" in names[0] and "prep_tool" in names[0]
    assert model.bind_history[0] == model.bind_history[-1]
    assert model.histories[0][0].content == model.histories[-1][0].content
    assert model.histories[0][1].content == model.histories[-1][1].content
    assert {"find_ingredients", "search_recipes", "get_recipe", "get_search_options", "list_skills", "load_skill"} <= names[-1]


def test_latest_prep_quantities_reach_model_without_original_render_data():
    snapshot = {
        "revision": 2, "stage": "prep", "components": [
            {"kind": "meal_plan", "data": {"recipes": [
                {"recipe_id": "shrimp", "original_servings": 1, "target_servings": 2},
            ]}},
            {"kind": "prep_checklist", "data": {"items": [
                {"id": "garlic", "recipe_id": "shrimp", "canonical_name": "蒜蓉",
                 "amount_text": "100 g", "amount_value": 100, "unit": "g", "scaled": True,
                 "estimated": False, "status": "pending"},
                {"id": "oil", "recipe_id": "shrimp", "canonical_name": "食用油",
                 "amount_text": "40 ml", "amount_value": 40, "unit": "ml", "scaled": True,
                 "estimated": False, "status": "pending"},
            ]}},
        ],
    }

    def handler(request, context):
        return {"status": "ok", "ui_snapshot": snapshot, "actions": [],
                "views": [{"raw_text": "ORIGINAL_RENDER_ONLY 蒜蓉50g、食用油20ml"}]}

    core, model = make_core([tool("get_workspace", {}), answer("按当前两人份备料。")],
                            extra_tools=[InjectedToolSpec("get_workspace", "读取工作区", EmptyInput, handler)],
                            ui_context_model=UISnapshot)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "两人份，开始备菜"}],
                                      ui_context={"revision": 1, "stage": "prep"}))
    assert result.status == "completed"
    projected = json.loads(result.messages[2]["content"])
    items = projected["ui_snapshot"]["components"][1]["data"]["items"]
    assert [(item["amount_value"], item["unit"]) for item in items] == [(100, "g"), (40, "ml")]
    latest_system = model.histories[-1][0].content
    latest_result = next(message.content for message in reversed(model.histories[-1]) if isinstance(message, ToolMessage))
    assert '"original_servings":1,"target_servings":2' in latest_result
    assert '"amount_text":"100 g"' in latest_result and '"amount_text":"40 ml"' in latest_result
    assert "原始步骤里的数字是原始份量的引用" in latest_system
    assert "不能用旧get_recipe" in latest_system
    assert "ORIGINAL_RENDER_ONLY" not in result.messages[2]["content"]


def test_invalid_action_is_never_emitted():
    def handler(request, context):
        return {"status": "ok", "actions": [{"kind": "run_shell", "component": {}}]}

    core, _ = make_core([tool("show_form", {}), answer("操作未完成。")],
                       extra_tools=[InjectedToolSpec("show_form", "展示表单", EmptyInput, handler)],
                       action_model=UIAction)
    events = []
    result = asyncio.run(core.run_turn([{"role": "user", "content": "开始"}], events.append))
    assert result.status == "completed"
    assert result.actions == []
    assert result.tool_calls[0].result["status"] == "tool_error"
    assert not any(event["type"] == "ui_action" for event in events)


def make_skill(tmp_path, *, body="DEFERRED_SKILL_BODY", name="recipe-helper"):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: 食材检索指导\n---\n{body}", encoding="utf-8")
    return directory


def test_skills_metadata_is_progressive_and_body_loaded_only_by_name(tmp_path):
    directory = make_skill(tmp_path)
    registry = SkillRegistry([directory])
    core, model = make_core([tool("load_skill", {"name": "recipe-helper"}), answer("按已加载指引处理。")],
                            skills=registry)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "使用检索指导"}]))
    assert result.status == "completed"
    assert "recipe-helper" in model.histories[0][0].content
    assert "DEFERRED_SKILL_BODY" not in model.histories[0][0].content
    assert any(isinstance(message, ToolMessage) and "DEFERRED_SKILL_BODY" in message.content
               for message in model.histories[-1])
    assert registry.load("../../outside")["status"] == "not_found"
    assert registry.load("unregistered")["status"] == "not_found"
    assert not {"shell", "execute_sql", "read_file"} & {tool.name for tool in core.tools}


def test_skills_read_budget_and_change_detection(tmp_path):
    directory = make_skill(tmp_path)
    registry = SkillRegistry([directory])
    (directory / "SKILL.md").write_text("---\nname: recipe-helper\ndescription: 修改\n---\nchanged", encoding="utf-8")
    assert registry.load("recipe-helper")["status"] == "skill_changed"
    huge = make_skill(tmp_path, name="large-skill", body="x" * 1200)
    with pytest.raises(ValueError, match="read budget"):
        SkillRegistry([huge], max_file_chars=1000)


def test_shared_runner_isolates_concurrent_host_sessions():
    barrier = asyncio.Barrier(2)

    async def handler(request, context):
        await asyncio.wait_for(barrier.wait(), timeout=2)
        return {"status": "ok", "actions": [{"kind": "show_component", "component": {
            "id": context.host_context["session"], "kind": "test",
        }}]}

    core, _ = make_core([tool("show_form", {}), tool("show_form", {}), answer("A"), answer("B")],
                       extra_tools=[InjectedToolSpec("show_form", "展示", EmptyInput, handler)], action_model=UIAction)

    async def exercise():
        return await asyncio.gather(
            core.run_turn([{"role": "user", "content": "A"}], tool_context={"session": "A"}),
            core.run_turn([{"role": "user", "content": "B"}], tool_context={"session": "B"}),
        )

    first, second = asyncio.run(exercise())
    assert first.status == second.status == "completed"
    assert first.actions[0]["component"]["id"] == "A"
    assert second.actions[0]["component"]["id"] == "B"


def test_skill_total_turn_budget_and_path_schema(tmp_path):
    directory = make_skill(tmp_path, body="x" * 1200)
    registry = SkillRegistry([directory])
    model = ScriptedModel([tool("load_skill", {"name": "../../outside"}),
                           tool("load_skill", {"name": "recipe-helper"}, "budget"), answer("已达到预算。")])
    core = ChefMateAgent(Service(), AgentConfig(model="test", max_skill_chars=1000), model=model,
                        request_models=REQUESTS, skills=registry)
    result = asyncio.run(core.run_turn([{"role": "user", "content": "读取指导"}]))
    assert result.status == "completed"
    assert result.tool_calls[0].result["status"] == "invalid_arguments"
    assert result.tool_calls[1].result["status"] == "skill_budget_exceeded"
    assert "x" * 1200 not in result.messages[-2]["content"]


def test_ui_context_validation_and_replay_preserve_user_text():
    core, model = make_core([answer("先查看已保存状态。"), answer("继续。")], ui_context_model=UISnapshot)
    first = asyncio.run(core.run_turn([{"role": "user", "content": "我选了第二道"}],
                                     ui_context={"revision": 1, "stage": "search"}))
    second = asyncio.run(core.run_turn(first.messages + [{"role": "user", "content": "继续"}],
                                      ui_context={"revision": 2, "stage": "prep"}))
    assert second.status == "completed"
    assert second.messages[0]["content"] == "我选了第二道"
    assert second.messages[0]["ui_context"]["revision"] == 1
    assert second.messages[-2]["ui_context"]["revision"] == 2
    assert model.histories[0][0].content == model.histories[-1][0].content
    assert model.histories[0][1].content == model.histories[-1][1].content
    assert '"stage":"prep"' in next(message.content for message in reversed(model.histories[-1]) if isinstance(message, HumanMessage))
    bad = asyncio.run(core.run_turn([{"role": "user", "content": "继续"}],
                                   ui_context={"revision": "untrusted", "stage": "prep"}))
    assert bad.error.code == "invalid_ui_context"


def test_failed_turn_preserves_human_snapshot_without_generated_tool_batch():
    core, _ = make_core([RuntimeError("private provider failure")], ui_context_model=UISnapshot)
    history = [{"role": "user", "content": "我选了第二道"}]
    result = asyncio.run(core.run_turn(history, ui_context={"revision": 3, "stage": "prep"}))
    assert result.status == "error"
    assert [message["role"] for message in result.messages] == ["user"]
    assert result.messages[0]["content"] == "我选了第二道"
    assert result.messages[0]["ui_context"]["revision"] == 3
    assert history == [{"role": "user", "content": "我选了第二道"}]
