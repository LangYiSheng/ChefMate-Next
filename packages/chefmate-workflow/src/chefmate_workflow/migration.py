"""Read old purchasing state once as the single preparation workflow."""

import copy
import hashlib
import json


def _identity(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "shopping:" + hashlib.sha256(encoded.encode()).hexdigest()[:20]


def migrate_snapshot(value):
    if not isinstance(value, dict):
        return value
    result = copy.deepcopy(value)
    if result.get("stage") == "shopping":
        result["stage"] = "prep"
    # Historical compact purchasing rows lack per-recipe IDs. Do not fabricate prep confirmations.
    components = [component.model_dump(mode="json") if hasattr(component, "model_dump") else component
                  for component in result.get("components", [])]
    result["components"] = [component for component in components
                            if component.get("kind") != "shopping_list"]
    for component in result["components"]:
        if component.get("kind") == "task_progress" and component.get("data", {}).get("stage") == "shopping":
            component["data"]["stage"] = "prep"
    return result


def migrate_workspace(value):
    if not isinstance(value, dict):
        return value
    if value.get("schema_version") == 2 and "shopping_checked" not in value and value.get("stage") != "shopping":
        return value
    result = copy.deepcopy(value)
    if result.get("schema_version", 1) != 1:
        return result  # Unknown future formats must still fail strict validation.
    checked = set(result.pop("shopping_checked", []))
    valid_ids = {f'{recipe["recipe_id"]}:ingredient:{item.get("ordinal", index)}'
                 for recipe in result.get("meal_plan", [])
                 for index, item in enumerate(recipe.get("recipe", {}).get("ingredients", []))}
    ready = checked & valid_ids
    for component in result.get("components", []):
        if component.get("kind") != "shopping_list":
            continue
        for item in component.get("data", {}).get("items", []):
            sources = item.get("source_item_ids", [])
            key = (item.get("ingredient_id"), item.get("canonical_name"), item.get("unit"),
                   sources[0] if item.get("amount_value") is None and len(sources) == 1 else "")
            if item.get("id") in checked or _identity(key) in checked or item.get("checked"):
                ready.update(set(sources) & valid_ids)
    statuses = result.setdefault("prep_status", {})
    for identity in ready:
        if statuses.get(identity, "pending") == "pending":
            statuses[identity] = "ready"
    if result.get("stage") == "shopping":
        result["stage"] = "prep"
    old = next((item for item in result.get("components", []) if item.get("kind") == "shopping_list"), None)
    result["components"] = [item for item in result.get("components", []) if item.get("kind") != "shopping_list"]
    if old and not any(item.get("kind") == "prep_checklist" for item in result["components"]):
        result["components"].append({**old, "kind": "prep_checklist", "title": "逐项确认备料",
                                     "data": {"items": [], "recipe_ids": old.get("data", {}).get("recipe_ids"),
                                              "notes": old.get("data", {}).get("notes", [])}})
    for component in result["components"]:
        if component.get("kind") == "task_progress" and component.get("data", {}).get("stage") == "shopping":
            component["data"]["stage"] = "prep"
    result["schema_version"] = 2
    return result
