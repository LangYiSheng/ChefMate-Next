"""Real SQLite/PostgreSQL money allocation, rollover, reconciliation and recovery."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql

from chefmate_playground.accounts import AccountError, AccountsRepository, QuotaExceeded
from chefmate_playground.database import Database
from chefmate_playground.routes.accounts import build_router as auth_router
from chefmate_playground.routes.entitlements import build_router


PASSWORD = "membership-fixture-password-123"


@pytest.fixture(params=["sqlite", "postgres"])
def billing(request, tmp_path):
    schema = "billing_" + uuid4().hex[:24]
    dsn = os.environ.get("CHEFMATE_TEST_APP_DSN", "") if request.param == "postgres" else ""
    if request.param == "postgres" and not dsn:
        pytest.skip("Set CHEFMATE_TEST_APP_DSN for isolated PostgreSQL billing tests")
    clock = [datetime(2026, 10, 27, tzinfo=timezone.utc)]
    try:
        store = AccountsRepository(Database(tmp_path / "accounts.db", url=dsn, schema=schema), now=lambda: clock[0])
        admin, user = store.bootstrap_admin("owner", PASSWORD), store.register("member", PASSWORD)
        store.update_pricing(admin["id"], {"input_per_million": "1", "cached_input_per_million": "1", "output_per_million": "1"})
        yield store, admin, user, clock
    finally:
        if dsn:
            with psycopg.connect(dsn) as connection:
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def funds(case):
    store, _, user, _ = case
    return store.get_entitlements(user["id"])["entitlements"]


def member(case, *, weekly=10_000_000, hourly=2_000_000, days=30, request_id=None):
    store, admin, user, _ = case
    result = store.grant_membership(admin["id"], user["id"], label="ChefMate 会员", duration_days=days,
        weekly_limit_micros=weekly, five_hour_limit_micros=hourly, request_id=request_id or uuid4().hex)
    return result["entitlements"]["memberships"][-1]


def base(case, amount):
    store, admin, user, _ = case
    store.update_user(admin["id"], user["id"], {"cost_limit_micros": amount})


def credit(case, amount=10_000_000, request_id=None):
    store, admin, user, _ = case
    return store.grant_credit(admin["id"], user["id"], amount_micros=amount,
                              label="额外充值额度", request_id=request_id or uuid4().hex)


def charge(case, amount, *, turn=None):
    store, _, user, _ = case
    turn = turn or uuid4().hex
    store.reserve_usage(user["id"], turn, tokens=max(1, amount), cost_micros=amount)
    store.settle_usage(user["id"], turn, {"attempted_calls": 1,
        "calls": [{"input_tokens": amount, "output_tokens": 0, "total_tokens": amount}]})
    return turn


def test_default_base_is_five_yuan_and_admin_remains_unlimited(billing):
    store, admin, user, _ = billing
    value = funds(billing)
    assert value["available_micros"] == 5_000_000 and not value["memberships"]
    assert value["permanent"]["sources"][0]["kind"] == "base"
    assert store.get_user(admin["id"])["quota"]["funds"]["available_micros"] is None
    assert store.get_user(user["id"])["quota"]["token_limit"] is None


def test_example_spends_limited_membership_then_base_then_topup(billing):
    member(billing)
    credit(billing)
    assert funds(billing)["available_micros"] == 17_000_000
    charge(billing, 1_500_000)
    charge(billing, 1_000_000)
    charge(billing, 5_000_000)
    value = funds(billing)
    assert value["memberships"][0]["used_micros"] == 2_000_000
    assert [source["used_micros"] for source in value["permanent"]["sources"]] == [5_000_000, 500_000]
    assert value["available_micros"] == 9_500_000


def test_five_hour_renewal_is_still_limited_by_weekly_budget(billing):
    store, _, _, clock = billing
    member(billing, weekly=3_000_000)
    charge(billing, 2_000_000)
    clock[0] += timedelta(hours=5, seconds=1)
    charge(billing, 2_000_000)
    value = funds(billing)
    assert value["memberships"][0]["week"]["used_micros"] == 3_000_000
    assert value["memberships"][0]["five_hour"]["used_micros"] == 1_000_000
    assert value["memberships"][0]["available_micros"] == 0
    assert value["permanent"]["used_micros"] == 1_000_000
    clock[0] += timedelta(days=7)
    assert funds(billing)["memberships"][0]["available_micros"] == 2_000_000
    assert store.get_entitlements(billing[2]["id"])["entitlements"]["available_micros"] == 6_000_000


def test_windows_start_only_at_first_positive_reservation_and_stop_at_week_boundary(billing):
    store, _, user, clock = billing
    member(billing)
    assert funds(billing)["memberships"][0]["week"]["starts_at"] is None
    clock[0] += timedelta(days=2)
    store.reserve_usage(user["id"], "zero")
    assert funds(billing)["memberships"][0]["week"]["starts_at"] is None
    store.release_usage(user["id"], "zero")
    charge(billing, 1)
    start = clock[0]
    clock[0] += timedelta(days=6, hours=23)
    charge(billing, 1)
    membership = funds(billing)["memberships"][0]
    assert datetime.fromisoformat(membership["week"]["starts_at"]) == start
    assert membership["five_hour"]["resets_at"] == membership["week"]["resets_at"]


def test_expiration_uses_permanent_credit_and_keeps_history(billing):
    _, _, _, clock = billing
    member(billing, days=1)
    charge(billing, 10)
    clock[0] += timedelta(days=1)
    charge(billing, 20)
    value = funds(billing)
    assert value["memberships"][0]["status"] == "expired"
    assert value["memberships"][0]["used_micros"] == 10
    assert value["memberships"][0]["available_micros"] == 0
    assert value["permanent"]["used_micros"] == 20


def test_multiple_memberships_spend_earliest_expiry_first(billing):
    member(billing, days=30)
    member(billing, days=2)
    charge(billing, 50)
    value = funds(billing)
    assert value["memberships"][0]["duration_days"] == 2
    assert [item["used_micros"] for item in value["memberships"]] == [50, 0]
    assert value["memberships"][1]["week"]["starts_at"] is None
    assert value["memberships"][1]["five_hour"]["starts_at"] is None


def test_concurrent_reservation_cannot_overbook_shared_window(billing):
    store, _, user, _ = billing
    base(billing, 0)
    member(billing, weekly=100, hourly=100)
    store.reserve_usage(user["id"], "first")
    store.reserve_usage(user["id"], "second")
    def attempt(turn):
        try:
            store.resize_usage(user["id"], turn, tokens=80, cost_micros=80)
            return True
        except QuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, ("first", "second"))) == 2
    value = funds(billing)
    assert value["available_micros"] == 0
    assert value["memberships"][0]["reserved_micros"] == 100
    with pytest.raises(QuotaExceeded):
        store.reserve_usage(user["id"], "third", cost_micros=1)
    store.release_usage(user["id"], "first")
    store.release_usage(user["id"], "second")
    assert funds(billing)["available_micros"] == 100


def test_late_round_refund_stays_in_its_original_five_hour_window(billing):
    store, admin, user, clock = billing
    base(billing, 0)
    member(billing, weekly=10, hourly=2)
    seed = charge(billing, 1)
    store.reconcile_usage(admin["id"], user["id"], seed, revision=0, final_cost_micros=0, reason="首轮免费核对")
    clock[0] += timedelta(hours=4, minutes=59)
    store.reserve_usage(user["id"], "rounds")
    store.resize_usage(user["id"], "rounds", tokens=2, cost_micros=2, cost_portions={"1": 2})
    store.resize_usage(user["id"], "rounds", tokens=1, cost_micros=1, cost_portions={"1": 1}, observed=True)
    clock[0] += timedelta(minutes=2)
    store.resize_usage(user["id"], "rounds", tokens=3, cost_micros=3, cost_portions={"1": 1, "2": 2})
    store.resize_usage(user["id"], "rounds", tokens=2, cost_micros=2, cost_portions={"1": 0, "2": 2}, observed=True)
    value = funds(billing)
    assert value["memberships"][0]["five_hour"]["reserved_micros"] == 2
    assert value["memberships"][0]["week"]["reserved_micros"] == 2
    assert value["available_micros"] == 0


def test_whole_turn_reservation_can_become_round_portions_without_duplicate_budget(billing):
    store, _, user, _ = billing
    base(billing, 0)
    member(billing, weekly=2, hourly=2)
    store.reserve_usage(user["id"], "remap", cost_micros=2)
    store.resize_usage(user["id"], "remap", tokens=2, cost_micros=2, cost_portions={"round-1": 2})
    value = funds(billing)
    assert value["memberships"][0]["reserved_micros"] == 2
    assert value["permanent"]["reserved_micros"] == 0


def test_unadmitted_observation_cannot_start_a_fresh_membership_window(billing):
    store, _, user, clock = billing
    member(billing)
    store.reserve_usage(user["id"], "unexpected")
    clock[0] += timedelta(minutes=1)
    store.resize_usage(user["id"], "unexpected", tokens=2, cost_micros=2,
        cost_portions={"unadmitted-round": 2}, observed=True)
    value = funds(billing)
    assert value["memberships"][0]["week"]["starts_at"] is None
    assert value["permanent"]["reserved_micros"] == 2


def test_settlement_increase_does_not_reuse_an_inactive_remapped_portion(billing):
    store, admin, user, clock = billing
    base(billing, 0)
    member(billing, weekly=10, hourly=2)
    seed = charge(billing, 1)
    store.reconcile_usage(admin["id"], user["id"], seed, revision=0, final_cost_micros=0, reason="首轮免费核对")
    clock[0] += timedelta(hours=4, minutes=59)
    store.reserve_usage(user["id"], "remapped", cost_micros=2)
    clock[0] += timedelta(minutes=2)
    store.resize_usage(user["id"], "remapped", tokens=2, cost_micros=2, cost_portions={"new-round": 2})
    store.settle_usage(user["id"], "remapped", {"calls": [{"input_tokens": 4, "output_tokens": 0, "total_tokens": 4}]})
    value = funds(billing)
    assert value["memberships"][0]["used_micros"] == 2
    assert value["permanent"]["used_micros"] == 2


def test_reconciliation_never_refunds_into_new_window_and_raw_usage_stays(billing):
    store, admin, user, clock = billing
    member(billing)
    turn = charge(billing, 2_000_000)
    clock[0] += timedelta(hours=5, seconds=1)
    before = funds(billing)["memberships"][0]["five_hour"]
    result = store.reconcile_usage(admin["id"], user["id"], turn, revision=0, final_cost_micros=0, reason="供应商核对退款")
    after = funds(billing)["memberships"][0]["five_hour"]
    assert before == after and after["starts_at"] is None
    assert result["entry"]["cost_micros"] == 2_000_000
    store.settle_usage(user["id"], turn, {"calls": [{"input_tokens": 2_100_000, "output_tokens": 0, "total_tokens": 2_100_000}]})
    assert funds(billing)["memberships"][0]["used_micros"] == 0


def test_reconciliation_refunds_permanent_tail_before_member_and_replay_does_not_add(billing):
    store, admin, user, _ = billing
    member(billing, weekly=100, hourly=2)
    turn = charge(billing, 3)
    result = store.reconcile_usage(admin["id"], user["id"], turn, revision=0, final_cost_micros=1, reason="核对金额")
    assert result["user"]["quota"]["funds"]["permanent"]["used_micros"] == 0
    assert funds(billing)["memberships"][0]["used_micros"] == 1
    with pytest.raises(AccountError) as error:
        store.reconcile_usage(admin["id"], user["id"], turn, revision=0, final_cost_micros=0, reason="旧版本")
    assert error.value.status_code == 409


def test_refund_and_later_recharge_cannot_overbook_old_membership_window(billing):
    store, admin, user, _ = billing
    base(billing, 0)
    member(billing, weekly=2, hourly=2)
    first = charge(billing, 2)
    store.reconcile_usage(admin["id"], user["id"], first, revision=0, final_cost_micros=0, reason="退款")
    charge(billing, 2)
    store.reconcile_usage(admin["id"], user["id"], first, revision=1, final_cost_micros=2, reason="退款撤销")
    value = funds(billing)
    assert value["memberships"][0]["week"]["used_micros"] == 2
    assert value["permanent"]["overdrawn_micros"] == 2
    assert value["available_micros"] == 0


def test_actual_overrun_becomes_debt_and_new_credit_covers_it(billing):
    store, _, user, _ = billing
    base(billing, 10)
    store.reserve_usage(user["id"], "overrun", cost_micros=10)
    store.resize_usage(user["id"], "overrun", tokens=20, cost_micros=20, observed=True)
    store.settle_usage(user["id"], "overrun", {"calls": [{"input_tokens": 20, "output_tokens": 0, "total_tokens": 20}]})
    assert funds(billing)["permanent"]["overdrawn_micros"] == 10
    credit(billing, 15)
    assert funds(billing)["available_micros"] == 5
    store.reserve_usage(user["id"], "last-positive", cost_micros=6)
    assert funds(billing)["available_micros"] == 0
    assert funds(billing)["permanent"]["reserved_micros"] == 5
    with pytest.raises(QuotaExceeded):
        store.reserve_usage(user["id"], "nothing-left", cost_micros=1)


def test_one_micro_balance_can_admit_a_large_estimate_then_settles_exact_cost(billing):
    store, _, user, _ = billing
    base(billing, 1)
    store.reserve_usage(user["id"], "last-micro", tokens=100, cost_micros=100)
    quota = store.get_user(user["id"])["quota"]
    assert quota["reserved_cost_micros"] == 1 and quota["funds"]["permanent"]["used_micros"] == 0
    assert store.reservation_pricing(user["id"], "last-micro")["configured"]
    with pytest.raises(QuotaExceeded, match="等待结算"):
        store.reserve_usage(user["id"], "concurrent", cost_micros=1)
    usage = {"calls": [{"input_tokens": 2, "output_tokens": 0, "total_tokens": 2}]}
    store.settle_usage(user["id"], "last-micro", usage)
    store.settle_usage(user["id"], "last-micro", usage)
    value = funds(billing)
    assert value["permanent"]["used_micros"] == 2
    assert value["permanent"]["overdrawn_micros"] == 1 and value["available_micros"] == 0
    with pytest.raises(QuotaExceeded, match="联系管理员"):
        store.reserve_usage(user["id"], "after-zero", cost_micros=1)


def test_zero_real_cost_releases_the_last_balance_without_marking_it_used(billing):
    store, _, user, _ = billing
    base(billing, 1)
    store.reserve_usage(user["id"], "free-response", cost_micros=100)
    store.settle_usage(user["id"], "free-response", {"calls": [{"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}]})
    value = funds(billing)
    assert value["permanent"]["used_micros"] == 0 and value["available_micros"] == 1
    store.reserve_usage(user["id"], "still-positive", cost_micros=100)
    assert funds(billing)["permanent"]["reserved_micros"] == 1


def test_capped_estimate_retains_membership_base_topup_order_and_refunds_exactly(billing):
    store, admin, user, _ = billing
    base(billing, 1)
    member(billing, weekly=2, hourly=2)
    credit(billing, 1)
    store.reserve_usage(user["id"], "last-funds", tokens=100, cost_micros=100)
    held = funds(billing)
    assert held["memberships"][0]["reserved_micros"] == 2
    assert [source["reserved_micros"] for source in held["permanent"]["sources"]] == [1, 1]
    store.settle_usage(user["id"], "last-funds", {"calls": [{"input_tokens": 7, "output_tokens": 0, "total_tokens": 7}]})
    value = funds(billing)
    assert value["memberships"][0]["used_micros"] == 2
    assert [source["used_micros"] for source in value["permanent"]["sources"]] == [4, 1]
    assert value["permanent"]["overdrawn_micros"] == 3
    store.reconcile_usage(admin["id"], user["id"], "last-funds", revision=0, final_cost_micros=1, reason="核对最终费用")
    refunded = funds(billing)
    assert refunded["memberships"][0]["used_micros"] == 1
    assert refunded["permanent"]["used_micros"] == 0 and refunded["available_micros"] == 3


def test_recovery_keeps_full_unknown_estimate_after_last_balance_was_collateralized(billing):
    store, _, user, clock = billing
    base(billing, 1)
    store.reserve_usage(user["id"], "unknown-last", tokens=100, cost_micros=100)
    assert funds(billing)["permanent"]["reserved_micros"] == 1
    reopened = AccountsRepository(store.database, now=lambda: clock[0])
    assert reopened.recover_reservations() == 1
    quota = reopened.get_user(user["id"])["quota"]
    assert quota["reserved_cost_micros"] == 0
    assert quota["estimated_cost_micros"] == 100
    assert quota["funds"]["permanent"]["overdrawn_micros"] == 99


def test_concurrent_last_micro_is_collateralized_once(billing):
    store, _, user, _ = billing
    base(billing, 1)

    def attempt(index):
        try:
            store.reserve_usage(user["id"], f"tail-{index}", cost_micros=100)
            return True
        except QuotaExceeded:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1
    assert funds(billing)["permanent"]["reserved_micros"] == 1


def test_grants_are_idempotent_and_reusing_request_with_different_body_is_conflict(billing):
    credit(billing, 10, request_id="one-topup")
    credit(billing, 10, request_id="one-topup")
    assert len(funds(billing)["permanent"]["sources"]) == 2
    with pytest.raises(AccountError) as error:
        credit(billing, 11, request_id="one-topup")
    assert error.value.status_code == 409
    member(billing, request_id="one-membership")
    member(billing, request_id="one-membership")
    assert len(funds(billing)["memberships"]) == 1


def test_revoking_membership_keeps_old_reservation_but_stops_future_admission(billing):
    store, admin, user, _ = billing
    base(billing, 0)
    value = member(billing, weekly=10, hourly=2)
    store.reserve_usage(user["id"], "admitted", cost_micros=1)
    store.revoke_membership(admin["id"], user["id"], value["id"], revision=1, reason="停止续用")
    assert funds(billing)["memberships"][0]["reserved_micros"] == 1
    assert funds(billing)["memberships"][0]["revocation_reason"] == "停止续用"
    with pytest.raises(QuotaExceeded):
        store.reserve_usage(user["id"], "next", cost_micros=1)
    store.settle_usage(user["id"], "admitted", {"calls": [{"input_tokens": 1, "output_tokens": 0, "total_tokens": 1}]})
    assert funds(billing)["memberships"][0]["used_micros"] == 1


def test_recovery_retains_unknown_amount_in_original_scope(billing):
    store, _, user, clock = billing
    member(billing)
    store.reserve_usage(user["id"], "crash", tokens=20, cost_micros=10)
    restored = AccountsRepository(store.database, now=lambda: clock[0])
    assert restored.recover_reservations() == 1
    value = restored.get_user(user["id"])["quota"]["funds"]["memberships"][0]
    assert value["estimated_micros"] == value["used_micros"] == 10
    assert value["reserved_micros"] == 0


def test_model_profile_pricing_override_is_immutable_and_validated(billing):
    store, admin, user, _ = billing
    quote = {"currency": "CNY", "configured": True, "input_per_million": "2", "output_per_million": "3",
             "cached_input_per_million": "1", "model_profile_id": "a" * 32, "model_profile_label": "被测模型"}
    store.reserve_usage(user["id"], "profile", cost_micros=100, pricing_override=quote)
    store.update_pricing(admin["id"], {"input_per_million": "99"})
    store.settle_usage(user["id"], "profile", {"calls": [{"input_tokens": 10, "output_tokens": 0, "total_tokens": 10}]})
    row = store.get_usage(user["id"])[0]
    assert row["cost_micros"] == 20 and row["pricing"]["model_profile_id"] == "a" * 32
    with pytest.raises(AccountError):
        store.reserve_usage(user["id"], "bad", pricing_override={**quote, "api_key": "must-not-accept"})


def test_old_usage_and_pending_reservations_migrate_without_new_credit(billing):
    store, _, user, clock = billing
    charge(billing, 100)
    store.reserve_usage(user["id"], "pending", cost_micros=50)
    with store._connect() as db:
        db.execute("DELETE FROM account_budget_allocations")
        db.execute("DELETE FROM account_budget_portions")
        db.execute("DELETE FROM account_budget_turns")
        db.execute("DELETE FROM account_credit_grants")
    reopened = AccountsRepository(store.database, now=lambda: clock[0])
    value = reopened.get_user(user["id"])["quota"]["funds"]
    assert value["available_micros"] == 4_999_850
    assert value["permanent"]["used_micros"] == 100 and value["permanent"]["reserved_micros"] == 50
    assert len(value["permanent"]["sources"]) == 1


def test_finance_http_is_admin_only_and_uses_server_identity(billing):
    store, admin, user, _ = billing
    app = FastAPI()
    app.include_router(auth_router(store))
    app.include_router(build_router(store))
    with TestClient(app) as owner, TestClient(app) as member_client:
        assert member_client.get("/api/auth/entitlements").status_code == 401
        owner.post("/api/auth/login", json={"username": admin["username"], "password": PASSWORD})
        member_client.post("/api/auth/login", json={"username": user["username"], "password": PASSWORD})
        url = "/api/admin/users/" + user["id"] + "/credit-grants"
        body = {"amount_micros": 10, "label": "测试充值", "request_id": "http-credit"}
        assert member_client.post(url, json=body).status_code == 403
        assert owner.post(url, json=body).status_code == 200
        assert owner.post(url, json=body).status_code == 200
        assert member_client.get("/api/auth/entitlements").json()["user"]["id"] == user["id"]
        assert owner.get("/api/admin/users/" + user["id"] + "/entitlements").json()["entitlements"]["available_micros"] == 5_000_010
