"""Installed recipe counts include real candidates with incomplete nutrition."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_retrieval import RetrievalService
from chefmate_storage import RecipeStore


def test_status_and_about_count_all_358_without_changing_readiness(tmp_path):
    url = os.environ.get("CHEFMATE_TEST_APP_DSN")
    if not url:
        pytest.skip("Set CHEFMATE_TEST_APP_DSN for the isolated PostgreSQL catalog check")
    suffix = uuid4().hex[:24]
    catalog_schema, personal_schema = "status_catalog_" + suffix, "status_personal_" + suffix
    try:
        store = RecipeStore(url, schema=catalog_schema)
        store.migrate()
        seed = Path(__file__).resolve().parents[3] / "data" / "seed" / "catalog"
        store.seed_from_export(seed, data_version="status-bundled", allow_unapproved=True)
        assert store.search()["total"] == 0  # Strict recommendation remains strict.
        app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "sessions.db",
            app_database_url=url, app_database_schema=personal_schema), service=RetrievalService(store), agent=object())
        with TestClient(app) as client:
            status = client.get("/api/status")
            assert status.status_code == 200 and status.json()["database"]["recipe_count"] == 358
            assert client.get("/api/about").json()["recipe_count"] == 358
        assert not (tmp_path / "sessions.db").exists()
    finally:
        with psycopg.connect(url) as connection:
            for schema in (catalog_schema, personal_schema):
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
