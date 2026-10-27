"""Real bundled recipes are discoverable without changing review or nutrition facts."""

from pathlib import Path

from chefmate_retrieval import RetrievalService
from chefmate_storage import RecipeFilter


def test_bundled_catalog_general_discovery_preserves_strict_readiness_and_unknown_nutrition(
        postgres_store, postgres_execute):
    directory = Path(__file__).resolve().parents[3] / "data" / "seed" / "catalog"
    postgres_store.replace_from_export(directory, data_version="bundled-discovery-acceptance", allow_unapproved=True)
    service = RetrievalService(postgres_store)

    general = service.search_recipes({})
    assert general["status"] == "ok" and general["total"] == 358 and len(general["items"]) == 8
    assert general["normalized_query"]["ready_for_recommendation"] is None
    assert all(item["ready_for_recommendation"] is False for item in general["items"])
    assert any("尚未达到完整推荐条件" in note for note in general["notes"])
    assert service.search_recipes({"ready_for_recommendation": True})["total"] == 0
    assert service.search_recipes({"ready_for_recommendation": False})["total"] == 358
    assert postgres_store.search(RecipeFilter())["total"] == 0
    assert postgres_execute("SELECT count(*) FROM recipes WHERE ready_for_recommendation")[0][0] == 0

    first = general["items"][0]
    assert service.search_recipes({"name": first["name"]})["total"] >= 1
    lookup = service.find_ingredients({"queries": ["鸡蛋"]})
    assert lookup["status"] == "ok" and lookup["items"]
    selected = lookup["items"][0]["selector"]
    ingredient_result = service.search_recipes({"ingredient_groups": [{"any_of": [selected]}]})
    assert ingredient_result["status"] == "ok" and ingredient_result["total"] > 0
    assert all(item["matched_ingredients"] for item in ingredient_result["items"])

    missing_id, missing_name = postgres_execute("SELECT r.recipe_id,r.name FROM recipes r JOIN recipe_nutrition n "
        "ON r.recipe_id=n.recipe_id WHERE n.per_serving_protein_g IS NULL ORDER BY r.recipe_id LIMIT 1")[0]
    unfiltered = service.search_recipes({"name": missing_name, "limit": 100})
    assert missing_id in {item["recipe_id"] for item in unfiltered["items"]}
    constrained = service.search_recipes({"name": missing_name, "limit": 100,
        "nutrients": [{"name": "protein_g", "basis": "per_serving", "minimum": 0, "allow_estimated": True}]})
    assert missing_id not in {item["recipe_id"] for item in constrained["items"]}
