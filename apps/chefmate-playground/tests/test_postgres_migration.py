"""Real PostgreSQL migration acceptance; skipped without an explicit test DSN."""

import hashlib
import json
import os
from uuid import uuid4
from dataclasses import dataclass, field

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.accounts import AccountsRepository
from chefmate_playground.database import Database
from chefmate_playground.memory import MemoryRepository
from chefmate_playground.migrate_data import migrate_runtime
from chefmate_playground.sessions import SessionRepository


@dataclass
class Destination:
    url: str = field(repr=False)
    prefix: str


@pytest.fixture
def destination():
    url = os.environ.get("CHEFMATE_TEST_APP_DSN")
    if not url:
        pytest.skip("CHEFMATE_TEST_APP_DSN not configured")
    prefix = "mig" + uuid4().hex[:10]
    yield Destination(url, prefix)
    with psycopg.connect(url) as db:
        schemas = [row[0] for row in db.execute("SELECT schema_name FROM information_schema.schemata")
                   if row[0].startswith(prefix + "_")]
        for schema in schemas:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_migrates_identity_content_settings_images_and_prices_without_changing_source(tmp_path, destination):
    url, prefix = destination.url, destination.prefix
    source, target = tmp_path / "source", tmp_path / "target"
    accounts = AccountsRepository(source / "accounts.db")
    admin = accounts.bootstrap_admin("migration-admin", "test-only-password-123")
    user = accounts.register("migrate.user", "test-only-password-123")
    accounts.complete_onboarding(user["id"], {"display_name": "迁移厨房", "use_for_ai": True})
    accounts.update_pricing(admin["id"], {"input_per_million": "2", "output_per_million": "4"})
    accounts.reserve_usage(user["id"], "fixture-turn", tokens=1000)
    accounts.settle_usage(user["id"], "fixture-turn", {"input_tokens": 30, "output_tokens": 10,
        "total_tokens": 40, "attempted_calls": 1}, model="fixture-model")
    path = accounts.tenant_database(user["id"])
    sessions = SessionRepository(path)
    session = sessions.create()
    session["messages"] = [{"role": "user", "content": "这段话和图片都要保留"}]
    session["title"] = "迁移前的厨房"
    sessions.save(session)
    memory = MemoryRepository(path)
    memory.update_user("不吃香菜", 0)
    (path.parent / "images").mkdir()
    (path.parent / "images" / "fixture.jpg").write_bytes(b"fixture-image-payload")
    (path.parent / "settings.json").write_text(json.dumps({"ui_preferences": {"theme": "dark"}}), encoding="utf-8")
    original_hashes = {str(file.relative_to(source)): hashlib.sha256(file.read_bytes()).hexdigest()
                       for file in source.rglob("*") if file.is_file()}
    result = migrate_runtime(source, target, url, schema_prefix=prefix)
    assert result["ok"] and result["source_preserved"] and result["users"] == 2
    assert result["counts"][user["id"]]["sessions"] == 1
    assert original_hashes == {str(file.relative_to(source)): hashlib.sha256(file.read_bytes()).hexdigest()
                              for file in source.rglob("*") if file.is_file()}
    assert not list(target.rglob("*.db"))
    assert (target / "users" / user["id"] / "images" / "fixture.jpg").read_bytes() == b"fixture-image-payload"
    app = create_app(PlaygroundConfig(sessions_database=target / "sessions.db", app_database_url=url,
                                     app_database_schema=prefix))
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "migrate.user", "password": "test-only-password-123"}).status_code == 200
        assert client.get("/api/sessions/" + session["id"]).json()["title"] == "迁移前的厨房"
        assert client.get("/api/memory").json()["user_notes"] == "不吃香菜"
        assert client.get("/api/ui-preferences").json()["values"]["theme"] == "dark"
        assert client.get("/api/auth/quota").json()["quota"]["used_tokens"] == 40
    with pytest.raises(ValueError, match="目标"):
        migrate_runtime(source, tmp_path / "second-target", url, schema_prefix=prefix)


def test_legacy_single_user_requires_owner_and_preserves_new_defaults(tmp_path, destination):
    url, prefix = destination.url, destination.prefix
    source = tmp_path / "legacy"
    sessions = SessionRepository(source / "sessions.db")
    session = sessions.create()
    session["title"] = "老厨房"
    sessions.save(session)
    with pytest.raises(ValueError, match="旧单用户库"):
        migrate_runtime(source, tmp_path / "unassigned", url, schema_prefix=prefix)
    result = migrate_runtime(source, tmp_path / "assigned", url, schema_prefix=prefix,
        legacy_owner="owner", admin_username="owner", admin_password="test-only-password-123")
    account_db = Database(tmp_path / "assigned" / "accounts.db", url=url, schema=prefix + "_accounts")
    owner = AccountsRepository(account_db).list_users()[0]
    database = Database(tmp_path / "assigned" / "users" / owner["id"] / "sessions.db", url=url,
                        schema=prefix + "_user_" + owner["id"])
    assert SessionRepository(database).get(session["id"])["title"] == "老厨房"
    assert MemoryRepository(database).snapshot()["user_notes"] == ""
    assert result["users"] == 1


def test_refuses_nonempty_destination_and_invalid_schema_before_writing(tmp_path, destination):
    url, prefix = destination.url, destination.prefix
    source, target = tmp_path / "source", tmp_path / "protected"
    SessionRepository(source / "sessions.db")
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="必须为空"):
        migrate_runtime(source, target, url, schema_prefix=prefix)
    assert (target / "keep.txt").read_text() == "keep"
    with pytest.raises(ValueError, match="schema"):
        migrate_runtime(source, tmp_path / "unused", url, schema_prefix="x; DROP SCHEMA public")
