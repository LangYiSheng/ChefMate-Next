"""Tool contracts, snapshot rejection and evidence behavior without a database."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from chefmate_retrieval import IngredientLookupRequest, RecipeSearchRequest, RetrievalService

SNAPSHOT = {"data_version": "fixture", "imported_at": "2026-10-13T00:00:00+00:00"}


def test_default_search_page_is_small_and_explicit_paging_remains_available():
    assert RecipeSearchRequest().limit == 8
    assert RecipeSearchRequest().ready_for_recommendation is None
    assert RecipeSearchRequest(ready_for_recommendation=True).ready_for_recommendation is True
    assert RecipeSearchRequest(limit=20, offset=8).limit == 20


class MemorySession:
    def __init__(self):
        self.calls = []

    def metadata(self):
        return {"data_version": "fixture", "imported_at": datetime(2026, 10, 13, tzinfo=timezone.utc)}

    def search_options(self):
        return {"taste": [{"code": "spicy", "recipe_count": 1}],
                "tool": [{"code": "wok", "recipe_count": 1}], "cooking_method": [],
                "nutrition": [], "difficulty": [], "category": [], "allergen": []}

    def selector_exists(self, ingredient_id, canonical_name):
        self.calls.append(("exists", ingredient_id, canonical_name))
        return ingredient_id == "shrimp" and canonical_name in (None, "虾仁")

    def missing_selectors(self, selectors):
        return [{"ingredient_id": selector.ingredient_id, "canonical_name": selector.canonical_name}
                for selector in selectors if not self.selector_exists(selector.ingredient_id, selector.canonical_name)]

    def lookup_ingredients(self, queries, fdc_ids):
        self.calls.append(("lookup", queries, fdc_ids))
        if not queries and not fdc_ids:
            return []
        return deepcopy([
            {"ingredient_id": "shrimp", "canonical_name": "虾仁", "aliases": ["鲜虾仁"], "categories": ["seafood"],
             "fdc_ids": ["175179"], "recipe_count": 2, "recommendable_recipe_count": 2,
             "examples": [{"raw_text": "虾仁100g", "recipe_id": "r1"}]},
            {"ingredient_id": "mixed_filling", "canonical_name": "混合馅", "aliases": [], "categories": ["other"],
             "fdc_ids": ["175179"], "recipe_count": 1, "recommendable_recipe_count": 1,
             "examples": [{"raw_text": "猪肉加虾滑或螺肉", "recipe_id": "r2"}]},
        ])

    def search(self, filters, **kwargs):
        self.calls.append(("search", filters, kwargs))
        return {"total": 1, "items": [{"recipe_id": "r1", "name": "虾仁汤", "warnings": ["虾仁可选"],
                                      "nutrition": {"total": {"protein_g": None}},
                                      "ingredients": [{"ingredient_id": "shrimp", "canonical_name": "虾仁",
                                                       "raw_text": "虾仁100g，可选", "grams": 100},
                                                      {"ingredient_id": "salt", "canonical_name": "盐"}]}]}

    def get_recipe(self, recipe_id):
        self.calls.append(("detail", recipe_id))
        return None


class MemoryStore:
    def __init__(self):
        self.session = MemorySession()
        self.read_count = 0

    @contextmanager
    def read_session(self):
        self.read_count += 1
        yield self.session


@pytest.fixture
def service():
    return RetrievalService(MemoryStore())


@pytest.mark.parametrize("payload", [
    {"ingredient_groups": [{"any_of": []}]}, {"limit": True}, {"limit": "10"}, {"limit": 0},
    {"max_total_time_min": float("nan")}, {"max_total_time_min": True},
    {"min_total_time_min": 30, "max_total_time_min": 20}, {"difficulties": ["easy"]},
    {"sort": {"field": "name; DROP TABLE recipes"}}, {"unknown_filter": "ignored"},
    {"nutrients": [{"name": "protein_g"}]}, {"available_tools": "wok"},
    {"name": "   "}, {"ingredient_groups": [{"any_of": [{"ingredient_id": " "}]}]},
])
def test_reject_invalid_query_before_any_database_access(service, payload):
    with pytest.raises(ValidationError):
        service.search_recipes(payload)
    assert service.store.read_count == 0


def test_lookup_requires_real_query_and_schema_is_inspectable():
    with pytest.raises(ValidationError):
        IngredientLookupRequest()
    with pytest.raises(ValidationError):
        IngredientLookupRequest(fdc_ids=["9999999999999999999"])
    assert IngredientLookupRequest.model_json_schema()["additionalProperties"] is False
    assert "ingredient_groups" in RecipeSearchRequest.model_json_schema()["properties"]


def test_snapshot_rejects_same_version_with_new_import_time_before_query(service):
    stale = {**SNAPSHOT, "imported_at": "2026-10-12T00:00:00Z"}
    for method, request in ((service.search_recipes, {}), (service.get_recipe, {"recipe_id": "r1"}),
                            (service.find_ingredients, {"queries": ["虾"]})):
        result = method({**request, "expected_snapshot": stale})
        assert result["status"] == "snapshot_changed"
        assert result["snapshot"] == SNAPSHOT
        assert "items" not in result
    assert service.store.session.calls == []


def test_equivalent_timestamp_timezones_are_same_snapshot(service):
    result = service.get_recipe({"recipe_id": "missing", "expected_snapshot": {
        **SNAPSHOT, "imported_at": "2026-10-13T08:00:00+08:00"}})
    assert result["status"] == "not_found"


def test_usda_reference_does_not_become_synonym(service):
    class Foods:
        def search(self, query, limit):
            return [{"fdcId": 175179, "description": "Crustaceans, shrimp, raw", "dataType": "SR Legacy"}]
    service.foods = Foods()
    result = service.find_ingredients({"queries": ["虾仁"], "usda_query": "shrimp"})
    assert service.store.read_count == 1
    assert result["snapshot"] == SNAPSHOT
    assert result["items"][0]["selector"] == {"ingredient_id": "shrimp", "canonical_name": "虾仁"}
    assert "canonical_name" in result["items"][0]["matched_by"]
    assert result["items"][0]["reference_only"] is False
    assert result["items"][1]["reference_only"] is True
    assert result["items"][1]["matched_by"] == ["usda_reference"]


def test_unconfigured_usda_is_reported(service):
    result = service.find_ingredients({"usda_query": "shrimp"})
    assert result["status"] == "usda_unavailable"
    assert result["usda"]["error"]
    assert result["items"] == []


def test_unknown_selector_or_option_never_becomes_unfiltered_search(service):
    result = service.search_recipes({"ingredient_groups": [{"any_of": [{"ingredient_id": "made_up_shrimp"}]}],
                                     "cooking_methods_any": ["made_up_method"]})
    assert result["status"] == "invalid_selection"
    assert len(result["unknown_ingredients"]) == 1
    assert len(result["unknown_options"]) == 1
    assert all(call[0] != "search" for call in service.store.session.calls)


def test_search_keeps_empty_tool_set_null_nutrition_and_matching_evidence(service):
    result = service.search_recipes({"ingredient_groups": [{"any_of": [{"ingredient_id": "shrimp"}]}],
                                     "available_tools": [], "max_total_time_min": 30,
                                     "nutrients": [{"name": "protein_g", "minimum": 1, "allow_estimated": False}],
                                     "expected_snapshot": SNAPSHOT})
    assert service.store.read_count == 1
    assert result["status"] == "ok"
    search = next(call for call in service.store.session.calls if call[0] == "search")
    assert search[1].available_tools == ()
    assert search[1].nutrients[0].allow_estimated is False
    assert result["items"][0]["nutrition"]["total"]["protein_g"] is None
    assert result["items"][0]["warnings"] == ["虾仁可选"]
    assert result["items"][0]["matched_ingredients"] == [{"ingredient_id": "shrimp", "canonical_name": "虾仁",
                                                         "raw_text": "虾仁100g，可选", "grams": 100,
                                                         "matched_groups": [0]}]


def test_available_tools_null_keeps_no_restriction(service):
    service.search_recipes({"available_tools": None})
    search = next(call for call in service.store.session.calls if call[0] == "search")
    assert search[1].available_tools is None


def test_options_labels_come_from_contracts_and_current_data(service):
    result = service.get_search_options()
    assert result["snapshot"] == SNAPSHOT
    assert {item["code"]: item["label"] for item in result["options"]["taste"]}["spicy"] == "辣"
    assert {item["code"] for item in result["options"]["difficulty"]} == {"beginner", "normal", "advanced"}
    assert "required_tools" in result["input_contracts"]["available_tools"]


def test_exact_unique_option_labels_are_canonicalized_without_relaxing_filters(service):
    request = {"ingredient_groups": [{"any_of": [{"ingredient_id": "shrimp", "canonical_name": "虾仁"}]}],
               "available_tools": ["炒锅", "wok", "炉灶"], "taste_tags_any": ["辣"],
               "max_total_time_min": 20, "expected_snapshot": SNAPSHOT}
    result = service.search_recipes(request)
    assert result["status"] == "ok"
    assert result["normalized_query"]["available_tools"] == ["wok", "stove"]
    assert result["normalized_query"]["taste_tags_any"] == ["spicy"]
    assert result["normalized_options"] == [
        {"field": "taste_tags_any", "value": "辣", "code": "spicy", "reason": "exact_label"},
        {"field": "available_tools", "value": "炒锅", "code": "wok", "reason": "exact_label"},
        {"field": "available_tools", "value": "炉灶", "code": "stove", "reason": "exact_label"},
    ]
    filters = next(call[1] for call in service.store.session.calls if call[0] == "search")
    assert filters.available_tools == ("wok", "stove")
    assert filters.taste_tags_any == ("spicy",)
    assert filters.max_total_time_min == 20
    assert filters.ingredient_groups[0].any_of[0].ingredient_id == "shrimp"
    assert request["available_tools"] == ["炒锅", "wok", "炉灶"]


@pytest.mark.parametrize("value", ["WOK", "炒锅子", "made_up_tool"])
def test_unknown_option_returns_field_specific_codes_and_does_not_guess(service, value):
    result = service.search_recipes({"available_tools": [value], "max_total_time_min": 20,
                                     "ingredient_groups": [{"any_of": [{"ingredient_id": "shrimp"}]}]})
    assert result["status"] == "invalid_selection"
    assert result["unknown_ingredients"] == []
    assert result["unknown_options"] == [{"field": "available_tools", "value": value,
                                           "reason": "unknown_option", "candidate_codes": []}]
    assert {"code": "wok", "label": "炒锅"} in result["valid_options"]["available_tools"]
    assert "available_tools" in result["message"]
    assert "食材ID或形态不存在" not in result["message"]
    assert result["normalized_query"]["max_total_time_min"] == 20
    assert any("只修正available_tools" in hint for hint in result["recovery"])
    assert not any(call[0] == "search" for call in service.store.session.calls)


def test_ambiguous_display_label_is_rejected_instead_of_selecting_a_code(service, monkeypatch):
    monkeypatch.setattr("chefmate_retrieval.service.default_vocabulary", lambda: [
        SimpleNamespace(code="wok", label="炒锅", kind="tool", enabled=True),
        SimpleNamespace(code="pan", label="炒锅", kind="tool", enabled=True),
    ])
    result = service.search_recipes({"available_tools": ["炒锅"]})
    assert result["status"] == "invalid_selection"
    assert result["unknown_options"] == [{"field": "available_tools", "value": "炒锅",
                                           "reason": "ambiguous_label", "candidate_codes": ["pan", "wok"]}]
    assert result["normalized_options"] == []
    assert not any(call[0] == "search" for call in service.store.session.calls)
    assert service.search_recipes({"available_tools": ["wok"]})["status"] == "ok"


def test_unknown_ingredient_recovery_does_not_blame_valid_options(service):
    result = service.search_recipes({"ingredient_groups": [{"any_of": [{"ingredient_id": "made_up_shrimp"}]}],
                                     "available_tools": ["wok"]})
    assert result["status"] == "invalid_selection"
    assert result["unknown_options"] == []
    assert result["valid_options"] == {}
    assert "食材ID或形态不存在" in result["message"]
    assert "find_ingredients" in result["recovery"][0]
    assert not any(call[0] == "search" for call in service.store.session.calls)
