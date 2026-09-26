"""Pure arithmetic on reviewed USDA per-100g snapshots; missing never means zero."""

import json
from decimal import Decimal

from data_cleaning.schemas.cleaning import Nutrients, RecipeProfile

TAG_FIELDS = {
    "high_fat": "fat_g",
    "high_sodium": "sodium_mg",
    "high_sugar": "sugar_g",
    "high_calorie": "calories_kcal",
    "high_protein": "protein_g",
}


def build_result(bundle: dict) -> dict:
    recipe = bundle["recipe"]
    result = json.loads(recipe["extracted_json"])
    profile_row = bundle["profile"]
    profile = RecipeProfile.model_validate_json(profile_row["data_json"]) if profile_row else None
    fresh = bool(profile_row and profile_row["source_updated_at"] == recipe["updated_at"])
    blockers = []
    if recipe["status"] != "approved":
        blockers.append("原始提取未通过审核")
    if not profile_row or profile_row["status"] != "approved" or not fresh:
        blockers.append("标准化未审核或已过期")
    amounts = {i.ordinal: i for i in profile.items} if profile and fresh else {}
    totals = {key: Decimal(0) for key in Nutrients.model_fields}
    coverage = {key: 0 for key in Nutrients.model_fields}
    details = []
    allergens = set()
    for entry in bundle["items"]:
        raw, standard = entry["raw"], entry["standard"]
        item = json.loads(raw["data_json"])
        item["canonical_ingredient_id"] = standard["ingredient_id"] if standard else None
        item["canonical_name"] = standard["canonical_name"] if standard else None
        normalized = amounts.get(raw["ordinal"])
        item.update(
            normalized.model_dump(exclude={"ordinal"})
            if normalized
            else {"normalized_amount": None, "normalized_unit": None, "grams": None, "scalable": None, "evidence": ""}
        )
        reviewed = bool(raw["status"] == "approved" and standard and standard["status"] == "approved")
        if not reviewed:
            blockers.append(f"食材未审核：{raw['raw_name']}")
        if standard:
            allergens.update(json.loads(standard["allergens_json"]))
        grams = normalized.grams if normalized else None
        nutrient_ready = bool(
            reviewed and standard["nutrition_match_status"] == "approved" and standard["nutrition_json"]
        )
        per100 = (
            Nutrients.model_validate(json.loads(standard["nutrition_json"])["per_100g"]).model_dump()
            if nutrient_ready
            else Nutrients().model_dump()
        )
        calculated = {}
        for key, value in per100.items():
            if value is None or grams is None:
                calculated[key] = None
            else:
                contribution = Decimal(str(value)) * Decimal(str(grams)) / Decimal(100)
                totals[key] += contribution
                coverage[key] += 1
                calculated[key] = float(contribution)
        item["nutrition"] = calculated
        item["nutrition_source"] = (
            {
                "source": "USDA_FDC",
                "fdc_id": standard["nutrition_source_id"],
                "name": standard["nutrition_source_name"],
                "per_100g": per100,
                "reviewed_at": standard["nutrition_reviewed_at"],
            }
            if nutrient_ready
            else None
        )
        item["per_serving_amount"] = (
            (normalized.normalized_amount / profile.servings)
            if (
                normalized
                and normalized.scalable is True
                and normalized.normalized_amount is not None
                and profile.servings
            )
            else None
        )
        # Countable units never produce an automatic fraction such as 0.25 eggs.
        if item["per_serving_amount"] is not None and normalized.normalized_unit not in ("g", "kg", "ml", "l", "L"):
            if not float(item["per_serving_amount"]).is_integer():
                item["per_serving_amount"] = None
        details.append(item)
    count = len(details)
    complete = bool(count and all(v == count for v in coverage.values()))
    if not complete:
        blockers.append("存在未知克重、未确认 USDA 映射或缺失营养值")
    total = {k: float(v) if coverage[k] == count and count else None for k, v in totals.items()}
    servings = profile.servings if profile and fresh else None
    per_serving = (
        {k: float(Decimal(str(v)) / Decimal(str(servings))) if v is not None else None for k, v in total.items()}
        if servings
        else None
    )
    thresholds = bundle["thresholds"]
    basis_values = per_serving if thresholds.basis == "per_serving" else total
    tags = [
        tag
        for tag, field in TAG_FIELDS.items()
        if complete
        and basis_values
        and basis_values[field] is not None
        and basis_values[field] >= getattr(thresholds, tag)
    ]
    result.update(
        {
            "ingredients": details,
            "allergens": sorted(allergens),
            "servings": servings,
            "standardized_tags": profile.model_dump(
                include={"taste_tags", "cooking_methods", "required_tools", "difficulty"}
            )
            if profile and fresh
            else None,
            "nutrition": {
                "status": "complete" if complete else "incomplete",
                "total": total,
                "per_serving": per_serving,
                "coverage": coverage,
                "ingredient_count": count,
                "tags": tags,
                "thresholds": thresholds.model_dump(),
                "per_serving_note": None if servings else "人数未知，无法计算每人份营养",
                "basis_note": "按投入食材可食克重与每100g来源值估算，未模拟烹饪损耗、汤汁弃去或吸油量",
            },
            "ready_for_recommendation": not blockers,
            "blocking_reasons": blockers,
            "raw_source_text": recipe["raw_text"],
            "provenance": {
                k: recipe[k]
                for k in (
                    "revision",
                    "content_hash",
                    "status",
                    "review_note",
                    "reviewed_at",
                    "created_at",
                    "updated_at",
                )
            },
            "normalization_review": {
                "status": profile_row["status"],
                "reviewed_at": profile_row["reviewed_at"],
                "review_note": profile_row["review_note"],
            }
            if profile_row
            else None,
        }
    )
    return result
