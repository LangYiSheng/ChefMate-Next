"""Public filters reject unsafe dynamic fields and CLI mutations require explicit modes."""

import pytest

from chefmate_storage import NutrientRange, RecipeFilter, RecipeStore
from chefmate_storage.cli import main, parser
from chefmate_storage.queries import where_clause


@pytest.mark.parametrize("name,basis", [("sql; DROP TABLE recipes", "total"), ("protein_g", "unknown")])
def test_nutrient_dynamic_columns_use_closed_vocabulary(name, basis):
    with pytest.raises(ValueError):
        NutrientRange(name, minimum=0, basis=basis)


@pytest.mark.parametrize("value", [True, -1, float("inf"), float("nan"), "1"])
def test_invalid_numeric_bounds(value):
    with pytest.raises(ValueError):
        NutrientRange("protein_g", minimum=value)


def test_ranges_and_filter_inputs():
    with pytest.raises(ValueError):
        NutrientRange("protein_g", minimum=20, maximum=10)
    with pytest.raises(ValueError):
        NutrientRange("protein_g")
    with pytest.raises(ValueError):
        RecipeFilter(ingredient_ids="chicken")
    with pytest.raises(ValueError):
        RecipeFilter(ready_for_recommendation=1)
    assert RecipeFilter(ingredient_ids=["chicken", "chicken"]).ingredient_ids == ("chicken",)


@pytest.mark.parametrize("schema", ["public", "information_schema", "pg_bad", "Bad", "a;drop schema public", "x" * 64])
def test_schemas_cannot_target_shared_or_injected_names(schema):
    with pytest.raises(ValueError):
        RecipeStore("host=127.0.0.1", schema=schema)


def test_query_values_are_bound_and_literal_wildcards_are_escaped():
    text, args = where_clause(RecipeFilter(name="100%_'; DROP TABLE recipes; --", ingredient_ids=("egg'",)))
    assert "DROP" not in text
    assert args[0] == "%100\\%\\_'; DROP TABLE recipes; --%"
    assert args[-1] == "egg'"


def test_cli_replacement_needs_explicit_flag():
    with pytest.raises(SystemExit) as error:
        parser().parse_args(["import", "some-directory"])
    assert error.value.code == 2
    args = parser().parse_args(["import", "some-directory", "--replace"])
    assert args.replace


def test_cli_requires_dsn_but_does_not_print_credentials(monkeypatch, capsys):
    monkeypatch.delenv("CHEFMATE_DATABASE_URL", raising=False)
    assert main(["metadata"]) == 1
    assert "CHEFMATE_DATABASE_URL" in capsys.readouterr().err


def test_cli_nutrient_syntax():
    args = parser().parse_args(["search", "--nutrient", "protein_g:per_serving:20:"])
    assert args.nutrient == [NutrientRange("protein_g", minimum=20)]
    with pytest.raises(SystemExit):
        parser().parse_args(["search", "--nutrient", "bad"])
