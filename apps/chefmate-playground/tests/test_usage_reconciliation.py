"""Money reconciliation is append-only, privileged, CAS-protected, and portable."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chefmate_playground.accounts import AccountError, AccountsRepository
from chefmate_playground.database import Database
from chefmate_playground.routes.accounts import build_router

PASSWORD = "fixture-reconciliation-password-123"


@pytest.fixture(params=("sqlite", "postgres"))
def ledger(tmp_path, request):
    url = os.environ.get("CHEFMATE_TEST_APP_DSN") if request.param == "postgres" else ""
    if request.param == "postgres" and not url:
        pytest.skip("CHEFMATE_TEST_APP_DSN is required for PostgreSQL reconciliation acceptance")
    database = Database(tmp_path / "accounts.db", url=url, schema="recon_test_" + uuid4().hex[:20])
    store = AccountsRepository(database)
    admin = store.bootstrap_admin("owner", PASSWORD)
    user = store.register("member", PASSWORD)
    store.update_user(admin["id"], user["id"], {"cost_limit_micros": 1000})
    store.update_pricing(admin["id"], {"input_per_million": "1", "output_per_million": "1",
                                      "cached_input_per_million": "1"})
    yield store, admin, user
    if url:
        import psycopg
        from psycopg import sql
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(database.schema)))


def unknown(store, user, turn="unknown"):
    store.reserve_usage(user["id"], turn, tokens=100, cost_micros=600)
    store.settle_usage(user["id"], turn, {"attempted_calls": 1, "calls": []}, status="error")


def test_admin_can_release_unknown_money_without_fabricating_provider_tokens(ledger):
    store, admin, user = ledger
    unknown(store, user)
    assert store.get_user(user["id"])["quota"]["balance_micros"] == 400
    response = store.reconcile_usage(admin["id"], user["id"], "unknown", revision=0,
        final_cost_micros=0, reason="已核对供应商记录，本次连接失败未收费。")
    entry, quota = response["entry"], response["user"]["quota"]
    assert response["ok"] and quota["balance_micros"] == 1000
    assert entry["total_tokens"] is None and entry["incomplete"]
    assert entry["cost_micros"] == 0 and entry["estimated_cost_micros"] == 600
    assert entry["effective_cost_micros"] == entry["effective_estimated_cost_micros"] == 0
    assert entry["reconciliation"]["revision"] == 1
    assert quota["estimated_cost_micros"] == 0 and quota["estimated_tokens"] == 100
    summary = store.usage_summary()["summary"]
    assert summary["cost_micros"] == summary["estimated_cost_micros"] == 0
    assert summary["reconciled_turns"] == summary["incomplete_turns"] == 1
    with store._connect() as connection:
        assert connection.execute("SELECT estimated_cost_micros FROM account_usage WHERE turn_id=?", ("unknown",)).fetchone()[0] == 600


def test_repeated_and_late_provider_observations_do_not_override_manual_money(ledger):
    store, admin, user = ledger
    usage = {"attempted_calls": 1, "calls": [{"input_tokens": 60, "output_tokens": 40, "total_tokens": 100}]}
    store.reserve_usage(user["id"], "known", tokens=200, cost_micros=200)
    store.settle_usage(user["id"], "known", usage)
    original = store.get_usage(user["id"])[0]
    store.reconcile_usage(admin["id"], user["id"], "known", revision=0, final_cost_micros=75,
                          reason="依据供应商确认的实际计入金额核对。")
    store.settle_usage(user["id"], "known", usage)
    late = {"attempted_calls": 1, "calls": [{"input_tokens": 80, "output_tokens": 40, "total_tokens": 120}]}
    store.settle_usage(user["id"], "known", late)
    entry = store.get_usage(user["id"])[0]
    assert entry["cost_micros"] == 120 and entry["effective_cost_micros"] == 75
    assert entry["pricing"] == original["pricing"]
    assert entry["total_tokens"] == 120  # Later real observations remain visible.
    assert store.get_user(user["id"])["quota"]["balance_micros"] == 925
    summary = store.usage_summary()["summary"]
    assert summary["cost_micros"] == 75 and summary["reported_cost_micros"] == 120
    assert summary["reconciled_turns"] == 1


def test_recovery_scan_cannot_overwrite_a_concurrent_real_final_settlement(ledger, monkeypatch):
    store, _, user = ledger
    store.reserve_usage(user["id"], "finishing", tokens=100, cost_micros=100)
    real_settle = store.settle_usage
    usage = {"attempted_calls": 1, "calls": [{"input_tokens": 6, "output_tokens": 4, "total_tokens": 10}]}

    def finish_after_scan(*args, **kwargs):
        # recover_reservations has already collected the pending row. A real
        # request now finishes before recovery acquires its write transaction.
        real_settle(user["id"], "finishing", usage, status="completed")
        return real_settle(*args, **kwargs)

    monkeypatch.setattr(store, "settle_usage", finish_after_scan)
    assert store.recover_reservations() == 0
    entry = store.get_usage(user["id"])[0]
    assert entry["status"] == "completed" and not entry["incomplete"]
    assert entry["total_tokens"] == entry["cost_micros"] == 10
    assert entry["estimated_cost_micros"] == entry["estimated_tokens"] == 0
    assert store.get_user(user["id"])["quota"]["balance_micros"] == 990


def test_concurrent_cas_and_revisions_preserve_every_manual_record(ledger):
    store, admin, user = ledger
    unknown(store, user)
    def reconcile(amount):
        try:
            return store.reconcile_usage(admin["id"], user["id"], "unknown", revision=0,
                                          final_cost_micros=amount, reason="并发核对测试依据。")
        except AccountError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reconcile, (100, 200)))
    assert sum(isinstance(result, dict) for result in results) == 1 and 409 in results
    store.reconcile_usage(admin["id"], user["id"], "unknown", revision=1, final_cost_micros=50,
                          reason="补充核对后更正为 50 微元。")
    history = store.reconciliations(user["id"], "unknown")
    assert [entry["revision"] for entry in history] == [2, 1]
    assert history[0]["final_cost_micros"] == 50
    assert store.get_user(user["id"])["quota"]["balance_micros"] == 950
    assert store.usage_summary()["summary"]["reconciled_turns"] == 1


def test_running_or_reserved_ledger_is_not_reconcilable(ledger):
    store, admin, user = ledger
    store.reserve_usage(user["id"], "running", tokens=100, cost_micros=100)
    usage = {"attempted_calls": 1, "calls": [{"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}]}
    store.settle_usage(user["id"], "running", usage, status="running")
    assert store.get_usage(user["id"])[0]["active"] is True
    with pytest.raises(AccountError) as error:
        store.reconcile_usage(admin["id"], user["id"], "running", revision=0, final_cost_micros=0, reason="尚不能核对。")
    assert error.value.status_code == 409
    store.settle_usage(user["id"], "running", usage, status="completed")
    assert store.reconcile_usage(admin["id"], user["id"], "running", revision=0,
        final_cost_micros=2, reason="完成后核对。") ["entry"]["active"] is False


def test_reconciliation_requires_admin_valid_money_and_reason(ledger):
    store, admin, user = ledger
    unknown(store, user)
    with pytest.raises(AccountError) as error:
        store.reconcile_usage(user["id"], user["id"], "unknown", revision=0, final_cost_micros=0, reason="无权限。")
    assert error.value.status_code == 403
    for patch in ({"revision": True}, {"final_cost_micros": -1}, {"final_cost_micros": 10**15 + 1},
                  {"reason": "  "}, {"reason": "x" * 501}):
        with pytest.raises(AccountError) as error:
            store.reconcile_usage(admin["id"], user["id"], "unknown", **{
                "revision": 0, "final_cost_micros": 0, "reason": "核对依据。", **patch})
        assert error.value.status_code == 422
    assert not store.reconciliations(user["id"], "unknown")


def test_http_reconciliation_contract_and_history_are_admin_only(ledger):
    store, _, user = ledger
    unknown(store, user)
    app = FastAPI()
    app.include_router(build_router(store))
    uri = f"/api/admin/users/{user['id']}/usage/unknown"
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "member", "password": PASSWORD})
        assert client.post(uri + "/reconcile", json={"revision": 0, "final_cost_micros": 0, "reason": "无权限。"}).status_code == 403
        client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
        before = client.get(f"/api/admin/users/{user['id']}/usage").json()["items"][0]
        assert before["turn_id"] == "unknown" and before["reconciliation"] is None
        response = client.post(uri + "/reconcile", json={"revision": 0, "final_cost_micros": 0, "reason": "供应商确认未收费。"})
        assert response.status_code == 200
        assert response.json()["entry"]["reconciliation"]["final_cost_micros"] == 0
        assert response.json()["user"]["quota"]["balance_micros"] == 1000
        assert len(client.get(uri + "/reconciliations").json()["items"]) == 1
        assert client.post(uri + "/reconcile", json={"revision": 0, "final_cost_micros": 500,
            "reason": "过期草稿。"}).status_code == 409
