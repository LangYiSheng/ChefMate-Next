import json

import pytest

from chefmate_core.estimation import ESTIMATE_KEY, estimate_missing
from chefmate_core.nutrition import calculate, food_key, keep_snapshots
from chefmate_core.schemas import ConversionSettings, TagThresholds
from data_cleaning.services.conversion import enrich_nutrition
from test_conversion import document, repo, USDA  # noqa: F401


class EstimateAI:
    settings = {"model": "test-only"}

    def request(self, purpose, payload, schema, entity, validator=None, **kwargs):
        assert purpose == "nutrition_estimate"
        result = schema.model_validate({
            "ingredients": [{"ingredient_id": item["ingredient_id"], "grams": 100,
                             "per_100g": {k: 10 for k in item["nutrients_missing"]},
                             "reason": "测试假设：常见份量100g，非实测"} for item in payload["missing"]],
            "servings": 3, "servings_reason": "测试份量估计",
        })
        if validator:
            validator(result)
        return result


def test_estimates_fill_gaps_preserve_facts_and_invalidate():
    doc = document()
    doc.ingredients[0].grams = None
    snapshots = {food_key(doc.ingredients[0]): {"per_100g": {"protein_g": 7}, "source": "USDA_FDC"}}
    snapshots[ESTIMATE_KEY] = estimate_missing(doc, snapshots, EstimateAI())
    result = calculate(doc, snapshots, TagThresholds())
    assert result["status"] == "estimated"
    assert result["total"]["protein_g"] == 7
    assert result["per_serving"]["protein_g"] == 3.5
    assert result["servings"] == 2 and not result["estimated_servings"]
    assert result["ingredients"][0]["estimated_grams"]
    assert doc.ingredients[0].grams is None
    assert ESTIMATE_KEY in keep_snapshots(doc, snapshots)
    doc.ingredients[0].raw_text = "换成另一个份量"
    assert ESTIMATE_KEY not in keep_snapshots(doc, snapshots)
    assert calculate(doc, snapshots, TagThresholds())["total"]["protein_g"] is None


def test_known_weight_and_missing_servings():
    doc = document()
    doc.servings = None
    snapshots = {ESTIMATE_KEY: estimate_missing(doc, {}, EstimateAI())}
    result = calculate(doc, snapshots, TagThresholds())
    assert result["total"]["protein_g"] == 20  # actual 200g takes precedence over guessed 100g
    assert result["estimated_servings"] and result["servings"] == 3
    assert not result["ingredients"][0]["estimated_grams"]


def test_estimate_persistence_export_and_disable(repo):  # noqa: F811
    doc = document()
    doc.ingredients[0].grams = None
    row = repo.get_recipe("r")
    repo.save_conversion("r", doc, source_hash=row["content_hash"], expected=None, prompt="test")
    # Known USDA nutrient values avoid any matching call; only estimation is needed.
    saved = repo.conversion("r")
    snapshots = {food_key(doc.ingredients[0]): {"source": "USDA_FDC", "fdc_id": "123",
                 "per_100g": {key: 5 for key in ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")}}}
    repo.save_conversion("r", doc, source_hash=row["content_hash"], expected=saved["updated_at"], nutrition=snapshots, prompt="test")
    enrich_nutrition(repo, "r", settings=ConversionSettings(estimate_missing=True), ai=EstimateAI(), usda=USDA())
    saved = repo.conversion("r")
    assert ESTIMATE_KEY in json.loads(saved["nutrition_json"])
    assert saved["status"] == "pending_review"
    result = calculate(doc, json.loads(saved["nutrition_json"]), TagThresholds())
    assert result["estimated"]
    exported = repo.conversion_export(approved_only=False)["recipes"][0]
    assert exported["nutrition"]["estimated"]
    assert exported["nutrition"]["estimation"]["source"] == "AI_ESTIMATE"
    assert not exported["ready_for_recommendation"]
    assert all("__ai_estimate__" != mapping.get("name") for mapping in repo.confirmed_usda_mappings())
    enrich_nutrition(repo, "r", settings=ConversionSettings(), ai=EstimateAI(), usda=USDA())
    assert ESTIMATE_KEY not in json.loads(repo.conversion("r")["nutrition_json"])


def test_estimation_failure_keeps_existing_snapshots(repo):  # noqa: F811
    doc = document()
    doc.ingredients[0].grams = None
    row = repo.get_recipe("r")
    repo.save_conversion("r", doc, source_hash=row["content_hash"], expected=None, prompt="test")

    class FailingAI(EstimateAI):
        def request(self, purpose, payload, schema, entity, validator=None, **kwargs):
            if purpose == "nutrition_estimate":
                raise ValueError("synthetic failure")
            return schema(fdc_id="123", confidence=1, reason="test")

    with pytest.raises(ValueError, match="AI 估算失败"):
        enrich_nutrition(repo, "r", settings=ConversionSettings(estimate_missing=True), ai=FailingAI(), usda=USDA())
    assert json.loads(repo.conversion("r")["nutrition_json"])[food_key(doc.ingredients[0])]["per_100g"]
