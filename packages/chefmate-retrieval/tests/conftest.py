"""Reuse the storage package's real export fixtures and isolated PostgreSQL schemas."""

import importlib.util
from pathlib import Path

fixture_path = Path(__file__).resolve().parents[2] / "chefmate-storage" / "tests" / "conftest.py"
spec = importlib.util.spec_from_file_location("chefmate_storage_test_fixtures", fixture_path)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
make_recipe = fixtures.make_recipe
make_export = fixtures.make_export
postgres_store = fixtures.postgres_store
postgres_execute = fixtures.postgres_execute
