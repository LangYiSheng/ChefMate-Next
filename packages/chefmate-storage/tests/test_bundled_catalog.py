"""The Git-tracked startup catalog must remain a valid, honest projection."""

import json
import runpy
from pathlib import Path

from chefmate_storage.importer import load_export


ROOT = Path(__file__).resolve().parents[3]


def test_bundled_catalog_matches_manifest_and_preserves_readiness():
    directory = ROOT / "data" / "seed" / "catalog"
    snapshot = load_export(directory, data_version="chefmate-bundled-20261005", allow_unapproved=True)
    report = json.loads((directory / "projection-report.json").read_text(encoding="utf-8"))
    assert snapshot.recipe_count == report["recipe_count"] == 358
    assert {name: len(rows) for name, rows in snapshot.tables.items()} == report["tables"]
    assert not any(recipe["ready_for_recommendation"] for recipe in snapshot.tables["recipes"])
    assert {recipe["source"].split("/")[0] for recipe in snapshot.tables["recipes"]} == {"HowToCook"}


def test_seed_packaging_removes_bulk_data_without_changing_relational_values(tmp_path, make_export, make_recipe):
    recipe = make_recipe()
    source = make_export([recipe])
    original_bytes = (source / "recipes.json").read_bytes()
    target = tmp_path / "compact"
    package = runpy.run_path(str(ROOT / "scripts" / "package_catalog_seed.py"))["package"]
    result = package(source, target, "packaging-fixture")
    assert result["all_online_tables_identical"]
    assert (source / "recipes.json").read_bytes() == original_bytes
    compact = json.loads((target / "recipes.json").read_text(encoding="utf-8"))
    assert "raw_source_text" not in compact[0]
    assert "prompt" not in compact[0]["provenance"]
    assert "candidates" not in compact[0]["nutrition"]["ingredients"][0]["source"]
    assert load_export(source, data_version="packaging-fixture").tables == load_export(target, data_version="packaging-fixture").tables
