"""Real SQL tests in disposable schemas; never replace the caller's current dataset."""

import pytest
import os
import psycopg

from chefmate_retrieval import RetrievalService
from chefmate_storage import RecipeStore


def search_ids(result):
    assert result["status"] == "ok"
    return [item["recipe_id"] for item in result["items"]]


@pytest.fixture
def retrieval_fixture(postgres_store, make_recipe, make_export):
    whole = make_recipe("whole", "蒜蓉虾", ingredient_ids=("shrimp", "tomato"))
    peeled = make_recipe("peeled", "虾仁蛋", ingredient_ids=("shrimp", "egg"))
    red = make_recipe("red", "番茄红虾", ingredient_ids=("red_shrimp", "tomato"))
    no_tools = make_recipe("no_tools", "不用厨具", ingredient_ids=("egg",))
    for recipe in (whole, peeled, red, no_tools):
        recipe["difficulty"] = "beginner"
        recipe["required_tools"] = ["stove", "wok"]
        recipe["taste_tags"] = ["light"]
        recipe["cooking_methods"] = ["stir_fry"]
    no_tools["required_tools"] = []
    no_tools["total_time_min"] = None
    no_tools["prep_time_min"] = None
    no_tools["cook_time_min"] = None
    for recipe, name in ((whole, "整虾"), (peeled, "虾仁"), (red, "红虾")):
        recipe["ingredients"][0]["canonical_name"] = name
        recipe["ingredients"][0]["raw_name"] = name
        recipe["ingredients"][0]["raw_text"] = name + "100g"
        recipe["nutrition"]["ingredients"][0]["name"] = name
    whole["total_time_min"] = 30
    whole["prep_time_min"] = 10
    whole["cook_time_min"] = 20
    red["total_time_min"] = 10
    red["prep_time_min"] = 5
    red["cook_time_min"] = 5
    red["taste_tags"] = ["spicy"]
    postgres_store.replace_from_export(make_export([whole, peeled, red, no_tools]), data_version="fixtures")
    return RetrievalService(postgres_store)


def test_or_within_groups_and_and_between_groups(retrieval_fixture):
    query = {"ingredient_groups": [
        {"any_of": [{"ingredient_id": "shrimp"}, {"ingredient_id": "red_shrimp"}]},
        {"any_of": [{"ingredient_id": "tomato"}]},
    ], "sort": {"field": "total_time_min", "direction": "asc"}, "limit": 1}
    first = retrieval_fixture.search_recipes(query)
    second = retrieval_fixture.search_recipes({**query, "offset": 1})
    assert first["total"] == 2
    assert first["has_more"] is True
    assert search_ids(first) == ["red"]
    assert search_ids(second) == ["whole"]
    assert second["has_more"] is False
    assert {tuple(item["matched_groups"]) for item in first["items"][0]["matched_ingredients"]} == {(0,), (1,)}


def test_specific_shape_and_exclusion(retrieval_fixture):
    assert search_ids(retrieval_fixture.search_recipes({"ingredient_groups": [
        {"any_of": [{"ingredient_id": "shrimp", "canonical_name": "虾仁"}]}]})) == ["peeled"]
    assert search_ids(retrieval_fixture.search_recipes({"ingredient_groups": [
        {"any_of": [{"ingredient_id": "shrimp"}]}],
        "exclude_ingredients": [{"ingredient_id": "shrimp", "canonical_name": "虾仁"}]})) == ["whole"]


def test_tool_subset_and_unknown_time(retrieval_fixture):
    assert search_ids(retrieval_fixture.search_recipes({"available_tools": []})) == ["no_tools"]
    assert search_ids(retrieval_fixture.search_recipes({"available_tools": ["stove"]})) == ["no_tools"]
    assert retrieval_fixture.search_recipes({"available_tools": ["stove", "wok"]})["total"] == 4
    assert retrieval_fixture.search_recipes({"max_total_time_min": 30})["total"] == 3
    assert search_ids(retrieval_fixture.search_recipes({"sort": {"field": "total_time_min", "direction": "desc"}}))[-1] == "no_tools"


def test_alias_lookup_literal_patterns_and_snapshot_invalidation(retrieval_fixture, postgres_store,
                                                               make_recipe, make_export):
    lookup = retrieval_fixture.find_ingredients({"queries": ["西红柿"]})
    assert lookup["total"] == 1
    assert lookup["items"][0]["ingredient_id"] == "tomato"
    assert lookup["items"][0]["matched_by"] == ["alias"]
    assert retrieval_fixture.find_ingredients({"queries": ["%"]})["total"] == 0
    assert retrieval_fixture.find_ingredients({"queries": ["_; DROP TABLE recipes"]})["total"] == 0
    old = lookup["snapshot"]
    recipe = make_recipe("new")
    recipe["difficulty"] = "beginner"
    # Same caller-defined version still creates a different imported_at.
    postgres_store.replace_from_export(make_export([recipe]), data_version="fixtures")
    assert retrieval_fixture.search_recipes({"expected_snapshot": old})["status"] == "snapshot_changed"
    assert retrieval_fixture.get_recipe({"recipe_id": "new", "expected_snapshot": old})["status"] == "snapshot_changed"


def test_nutrition_estimates_checked_per_filtered_metric(postgres_store, postgres_execute, make_recipe, make_export):
    recipe = make_recipe("estimated")
    recipe["difficulty"] = "normal"
    postgres_store.replace_from_export(make_export([recipe]), data_version="estimate-fixture")
    postgres_execute("UPDATE recipe_nutrition SET estimated_total_calories_kcal=true, "
                     "estimated_per_serving_calories_kcal=true,estimated=true,status='estimated'")
    service = RetrievalService(postgres_store)
    assert search_ids(service.search_recipes({"nutrients": [{"name": "protein_g", "minimum": 1,
                                                            "allow_estimated": False}]})) == ["estimated"]
    assert service.search_recipes({"nutrients": [{"name": "calories_kcal", "minimum": 1,
                                                 "allow_estimated": False}]})["total"] == 0
    postgres_execute("UPDATE recipe_nutrition SET per_serving_protein_g=NULL")
    assert service.search_recipes({"nutrients": [{"name": "protein_g", "maximum": 100}]})["total"] == 0
    assert service.get_recipe({"recipe_id": "estimated"})["recipe"]["nutrition"]["per_serving"]["protein_g"] is None


def test_session_expires_when_transaction_is_closed(postgres_store):
    with postgres_store.read_session() as session:
        assert session.metadata() is None
    with pytest.raises(RuntimeError, match="closed"):
        session.metadata()


def test_read_query_timeout_stops_background_statement_and_connection_recovers(postgres_store):
    timed = RecipeStore(os.environ["CHEFMATE_TEST_DSN"], schema=postgres_store.schema,
                        read_statement_timeout_ms=100)
    with pytest.raises(psycopg.errors.QueryCanceled):
        with timed.read_session() as session:
            session._active().execute("SELECT pg_catalog.pg_sleep(0.3)")
    assert timed.metadata() is None


def test_read_session_cannot_mutate_even_with_direct_sql(postgres_store):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with postgres_store.read_session() as session:
            session._active().execute("UPDATE recipes SET name=name")
    assert postgres_store.search()["total"] == 0
