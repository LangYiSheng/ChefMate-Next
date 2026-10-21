"""Offline HTTP regression through the real agent, workflow and persistence."""
import copy
import json

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, message_chunk_to_message
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from chefmate_agent import AgentConfig, ChefMateAgent
from chefmate_agent.provider import ArgumentIntegrity, ProviderIntegrityError
from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.interactions import make_tool_specs
from chefmate_workflow import UIAction, UISnapshot, WorkflowService


SNAPSHOT = {"data_version": "contract-fixture", "imported_at": "2026-10-21T00:00:00+00:00"}
DEVELOPER = {"X-ChefMate-Developer": "1"}


class RecipeSource:
    """Read-only retrieval-shaped fixture; the workflow freezes its actual data."""

    def __init__(self):
        self.searches = []
        self.reads = []
        self.recipe = {
            "recipe_id": "r1", "name": "清淡虾仁汤", "description": "离线契约回归菜谱", "servings": 1.,
            "estimated_servings": False, "total_time_min": 15., "difficulty": "beginner", "warnings": [],
            "ingredients": [
                {"ordinal": 0, "ingredient_id": "shrimp", "canonical_name": "虾仁", "raw_name": "虾仁",
                 "raw_text": "虾仁100克", "raw_amount": "100", "raw_unit": "g", "grams": 100.,
                 "scalable": True, "estimated_grams": False},
                {"ordinal": 1, "ingredient_id": "salt", "canonical_name": "盐", "raw_name": "盐",
                 "raw_text": "盐1克", "raw_amount": "1", "raw_unit": "g", "grams": 1.,
                 "scalable": False, "estimated_grams": False},
            ],
            "steps": ["洗净虾仁，加入沸水煮5分钟。", "加入盐后盛出。"],
        }

    def get_search_options(self):
        return {"status": "ok", "snapshot": dict(SNAPSHOT), "recipe_count": 1, "difficulties": ["beginner"]}

    def search_recipes(self, request):
        self.searches.append(copy.deepcopy(request))
        assert request["max_total_time_min"] == 20
        return {"status": "ok", "snapshot": dict(SNAPSHOT), "total": 1, "items": [copy.deepcopy(self.recipe)]}

    def get_recipe(self, request):
        self.reads.append(copy.deepcopy(request))
        assert request == {"recipe_id": "r1", "expected_snapshot": SNAPSHOT}
        return {"status": "ok", "snapshot": dict(SNAPSHOT), "recipe": copy.deepcopy(self.recipe)}

    def find_ingredients(self, request):
        raise AssertionError("This regression has no ingredient lookup requirement")


class ScriptedModel(BaseChatModel):
    responses: list = Field(default_factory=list, exclude=True)
    histories: list = Field(default_factory=list, exclude=True)
    bindings: list = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self):
        return "offline-contract-regression"

    def bind_tools(self, tools, **kwargs):
        converted = [convert_to_openai_tool(tool) for tool in tools]
        self.bindings.append(copy.deepcopy(converted))
        return self.bind(tools=converted, **kwargs)

    def _generate(self, messages, **kwargs):
        raise NotImplementedError("This fixture uses the async real agent runner")

    async def _agenerate(self, messages, **kwargs):
        aggregate = None
        async for generation in self._astream(messages, **kwargs):
            aggregate = generation if aggregate is None else aggregate + generation
        return ChatResult(generations=[ChatGeneration(message=message_chunk_to_message(aggregate.message))])

    async def _astream(self, messages, **kwargs):
        self.histories.append(copy.deepcopy(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        yield ChatGenerationChunk(message=response)


def tool(name, arguments, identity):
    encoded = json.dumps(arguments, ensure_ascii=False)
    return AIMessageChunk(content="", tool_call_chunks=[
        {"name": name, "args": encoded, "id": identity, "index": 0},
    ], additional_kwargs={"tool_calls": [{"index": 0, "id": identity, "type": "function",
                                         "function": {"name": name, "arguments": encoded}}]})


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def make_app(tmp_path, responses):
    source, model = RecipeSource(), ScriptedModel(responses=list(responses))
    agent = ChefMateAgent(source, AgentConfig(model="offline-contract", max_model_rounds=8), model=model,
                         extra_tools=make_tool_specs(), ui_context_model=UISnapshot, action_model=UIAction)
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "contract.db"), service=source, agent=agent)
    return app, source, model


def test_masked_preferences_search_choices_and_optional_replies_complete_over_real_http(tmp_path):
    padded_values = {"servings": 1., "max_total_time_min": 20., "taste_tags": [], "available_tools": None,
                     "ingredient_terms": [], "exclude_ingredient_terms": [], "dietary_notes": "", "allow_estimated": True}
    answer = "已按一人、20分钟以内找到清淡虾仁汤，可以在上方选择菜谱。"
    app, source, model = make_app(tmp_path, [
        tool("update_preferences", {"expected_revision": 1, "changed_fields": ["servings", "max_total_time_min"],
                                    "values": padded_values}, "preferences"),
        tool("search_recipes", {"max_total_time_min": 20., "limit": 8, "offset": 0,
                                "sort": {"field": "total_time_min", "direction": "asc"}}, "search"),
        tool("show_recipe_choices", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT}, "choices"),
        tool("suggest_replies", {}, "optional-replies"),
        AIMessageChunk(content=answer),
    ])
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        seeded = client.post(uri + "/actions", json={"type": "update_preferences", "base_revision": 0,
            "action_id": "seed-preferences", "payload": {"values": {"taste_tags": ["light"],
                "allow_estimated": False, "dietary_notes": "保留这条饮食备注"}}}).json()
        assert seeded["status"] == "ok"
        assert isinstance(app.state.workspace.service(uri.rsplit("/", 1)[1]), WorkflowService)

        response = client.post(uri + "/turn", json={"message": "我一个人吃，只有20分钟，请找适合的简单菜谱。",
                                                     "request_id": "contract-flow"})
        stream = events(response)
        result = next(event for event in stream if event["type"] == "result")
        assert response.status_code == 200 and stream[-1]["status"] == result["status"] == "completed"
        assert result["text"] == answer and len(result["suggested_replies"]) == 3
        assert len(model.histories) == 5 and model.responses == []
        assert all(event["type"] not in {"debug_context", "skill_event"} for event in stream)
        assert not any(event.get("name") == "suggest_replies" for event in stream)

        workspace = client.get(uri + "/workspace").json()
        preferences = workspace["workspace"]["preferences"]
        assert preferences["servings"] == 1 and preferences["max_total_time_min"] == 20
        assert preferences["taste_tags"] == ["light"] and preferences["allow_estimated"] is False
        assert preferences["dietary_notes"] == "保留这条饮食备注"
        choices = next(view for view in workspace["views"] if view["kind"] == "recipe_choices")
        assert choices["data"]["recipes"][0]["recipe_id"] == "r1"
        assert choices["data"]["recipes"][0]["source"]["data_snapshot"] == SNAPSHOT
        assert len(source.searches) == len(source.reads) == 1
        assert len(client.get(uri).json()["messages"][-1]["suggested_replies"]) == 3

        debug = client.get(uri + "/debug", headers=DEVELOPER).json()["capture"]
        assert debug["status"] == "completed" and debug["usage"]["model_calls"] == 5
        traces = debug["debug_trace"]
        assert len(traces) == 5 and all("学姐" not in trace["system"] for trace in traces)
        for trace, binding in zip(traces, model.bindings, strict=True):
            names = {item["name"] for item in trace["tools"]}
            assert names == {item["function"]["name"] for item in binding}
            assert all(item["available"] for item in trace["tools"])
            assert "set_meal_plan" not in names and "show_shopping_list" not in names
            assert "set_cooking_timer" not in names and "update_prep_items" not in names
        assert "suggest_replies" in {item["name"] for item in traces[3]["tools"]}
        assert "suggest_replies" not in {item["name"] for item in traces[4]["tools"]}
        failure = next(call["result"] for call in debug["tool_calls"] if call["name"] == "suggest_replies")
        assert failure["status"] == "invalid_arguments"
        assert failure["errors"][0]["loc"] == ["suggested_replies"] and failure["errors"][0]["type"] == "missing"
        assert failure["errors"][0]["hint"] and failure["recovery"]["action"] == "answer"


def test_provider_integrity_error_is_private_debug_evidence_and_never_runs_a_tool(tmp_path):
    guard = ArgumentIntegrity("chat_completions")
    try:
        guard.chat_chunk({"choices": [{"index": 0, "finish_reason": "tool_calls", "delta": {"tool_calls": [
            {"index": 0, "id": "corrupt", "function": {"name": "update_preferences",
             "arguments": '{"api_key":"private-fixture-value","image":"data:image/png;base64,secret"'}},
        ]}}]})
    except ProviderIntegrityError as failure:
        provider_error = failure
    else:
        raise AssertionError("Corrupt fixture unexpectedly passed")
    app, source, model = make_app(tmp_path, [provider_error])
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        response = client.post(uri + "/turn", json={"message": "请按一人份安排"})
        stream = events(response)
        assert stream[-1]["status"] == "error" and len(model.histories) == 1
        assert not source.searches and not source.reads
        assert not any(event["type"] == "tool_start" for event in stream)
        assert all(event["type"] != "debug_context" for event in stream)
        assert client.get(uri + "/debug").status_code == 404
        public = client.get(uri).json()
        assert public["last_error"]["code"] == provider_error.code
        assert "parameter_integrity" not in json.dumps(public)
        capture = client.get(uri + "/debug", headers=DEVELOPER).json()["capture"]
        diagnostic = capture["debug_trace"][0]["parameter_integrity"]
        assert diagnostic["status"] == provider_error.code
        assert diagnostic["calls"][0]["raw_length"] > 0 and diagnostic["calls"][0]["raw_sha256"]
        assert all(secret not in response.text + json.dumps(capture) for secret in
                   ["private-fixture-value", "data:image/png", "base64,secret"])
