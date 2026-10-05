"""Verify the example's score, missing supplies, flags, and read-only behavior."""

import importlib.util
import os
from pathlib import Path

import pytest


def rank_demo(*args, **kwargs):
    example = Path(__file__).parents[1] / "examples" / "query_and_score.py"
    spec = importlib.util.spec_from_file_location("chefmate_score_example", example)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.rank_demo(*args, **kwargs)


def test_scoring_unique_supplies_time_and_estimate_discount(
    postgres_store, make_recipe, make_export,
):
    fast = make_recipe("fast", "快手鸡蛋", ingredient_ids=("egg", "egg"))
    missing = make_recipe("missing", "需要补鸡肉", ingredient_ids=("egg", "chicken"))
    unknown_time = make_recipe("unknown_time", "时间未知", ingredient_ids=("egg",))
    estimated = make_recipe("estimated", "估算鸡蛋", ingredient_ids=("egg",))
    for recipe in (fast, missing, unknown_time, estimated):
        recipe.update(total_time_min=3, difficulty="beginner")
    unknown_time["total_time_min"] = None
    fields = list(estimated["nutrition"]["total"])
    estimated["ingredients"][0]["grams"] = None
    estimated["nutrition"]["ingredients"][0].update(
        original_grams=None, estimated_grams=True, estimated=True, estimated_nutrients=fields,
    )
    estimated["nutrition"].update(status="estimated", estimated=True, estimated_total_fields=fields,
                                  estimated_per_serving_fields=fields)
    estimated["ready_for_recommendation"] = False
    postgres_store.replace_from_export(make_export([fast, missing, estimated, unknown_time]), data_version="demo")
    before = postgres_store.metadata()
    # Use the same caller-supplied test DSN, never the production CLI variable.
    output = rank_demo(os.environ["CHEFMATE_TEST_DSN"], schema=postgres_store.schema,
                       pantry=("egg",), include_unready=True)
    rows = {row["recipe_id"]: row for row in output["ranking"]}
    assert [row["recipe_id"] for row in output["ranking"]] == ["fast", "estimated", "missing"]
    assert rows["fast"]["score"] == 97
    assert rows["fast"]["main_count"] == rows["fast"]["pantry_count"] == 1
    assert rows["estimated"]["score"] == 92
    assert rows["estimated"]["quality_score"] == 5
    assert rows["missing"]["score"] == 77
    assert rows["missing"]["missing_ingredients"] == ["鸡肉"]
    assert output["data_version"] == "demo"
    assert postgres_store.metadata() == before
    default = rank_demo(os.environ["CHEFMATE_TEST_DSN"], schema=postgres_store.schema, pantry=("egg",))
    assert "estimated" not in {row["recipe_id"] for row in default["ranking"]}
    injected = rank_demo(os.environ["CHEFMATE_TEST_DSN"], schema=postgres_store.schema,
                         pantry=("egg'; DROP TABLE recipes; --",), include_unready=True)
    assert injected["ranking"] == []
    assert postgres_store.get_recipe("fast") is not None


@pytest.mark.parametrize("kwargs", [
    {"max_time": 0}, {"max_time": float("nan")}, {"limit": True}, {"pantry": []}, {"include_unready": 1},
])
def test_invalid_demo_inputs_do_not_connect(kwargs):
    with pytest.raises(ValueError):
        rank_demo("host=unused", **kwargs)
