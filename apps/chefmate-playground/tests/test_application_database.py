"""Portable SQL unit tests and explicit, isolated real PostgreSQL acceptance."""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from chefmate_playground.accounts import AccountsRepository, QuotaExceeded
from chefmate_playground.cookbook import CookbookRepository
from chefmate_playground.database import Database, DatabaseRow, postgres_statement
from chefmate_playground.journal import JournalRepository
from chefmate_playground.memory import MemoryRepository
from chefmate_playground.sessions import SessionRepository
from chefmate_playground.usage import UsageRepository


def test_parameter_translation_preserves_sql_literals_and_escapes_percent():
    statement = postgres_statement("SELECT '?' literal,'50%' percent WHERE value=?", parameters=True)
    assert statement == "SELECT '?' literal,'50%%' percent WHERE value=%s"
    assert postgres_statement("INSERT OR IGNORE INTO thing VALUES(?)", parameters=True) == (
        "INSERT INTO thing VALUES(%s) ON CONFLICT DO NOTHING")


def test_row_adapter_has_sqlite_row_mapping_and_tuple_semantics():
    row = DatabaseRow(("cook", 123), ("name", "tokens"))
    assert row[0] == row["name"] == "cook"
    assert tuple(row) == ("cook", 123)
    assert dict(row) == {"name": "cook", "tokens": 123}


def test_database_identity_and_metadata_are_explicit(tmp_path):
    database = Database(tmp_path / "sessions.db")
    assert Database.coerce(database) is database
    assert not database.is_postgres
    with database.connect() as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY,body TEXT)")
    assert database.tables() == ["sample"]
    assert [column["name"] for column in database.table_columns("sample")] == ["id", "body"]
    with pytest.raises(ValueError):
        Database(tmp_path / "bad.db", url="postgresql://hidden-secret@localhost/db", schema='x";DROP SCHEMA public')
    assert "hidden-secret" not in repr(Database(tmp_path / "db", url="postgresql://hidden-secret@localhost/db"))


@pytest.fixture
def postgres_database(tmp_path):
    dsn = os.environ.get("CHEFMATE_TEST_APP_DSN")
    if not dsn:
        pytest.skip("CHEFMATE_TEST_APP_DSN is required for isolated PostgreSQL app-data acceptance")
    import psycopg
    from psycopg import sql
    schema = "chefmate_test_" + uuid4().hex[:20]
    database = Database(tmp_path / "logical" / "sessions.db", url=dsn, schema=schema)
    yield database
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(database.schema)))


def test_postgres_all_application_repositories_and_restart(postgres_database):
    database = postgres_database
    sessions = SessionRepository(database)
    cookbook = CookbookRepository(database)
    journal = JournalRepository(database)
    memory = MemoryRepository(database, journal)
    usage = UsageRepository(database)
    assert journal.food_logs.database is database
    session = sessions.create()
    sessions.mutate(session["id"], lambda state: state["messages"].append({"role": "user", "content": "想吃番茄"}))
    sessions.update_metadata(session["id"], title="PostgreSQL 厨房", pinned=True)
    assert sessions.page(limit=1)[0][0]["title"] == "PostgreSQL 厨房"
    capture = {"turn_id": "fixture-turn", "created_at": session["created_at"], "debug_trace": [], "status": "completed"}
    sessions.save_debug_run(session["id"], "fixture-turn", capture)
    assert sessions.debug_run(session["id"], "fixture-turn") == capture
    cookbook.favorite("official-r1", True)
    cookbook.favorite("official-r1", True)
    assert cookbook.favorites() == ["official-r1"]
    before = memory.snapshot()
    memory.update_user("少放辣椒", before["revision"])
    assert memory.context()["user_notes"] == "少放辣椒"
    assert memory.search_history("番茄")["items"]
    usage.record(session["id"], "fixture-turn", {"attempted_calls": 1, "calls": [
        {"round": 1, "input_tokens": 15, "output_tokens": 5, "total_tokens": 20}]})
    assert usage.summary()["all_time"]["total_tokens"] == 20
    tables = set(database.tables())
    assert {"sessions", "session_debug", "personal_recipes", "food_journal", "personal_food_logs",
            "personal_memory", "kitchen_profile", "model_usage_ledger"} <= tables
    assert "bytea" in {column["type"] for column in database.table_columns("session_debug")}
    assert not database.path.exists(), "PostgreSQL storage must not silently create a personal SQLite database"
    reconnected = Database(database.path, url=database.url, schema=database.schema)
    assert SessionRepository(reconnected).get(session["id"])["title"] == "PostgreSQL 厨房"
    assert MemoryRepository(reconnected).context()["user_notes"] == "少放辣椒"
    assert CookbookRepository(reconnected).favorites() == ["official-r1"]


def test_postgres_accounts_concurrent_quota_and_price_snapshot(postgres_database):
    accounts = AccountsRepository(postgres_database)
    admin = accounts.bootstrap_admin("owner", "fixture-password-for-db-123")
    user = accounts.register("cook", "fixture-password-for-db-123")
    assert accounts.authenticate_session(accounts.issue_session(user["id"]))["id"] == user["id"]
    accounts.update_user(admin["id"], user["id"], {"developer_allowed": True, "cost_limit_micros": 100})
    accounts.update_pricing(admin["id"], {"input_per_million": "2", "output_per_million": "4",
                                         "cached_input_per_million": "1"})
    def reserve(index):
        try:
            accounts.reserve_usage(user["id"], f"turn-{index}", tokens=60, cost_micros=60)
            return index
        except QuotaExceeded:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(4)))
    admitted = [value for value in results if value is not None]
    # A positive tail admits a final operation even below its full estimate:
    # collateral is 60 + 40, never four copies of the same remaining money.
    assert len(admitted) == 2
    reserved = accounts.get_user(user["id"])["quota"]
    assert reserved["reserved_cost_micros"] == 100
    assert reserved["funds"]["available_micros"] == 0
    accounts.update_pricing(admin["id"], {"input_per_million": "200"})
    for index in admitted:
        accounts.settle_usage(user["id"], f"turn-{index}", {"attempted_calls": 1, "calls": [
            {"input_tokens": 20, "output_tokens": 10, "cached_tokens": 5, "total_tokens": 30}]})
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["used_tokens"] == 60
    assert quota["used_cost_micros"] == 150
    assert quota["reserved_tokens"] == 0
    assert quota["reserved_cost_micros"] == 0
    assert quota["funds"]["available_micros"] == 0
    with pytest.raises(QuotaExceeded):
        accounts.reserve_usage(user["id"], "after-tail-settlement", tokens=1, cost_micros=1)
    assert type(quota["used_tokens"]) is int
    assert accounts.audit_log()[0]["id"] > 0
    with accounts._connect() as connection:
        assert connection.execute("SELECT developer_allowed FROM accounts WHERE id=?", (user["id"],)).fetchone()[0] == 1


def test_postgres_transactions_rollback_and_schema_archive(postgres_database):
    database = postgres_database
    with database.connect(row_factory=sqlite3.Row) as connection:
        connection.execute("CREATE TABLE example(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO example VALUES(?,?)", (1, "保留这条记录"))
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE example SET value=? WHERE id=?", ("应回滚", 1))
            raise RuntimeError("rollback fixture")
    with database.connect(row_factory=sqlite3.Row) as connection:
        row = connection.execute("SELECT * FROM example").fetchone()
        assert dict(row) == {"id": 1, "value": "保留这条记录"}
    original = database.schema
    database.rename_schema(original + "_archive")
    assert database.schema.endswith("_archive")
    with database.connect() as connection:
        assert connection.execute("SELECT value FROM example").fetchone()[0] == "保留这条记录"


def test_postgres_recipe_intake_and_journal_round_trip(postgres_database):
    from datetime import datetime, timezone
    from test_cookbook import draft
    from test_journal import cooking_state
    from test_meal_logging import photo
    database = postgres_database
    cookbook = CookbookRepository(database)
    pending = cookbook.create_draft(draft())
    saved = cookbook.publish(pending["recipe_id"], pending["revision"])
    assert saved["revision"] == 2
    assert cookbook.retrieve(saved["recipe_id"], cookbook.snapshot(saved))["recipe"]["name"] == "番茄炒蛋"
    journal = JournalRepository(database, now=lambda: datetime(2026, 10, 21, 8, tzinfo=timezone.utc))
    food = journal.food_logs.create_draft(photo(), image_ids=["fixture-image"])
    assert food["status"] == "draft"
    published = journal.food_logs.publish(food["id"], food["revision"])
    assert published["status"] == "saved"
    assert journal.food_logs.publish(food["id"], food["revision"])["id"] == food["id"]
    assert journal.summary()["intake"]["record_count"] == 1
    assert journal.sync_session(cooking_state())
    assert journal.sync_session(cooking_state(completed=True))
    assert not journal.sync_session(cooking_state(completed=True))
    entry = journal.entries()[0]
    journal.feedback(entry["id"], rating=5, notes="保留100%原味，不加_糖")
    assert journal.get(entry["id"])["rating"] == 5
    assert journal.search("100%")[0]["id"] == entry["id"]
    assert journal.search("不加_")[0]["id"] == entry["id"]


def test_postgres_last_admin_concurrent_changes_are_serialized(postgres_database):
    accounts = AccountsRepository(postgres_database)
    first = accounts.bootstrap_admin("owner-one", "fixture-password-for-db-123")
    second = accounts.bootstrap_admin("owner-two", "fixture-password-for-db-123")
    def demote(user):
        from chefmate_playground.accounts import AccountError
        try:
            accounts.update_user(user["id"], user["id"], {"role": "user"})
            return True
        except AccountError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(demote, (first, second))) == 1
    assert sum(user["role"] == "admin" and not user["disabled"] for user in accounts.list_users()) == 1


def test_postgres_registration_policy_persists_and_changes_only_future_caps(postgres_database):
    from chefmate_playground.accounts import AccountError
    accounts = AccountsRepository(postgres_database)
    admin = accounts.bootstrap_admin("policy-owner", "fixture-policy-password-123")
    old = accounts.register("policy-old", "fixture-policy-password-123")
    policy = accounts.update_policy(admin["id"], {"revision": accounts.policy()["revision"],
        "default_cost_limit_micros": 2_000_000})
    new = accounts.register("policy-new", "fixture-policy-password-123")
    assert old["quota"]["cost_limit_micros"] == 5_000_000
    assert new["quota"]["token_limit"] is None
    assert new["quota"]["cost_limit_micros"] == 2_000_000
    reopened = AccountsRepository(Database(postgres_database.path, url=postgres_database.url,
        schema=postgres_database.schema), default_cost_limit_micros=999)
    assert reopened.policy() == policy
    accounts.update_policy(admin["id"], {"revision": policy["revision"], "registration_enabled": False})
    with pytest.raises(AccountError) as error:
        accounts.register("policy-closed", "fixture-policy-password-123")
    assert error.value.status_code == 403


def test_postgres_legacy_token_caps_become_money_once_and_keep_zero_blocked(postgres_database):
    accounts = AccountsRepository(postgres_database)
    positive = accounts.register("legacy-positive", "fixture-policy-password-123")
    blocked = accounts.register("legacy-blocked", "fixture-policy-password-123")
    with postgres_database.connect() as connection:
        connection.execute("UPDATE account_settings SET body=? WHERE name='policy'", (json.dumps({
            "revision": 3, "registration_enabled": True, "default_token_limit": 100_000,
            "default_cost_limit_micros": 7_000_000}),))
        connection.execute("UPDATE accounts SET token_limit=100,cost_limit_micros=NULL WHERE id=?", (positive["id"],))
        connection.execute("UPDATE accounts SET token_limit=0,cost_limit_micros=NULL WHERE id=?", (blocked["id"],))
    migrated = AccountsRepository(postgres_database, default_cost_limit_micros=9_000_000)
    assert migrated.get_user(positive["id"])["quota"]["cost_limit_micros"] == 7_000_000
    assert migrated.get_user(blocked["id"])["quota"]["cost_limit_micros"] == 0
    assert "default_token_limit" not in migrated.policy() and migrated.policy()["revision"] == 4
    reopened = AccountsRepository(postgres_database, default_cost_limit_micros=1)
    assert reopened.policy() == migrated.policy()
    assert sum(item["action"] == "migrate_monetary_budgets" for item in reopened.audit_log()) == 1
