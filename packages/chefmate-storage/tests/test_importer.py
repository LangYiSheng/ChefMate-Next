import copy
import hashlib
import json
from decimal import Decimal

import pytest

from chefmate_storage.importer import ExportValidationError, NUTRIENTS, load_export


def recipe(recipe_id="r1"):
    """Small realistic unified export, including blobs that must be discarded."""
    values = {name: 10 for name in NUTRIENTS}
    return {
        "recipe_id": recipe_id, "name": "豆腐汤", "description": "家常汤",
        "servings": 2, "original_servings": "两人份", "prep_time_min": 5,
        "cook_time_min": 10, "total_time_min": 15, "difficulty": "简单",
        "source": "家常菜.md", "steps": ["切豆腐", "煮汤"],
        "taste_tags": ["清淡"], "cooking_methods": ["煮"], "required_tools": ["锅"],
        "warnings": ["注意烫伤"], "blocking_reasons": [],
        "references": [{"title": "参考", "url": "https://example.com/recipe"}],
        "ingredients": [{
            "ingredient_id": "tofu", "canonical_name": "豆腐", "category": "豆制品",
            "raw_name": "嫩豆腐", "raw_amount": "半块", "raw_unit": "块",
            "raw_text": "嫩豆腐半块", "grams": 100, "scalable": True,
            "aliases": ["豆腐", "豆腐"], "allergens": ["大豆", "大豆"],
            "usda_query": "private cleaning query", "amount_evidence": "原文",
        }],
        "ready_for_recommendation": True,
        "raw_source_text": "discard this raw document",
        "provenance": {"status": "approved", "prompt": "private prompt", "source_hash": "private hash"},
        "nutrition": {
            "status": "complete", "estimated": False, "estimated_servings": False,
            "servings": 2, "total": values.copy(),
            "per_serving": {name: 5 for name in NUTRIENTS},
            "estimated_total_fields": [], "estimated_per_serving_fields": [],
            "tags": ["high_protein"], "tags_estimated": False,
            "ingredients": [{
                "name": "豆腐", "grams": 100, "original_grams": 100,
                "estimated": False, "estimated_grams": False, "estimated_nutrients": [],
                "nutrition": values.copy(),
                "source": {"source": "USDA_FDC", "fdc_id": "12345", "per_100g": values.copy(),
                           "raw": {"nested": "discard this USDA snapshot"}},
                "source_issue": None, "estimate": None,
            }],
        },
    }


def write_export(tmp_path, recipes=None, **metadata):
    recipes = [recipe()] if recipes is None else recipes
    tmp_path.mkdir(exist_ok=True)
    content = json.dumps(recipes, ensure_ascii=False).encode()
    (tmp_path / "recipes.json").write_bytes(content)
    mappings = b"[]"
    (tmp_path / "usda_mappings.json").write_bytes(mappings)
    manifest = {
        "schema_version": 2, "phase": "unified", "approved_only": True,
        "recipe_count": len(recipes), "created_at": "2026-10-05T05:30:28+00:00",
        "sha256": {"recipes.json": hashlib.sha256(content).hexdigest(),
                   "usda_mappings.json": hashlib.sha256(mappings).hexdigest()},
        **metadata,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def rewrite_manifest(tmp_path, manifest):
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_selected_raw_text_preserves_authored_whitespace(tmp_path):
    item = recipe()
    item["ingredients"][0]["raw_text"] = "  嫩豆腐半块\n"
    item["ingredients"][0]["raw_name"] = " 嫩豆腐 "
    write_export(tmp_path, [item])
    stored = load_export(tmp_path).tables["recipe_ingredients"][0]
    assert stored["raw_text"] == "  嫩豆腐半块\n"
    assert stored["raw_name"] == " 嫩豆腐 "


def test_project_selected_relational_fields_and_discard_all_blobs(tmp_path):
    write_export(tmp_path)
    snapshot = load_export(tmp_path, data_version="recipes-2026-10")
    assert snapshot.data_version == "recipes-2026-10"
    assert snapshot.export_schema_version == 2
    assert snapshot.exported_at.utcoffset().total_seconds() == 0
    assert snapshot.recipe_count == 1
    assert snapshot.tables["recipes"][0]["servings"] == Decimal(2)
    assert snapshot.tables["ingredients"] == [{"ingredient_id": "tofu"}]
    assert [row["ordinal"] for row in snapshot.tables["recipe_steps"]] == [0, 1]
    assert snapshot.tables["ingredient_aliases"] == [{"recipe_id": "r1", "ordinal": 0, "alias": "豆腐"}]
    assert snapshot.tables["ingredient_allergens"] == [{"recipe_id": "r1", "ordinal": 0, "allergen": "大豆"}]
    assert snapshot.tables["recipe_nutrition"][0]["total_sodium_mg"] == Decimal(10)
    assert snapshot.tables["recipe_ingredient_nutrition"][0]["fdc_id"] == "12345"
    retained = repr(snapshot.tables)
    for dropped in ("raw_source_text", "provenance", "amount_evidence", "usda_query", "discard this", "private"):
        assert dropped not in retained


def test_estimated_grams_servings_and_contributions_do_not_become_facts(tmp_path):
    value = recipe()
    value["servings"] = None
    value["ingredients"][0]["grams"] = None
    value["ready_for_recommendation"] = False
    nutrition = value["nutrition"]
    nutrition.update(status="estimated", estimated=True, estimated_servings=True,
                     estimated_total_fields=list(NUTRIENTS), estimated_per_serving_fields=list(NUTRIENTS),
                     tags_estimated=True)
    detail = nutrition["ingredients"][0]
    detail.update(original_grams=None, estimated=True, estimated_grams=True, estimated_nutrients=list(NUTRIENTS))
    detail["source"]["per_100g"]["sugar_g"] = None
    detail["estimate"] = {"per_100g": {"sugar_g": 10}, "reason": "AI guess"}
    write_export(tmp_path, [value])
    snapshot = load_export(tmp_path)
    assert snapshot.tables["recipes"][0]["servings"] == 2
    assert snapshot.tables["recipes"][0]["estimated_servings"] is True
    assert snapshot.tables["recipe_ingredients"][0]["grams"] == 100
    assert snapshot.tables["recipe_ingredients"][0]["estimated_grams"] is True
    row = snapshot.tables["recipe_ingredient_nutrition"][0]
    assert row["contribution_sugar_g"] == 10
    assert row["estimated_sugar_g"] is True
    assert row["per_100g_sugar_g"] is None
    assert next(row for row in snapshot.tables["recipe_labels"] if row["kind"] == "nutrition")["estimated"] is True


def test_unknown_nutrients_stay_null_instead_of_zero(tmp_path):
    value = recipe()
    value["ready_for_recommendation"] = False
    value["nutrition"]["status"] = "incomplete"
    value["nutrition"]["total"]["sugar_g"] = None
    value["nutrition"]["per_serving"]["sugar_g"] = None
    value["nutrition"]["ingredients"][0]["nutrition"]["sugar_g"] = None
    value["nutrition"]["ingredients"][0]["source"]["per_100g"]["sugar_g"] = None
    write_export(tmp_path, [value])
    snapshot = load_export(tmp_path)
    assert snapshot.tables["recipe_nutrition"][0]["total_sugar_g"] is None
    assert snapshot.tables["recipe_ingredient_nutrition"][0]["contribution_sugar_g"] is None


def test_same_ingredient_id_retains_occurrence_specific_metadata_and_repeated_rows(tmp_path):
    first = recipe()
    second = recipe("r2")
    second["ingredients"][0].update(canonical_name="老豆腐", category="其他", allergens=[], aliases=["硬豆腐"])
    second["nutrition"]["ingredients"][0]["name"] = "老豆腐"
    second["ingredients"].append(copy.deepcopy(second["ingredients"][0]))
    second["nutrition"]["ingredients"].append(copy.deepcopy(second["nutrition"]["ingredients"][0]))
    for name in NUTRIENTS:
        second["nutrition"]["total"][name] = 20
        second["nutrition"]["per_serving"][name] = 10
    write_export(tmp_path, [first, second])
    snapshot = load_export(tmp_path)
    assert len(snapshot.tables["ingredients"]) == 1
    assert [(r["recipe_id"], r["ordinal"], r["canonical_name"]) for r in snapshot.tables["recipe_ingredients"]] == [
        ("r1", 0, "豆腐"), ("r2", 0, "老豆腐"), ("r2", 1, "老豆腐"),
    ]
    assert snapshot.tables["ingredient_allergens"] == [{"recipe_id": "r1", "ordinal": 0, "allergen": "大豆"}]


@pytest.mark.parametrize("field,value,message", [
    ("schema_version", 1, "schema_version"), ("schema_version", True, "schema_version"),
    ("phase", "extraction", "phase"), ("recipe_count", 2, "actual count"),
    ("recipe_count", True, "nonnegative integer"), ("recipe_count", -1, "nonnegative integer"),
    ("created_at", "2026-10-05T12:00:00", "timezone"), ("created_at", "bad date", "ISO timestamp"),
    ("approved_only", "true", "boolean"),
])
def test_invalid_manifest_rejected(tmp_path, field, value, message):
    write_export(tmp_path, **{field: value})
    with pytest.raises(ExportValidationError, match=message):
        load_export(tmp_path)


def test_unapproved_export_requires_explicit_opt_in(tmp_path):
    write_export(tmp_path, approved_only=False)
    with pytest.raises(ExportValidationError, match="allow_unapproved"):
        load_export(tmp_path)
    assert load_export(tmp_path, allow_unapproved=True).recipe_count == 1


def test_empty_export_requires_explicit_opt_in_and_keeps_version(tmp_path):
    write_export(tmp_path, [])
    with pytest.raises(ExportValidationError, match="allow_empty"):
        load_export(tmp_path)
    snapshot = load_export(tmp_path, allow_empty=True)
    assert snapshot.recipe_count == 0
    assert snapshot.data_version == tmp_path.name
    assert all(not rows for rows in snapshot.tables.values())


@pytest.mark.parametrize("name", ["recipes.json", "usda_mappings.json"])
def test_hash_every_declared_file_even_unused_files(tmp_path, name):
    write_export(tmp_path)
    (tmp_path / name).write_bytes(b"[]")
    if name == "usda_mappings.json":
        (tmp_path / name).write_bytes(b"[{}]")
    with pytest.raises(ExportValidationError, match="SHA256 mismatch"):
        load_export(tmp_path)


@pytest.mark.parametrize("name", ["../outside.json", "..\\outside.json", "/absolute.json", "C:outside.json", "recipes.json:stream", "CON.json"])
def test_manifest_paths_cannot_escape_export_directory(tmp_path, name):
    manifest = write_export(tmp_path)
    manifest["sha256"][name] = "a" * 64
    rewrite_manifest(tmp_path, manifest)
    with pytest.raises(ExportValidationError, match="unsafe filename"):
        load_export(tmp_path)


def test_symlink_files_rejected(tmp_path):
    manifest = write_export(tmp_path)
    outside = tmp_path.parent / "outside.json"
    outside.write_bytes(b"[]")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this host")
    manifest["sha256"]["linked.json"] = hashlib.sha256(b"[]").hexdigest()
    rewrite_manifest(tmp_path, manifest)
    with pytest.raises(ExportValidationError, match="regular file inside"):
        load_export(tmp_path)


def test_missing_recipe_declaration_and_invalid_hash_are_rejected(tmp_path):
    manifest = write_export(tmp_path)
    del manifest["sha256"]["recipes.json"]
    rewrite_manifest(tmp_path, manifest)
    with pytest.raises(ExportValidationError, match="must be declared"):
        load_export(tmp_path)
    manifest["sha256"]["recipes.json"] = "not a hash"
    rewrite_manifest(tmp_path, manifest)
    with pytest.raises(ExportValidationError, match="invalid SHA256"):
        load_export(tmp_path)


def test_duplicate_recipe_ids_rejected(tmp_path):
    write_export(tmp_path, [recipe(), recipe()])
    with pytest.raises(ExportValidationError, match="duplicate recipe_id"):
        load_export(tmp_path)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "10", True])
def test_invalid_numeric_values_rejected(tmp_path, value):
    item = recipe()
    item["prep_time_min"] = value
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError):
        load_export(tmp_path)


@pytest.mark.parametrize("path,value,message", [
    (("ready_for_recommendation",), "false", "boolean"),
    (("ingredients", 0, "scalable"), 1, "boolean"),
    (("servings",), 0, "positive"),
    (("name",), " ", "empty"),
    (("recipe_id",), "", "empty"),
    (("ingredients", 0, "category"), "", "empty"),
    (("ingredients", 0, "raw_text"), None, "expected text"),
    (("ingredients",), [], "at least one ingredient"),
    (("steps",), [], "at least one step"),
    (("nutrition", "ingredients"), [], "count does not match"),
    (("nutrition", "ingredients", 0, "name"), "鸡肉", "name does not match"),
    (("nutrition", "ingredients", 0, "grams"), 200, "disagree with factual grams"),
    (("nutrition", "ingredients", 0, "estimated_nutrients"), ["invented"], "unknown nutrient"),
    (("nutrition", "per_serving", "protein_g"), 100, "disagrees with total"),
    (("references", 0, "url"), "javascript:alert(1)", "HTTP"),
    (("references", 0, "url"), "https://user:password@example.com/a", "credentials"),
])
def test_bad_recipe_relationships_and_types_rejected(tmp_path, path, value, message):
    item = recipe()
    owner = item
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError, match=message):
        load_export(tmp_path)


@pytest.mark.parametrize("field", ["grams", "servings"])
def test_fallback_values_require_estimate_flags(tmp_path, field):
    item = recipe()
    if field == "grams":
        item["ingredients"][0]["grams"] = None
        item["nutrition"]["ingredients"][0]["original_grams"] = None
    else:
        item["servings"] = None
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError, match="must be marked as estimated"):
        load_export(tmp_path)


def test_manifest_approval_is_not_a_substitute_for_each_recipe_review(tmp_path):
    item = recipe()
    item["provenance"]["status"] = "pending_review"
    item["ready_for_recommendation"] = False
    write_export(tmp_path, [item], approved_only=True)
    with pytest.raises(ExportValidationError, match="unapproved recipe"):
        load_export(tmp_path)
    assert load_export(tmp_path, allow_unapproved=True).recipe_count == 1
    item["ready_for_recommendation"] = True
    write_export(tmp_path, [item], approved_only=False)
    with pytest.raises(ExportValidationError, match="unapproved recipe cannot be ready"):
        load_export(tmp_path, allow_unapproved=True)


@pytest.mark.parametrize("option,value", [("allow_unapproved", "false"), ("allow_unapproved", 1), ("allow_empty", "true"), ("allow_empty", 0)])
def test_import_option_flags_require_actual_booleans(tmp_path, option, value):
    write_export(tmp_path)
    with pytest.raises(ExportValidationError, match="boolean"):
        load_export(tmp_path, **{option: value})


def test_totals_cannot_disagree_with_individual_ingredient_contributions(tmp_path):
    item = recipe()
    item["nutrition"]["total"]["protein_g"] = 20
    item["nutrition"]["per_serving"]["protein_g"] = 10
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError, match="disagrees with ingredient contributions"):
        load_export(tmp_path)


def test_parsed_bytes_must_still_match_manifest_hash(tmp_path, monkeypatch):
    from chefmate_storage import importer

    write_export(tmp_path)
    original_hash = importer._hash_file

    def hash_then_change(file):
        digest = original_hash(file)
        if file.name == "recipes.json":
            file.write_bytes(file.read_bytes() + b" ")
        return digest

    monkeypatch.setattr(importer, "_hash_file", hash_then_change)
    with pytest.raises(ExportValidationError, match="changed while being parsed"):
        load_export(tmp_path)


def test_non_usda_per100g_not_promoted_to_source_nutrition(tmp_path):
    item = recipe()
    item["ready_for_recommendation"] = False
    item["nutrition"].update(status="estimated", estimated=True, estimated_total_fields=list(NUTRIENTS),
                             estimated_per_serving_fields=list(NUTRIENTS))
    item["nutrition"]["ingredients"][0].update(estimated=True, estimated_nutrients=list(NUTRIENTS))
    item["nutrition"]["ingredients"][0]["source"].update(source="AI_ESTIMATE", fdc_id="invented")
    write_export(tmp_path, [item])
    row = load_export(tmp_path).tables["recipe_ingredient_nutrition"][0]
    assert row["fdc_id"] is None
    assert all(row[f"per_100g_{name}"] is None for name in NUTRIENTS)


@pytest.mark.parametrize("level,fields,message", [
    ("estimated_total_fields", [], "total estimate flags disagree"),
    ("estimated_total_fields", ["protein_g", "sugar_g"], "total estimate flags disagree"),
    ("estimated_per_serving_fields", [], "per-serving estimate flags disagree"),
    ("estimated_per_serving_fields", ["protein_g", "sugar_g"], "per-serving estimate flags disagree"),
])
def test_aggregate_estimate_markers_must_match_actual_contributions(tmp_path, level, fields, message):
    item = recipe()
    item["ready_for_recommendation"] = False
    item["nutrition"].update(status="estimated", estimated=True, estimated_total_fields=["protein_g"],
                             estimated_per_serving_fields=["protein_g"])
    detail = item["nutrition"]["ingredients"][0]
    detail.update(estimated=True, estimated_nutrients=["protein_g"])
    detail["source"]["per_100g"]["protein_g"] = None
    item["nutrition"][level] = fields
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError, match=message):
        load_export(tmp_path)


def test_estimated_grams_cannot_produce_unmarked_nutrient_contributions(tmp_path):
    item = recipe()
    item["ingredients"][0]["grams"] = None
    item["ready_for_recommendation"] = False
    item["nutrition"].update(status="estimated", estimated=True)
    item["nutrition"]["ingredients"][0].update(original_grams=None, estimated=True, estimated_grams=True)
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError, match="contribution needs an estimate flag"):
        load_export(tmp_path)


def test_factual_usda_per100g_and_contribution_must_agree(tmp_path):
    item = recipe()
    item["nutrition"]["ingredients"][0]["source"]["per_100g"]["protein_g"] = 20
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError, match="disagrees with USDA per_100g and grams"):
        load_export(tmp_path)


def test_nutrient_without_factual_usda_source_cannot_be_unestimated(tmp_path):
    item = recipe()
    item["nutrition"]["ingredients"][0]["source"]["per_100g"]["protein_g"] = None
    write_export(tmp_path, [item])
    with pytest.raises(ExportValidationError, match="contribution needs an estimate flag"):
        load_export(tmp_path)


def test_top_level_array_required_even_with_allow_empty(tmp_path):
    manifest = write_export(tmp_path, [])
    content = b"{}"
    (tmp_path / "recipes.json").write_bytes(content)
    manifest["sha256"]["recipes.json"] = hashlib.sha256(content).hexdigest()
    rewrite_manifest(tmp_path, manifest)
    with pytest.raises(ExportValidationError, match="top-level array"):
        load_export(tmp_path, allow_empty=True)


def test_invalid_json_trailing_content_and_duplicate_manifest_keys_rejected(tmp_path):
    manifest = write_export(tmp_path)
    content = (tmp_path / "recipes.json").read_bytes() + b" {}"
    (tmp_path / "recipes.json").write_bytes(content)
    manifest["sha256"]["recipes.json"] = hashlib.sha256(content).hexdigest()
    rewrite_manifest(tmp_path, manifest)
    with pytest.raises(ExportValidationError, match="cannot read export"):
        load_export(tmp_path)
    (tmp_path / "manifest.json").write_text('{"schema_version": 2, "schema_version": 2}', encoding="utf-8")
    with pytest.raises(ExportValidationError, match="duplicate key"):
        load_export(tmp_path)
