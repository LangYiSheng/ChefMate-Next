"""Integration tests run against a caller-provided PostgreSQL, never Docker."""

from __future__ import annotations

from copy import deepcopy

import psycopg
import pytest

from chefmate_storage import ExportValidationError, NutrientRange, RecipeFilter


def recipe_ids(result):
    return [item["recipe_id"] for item in result["items"]]


def test_migration_is_idempotent_and_metadata_is_initially_empty(postgres_store, postgres_execute):
    first = postgres_store.migrate()
    assert first >= 1
    assert postgres_store.migrate() == first
    assert postgres_store.metadata() is None
    assert postgres_store.search()["total"] == 0
    assert postgres_store.get_recipe("missing") is None
    assert postgres_execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND data_type IN ('json', 'jsonb')"
    )[0][0] == 0


def test_import_selected_data_order_and_repeat_without_duplicates(
    postgres_store, postgres_execute, make_recipe, make_export,
):
    recipe = make_recipe(ingredient_ids=("egg", "egg", "tomato"))
    directory = make_export([recipe])
    metadata = postgres_store.replace_from_export(directory, data_version="release-1")
    assert metadata == postgres_store.metadata()
    assert metadata["data_version"] == "release-1"
    assert metadata["export_schema_version"] == 2
    assert metadata["db_schema_version"] >= 1
    assert metadata["exported_at"]
    assert metadata["imported_at"]
    detail = postgres_store.get_recipe("r1")
    assert detail is not None
    for key in (
        "recipe_id", "name", "description", "source", "original_servings", "servings",
        "prep_time_min", "cook_time_min", "total_time_min", "difficulty", "steps",
        "warnings", "references", "taste_tags", "cooking_methods", "required_tools",
    ):
        assert detail[key] == recipe[key]
    assert [item["ingredient_id"] for item in detail["ingredients"]] == ["egg", "egg", "tomato"]
    assert [item["grams"] for item in detail["ingredients"]] == [100, 150, 200]
    for stored, original, original_nutrition in zip(
        detail["ingredients"], recipe["ingredients"], recipe["nutrition"]["ingredients"], strict=True,
    ):
        for key in (
            "ingredient_id", "canonical_name", "category", "raw_name", "raw_amount",
            "raw_unit", "raw_text", "grams", "scalable", "aliases", "allergens",
        ):
            assert stored[key] == original[key]
        assert stored["nutrition"]["contribution"] == original_nutrition["nutrition"]
        assert stored["nutrition"]["per_100g"] == original_nutrition["source"]["per_100g"]
        assert stored["nutrition"]["fdc_id"] == original_nutrition["source"]["fdc_id"]
        assert "amount_evidence" not in stored
        assert "usda_query" not in stored
    assert detail["nutrition"]["total"] == recipe["nutrition"]["total"]
    assert detail["nutrition"]["per_serving"] == recipe["nutrition"]["per_serving"]
    assert "raw_source_text" not in detail
    assert "provenance" not in detail
    assert "nutrition" not in metadata
    assert "sha256" not in metadata
    assert "recipe_count" not in metadata
    assert not any(value in repr(detail) for value in (
        "DO_NOT_STORE_RAW_SOURCE", "DO_NOT_STORE_SOURCE_HASH", "DO_NOT_STORE_REVIEW_NOTE",
        "DO_NOT_STORE_PROMPT", "review-only matching rationale",
    ))
    postgres_store.replace_from_export(directory, data_version="release-1")
    assert postgres_store.search()["total"] == 1
    assert postgres_execute("SELECT count(*) FROM recipe_ingredients")[0][0] == 3
    assert postgres_execute("SELECT count(*) FROM ingredients")[0][0] == 2


def test_full_replacement_removes_absent_recipes_and_unused_catalog(
    postgres_store, postgres_execute, make_recipe, make_export,
):
    old = make_recipe("old", ingredient_ids=("egg", "tomato"))
    current = make_recipe("new", "鸡肉", ingredient_ids=("chicken",))
    postgres_store.replace_from_export(make_export([old]), data_version="before")
    postgres_store.replace_from_export(make_export([current]), data_version="after")
    assert postgres_store.get_recipe("old") is None
    assert recipe_ids(postgres_store.search()) == ["new"]
    assert postgres_store.metadata()["data_version"] == "after"
    assert postgres_execute("SELECT ingredient_id FROM ingredients ORDER BY ingredient_id") == [("chicken",)]
    assert postgres_execute("SELECT count(*) FROM recipe_steps")[0][0] == len(current["steps"])


def test_invalid_export_leaves_current_data_and_metadata_unchanged(
    postgres_store, make_recipe, make_export,
):
    original = make_recipe("original")
    postgres_store.replace_from_export(make_export([original]), data_version="good")
    before = postgres_store.metadata()
    bad = make_export([make_recipe("bad")])
    with (bad / "recipes.json").open("ab") as output:
        output.write(b"\n")
    with pytest.raises(ExportValidationError):
        postgres_store.replace_from_export(bad, data_version="bad")
    assert postgres_store.metadata() == before
    assert recipe_ids(postgres_store.search()) == ["original"]


def test_database_failure_rolls_back_deleted_data_and_metadata(
    postgres_store, postgres_execute, make_recipe, make_export,
):
    postgres_store.replace_from_export(make_export([make_recipe("original")]), data_version="original")
    before = postgres_store.metadata()
    original_detail = postgres_store.get_recipe("original")
    postgres_execute("ALTER TABLE recipes ADD CONSTRAINT fixture_reject_name CHECK (name <> 'forbidden')")
    with pytest.raises(psycopg.errors.CheckViolation):
        postgres_store.replace_from_export(
            make_export([make_recipe("accepted", "accepted"), make_recipe("forbidden", "forbidden")]),
            data_version="failed",
        )
    assert postgres_store.metadata() == before
    assert postgres_store.get_recipe("original") == original_detail
    assert postgres_store.get_recipe("accepted") is None
    assert postgres_store.get_recipe("forbidden") is None


def test_ingredient_allergens_are_specific_to_each_recipe_occurrence(
    postgres_store, make_recipe, make_export,
):
    plain = make_recipe("plain", "普通鸡肉", ingredient_ids=("chicken",))
    marinated = make_recipe("marinated", "花生酱鸡肉", ingredient_ids=("chicken",))
    marinated["ingredients"][0].update(
        canonical_name="腌制鸡肉", category="prepared", aliases=["花生酱腌鸡"], allergens=["peanut"],
    )
    marinated["nutrition"]["ingredients"][0]["name"] = "腌制鸡肉"
    postgres_store.replace_from_export(make_export([plain, marinated]), data_version="allergens")
    assert recipe_ids(postgres_store.search(RecipeFilter(exclude_allergens=("peanut",)))) == ["plain"]
    assert postgres_store.get_recipe("plain")["ingredients"][0]["allergens"] == []
    occurrence = postgres_store.get_recipe("marinated")["ingredients"][0]
    assert occurrence["ingredient_id"] == "chicken"
    assert occurrence["canonical_name"] == "腌制鸡肉"
    assert occurrence["category"] == "prepared"
    assert occurrence["aliases"] == ["花生酱腌鸡"]
    assert occurrence["allergens"] == ["peanut"]


def test_filters_combine_and_unknown_nutrients_do_not_match(
    postgres_store, make_recipe, make_export,
):
    complete = make_recipe("complete", "完整菜谱")
    complete["nutrition"]["tags"] = ["high_protein"]
    simple = make_recipe("simple", "简单鸡蛋", ingredient_ids=("egg",))
    simple["taste_tags"] = ["sweet"]
    simple["cooking_methods"] = ["boil"]
    simple["required_tools"] = ["pot"]
    simple["total_time_min"] = 40
    incomplete = make_recipe("incomplete", "缺少营养", ingredient_ids=("chicken",))
    incomplete["ready_for_recommendation"] = False
    incomplete["nutrition"]["status"] = "incomplete"
    incomplete["nutrition"]["total"]["protein_g"] = None
    incomplete["nutrition"]["per_serving"]["protein_g"] = None
    incomplete["nutrition"]["ingredients"][0]["nutrition"]["protein_g"] = None
    incomplete["nutrition"]["ingredients"][0]["source"]["per_100g"]["protein_g"] = None
    incomplete["blocking_reasons"] = ["missing protein"]
    postgres_store.replace_from_export(make_export([complete, simple, incomplete]), data_version="filters")
    assert postgres_store.search()["total"] == 2
    assert postgres_store.search(RecipeFilter(ready_for_recommendation=None))["total"] == 3
    assert recipe_ids(postgres_store.search(RecipeFilter(ready_for_recommendation=False))) == ["incomplete"]
    assert postgres_store.get_recipe("incomplete")["blocking_reasons"] == ["missing protein"]
    filters = RecipeFilter(
        name="完整", ingredient_ids=("egg", "tomato"), exclude_ingredient_ids=("peanut",),
        taste_tags=("salty", "sweet"), cooking_methods=("stir_fry",), required_tools=("pan", "spatula"),
        nutrition_tags=("high_protein",), max_total_time_min=20, min_servings=2,
        nutrients=(NutrientRange("protein_g", minimum=10, maximum=15),),
    )
    assert recipe_ids(postgres_store.search(filters)) == ["complete"]
    assert postgres_store.search(RecipeFilter(exclude_ingredient_ids=("egg",)))["total"] == 0
    assert postgres_store.search(RecipeFilter(exclude_allergens=("egg",)))["total"] == 0
    assert recipe_ids(postgres_store.search(RecipeFilter(
        ready_for_recommendation=None, ingredient_ids=("chicken",),
        nutrients=(NutrientRange("protein_g", minimum=0),),
    ))) == []
    assert recipe_ids(postgres_store.search(RecipeFilter(
        nutrients=(NutrientRange("calories_kcal", minimum=200, maximum=300, basis="total"),),
    ))) == ["complete"]


def test_pagination_is_stable_and_name_search_treats_wildcards_as_text(
    postgres_store, make_recipe, make_export,
):
    recipes = [make_recipe(f"r{i}", "同名") for i in range(7)]
    recipes[3]["name"] = "100% 原味_鸡蛋"
    postgres_store.replace_from_export(make_export(recipes), data_version="pages")
    first = postgres_store.search(limit=3, offset=0)
    second = postgres_store.search(limit=3, offset=3)
    third = postgres_store.search(limit=3, offset=6)
    assert first["total"] == second["total"] == third["total"] == 7
    assert len(first["items"]) == 3
    assert len(third["items"]) == 1
    assert first == postgres_store.search(limit=3, offset=0)
    assert second == postgres_store.search(limit=3, offset=3)
    assert set(recipe_ids(first) + recipe_ids(second) + recipe_ids(third)) == {f"r{i}" for i in range(7)}
    assert recipe_ids(postgres_store.search(RecipeFilter(name="%"))) == ["r3"]
    assert recipe_ids(postgres_store.search(RecipeFilter(name="_"))) == ["r3"]
    assert postgres_store.search(limit=3, offset=100)["items"] == []


def test_estimated_servings_grams_and_nutrients_keep_markers_and_effective_values(
    postgres_store, make_recipe, make_export,
):
    recipe = make_recipe("estimated", ingredient_ids=("chicken",))
    recipe["servings"] = None
    recipe["ingredients"][0]["grams"] = None
    recipe["ingredients"][0]["raw_amount"] = "适量"
    recipe["ingredients"][0]["raw_unit"] = None
    recipe["ready_for_recommendation"] = False
    nutrition = recipe["nutrition"]
    fields = sorted(nutrition["total"])
    ingredient = nutrition["ingredients"][0]
    ingredient["source"]["per_100g"]["calories_kcal"] = None
    effective_per_100g = deepcopy(ingredient["source"]["per_100g"])
    effective_per_100g["calories_kcal"] = 150.0
    contribution = {key: value * 1.25 for key, value in effective_per_100g.items()}
    ingredient.update(
        grams=125.0, original_grams=None, estimated=True, estimated_grams=True,
        estimated_nutrients=fields, nutrition=contribution,
        estimate={"ingredient_id": "chicken", "grams": 125.0, "per_100g": {"calories_kcal": 150.0}},
    )
    nutrition.update(
        status="estimated", estimated=True, estimated_servings=True,
        estimated_total_fields=fields, estimated_per_serving_fields=fields,
        total=contribution, per_serving={key: value / 2 for key, value in contribution.items()},
        estimation={"servings": 2, "ingredients": [ingredient["estimate"]]},
    )
    postgres_store.replace_from_export(make_export([recipe]), data_version="estimates")
    detail = postgres_store.get_recipe("estimated")
    assert detail["servings"] == 2
    assert detail["nutrition"]["estimated_servings"] is True
    assert detail["nutrition"]["status"] == "estimated"
    assert detail["nutrition"]["estimated"] is True
    assert detail["nutrition"]["estimated_total_fields"] == fields
    assert detail["nutrition"]["estimated_per_serving_fields"] == fields
    assert detail["nutrition"]["total"] == contribution
    stored_ingredient = detail["ingredients"][0]
    assert stored_ingredient["grams"] == 125
    assert stored_ingredient["estimated_grams"] is True
    # USDA values retain their factual NULLs even when estimates contribute to totals.
    assert stored_ingredient["nutrition"]["per_100g"] == ingredient["source"]["per_100g"]
    assert stored_ingredient["nutrition"]["estimated"] is True
    assert stored_ingredient["nutrition"]["estimated_nutrients"] == fields
    assert "estimate" not in stored_ingredient["nutrition"]
    assert "estimation" not in detail["nutrition"]
    assert postgres_store.search()["total"] == 0
    assert recipe_ids(postgres_store.search(RecipeFilter(ready_for_recommendation=None))) == ["estimated"]


def test_empty_snapshot_requires_explicit_opt_in(postgres_store, make_recipe, make_export):
    postgres_store.replace_from_export(make_export([make_recipe()]), data_version="before")
    before = postgres_store.metadata()
    empty = make_export([])
    with pytest.raises(ExportValidationError):
        postgres_store.replace_from_export(empty, data_version="empty")
    assert postgres_store.metadata() == before
    assert postgres_store.search()["total"] == 1
    metadata = postgres_store.replace_from_export(empty, data_version="empty", allow_empty=True)
    assert metadata["data_version"] == "empty"
    assert postgres_store.search(RecipeFilter(ready_for_recommendation=None))["total"] == 0


def test_external_foreign_key_blocks_replacement_without_deleting_caller_data(
    postgres_store, postgres_execute, make_recipe, make_export,
):
    postgres_store.replace_from_export(make_export([make_recipe("old")]), data_version="old")
    metadata = postgres_store.metadata()
    postgres_execute("CREATE TABLE caller_bookmarks (recipe_id text PRIMARY KEY REFERENCES recipes(recipe_id))")
    postgres_execute("INSERT INTO caller_bookmarks(recipe_id) VALUES (%s)", ("old",))
    replacement = make_export([make_recipe("new", ingredient_ids=("chicken",))])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        postgres_store.replace_from_export(replacement, data_version="new")
    assert postgres_store.metadata() == metadata
    assert postgres_store.get_recipe("old") is not None
    assert postgres_store.get_recipe("new") is None
    assert postgres_execute("SELECT recipe_id FROM caller_bookmarks") == [("old",)]
    # Resolve the caller's reference explicitly, then internal child rows can cascade.
    postgres_execute("DELETE FROM caller_bookmarks")
    postgres_store.replace_from_export(replacement, data_version="new")
    assert postgres_execute("SELECT DISTINCT recipe_id FROM recipe_ingredients") == [("new",)]
    assert postgres_execute("SELECT DISTINCT recipe_id FROM recipe_steps") == [("new",)]


def test_query_text_is_parameterized_and_cannot_change_database(
    postgres_store, make_recipe, make_export,
):
    postgres_store.replace_from_export(make_export([make_recipe("safe")]), data_version="safe")
    metadata = postgres_store.metadata()
    malicious = "'; DROP TABLE recipes; --"
    assert postgres_store.search(RecipeFilter(name=malicious))["total"] == 0
    assert postgres_store.search(RecipeFilter(ingredient_ids=(malicious,)))["total"] == 0
    assert postgres_store.get_recipe(malicious) is None
    assert postgres_store.metadata() == metadata
    assert recipe_ids(postgres_store.search()) == ["safe"]
