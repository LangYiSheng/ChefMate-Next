"""Package a compact schema-2 catalog without changing any Storage projection.

Only fields consumed by the current online importer and small source evidence
are retained. Original archives stay separate; hashes identify that source.
"""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import ijson
from chefmate_storage.importer import load_export

RECIPE_FIELDS = {
    "recipe_id", "name", "description", "servings", "original_servings", "servings_evidence",
    "prep_time_min", "cook_time_min", "total_time_min", "difficulty", "source",
    "ready_for_recommendation", "steps", "taste_tags", "cooking_methods", "required_tools",
    "warnings", "blocking_reasons", "references", "ingredients", "nutrition", "provenance",
}
INGREDIENT_FIELDS = {
    "ingredient_id", "canonical_name", "category", "raw_name", "raw_amount", "raw_unit",
    "raw_text", "grams", "scalable", "aliases", "allergens", "amount_evidence",
}
NUTRITION_FIELDS = {
    "status", "estimated", "servings", "estimated_servings", "tags_estimated", "tags",
    "total", "per_serving", "estimated_total_fields", "estimated_per_serving_fields", "ingredients",
}
DETAIL_FIELDS = {
    "name", "grams", "original_grams", "estimated", "estimated_grams", "estimated_nutrients", "nutrition", "source",
}
SOURCE_FIELDS = {"source", "fdc_id", "name", "per_100g", "fetched_at"}
PROVENANCE_FIELDS = {"source_hash", "status", "reviewed_at", "review_note", "updated_at"}


def select(value, fields):
    return {key: item for key, item in value.items() if key in fields}


def compact_recipe(recipe):
    result = select(recipe, RECIPE_FIELDS)
    result["ingredients"] = [select(value, INGREDIENT_FIELDS) for value in recipe["ingredients"]]
    result["references"] = [select(value, {"title", "url"}) for value in recipe.get("references", [])]
    result["provenance"] = select(recipe.get("provenance", {}), PROVENANCE_FIELDS)
    nutrition = select(recipe["nutrition"], NUTRITION_FIELDS)
    details = []
    for value in recipe["nutrition"]["ingredients"]:
        detail = select(value, DETAIL_FIELDS)
        detail["source"] = select(value.get("source", {}), SOURCE_FIELDS)
        details.append(detail)
    nutrition["ingredients"] = details
    result["nutrition"] = nutrition
    return result


def exact_json(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite numeric data cannot be packaged")
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(map(exact_json, value)) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + exact_json(value[key]) for key in sorted(value)) + "}"
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(source, target, version, *, allow_unapproved=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be independent directories")
    if target.exists() and any(target.iterdir()):
        raise ValueError("Target must be a new or empty directory")
    original = load_export(source, data_version=version, allow_unapproved=allow_unapproved)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    target.mkdir(parents=True, exist_ok=True)
    counts = {"source_prefixes": {}, "ready_for_recommendation": 0, "approval_statuses": {}}
    with (source / "recipes.json").open("rb") as stream, (target / "recipes.json").open("w", encoding="utf-8", newline="\n") as output:
        output.write("[\n")
        for index, recipe in enumerate(ijson.items(stream, "item")):
            if index:
                output.write(",\n")
            output.write(exact_json(compact_recipe(recipe)))
            prefix = (recipe.get("source") or "unknown").split("/")[0]
            counts["source_prefixes"][prefix] = counts["source_prefixes"].get(prefix, 0) + 1
            counts["ready_for_recommendation"] += int(recipe.get("ready_for_recommendation", False))
            status = recipe.get("provenance", {}).get("status", "unknown")
            counts["approval_statuses"][status] = counts["approval_statuses"].get(status, 0) + 1
        output.write("\n]\n")
    bundled_manifest = {"schema_version": 2, "phase": "unified", "data_version": version,
        "approved_only": manifest["approved_only"], "recipe_count": original.recipe_count,
        "created_at": manifest["created_at"], "sha256": {"recipes.json": sha256(target / "recipes.json")},
        "notice": "Compact online projection. Approval, readiness, estimates and unknown nutrition remain unchanged.",
        "source_export": {"directory": source.name, "recipes_sha256": manifest["sha256"]["recipes.json"],
            "manifest_sha256": sha256(source / "manifest.json")}}
    (target / "manifest.json").write_text(json.dumps(bundled_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    compact = load_export(target, data_version=version, allow_unapproved=allow_unapproved)
    if original.tables != compact.tables:
        raise ValueError("Compact catalog changed online table projections; refusing to publish")
    result = {"data_version": version, "recipe_count": compact.recipe_count,
        "source_bytes": (source / "recipes.json").stat().st_size, "bundled_bytes": (target / "recipes.json").stat().st_size,
        "tables": {name: len(rows) for name, rows in compact.tables.items()}, **counts,
        "all_online_tables_identical": True, "sha256": bundled_manifest["sha256"]}
    (target / "projection-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--allow-unapproved", action="store_true")
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.target, args.data_version, allow_unapproved=args.allow_unapproved), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
