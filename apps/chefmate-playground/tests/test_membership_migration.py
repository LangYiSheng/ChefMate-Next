"""A frozen SQLite membership ledger survives PostgreSQL's FK copy ordering."""

import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from chefmate_playground.accounts import AccountsRepository
from chefmate_playground.database import Database
from chefmate_playground.migrate_data import migrate_runtime


TABLES = ("account_credit_grants", "account_memberships", "account_membership_windows", "account_budget_turns",
          "account_budget_portions", "account_budget_allocations")


def fingerprints(root):
    return {str(file.relative_to(root)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in root.rglob("*") if file.is_file()}


def ledger_rows(accounts):
    with accounts._connect() as db:
        return {table: sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) for row in db.execute(
            "SELECT * FROM " + table)) for table in TABLES}


def test_memberships_windows_allocations_and_pending_reservations_migrate_exactly(tmp_path):
    url = os.environ.get("CHEFMATE_TEST_APP_DSN", "")
    if not url:
        pytest.skip("CHEFMATE_TEST_APP_DSN is required for actual ledger migration")
    prefix = "membermig_" + uuid4().hex[:10]
    source, target = tmp_path / "source", tmp_path / "target"
    instant = datetime.now(timezone.utc)
    accounts = AccountsRepository(source / "accounts.db", now=lambda: instant)
    owner = accounts.bootstrap_admin("owner", "migration-member-password-123")
    user = accounts.register("cook", "migration-member-password-123")
    accounts.update_pricing(owner["id"], {"input_per_million": "1", "output_per_million": "1", "cached_input_per_million": "1"})
    accounts.grant_membership(owner["id"], user["id"], label="30天 ChefMate会员", duration_days=30,
        weekly_limit_micros=10_000_000, five_hour_limit_micros=2_000_000, request_id="member-fixture")
    accounts.grant_credit(owner["id"], user["id"], amount_micros=10_000_000, label="额外充值额度", request_id="credit-fixture")
    for index, amount in enumerate((1_500_000, 750_000, 5_000_000)):
        turn_id = "settled-" + str(index)
        accounts.reserve_usage(user["id"], turn_id, tokens=amount, cost_micros=amount)
        accounts.settle_usage(user["id"], turn_id, {"attempted_calls": 1,
            "calls": [{"input_tokens": amount, "output_tokens": 0, "total_tokens": amount}]})
    accounts.reserve_usage(user["id"], "pending-fixture", tokens=200_000, cost_micros=200_000)
    before_rows = ledger_rows(accounts)
    before_funds = accounts.get_entitlements(user["id"])["entitlements"]
    assert all(before_rows.values())
    assert before_funds["memberships"][0]["used_micros"] == 2_000_000
    assert [item["used_micros"] for item in before_funds["permanent"]["sources"]] == [5_000_000, 250_000]
    source_hashes = fingerprints(source)
    try:
        result = migrate_runtime(source, target, url, schema_prefix=prefix)
        assert result["ok"] and result["source_preserved"]
        assert source_hashes == fingerprints(source)
        copied = AccountsRepository(Database(target / "accounts.db", url=url, schema=prefix + "_accounts"), now=lambda: instant)
        assert ledger_rows(copied) == before_rows
        assert copied.get_entitlements(user["id"])["entitlements"] == before_funds
        assert copied.reservation_pricing(user["id"], "pending-fixture") == accounts.reservation_pricing(user["id"], "pending-fixture")
        assert source_hashes == fingerprints(source)
        # A repeat startup must not mint another welcome grant or duplicate allocations.
        again = AccountsRepository(copied.database, now=lambda: instant)
        assert ledger_rows(again) == before_rows
        assert not list(target.rglob("*.db"))
    finally:
        with psycopg.connect(url) as db:
            names = [row[0] for row in db.execute("SELECT schema_name FROM information_schema.schemata")
                     if row[0].startswith(prefix + "_")]
            for name in names:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
