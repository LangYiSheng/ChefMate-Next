"""Failed offline migrations preserve source and cannot escape target namespaces."""

import hashlib
import os
from dataclasses import dataclass, field
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from chefmate_playground.accounts import AccountsRepository
from chefmate_playground.database import Database
from chefmate_playground.migrate_data import migrate_runtime
from chefmate_playground.sessions import SessionRepository


@dataclass
class Destination:
    url: str = field(repr=False)
    prefix: str


@pytest.fixture
def destination():
    dsn = os.environ.get("CHEFMATE_TEST_APP_DSN")
    if not dsn:
        pytest.skip("CHEFMATE_TEST_APP_DSN is required for migration safety acceptance")
    prefix = "safe" + uuid4().hex[:10]
    yield Destination(dsn, prefix)
    with psycopg.connect(dsn, autocommit=True) as connection:
        names = connection.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (prefix + "%",)).fetchall()
        for row in names:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(row[0])))


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_migration_rejects_traversing_identity_before_copying_files(tmp_path, destination):
    source, target = tmp_path / "source", tmp_path / "target"
    accounts = AccountsRepository(source / "accounts.db")
    user = accounts.register("untrusted-id", "fixture-migration-password-123")
    with accounts._connect() as connection:
        connection.execute("UPDATE accounts SET id=? WHERE id=?", ("../..", user["id"]))
    # This neighbour is deliberately outside source; it must never be imported.
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "private.jpg").write_bytes(b"not part of the source runtime")
    before = hashes(source)
    with pytest.raises(ValueError):
        migrate_runtime(source, target, destination.url, schema_prefix=destination.prefix)
    assert hashes(source) == before
    assert not list(target.rglob("private.jpg"))
    with psycopg.connect(destination.url) as connection:
        assert not connection.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (
            destination.prefix + "_accounts",)).fetchone()


def test_unknown_source_table_rolls_back_and_drops_only_its_staging(tmp_path, destination, monkeypatch):
    from chefmate_playground import migrate_data
    identity = uuid4()
    monkeypatch.setattr(migrate_data, "uuid4", lambda: identity)
    source, target = tmp_path / "source", tmp_path / "target"
    sessions = SessionRepository(source / "sessions.db")
    with sessions._connect() as connection:
        connection.execute("CREATE TABLE mystery_private(id TEXT)")
        connection.execute("INSERT INTO mystery_private VALUES(?)", ("must stay in source",))
    before = hashes(source)
    with pytest.raises(ValueError, match="未知表"):
        migrate_runtime(source, target, destination.url, schema_prefix=destination.prefix,
            legacy_owner="owner", admin_username="owner", admin_password="fixture-migration-password-123")
    assert hashes(source) == before
    with psycopg.connect(destination.url) as connection:
        staging = "cm_migrate_" + identity.hex[:12]
        assert not connection.execute("SELECT 1 FROM pg_namespace WHERE nspname LIKE %s", (staging + "%",)).fetchone()
        assert not connection.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (
            destination.prefix + "_accounts",)).fetchone()


def test_migrated_account_audit_sequence_advances_after_explicit_ids(tmp_path, destination):
    source, target = tmp_path / "source", tmp_path / "target"
    accounts = AccountsRepository(source / "accounts.db")
    admin = accounts.bootstrap_admin("owner", "fixture-migration-password-123")
    user = accounts.register("member", "fixture-migration-password-123")
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": 12345})
    accounts.update_pricing(admin["id"], {"input_per_million": "1", "output_per_million": "1",
                                          "cached_input_per_million": "1"})
    accounts.reserve_usage(user["id"], "migrated-turn", tokens=100, cost_micros=100)
    accounts.settle_usage(user["id"], "migrated-turn", {"attempted_calls": 1, "calls": [
        {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10}]})
    for revision, amount in ((0, 5), (1, 4)):
        accounts.reconcile_usage(admin["id"], user["id"], "migrated-turn", revision=revision,
                                 final_cost_micros=amount, reason="迁移前已核对供应商记录。")
    maximum = max(row["id"] for row in accounts.audit_log())
    before = hashes(source)
    migrate_runtime(source, target, destination.url, schema_prefix=destination.prefix)
    assert hashes(source) == before
    copied = AccountsRepository(Database(target / "accounts.db", url=destination.url,
                                          schema=destination.prefix + "_accounts"))
    assert [item["revision"] for item in copied.reconciliations(user["id"], "migrated-turn")] == [2, 1]
    entry = copied.get_usage(user["id"])[0]
    assert entry["cost_micros"] == 10 and entry["effective_cost_micros"] == 4
    assert copied.get_user(user["id"])["quota"]["balance_micros"] == 12341
    copied.update_user(admin["id"], user["id"], {"developer_allowed": True})
    assert copied.audit_log()[0]["id"] > maximum


def test_sqlite_wal_sidecars_require_stopped_checkpointed_source(tmp_path, destination):
    source = tmp_path / "source"
    SessionRepository(source / "sessions.db")
    (source / "sessions.db-wal").write_bytes(b"unresolved fixture WAL")
    before = hashes(source)
    with pytest.raises(ValueError):
        migrate_runtime(source, tmp_path / "target", destination.url, schema_prefix=destination.prefix,
            legacy_owner="owner", admin_username="owner", admin_password="fixture-migration-password-123")
    assert hashes(source) == before
