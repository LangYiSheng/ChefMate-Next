"""Synthetic fixtures for independent package tests (no cleaning app imports)."""
import json
import zipfile

import pytest


@pytest.fixture
def recipe():
    from chefmate_contracts import RecipeDocument
    return RecipeDocument.model_validate(dict(
        name="土豆汤", description=None, original_servings="2人份", servings=2,
        servings_evidence="原文2人份", prep_time_min=None, cook_time_min=None, total_time_min=None,
        difficulty="beginner", ingredients=[dict(
            raw_name="马铃薯", raw_amount="200g", raw_unit="g", raw_text="马铃薯200g",
            ingredient_id="potato", canonical_name="土豆", category="vegetable", aliases=["马铃薯"],
            allergens=[], grams=200, amount_evidence="原文200g", scalable=True, usda_query="potato raw")],
        steps=["煮熟"], taste_tags=["light"], cooking_methods=["boil"], required_tools=["pot"], warnings=[]))


@pytest.fixture
def catalogue(tmp_path):
    from chefmate_food_data import Dataset, LocalFoodData, import_datasets
    folder = tmp_path / "archives"
    folder.mkdir()
    datasets = (Dataset("Foundation", "test", "foundation.zip", "FoundationFoods", 1),
                Dataset("SR Legacy", "test", "sr.zip", "SRLegacyFoods", 1))
    for index, dataset in enumerate(datasets):
        food = {"fdcId": 123 + index, "description": "SYNTHETIC potato raw", "dataType": dataset.data_type,
                "foodNutrients": [{"nutrient": {"id": key, "unitName": unit}, "amount": value}
                                 for key, unit, value in [(1008, "KCAL", 100), (1003, "G", 2),
                                                         (1004, "G", 1), (1005, "G", 20),
                                                         (2000, "G", 1), (1093, "MG", 5)]]}
        with zipfile.ZipFile(folder / dataset.filename, "w") as archive:
            archive.writestr("foods.json", json.dumps({dataset.root: [food]}))
    path = tmp_path / "usda.db"
    import_datasets(folder, path, datasets=datasets)
    return LocalFoodData(path)
