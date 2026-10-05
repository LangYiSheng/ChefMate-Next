from chefmate_food_data import FoodRecord


def test_normalized_and_raw_queries_preserve_two_sources(catalogue):
    assert catalogue.status()["count"] == 2
    assert {d["data_type"] for d in catalogue.list_datasets()} == {"Foundation", "SR Legacy"}
    candidates = catalogue.search("potato raw")
    assert [c["fdcId"] for c in candidates] == [123, 124]
    record = catalogue.get_food("123")
    assert isinstance(record, FoodRecord)
    assert record.nutrients_per_100g.calories_kcal == 100
    assert record.provenance["version"] == "test"
    assert catalogue.get_raw_food("123") == catalogue.food("123")
    assert catalogue.get_food("124").data_type == "SR Legacy"
