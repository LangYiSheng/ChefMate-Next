"""Validate unified cleaning exports and project them into current relational rows.

The large source export is streamed one recipe at a time. No source document,
USDA snapshot, manifest blob, or recipe history is retained in the snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

import ijson


NUTRIENTS = (
    "calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg",
)
TABLE_NAMES = (
    "recipes", "recipe_steps", "recipe_labels", "recipe_notes", "recipe_references",
    "ingredients", "recipe_ingredients", "ingredient_aliases", "ingredient_allergens",
    "recipe_nutrition", "recipe_ingredient_nutrition",
)


class ExportValidationError(ValueError):
    """An export cannot be safely interpreted as a complete current dataset."""


@dataclass(frozen=True)
class ExportSnapshot:
    data_version: str
    export_schema_version: int
    exported_at: datetime
    tables: dict[str, list[dict[str, Any]]]
    recipe_count: int


def _object(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise ExportValidationError(f"{field}: expected an object")
    return value


def _array(value: Any, field: str) -> list:
    if not isinstance(value, list):
        raise ExportValidationError(f"{field}: expected an array")
    return value


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ExportValidationError(f"{field}: expected text")
    if not value.strip():
        if required:
            raise ExportValidationError(f"{field}: cannot be empty")
        return None
    if "\x00" in value:
        raise ExportValidationError(f"{field}: text cannot contain a NUL character")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ExportValidationError(f"{field}: expected a boolean")
    return value


def _number(value: Any, field: str, *, positive: bool = False) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ExportValidationError(f"{field}: expected a number or null")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or (positive and result == 0):
        condition = "positive" if positive else "nonnegative"
        raise ExportValidationError(f"{field}: expected a finite {condition} number")
    # PostgreSQL NUMERIC is finite too; avoid values which cannot be compared or
    # represented by public numeric callers, even if Decimal accepts them.
    if not math.isfinite(float(result)):
        raise ExportValidationError(f"{field}: number exceeds the supported finite range")
    return result


def _same_number(first: Decimal | None, second: Decimal | None) -> bool:
    if first is None or second is None:
        return first is second
    return abs(first - second) <= max(Decimal("1e-9"), abs(first) * Decimal("1e-9"))


def _strings(value: Any, field: str) -> list[str]:
    result = []
    for index, item in enumerate(_array(value, field)):
        result.append(_text(item, f"{field}[{index}]", required=True))
    return result


def _estimated_fields(value: Any, field: str) -> set[str]:
    fields = set(_strings(value, field))
    if fields - set(NUTRIENTS):
        raise ExportValidationError(f"{field}: contains unknown nutrient names")
    return fields


def _nutrients(value: Any, field: str) -> dict[str, Decimal | None]:
    values = {} if value is None else _object(value, field)
    return {name: _number(values.get(name), f"{field}.{name}") for name in NUTRIENTS}


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ExportValidationError(f"manifest: duplicate key {name!r}")
        result[name] = value
    return result


def _safe_file(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
        raise ExportValidationError(f"manifest.sha256: unsafe filename {name!r}")
    if name.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ExportValidationError(f"manifest.sha256: unsafe filename {name!r}")
    file = directory / name
    if file.is_symlink() or file.resolve().parent != directory or not file.is_file():
        raise ExportValidationError(f"export file {name!r}: must be a regular file inside the export directory")
    return file


def _hash_file(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _DigestReader:
    """Hash the actual bytes consumed by the streaming parser as well."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        value = self.stream.read(size)
        self.digest.update(value)
        return value

    def readinto(self, buffer: bytearray) -> int:
        value = self.read(len(buffer))
        buffer[:len(value)] = value
        return len(value)


def _project_recipe(recipe: dict, tables: dict[str, list[dict]], ingredient_ids: set[str],
                    *, allow_unapproved: bool) -> None:
    recipe = _object(recipe, "recipe")
    recipe_id = _text(recipe.get("recipe_id"), "recipe.recipe_id", required=True)
    prefix = f"recipe {recipe_id!r}"
    provenance = _object(recipe.get("provenance", {}), f"{prefix}.provenance")
    approval_status = _text(provenance.get("status"), f"{prefix}.provenance.status")
    if approval_status != "approved" and not allow_unapproved:
        raise ExportValidationError(f"{prefix}: unapproved recipe; explicitly set allow_unapproved=True to import it")
    nutrition = _object(recipe.get("nutrition"), f"{prefix}.nutrition")
    status = _text(nutrition.get("status"), f"{prefix}.nutrition.status", required=True)
    if status not in {"complete", "estimated", "incomplete"}:
        raise ExportValidationError(f"{prefix}.nutrition.status: unsupported status {status!r}")
    estimated = _boolean(nutrition.get("estimated", False), f"{prefix}.nutrition.estimated")
    estimated_servings = _boolean(nutrition.get("estimated_servings", False), f"{prefix}.estimated_servings")
    original_servings = _number(recipe.get("servings"), f"{prefix}.servings", positive=True)
    calculated_servings = _number(nutrition.get("servings"), f"{prefix}.nutrition.servings", positive=True)
    if original_servings is not None:
        if estimated_servings or (calculated_servings is not None and not _same_number(original_servings, calculated_servings)):
            raise ExportValidationError(f"{prefix}: nutrition servings disagree with factual servings")
        servings = original_servings
    else:
        servings = calculated_servings
        if servings is not None and not estimated_servings:
            raise ExportValidationError(f"{prefix}: fallback servings must be marked as estimated")
    if estimated_servings and servings is None:
        raise ExportValidationError(f"{prefix}: estimated servings have no value")
    ready = _boolean(recipe.get("ready_for_recommendation", False), f"{prefix}.ready_for_recommendation")
    if ready and approval_status != "approved":
        raise ExportValidationError(f"{prefix}: an unapproved recipe cannot be ready for recommendation")
    tables["recipes"].append({
        "recipe_id": recipe_id,
        "name": _text(recipe.get("name"), f"{prefix}.name", required=True),
        "description": _text(recipe.get("description"), f"{prefix}.description"),
        "servings": servings,
        "original_servings": _text(recipe.get("original_servings"), f"{prefix}.original_servings"),
        **{name: _number(recipe.get(name), f"{prefix}.{name}") for name in ("prep_time_min", "cook_time_min", "total_time_min")},
        "difficulty": _text(recipe.get("difficulty"), f"{prefix}.difficulty"),
        "source": _text(recipe.get("source"), f"{prefix}.source"),
        "ready_for_recommendation": ready,
        "estimated_servings": estimated_servings,
    })
    steps = _strings(recipe.get("steps"), f"{prefix}.steps")
    if not steps:
        raise ExportValidationError(f"{prefix}.steps: at least one step is required")
    for ordinal, description in enumerate(steps):
        tables["recipe_steps"].append({"recipe_id": recipe_id, "ordinal": ordinal, "description": description})
    tags_estimated = _boolean(nutrition.get("tags_estimated", False), f"{prefix}.nutrition.tags_estimated")
    for field, kind, owner in (
        ("taste_tags", "taste", recipe), ("cooking_methods", "cooking_method", recipe),
        ("required_tools", "tool", recipe), ("tags", "nutrition", nutrition),
    ):
        for value in dict.fromkeys(_strings(owner.get(field, []), f"{prefix}.{field}")):
            tables["recipe_labels"].append({"recipe_id": recipe_id, "kind": kind, "value": value,
                                           "estimated": tags_estimated if kind == "nutrition" else False})
    for field, kind in (("warnings", "warning"), ("blocking_reasons", "blocking_reason")):
        for ordinal, description in enumerate(_strings(recipe.get(field, []), f"{prefix}.{field}")):
            tables["recipe_notes"].append({"recipe_id": recipe_id, "kind": kind, "ordinal": ordinal, "description": description})
    for ordinal, value in enumerate(_array(recipe.get("references", []), f"{prefix}.references")):
        reference = _object(value, f"{prefix}.references[{ordinal}]")
        url = _text(reference.get("url"), f"{prefix}.references[{ordinal}].url", required=True)
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or any(char.isspace() for char in url):
            raise ExportValidationError(f"{prefix}.references[{ordinal}].url: expected an HTTP(S) URL without credentials")
        tables["recipe_references"].append({"recipe_id": recipe_id, "ordinal": ordinal,
                                           "title": _text(reference.get("title"), f"{prefix}.references[{ordinal}].title", required=True), "url": url})
    ingredients = _array(recipe.get("ingredients"), f"{prefix}.ingredients")
    details = _array(nutrition.get("ingredients"), f"{prefix}.nutrition.ingredients")
    if not ingredients:
        raise ExportValidationError(f"{prefix}.ingredients: at least one ingredient is required")
    if len(ingredients) != len(details):
        raise ExportValidationError(f"{prefix}: nutrition ingredient count does not match recipe ingredient count")
    detail_estimates = False
    for ordinal, (ingredient, detail) in enumerate(zip(ingredients, details, strict=True)):
        detail_estimates |= _project_ingredient(recipe_id, ordinal, ingredient, detail, tables, ingredient_ids)
    total = _nutrients(nutrition.get("total"), f"{prefix}.nutrition.total")
    per_serving = _nutrients(nutrition.get("per_serving"), f"{prefix}.nutrition.per_serving")
    estimated_total = _estimated_fields(nutrition.get("estimated_total_fields", []), f"{prefix}.nutrition.estimated_total_fields")
    estimated_per_serving = _estimated_fields(nutrition.get("estimated_per_serving_fields", []), f"{prefix}.nutrition.estimated_per_serving_fields")
    detail_rows = tables["recipe_ingredient_nutrition"][-len(ingredients):]
    derived_total_flags = {name for row in detail_rows for name in NUTRIENTS if row[f"estimated_{name}"]}
    if estimated_total != derived_total_flags:
        raise ExportValidationError(f"{prefix}: total estimate flags disagree with ingredient contribution flags")
    derived_per_serving_flags = derived_total_flags | (set(NUTRIENTS) if estimated_servings else set())
    if estimated_per_serving != derived_per_serving_flags:
        raise ExportValidationError(f"{prefix}: per-serving estimate flags disagree with total and servings flags")
    for name in NUTRIENTS:
        contributions = [row[f"contribution_{name}"] for row in detail_rows]
        expected_total = sum(contributions, Decimal(0)) if all(value is not None for value in contributions) else None
        if not _same_number(expected_total, total[name]):
            raise ExportValidationError(f"{prefix}.nutrition.total.{name}: disagrees with ingredient contributions")
        expected = total[name] / servings if total[name] is not None and servings is not None else None
        if not _same_number(expected, per_serving[name]):
            raise ExportValidationError(f"{prefix}.nutrition.per_serving.{name}: disagrees with total and servings")
    has_estimates = bool(estimated_total or estimated_per_serving or estimated_servings or detail_estimates)
    if estimated != has_estimates:
        raise ExportValidationError(f"{prefix}.nutrition.estimated: disagrees with field estimate flags")
    expected_status = ("estimated" if estimated else "complete") if all(value is not None for value in total.values()) else "incomplete"
    if status != expected_status:
        raise ExportValidationError(f"{prefix}.nutrition.status: disagrees with totals and estimate flags")
    if ready and status != "complete":
        raise ExportValidationError(f"{prefix}: only complete factual nutrition can be ready for recommendation")
    tables["recipe_nutrition"].append({
        "recipe_id": recipe_id, "status": status, "estimated": estimated,
        **{f"total_{name}": total[name] for name in NUTRIENTS},
        **{f"per_serving_{name}": per_serving[name] for name in NUTRIENTS},
        **{f"estimated_total_{name}": name in estimated_total for name in NUTRIENTS},
        **{f"estimated_per_serving_{name}": name in estimated_per_serving for name in NUTRIENTS},
    })


def _project_ingredient(recipe_id: str, ordinal: int, ingredient: Any, detail: Any,
                        tables: dict[str, list[dict]], ingredient_ids: set[str]) -> bool:
    prefix = f"recipe {recipe_id!r}.ingredients[{ordinal}]"
    ingredient = _object(ingredient, prefix)
    detail = _object(detail, f"{prefix}.nutrition")
    canonical_name = _text(ingredient.get("canonical_name"), f"{prefix}.canonical_name", required=True)
    if _text(detail.get("name"), f"{prefix}.nutrition.name", required=True) != canonical_name:
        raise ExportValidationError(f"{prefix}: nutrition ingredient name does not match its ordinal")
    ingredient_id = _text(ingredient.get("ingredient_id"), f"{prefix}.ingredient_id", required=True)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", ingredient_id):
        raise ExportValidationError(f"{prefix}.ingredient_id: expected a stable lowercase identifier")
    factual_grams = _number(ingredient.get("grams"), f"{prefix}.grams")
    calculated_grams = _number(detail.get("grams"), f"{prefix}.nutrition.grams")
    if "original_grams" in detail and not _same_number(factual_grams, _number(detail["original_grams"], f"{prefix}.nutrition.original_grams")):
        raise ExportValidationError(f"{prefix}: nutrition original grams disagree with recipe grams")
    estimated_grams = _boolean(detail.get("estimated_grams", False), f"{prefix}.estimated_grams")
    if factual_grams is not None:
        if estimated_grams or not _same_number(factual_grams, calculated_grams):
            raise ExportValidationError(f"{prefix}: nutrition grams disagree with factual grams")
        grams = factual_grams
    else:
        grams = calculated_grams
        if grams is not None and not estimated_grams:
            raise ExportValidationError(f"{prefix}: fallback grams must be marked as estimated")
    if estimated_grams and grams is None:
        raise ExportValidationError(f"{prefix}: estimated grams have no value")
    if ingredient_id not in ingredient_ids:
        tables["ingredients"].append({"ingredient_id": ingredient_id})
        ingredient_ids.add(ingredient_id)
    tables["recipe_ingredients"].append({
        "recipe_id": recipe_id, "ordinal": ordinal, "ingredient_id": ingredient_id,
        "canonical_name": canonical_name, "category": _text(ingredient.get("category"), f"{prefix}.category", required=True),
        **{name: _text(ingredient.get(name), f"{prefix}.{name}", required=name in {"raw_name", "raw_text"})
           for name in ("raw_name", "raw_amount", "raw_unit", "raw_text")},
        "grams": grams, "scalable": _boolean(ingredient.get("scalable", False), f"{prefix}.scalable"),
        "estimated_grams": estimated_grams,
    })
    for source, table, column in (("aliases", "ingredient_aliases", "alias"), ("allergens", "ingredient_allergens", "allergen")):
        for value in dict.fromkeys(_strings(ingredient.get(source, []), f"{prefix}.{source}")):
            tables[table].append({"recipe_id": recipe_id, "ordinal": ordinal, column: value})
    source = _object(detail.get("source", {}), f"{prefix}.nutrition.source")
    per_100g = _nutrients(source.get("per_100g") if source.get("source") == "USDA_FDC" else None,
                          f"{prefix}.nutrition.source.per_100g")
    fdc_id = source.get("fdc_id") if source.get("source") == "USDA_FDC" else None
    if fdc_id is not None:
        if isinstance(fdc_id, bool) or not isinstance(fdc_id, (str, int)) or not re.fullmatch(r"[0-9]+", str(fdc_id)) or int(fdc_id) <= 0:
            raise ExportValidationError(f"{prefix}.nutrition.source.fdc_id: expected a positive USDA identifier")
        fdc_id = str(fdc_id)
    contribution = _nutrients(detail.get("nutrition"), f"{prefix}.nutrition.contribution")
    estimated_fields = _estimated_fields(detail.get("estimated_nutrients", []), f"{prefix}.estimated_nutrients")
    for name in NUTRIENTS:
        if grams is not None and per_100g[name] is not None:
            expected_contribution = per_100g[name] * grams / 100
            if not _same_number(expected_contribution, contribution[name]):
                raise ExportValidationError(f"{prefix}.nutrition.contribution.{name}: disagrees with USDA per_100g and grams")
        if contribution[name] is not None:
            if grams is None:
                raise ExportValidationError(f"{prefix}.nutrition.contribution.{name}: a contribution needs effective grams")
            if (estimated_grams or per_100g[name] is None) and name not in estimated_fields:
                raise ExportValidationError(f"{prefix}.nutrition.contribution.{name}: contribution needs an estimate flag")
    estimated = _boolean(detail.get("estimated", False), f"{prefix}.estimated")
    if estimated != bool(estimated_grams or estimated_fields):
        raise ExportValidationError(f"{prefix}.estimated: disagrees with field estimate flags")
    tables["recipe_ingredient_nutrition"].append({
        "recipe_id": recipe_id, "ordinal": ordinal, "estimated": estimated, "fdc_id": fdc_id,
        **{f"contribution_{name}": contribution[name] for name in NUTRIENTS},
        **{f"per_100g_{name}": per_100g[name] for name in NUTRIENTS},
        **{f"estimated_{name}": name in estimated_fields for name in NUTRIENTS},
    })
    return estimated


def load_export(directory: Path | str, *, data_version: str | None = None,
                allow_unapproved: bool = False, allow_empty: bool = False) -> ExportSnapshot:
    """Validate a schema-2 unified export before any database mutation.

    Only declared files are read. Every declared file is hashed, but only
    recipes.json is parsed into rows. Unapproved and empty datasets each need
    explicit opt-in because the database importer replaces the whole dataset.
    """
    _boolean(allow_unapproved, "allow_unapproved")
    _boolean(allow_empty, "allow_empty")
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ExportValidationError(f"export directory does not exist: {directory}")
    try:
        manifest_file = _safe_file(directory, "manifest.json")
        if manifest_file.stat().st_size > 1024 * 1024:
            raise ExportValidationError("manifest.json exceeds the 1 MiB metadata limit")
        manifest = _object(json.loads(manifest_file.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys), "manifest")
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 2 or manifest.get("phase") != "unified":
            raise ExportValidationError("only schema_version=2, phase='unified' exports are supported")
        approved = _boolean(manifest.get("approved_only"), "manifest.approved_only")
        if not approved and not allow_unapproved:
            raise ExportValidationError("unapproved export: explicitly set allow_unapproved=True to import it")
        count = manifest.get("recipe_count")
        if type(count) is not int or count < 0:
            raise ExportValidationError("manifest.recipe_count: expected a nonnegative integer")
        created_at = _text(manifest.get("created_at"), "manifest.created_at", required=True)
        try:
            exported_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ExportValidationError("manifest.created_at: expected an ISO timestamp") from error
        if exported_at.tzinfo is None or exported_at.utcoffset() is None:
            raise ExportValidationError("manifest.created_at: timestamp must include a timezone")
        hashes = _object(manifest.get("sha256"), "manifest.sha256")
        if "recipes.json" not in hashes:
            raise ExportValidationError("manifest.sha256: recipes.json must be declared")
        for name, expected in hashes.items():
            file = _safe_file(directory, name)
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                raise ExportValidationError(f"manifest.sha256[{name!r}]: invalid SHA256 digest")
            if _hash_file(file) != expected.lower():
                raise ExportValidationError(f"export file {name!r}: SHA256 mismatch")
        version = _text(data_version if data_version is not None else directory.name, "data_version", required=True)
        tables = {name: [] for name in TABLE_NAMES}
        recipe_ids: set[str] = set()
        ingredient_ids: set[str] = set()
        with _safe_file(directory, "recipes.json").open("rb") as stream:
            first = stream.read(1)
            while first and first in b" \t\r\n":
                first = stream.read(1)
            if first != b"[":
                raise ExportValidationError("recipes.json: expected a top-level array")
            stream.seek(0)
            reader = _DigestReader(stream)
            for recipe in ijson.items(reader, "item"):
                recipe = _object(recipe, "recipe")
                recipe_id = _text(recipe.get("recipe_id"), "recipe.recipe_id", required=True)
                if recipe_id in recipe_ids:
                    raise ExportValidationError(f"duplicate recipe_id {recipe_id!r}")
                recipe_ids.add(recipe_id)
                _project_recipe(recipe, tables, ingredient_ids, allow_unapproved=allow_unapproved)
            if reader.digest.hexdigest() != hashes["recipes.json"].lower():
                raise ExportValidationError("recipes.json changed while being parsed: SHA256 mismatch")
        if len(recipe_ids) != count:
            raise ExportValidationError(f"manifest.recipe_count={count} disagrees with actual count {len(recipe_ids)}")
        if not recipe_ids and not allow_empty:
            raise ExportValidationError("empty export: explicitly set allow_empty=True to replace with an empty dataset")
        return ExportSnapshot(version, 2, exported_at, tables, len(recipe_ids))
    except ExportValidationError:
        raise
    except (OSError, UnicodeError, ValueError, ijson.JSONError) as error:
        raise ExportValidationError(f"cannot read export: {error}") from error
