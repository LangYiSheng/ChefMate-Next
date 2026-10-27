from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path

import pytest

from chefmate_storage.database import SeedConflictError


def _init_module():
    path = Path(__file__).resolve().parents[3] / "docker" / "seed_catalog.py"
    spec = importlib.util.spec_from_file_location("docker_seed_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundled_catalog_init_and_restart_preserve_real_flags(postgres_store, postgres_execute, monkeypatch, tmp_path):
    module = _init_module()
    directory = Path(__file__).resolve().parents[3] / "data" / "seed" / "catalog"
    monkeypatch.setenv("CHEFMATE_DATABASE_URL", postgres_store._dsn)
    monkeypatch.setenv("CHEFMATE_DATABASE_SCHEMA", postgres_store.schema)
    marker = tmp_path / "catalog status" / "ready.json"
    first = module.initialize(directory, marker)
    imported_at = postgres_store.metadata()["imported_at"]
    second = module.initialize(directory, marker)
    assert first["status"] == "seeded" and second["status"] == "verified"
    assert first["recipe_count"] == 358 and first["source_approved_only"] is False
    assert postgres_store.metadata()["imported_at"] == imported_at
    assert json.loads(marker.read_text(encoding="utf-8"))["ready"] is True
    assert postgres_execute("SELECT count(*) FROM recipes WHERE ready_for_recommendation")[0][0] == 0
    source = json.loads((directory / "recipes.json").read_text(encoding="utf-8"))
    assert sum(recipe["provenance"]["status"] == "approved" for recipe in source) == 2


def test_init_removes_stale_ready_marker_on_conflicting_data(postgres_store, make_export, make_recipe,
                                                          monkeypatch, tmp_path):
    module = _init_module()
    postgres_store.replace_from_export(make_export([make_recipe()]), data_version="operator-dataset")
    monkeypatch.setenv("CHEFMATE_DATABASE_URL", postgres_store._dsn)
    monkeypatch.setenv("CHEFMATE_DATABASE_SCHEMA", postgres_store.schema)
    marker = tmp_path / "ready.json"
    marker.write_text('{"ready":true}', encoding="utf-8")
    directory = Path(__file__).resolve().parents[3] / "data" / "seed" / "catalog"
    with pytest.raises(SeedConflictError):
        module.initialize(directory, marker)
    assert not marker.exists()
    assert postgres_store.metadata()["data_version"] == "operator-dataset"


def test_init_rejects_changed_bundle_before_database_access(tmp_path):
    module = _init_module()
    directory = tmp_path / "bundle"
    directory.mkdir()
    (directory / "recipes.json").write_text("[]", encoding="utf-8")
    (directory / "manifest.json").write_text(json.dumps({"data_version": module.BUNDLED_VERSION,
        "sha256": {"recipes.json": module.BUNDLED_SHA256}}), encoding="utf-8")
    marker = tmp_path / "ready.json"
    marker.write_text('{"ready":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        module.initialize(directory, marker)
    assert not marker.exists()


def test_seed_is_idempotent_and_preserves_import_timestamp(postgres_store, make_export, make_recipe):
    directory = make_export([make_recipe()])
    first = postgres_store.seed_from_export(directory, data_version="bundled-test")
    second = postgres_store.seed_from_export(directory, data_version="bundled-test")
    assert first["status"] == "seeded" and second["status"] == "verified"
    assert first["imported_at"] == second["imported_at"]
    assert second["recipe_count"] == 1 and postgres_store.search()["total"] == 1


def test_seed_rejects_different_dataset_without_replacement(postgres_store, make_export, make_recipe):
    first = make_export([make_recipe("first")])
    other = make_export([make_recipe("second")])
    before = postgres_store.seed_from_export(first, data_version="first-version")
    with pytest.raises(SeedConflictError):
        postgres_store.seed_from_export(other, data_version="second-version")
    assert postgres_store.metadata()["imported_at"] == before["imported_at"]
    assert postgres_store.get_recipe("first") and postgres_store.get_recipe("second") is None


def test_seed_detects_same_version_row_changes_without_repair(postgres_store, postgres_execute, make_export, make_recipe):
    directory = make_export([make_recipe()])
    postgres_store.seed_from_export(directory, data_version="same-version")
    postgres_execute("UPDATE recipe_steps SET description='operator modified' WHERE ordinal=0")
    with pytest.raises(SeedConflictError):
        postgres_store.seed_from_export(directory, data_version="same-version")
    assert postgres_execute("SELECT description FROM recipe_steps WHERE ordinal=0")[0][0] == "operator modified"


def test_seed_rejects_unlabelled_nonempty_tables(postgres_store, postgres_execute, make_export, make_recipe):
    directory = make_export([make_recipe()])
    postgres_store.seed_from_export(directory, data_version="same-version")
    postgres_execute("DELETE FROM dataset_metadata")
    with pytest.raises(SeedConflictError):
        postgres_store.seed_from_export(directory, data_version="same-version")
    assert postgres_store.search()["total"] == 1


def test_concurrent_first_seed_serializes_without_duplicate_rows(postgres_store, make_export, make_recipe):
    directory = make_export([make_recipe()])
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(postgres_store.seed_from_export, directory, data_version="concurrent") for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(result["status"] for result in results) == ["seeded", "verified"]
    assert len({result["imported_at"] for result in results}) == 1
    assert postgres_store.search()["total"] == 1
