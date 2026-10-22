"""Calculate directly from the reviewed document and immutable USDA snapshots."""

import hashlib
import json
from decimal import Decimal

from chefmate_food_data.nutrients import NUTRIENT_IDS as NUTRIENT_IDS, parse_nutrients as parse_nutrients

from .estimation import ESTIMATE_KEY, valid_estimate
from .schemas import Nutrients, TagThresholds
from .schemas import ConvertedIngredient, ConvertedRecipe
TAG_FIELDS = {
    "high_fat": "fat_g", "high_sodium": "sodium_mg", "high_sugar": "sugar_g",
    "high_calorie": "calories_kcal", "high_protein": "protein_g",
}


def food_key(item: ConvertedIngredient) -> str:
    return hashlib.sha256(
        json.dumps([item.ingredient_id, item.canonical_name, item.usda_query], ensure_ascii=False).encode()
    ).hexdigest()


def keep_snapshots(document: ConvertedRecipe, snapshots: dict) -> dict:
    valid = {food_key(item) for item in document.ingredients}
    kept = {key: value for key, value in snapshots.items() if key in valid}
    if valid_estimate(document, snapshots):
        kept[ESTIMATE_KEY] = snapshots[ESTIMATE_KEY]
    return kept


def calculate(document: ConvertedRecipe, snapshots: dict, thresholds: TagThresholds) -> dict:
    totals = {key: Decimal(0) for key in Nutrients.model_fields}
    coverage = {key: 0 for key in totals}
    details = []
    estimate = valid_estimate(document, snapshots)
    estimates = {item["ingredient_id"]: item for item in estimate.get("ingredients", [])}
    estimated_fields = set()
    for item in document.ingredients:
        snapshot = snapshots.get(food_key(item), {})
        values = Nutrients.model_validate(snapshot.get("per_100g", {})).model_dump()
        guess = estimates.get(item.ingredient_id, {})
        grams = item.grams if item.grams is not None else guess.get("grams")
        estimated_grams = item.grams is None and grams is not None
        field_estimates = []
        contributions = {}
        for key, value in values.items():
            estimated_value = value is None and guess.get("per_100g", {}).get(key) is not None
            if estimated_value:
                value = guess["per_100g"][key]
            if grams is not None and value is not None and (estimated_grams or estimated_value):
                field_estimates.append(key)
                estimated_fields.add(key)
            amount = (
                Decimal(str(value)) * Decimal(str(grams)) / 100
                if value is not None and grams is not None
                else None
            )
            contributions[key] = float(amount) if amount is not None else None
            if amount is not None:
                totals[key] += amount
                coverage[key] += 1
        details.append(
            {
                "name": item.canonical_name,
                "grams": grams,
                "original_grams": item.grams,
                "estimated": bool(field_estimates or estimated_grams),
                "estimated_grams": estimated_grams,
                "estimated_nutrients": field_estimates,
                "estimate": guess or None,
                "nutrition": contributions,
                "source": snapshot,
                "source_issue": snapshot.get("error"),
                "issue": ("缺少克重" if grams is None else None)
                or ("仍有缺失营养指标" if any(v is None for v in contributions.values()) else None),
            }
        )
    count = len(document.ingredients)
    total = {key: float(value) if coverage[key] == count else None for key, value in totals.items()}
    servings = document.servings if document.servings is not None else estimate.get("servings")
    estimated_servings = document.servings is None and servings is not None
    per_serving = (
        {
            key: float(Decimal(str(value)) / Decimal(str(servings))) if value is not None else None
            for key, value in total.items()
        }
        if servings
        else None
    )
    complete = all(value == count for value in coverage.values())
    basis = per_serving if thresholds.basis == "per_serving" else total
    tags = [
        tag
        for tag, field in TAG_FIELDS.items()
        if complete and basis and basis[field] is not None and basis[field] >= getattr(thresholds, tag)
    ]
    has_estimates = bool(estimated_fields or estimated_servings or any(item["estimated"] for item in details))
    return {
        "status": ("estimated" if has_estimates else "complete") if complete else "incomplete",
        "estimated": has_estimates,
        "estimated_total_fields": sorted(estimated_fields),
        "estimated_per_serving_fields": sorted(estimated_fields | (set(totals) if estimated_servings else set())),
        "servings": servings,
        "estimated_servings": estimated_servings,
        "estimation": estimate or None,
        "tags_estimated": bool(tags and has_estimates),
        "total": total,
        "per_serving": per_serving,
        "tags": tags,
        "coverage": coverage,
        "ingredients": details,
        "thresholds": thresholds.model_dump(),
        "note": "按可食克重计算；带 estimated 标记的值含 AI 估算，仅供参考。未能估算的未知值不按0计，不模拟烹饪损耗。营养预览随整篇菜谱一并审核。",
    }
