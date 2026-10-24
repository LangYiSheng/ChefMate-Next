"""Prior successful searches remain valid evidence within the same dataset snapshot."""

import asyncio
import json

import pytest

from chefmate_agent import ChefMateAgent
from test_agent import SNAPSHOT, Service, agent, answer, tool


def search_result(identity, *, snapshot=SNAPSHOT, status="ok"):
    return {"role": "tool", "name": "search_recipes", "content": json.dumps({
        "status": status, "snapshot": snapshot, "items": [{"recipe_id": identity}] if identity else [],
    })}


def test_detailed_recipe_from_an_earlier_search_survives_a_complementary_search_across_turns():
    class ComplementaryService(Service):
        def search_recipes(self, request):
            self.calls.append(("search_recipes", request))
            identity = "potato" if request["max_total_time_min"] == 10 else "beef"
            return {"status": "ok", "snapshot": SNAPSHOT, "items": [{"recipe_id": identity}], "total": 1}

        def get_recipe(self, request):
            self.calls.append(("get_recipe", request))
            return {"status": "ok", "snapshot": SNAPSHOT, "recipe": {"recipe_id": request["recipe_id"]}}

    core, _, service = agent([
        tool("search_recipes", {"max_total_time_min": 10}, "potato-search"),
        tool("search_recipes", {"max_total_time_min": 30}, "beef-search"), answer("可以选土豆和牛肉。"),
        tool("get_recipe", {"recipe_id": "potato", "expected_snapshot": SNAPSHOT}, "potato-detail"), answer(),
    ], ComplementaryService())
    first = asyncio.run(core.run_turn([{"role": "user", "content": "帮我搭配两道菜"}]))
    assert first.status == "completed"
    assert [recipe["recipe_id"] for recipe in first.recipes] == ["beef"]
    second = asyncio.run(core.run_turn(first.messages + [{"role": "user", "content": "先看看土豆怎么做"}]))
    assert second.status == "completed"
    assert second.tool_calls[0].result["status"] == "ok"
    assert second.selected_recipe["recipe_id"] == "potato"
    assert [name for name, _ in service.calls] == ["search_recipes", "search_recipes", "get_recipe"]


@pytest.mark.parametrize("latest_status,latest_id", [("ok", "beef"), ("ok", None), ("invalid_selection", "beef")])
def test_recipe_evidence_is_not_replaced_by_a_later_page_empty_or_invalid_query(latest_status, latest_id):
    history = [search_result("potato"), search_result(latest_id, status=latest_status)]
    assert ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "potato", "expected_snapshot": SNAPSHOT,
    }, history) is None
    refused = ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "invented", "expected_snapshot": SNAPSHOT,
    }, history)
    assert refused["status"] == "unverified_reference"


def test_new_dataset_snapshot_invalidates_prior_recipe_evidence_and_requires_exact_snapshot():
    fresh = {**SNAPSHOT, "imported_at": "2026-10-14T00:00:00+00:00"}
    history = [search_result("potato"), search_result("beef", snapshot=fresh)]
    assert ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "potato", "expected_snapshot": SNAPSHOT,
    }, history)["status"] == "unverified_reference"
    wrong_snapshot = ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "beef", "expected_snapshot": SNAPSHOT,
    }, history)
    assert wrong_snapshot["status"] == "snapshot_required"
    assert wrong_snapshot["snapshot"] == fresh
    assert ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "beef", "expected_snapshot": fresh,
    }, history) is None


@pytest.mark.parametrize("personal_status", ["ok", "snapshot_changed"])
def test_personal_recipe_detail_snapshot_does_not_invalidate_official_recipe_evidence(personal_status):
    custom = {"data_version": "custom-egg:v2", "imported_at": SNAPSHOT["imported_at"]}
    output = {"status": personal_status, "snapshot": custom}
    if personal_status == "ok":
        output["recipe"] = {"recipe_id": "custom-egg"}
    history = [search_result("potato"), {"role": "assistant", "tool_calls": [{"id": "personal-read",
        "function": {"name": "get_recipe", "arguments": json.dumps({"recipe_id": "custom-egg"})}}]},
        {"role": "tool", "name": "get_recipe", "tool_call_id": "personal-read", "content": json.dumps(output)}]
    assert ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "potato", "expected_snapshot": SNAPSHOT,
    }, history) is None


def test_unrelated_app_tool_snapshot_cannot_relabel_official_reference_evidence():
    unrelated = {"data_version": "memory-v2", "imported_at": SNAPSHOT["imported_at"]}
    history = [search_result("potato"), {"role": "tool", "name": "read_personal_data",
        "content": json.dumps({"status": "ok", "snapshot": unrelated})}]
    assert ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "potato", "expected_snapshot": SNAPSHOT,
    }, history) is None
    assert ChefMateAgent._verify_references("get_recipe", {
        "recipe_id": "potato", "expected_snapshot": unrelated,
    }, history)["status"] == "snapshot_required"


def test_dataset_change_invalidates_this_turn_read_deduplication_but_fresh_results_are_deduplicated():
    fresh = {**SNAPSHOT, "imported_at": "2026-10-14T00:00:00+00:00"}

    class ReimportedService(Service):
        current = SNAPSHOT

        def get_search_options(self):
            self.calls.append(("get_search_options", {}))
            return {"status": "ok", "snapshot": self.current}

        def search_recipes(self, request):
            self.calls.append(("search_recipes", request))
            self.current = fresh
            return {"status": "snapshot_changed", "snapshot": fresh}

    core, _, service = agent([
        tool("get_search_options", {}, "old-options"),
        tool("search_recipes", {"expected_snapshot": SNAPSHOT}, "changed-search"),
        tool("get_search_options", {}, "fresh-options"),
        tool("get_search_options", {}, "repeat-options"), answer(),
    ], ReimportedService())
    result = asyncio.run(core.run_turn([{"role": "user", "content": "导入更新后继续查选项"}]))
    assert result.status == "completed"
    assert [record.result["status"] for record in result.tool_calls] == ["ok", "snapshot_changed", "ok", "duplicate_query"]
    assert result.tool_calls[2].result["snapshot"] == fresh
    assert result.tool_calls[3].result["previous_call_id"] == "fresh-options"
    assert [name for name, _ in service.calls] == ["get_search_options", "search_recipes", "get_search_options"]


def test_successful_new_dataset_read_invalidates_earlier_options_cache():
    fresh = {**SNAPSHOT, "imported_at": "2026-10-14T00:00:00+00:00"}

    class FreshService(Service):
        current = SNAPSHOT

        def get_search_options(self):
            self.calls.append(("get_search_options", {}))
            return {"status": "ok", "snapshot": self.current}

        def search_recipes(self, request):
            self.calls.append(("search_recipes", request))
            self.current = fresh
            return {"status": "ok", "snapshot": fresh, "items": []}

    core, _, service = agent([tool("get_search_options", {}, "old"), tool("search_recipes", {}, "new-search"),
                             tool("get_search_options", {}, "fresh"), answer()], FreshService())
    result = asyncio.run(core.run_turn([{"role": "user", "content": "继续查询更新后的数据"}]))
    assert result.status == "completed"
    assert all(record.result["status"] == "ok" for record in result.tool_calls)
    assert result.tool_calls[-1].result["snapshot"] == fresh
    assert [name for name, _ in service.calls] == ["get_search_options", "search_recipes", "get_search_options"]


def test_personal_detail_does_not_clear_official_read_cache(tmp_path):
    from chefmate_agent import AgentConfig, SkillRegistry
    from chefmate_agent.runner import TurnRuntimeContext
    from langchain_core.messages import ToolMessage
    from test_agent import REQUESTS, ScriptedModel

    root = tmp_path / "recipe-authoring"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: recipe-authoring\ndescription: Personal recipes\n---\nRead recipes.",
                                  encoding="utf-8")
    custom = {"data_version": "custom-egg:v2", "imported_at": SNAPSHOT["imported_at"]}

    class PersonalService(Service):
        def get_recipe(self, request):
            self.calls.append(("get_recipe", request))
            return {"status": "ok", "snapshot": custom, "recipe": {"recipe_id": "custom-egg"}}

    core = ChefMateAgent(PersonalService(), AgentConfig(model="test"), model=ScriptedModel([]),
                         request_models=REQUESTS, skills=SkillRegistry([root]))
    context = TurnRuntimeContext(read_queries={"official-cached-query": "official-call"}, read_snapshot=SNAPSHOT)
    messages = [ToolMessage(name="search_custom_recipes", tool_call_id="custom-search", content=json.dumps({
        "status": "ok", "items": [{"recipe_id": "custom-egg", "snapshot": custom}],
    }))]
    output = asyncio.run(core._make_tool("get_recipe", context, messages, "personal-detail").ainvoke({
        "recipe_id": "custom-egg", "expected_snapshot": custom,
    }))
    assert output["status"] == "ok"
    assert context.read_queries["official-cached-query"] == "official-call"
    assert context.read_snapshot == SNAPSHOT
