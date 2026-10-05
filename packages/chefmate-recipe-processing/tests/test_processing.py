import asyncio
from copy import deepcopy

import pytest

from chefmate_contracts import default_vocabulary
from chefmate_recipe_processing import (
    RecipeProcessor, RecipeSource, ProcessingOptions, process_recipe, aprocess_recipe,
    match_ingredients, amatch_ingredients, calculate_nutrition,
    estimate_missing, aestimate_missing,
)
from chefmate_recipe_processing.nutrition import food_key


class Model:
    def __init__(self, recipe):
        self.recipe = recipe
        self.calls = []
        self.settings = {"model": "synthetic"}
        self.progress = None

    def request(self, purpose, payload, schema, entity_id, validator=None, **kwargs):
        self.calls.append((purpose, entity_id))
        if purpose == "convert":
            result = self.recipe.model_copy(deep=True)
        elif purpose == "usda_match":
            result = schema(fdc_id="123", confidence=0.9, reason="synthetic")
        else:
            raise ValueError("synthetic estimation failure")
        if validator:
            validator(result)
        return result

    async def arequest(self, *args, **kwargs):
        await asyncio.sleep(0)
        return self.request(*args, **kwargs)


def stable(result):
    result = deepcopy(result.model_dump())
    def strip_observation_times(value):
        if isinstance(value, dict):
            value.pop("fetched_at", None)
            value.pop("created_at", None)
            for child in value.values():
                strip_observation_times(child)
        elif isinstance(value, list):
            for child in value:
                strip_observation_times(child)
    strip_observation_times(result)
    return result


def test_sync_async_same_workflow_and_callbacks_do_not_mutate_model(recipe, catalogue):
    model = Model(recipe)
    source = RecipeSource(kind="markdown", content="# 土豆汤", source_id="r")
    options = ProcessingOptions(with_nutrition=True)
    progress = []
    sync = process_recipe(source, ai=model, food_data=catalogue, vocabulary=default_vocabulary(),
                          options=options, progress=progress.append)
    asynchronous = asyncio.run(aprocess_recipe(source, ai=model, food_data=catalogue,
                                               vocabulary=default_vocabulary(), options=options,
                                               progress=progress.append))
    assert stable(sync) == stable(asynchronous)
    assert sync.nutrition["total"]["calories_kcal"] == 200
    assert progress and model.progress is None


def test_snapshot_reuse_and_missing_values(recipe, catalogue):
    model = Model(recipe)
    result = match_ingredients(recipe, ai=model, food_data=catalogue)
    calls = len(model.calls)
    repeated = asyncio.run(amatch_ingredients(recipe, ai=model, food_data=catalogue, snapshots=result.snapshots))
    assert not repeated.changed and len(model.calls) == calls
    unknown = calculate_nutrition(recipe, {})
    assert unknown["total"]["calories_kcal"] is None
    assert unknown["status"] == "incomplete"


def test_estimation_failure_retains_matching_result(recipe, catalogue):
    result = asyncio.run(aprocess_recipe("土豆汤", ai=Model(recipe), food_data=catalogue,
                                        vocabulary=default_vocabulary(),
                                        options={"with_nutrition": True, "estimate_missing": True}))
    # Complete nutrients/servings require no estimate and no extra model request.
    assert not result.issues and result.estimates is None
    recipe.ingredients[0].grams = None
    result = asyncio.run(aprocess_recipe("土豆汤", ai=Model(recipe), food_data=catalogue,
                                        vocabulary=default_vocabulary(),
                                        options={"with_nutrition": True, "estimate_missing": True}))
    assert result.ingredient_matches[food_key(recipe.ingredients[0])]["fdc_id"] == "123"
    assert result.issues[0].stage == "estimate"
    assert result.nutrition["status"] == "incomplete"


def test_out_of_candidate_choice_is_not_fetched(recipe, catalogue):
    class Invalid(Model):
        def request(self, purpose, payload, schema, entity_id, validator=None, **kwargs):
            return schema(fdc_id="999", confidence=1, reason="invalid")
    result = match_ingredients(recipe, ai=Invalid(recipe), food_data=catalogue)
    assert result.issues[0].status == "processing_failed"
    assert "fdc_id" not in next(iter(result.snapshots.values()))


def test_extraction_failure_propagates(recipe):
    class Invalid(Model):
        def request(self, *args, **kwargs):
            raise ValueError("extract failed")
    with pytest.raises(ValueError, match="extract failed"):
        process_recipe("菜谱", ai=Invalid(recipe), vocabulary=default_vocabulary())


def test_async_cancellation_is_not_converted_to_partial_result(recipe):
    async def scenario():
        started = asyncio.Event()
        class Slow(Model):
            async def arequest(self, *args, **kwargs):
                started.set()
                await asyncio.Event().wait()
        model = Slow(recipe)
        task = asyncio.create_task(aprocess_recipe("菜谱", ai=model, vocabulary=default_vocabulary()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not model.calls
    asyncio.run(scenario())


def test_legacy_progress_and_settings_are_not_modified(recipe):
    model = Model(recipe)
    settings = dict(model.settings)
    RecipeProcessor(model, progress=lambda value: None).extract("菜谱", vocabulary=default_vocabulary())
    assert model.progress is None and model.settings == settings


def test_async_estimate_preserves_facts_and_invalidates_on_edit(recipe):
    class Estimate(Model):
        def request(self, purpose, payload, schema, entity_id, validator=None, **kwargs):
            result = schema(ingredients=[{"ingredient_id": "potato", "grams": 100,
                            "per_100g": {key: 5 for key in ("calories_kcal", "protein_g", "fat_g",
                                                            "carbohydrate_g", "sugar_g", "sodium_mg")},
                            "reason": "synthetic assumption"}], servings=3, servings_reason="synthetic")
            if validator:
                validator(result)
            return result
    recipe.ingredients[0].grams = None
    key = food_key(recipe.ingredients[0])
    snapshots = {key: {"per_100g": {"protein_g": 7}, "source": "USDA_FDC"}}
    sync = estimate_missing(recipe, snapshots, Estimate(recipe))
    asynchronous = asyncio.run(aestimate_missing(recipe, snapshots, Estimate(recipe)))
    assert {k: v for k, v in sync.items() if k != "created_at"} == {
        k: v for k, v in asynchronous.items() if k != "created_at"}
    result = calculate_nutrition(recipe, snapshots, estimates=asynchronous)
    assert result["estimated"] and result["total"]["protein_g"] == 7
    assert result["servings"] == 2 and recipe.ingredients[0].grams is None
    recipe.ingredients[0].raw_text = "changed amount"
    assert calculate_nutrition(recipe, snapshots, estimates=asynchronous)["total"]["protein_g"] is None
