"""Calculate directly from the reviewed document and immutable USDA snapshots."""

import hashlib
import json
from decimal import Decimal

from data_cleaning.schemas.cleaning import Nutrients, TagThresholds
from data_cleaning.schemas.conversion import ConvertedIngredient, ConvertedRecipe
from data_cleaning.services.nutrition import TAG_FIELDS


def food_key(item: ConvertedIngredient) -> str:
    return hashlib.sha256(
        json.dumps([item.ingredient_id, item.canonical_name, item.usda_query], ensure_ascii=False).encode()
    ).hexdigest()


def keep_snapshots(document: ConvertedRecipe, snapshots: dict) -> dict:
    valid = {food_key(item) for item in document.ingredients}
    return {key: value for key, value in snapshots.items() if key in valid}


def calculate(document: ConvertedRecipe, snapshots: dict, thresholds: TagThresholds) -> dict:
    totals = {key: Decimal(0) for key in Nutrients.model_fields}
    coverage = {key: 0 for key in totals}
    details = []
    for item in document.ingredients:
        snapshot = snapshots.get(food_key(item), {})
        values = Nutrients.model_validate(snapshot.get("per_100g", {})).model_dump()
        contributions = {}
        for key, value in values.items():
            amount = (
                Decimal(str(value)) * Decimal(str(item.grams)) / 100
                if value is not None and item.grams is not None
                else None
            )
            contributions[key] = float(amount) if amount is not None else None
            if amount is not None:
                totals[key] += amount
                coverage[key] += 1
        details.append(
            {
                "name": item.canonical_name,
                "grams": item.grams,
                "nutrition": contributions,
                "source": snapshot,
                "issue": snapshot.get("error")
                or ("缺少克重" if item.grams is None else None)
                or ("尚未匹配 USDA" if not snapshot.get("per_100g") else None),
            }
        )
    count = len(document.ingredients)
    total = {key: float(value) if coverage[key] == count else None for key, value in totals.items()}
    per_serving = (
        {
            key: float(Decimal(str(value)) / Decimal(str(document.servings))) if value is not None else None
            for key, value in total.items()
        }
        if document.servings
        else None
    )
    complete = all(value == count for value in coverage.values())
    basis = per_serving if thresholds.basis == "per_serving" else total
    tags = [
        tag
        for tag, field in TAG_FIELDS.items()
        if complete and basis and basis[field] is not None and basis[field] >= getattr(thresholds, tag)
    ]
    return {
        "status": "complete" if complete else "incomplete",
        "total": total,
        "per_serving": per_serving,
        "tags": tags,
        "coverage": coverage,
        "ingredients": details,
        "thresholds": thresholds.model_dump(),
        "note": "按食材可食克重计算；未知不按0计，不模拟烹饪损耗。营养预览随整篇菜谱一并审核。",
    }
