from chefmate_contracts import Nutrients
from .fooddata import DATA_TYPES


# USDA nutrient IDs, units and precedence. Absent values remain null.
NUTRIENT_IDS = {
    "calories_kcal": ([1008, 2048, 2047], "KCAL"),
    "protein_g": ([1003], "G"),
    "fat_g": ([1004], "G"),
    "carbohydrate_g": ([1005], "G"),
    "sugar_g": ([2000, 1063], "G"),
    "sodium_mg": ([1093], "MG"),
}


def parse_nutrients(food: dict) -> Nutrients:
    if food.get("dataType") not in (*DATA_TYPES, "Survey (FNDDS)"):
        raise ValueError("仅接受 Foundation、SR Legacy、Survey 的每100g数据，品牌标签数据需另行核实基准")
    if not food.get("fdcId") or not food.get("description"):
        raise ValueError("USDA 响应缺少食物来源标识")
    values = {}
    for field, (ids, unit) in NUTRIENT_IDS.items():
        values[field] = None
        for nutrient_id in ids:
            entries = [n for n in food.get("foodNutrients", []) if n.get("nutrient", {}).get("id") == nutrient_id]
            found = False
            for entry in entries:
                if str(entry["nutrient"].get("unitName", "")).upper() == unit and entry.get("amount") is not None:
                    values[field] = entry["amount"]
                    found = True
                    break
            if found:
                break
    return Nutrients.model_validate(values)
