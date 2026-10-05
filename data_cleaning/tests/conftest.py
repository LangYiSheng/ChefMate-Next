import pytest


@pytest.fixture(autouse=True)
def isolate_ai_configuration(monkeypatch, tmp_path):
    """Tests must not inherit the developer's selected provider or credentials."""
    monkeypatch.setenv("OPENAI_API_MODE", "responses")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    monkeypatch.setenv("USDA_DB_PATH", str(tmp_path / "not-installed.db"))


@pytest.fixture
def local_catalog(tmp_path):
    import json
    import zipfile
    from chefmate_food_data.datasets import Dataset, import_datasets
    from chefmate_food_data.fooddata import LocalFoodData
    folder = tmp_path / "archives"
    folder.mkdir()
    datasets = (
        Dataset("Foundation", "test", "foundation.zip", "FoundationFoods", 3, 1),
        Dataset("SR Legacy", "test", "sr.zip", "SRLegacyFoods", 2),
    )
    descriptions = {"Foundation": [(123, "SYNTHETIC potato raw"), (124, "Chicken breast raw"), (125, "Chicken breast cooked")],
                    "SR Legacy": [(126, "SYNTHETIC potato raw"), (127, "Egg white raw")]}
    for dataset in datasets:
        foods = [{"fdcId": number, "description": desc, "dataType": dataset.data_type,
                  "foodNutrients": [{"nutrient": {"id": 1008, "unitName": "KCAL"}, "amount": 100}]}
                 for number, desc in descriptions[dataset.data_type]]
        if dataset.expected_nulls:
            foods.append(None)
        with zipfile.ZipFile(folder / dataset.filename, "w") as archive:
            archive.writestr("foods.json", json.dumps({dataset.root: foods}))
    database = tmp_path / "usda.db"
    import_datasets(folder, database, datasets=datasets)
    return LocalFoodData(database), folder, datasets
