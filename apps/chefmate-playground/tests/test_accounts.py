"""Identity, privilege, reservation, and accounting regression tests."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chefmate_playground.accounts import (
    AccountError, AccountsRepository, COOKIE_NAME, QuotaExceeded, hash_password, verify_password,
)
from chefmate_playground.routes.accounts import build_router


PASSWORD = "correct horse soup 123"


@pytest.fixture
def accounts(tmp_path):
    store = AccountsRepository(tmp_path / "accounts.db")
    configure_fixture_prices(store)
    return store


def configure_fixture_prices(store):
    """Accounting fixtures explicitly price tokens; production starts unset."""
    with store._connect() as connection:
        prices = json.loads(connection.execute("SELECT body FROM account_settings WHERE name='pricing'").fetchone()[0])
        prices.update(configured=True, input_per_million="1", output_per_million="1", cached_input_per_million="1")
        connection.execute("UPDATE account_settings SET body=? WHERE name='pricing'", (json.dumps(prices),))


def admin_and_user(accounts):
    return accounts.bootstrap_admin("administrator", PASSWORD), accounts.register("cook", PASSWORD)


def client_for(accounts, **options):
    app = FastAPI()
    app.include_router(build_router(accounts, **options))
    return TestClient(app)


def test_hashing_is_salted_and_has_no_plaintext():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong", first)
    assert not verify_password(PASSWORD, "broken")


def test_registration_never_grants_admin_and_names_are_normalized(accounts):
    user = accounts.register("  Cook.1  ", PASSWORD)
    assert user["username"] == "cook.1"
    assert user["role"] == "user"
    assert not user["developer_allowed"]
    assert not user["onboarding_completed"]
    assert user["quota"]["token_limit"] is None
    assert user["quota"]["cost_limit_micros"] == user["quota"]["balance_micros"] == 5_000_000
    assert "password_hash" not in user
    with pytest.raises(AccountError) as error:
        accounts.register("COOK.1", PASSWORD)
    assert error.value.status_code == 409


@pytest.mark.parametrize("username", ["aa", "a b", "../cook", "x" * 33, "厨师"])
def test_invalid_account_names_fail(accounts, username):
    with pytest.raises(AccountError):
        accounts.register(username, PASSWORD)


@pytest.mark.parametrize("password", ["short", " " * 12, "x" * 129])
def test_invalid_passwords_fail(accounts, password):
    with pytest.raises(AccountError):
        accounts.register("cook", password)


def test_bootstrap_is_explicit_and_does_not_reset_existing_credentials(accounts):
    user = accounts.bootstrap_admin("admin", PASSWORD)
    accounts.change_password(user["id"], PASSWORD, "another password 123")
    assert accounts.bootstrap_admin("admin", PASSWORD)["id"] == user["id"]
    assert accounts.login("admin", "another password 123")["role"] == "admin"
    accounts.register("ordinary", PASSWORD)
    with pytest.raises(AccountError):
        accounts.bootstrap_admin("ordinary", PASSWORD)


def test_opaque_sessions_expire_revoke_and_never_store_the_token(tmp_path):
    instant = [datetime(2026, 10, 27, tzinfo=timezone.utc)]
    store = AccountsRepository(tmp_path / "accounts.db", now=lambda: instant[0])
    user = store.register("cook", PASSWORD)
    token = store.issue_session(user["id"])
    assert store.authenticate_session(token)["id"] == user["id"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT token_hash FROM account_sessions").fetchone()[0] != token
    store.revoke_session(token)
    assert store.authenticate_session(token) is None
    token = store.issue_session(user["id"])
    instant[0] += timedelta(days=8)
    assert store.authenticate_session(token) is None


def test_password_and_disable_revoke_sessions_and_admin_invariant(accounts):
    admin, user = admin_and_user(accounts)
    token = accounts.issue_session(user["id"])
    accounts.change_password(user["id"], PASSWORD, "new secure password 123")
    assert accounts.authenticate_session(token) is None
    token = accounts.issue_session(user["id"])
    accounts.update_user(admin["id"], user["id"], {"disabled": True})
    assert accounts.authenticate_session(token) is None
    with pytest.raises(AccountError):
        accounts.login("cook", "new secure password 123")
    with pytest.raises(AccountError) as error:
        accounts.update_user(admin["id"], admin["id"], {"disabled": True})
    assert error.value.status_code == 409
    with pytest.raises(AccountError):
        accounts.update_user(admin["id"], admin["id"], {"role": "user"})


def test_password_reset_racing_a_login_cannot_issue_a_new_old_credential_session(accounts):
    admin, user = admin_and_user(accounts)
    validated = accounts.login("cook", PASSWORD)
    accounts.reset_password(admin["id"], user["id"], "replacement secret 123")
    with pytest.raises(AccountError) as error:
        accounts.issue_session(user["id"], expected_revision=validated["session_revision"])
    assert error.value.status_code == 401


def test_login_uses_verified_revision_even_when_reset_races_during_password_check(tmp_path, monkeypatch):
    from chefmate_playground import accounts as account_module
    instant = datetime(2026, 10, 27, tzinfo=timezone.utc)
    store = AccountsRepository(tmp_path / "accounts.db", now=lambda: instant)
    admin, user = admin_and_user(store)
    original = account_module.verify_password
    def verify_and_reset(password, encoded):
        valid = original(password, encoded)
        store.reset_password(admin["id"], user["id"], "replacement secret 123")
        return valid
    monkeypatch.setattr(account_module, "verify_password", verify_and_reset)
    validated = store.login("cook", PASSWORD)
    assert validated["updated_at"] == store.get_user(user["id"])["updated_at"]
    with pytest.raises(AccountError):
        store.issue_session(user["id"], expected_revision=validated["session_revision"])


def test_only_admin_can_update_users_and_audit_never_contains_password(accounts):
    admin, user = admin_and_user(accounts)
    with pytest.raises(AccountError):
        accounts.update_user(user["id"], user["id"], {"role": "admin"})
    accounts.update_user(admin["id"], user["id"], {"developer_allowed": True, "cost_limit_micros": 1234})
    accounts.reset_password(admin["id"], user["id"], "reset password 123")
    assert accounts.login("cook", "reset password 123")["developer_allowed"]
    assert "reset password 123" not in str(accounts.audit_log())


def test_concurrent_admin_self_demotion_always_leaves_an_active_admin(accounts):
    first = accounts.bootstrap_admin("admin-one", PASSWORD)
    second = accounts.bootstrap_admin("admin-two", PASSWORD)
    def demote(user):
        try:
            accounts.update_user(user["id"], user["id"], {"role": "user"})
            return True
        except AccountError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, (first, second)))
    assert sum(results) == 1
    assert sum(user["role"] == "admin" and not user["disabled"] for user in accounts.list_users()) == 1


def test_onboarding_profile_is_durable_and_cannot_set_privileges(accounts):
    user = accounts.register("cook", PASSWORD)
    updated = accounts.complete_onboarding(user["id"], {"display_name": "小厨师", "household_size": 2,
        "cooking_level": "comfortable", "preferences": ["清淡", "清淡"], "theme": "dark"})
    assert updated["onboarding_completed"]
    assert updated["profile"]["preferences"] == ["清淡"]
    assert updated["profile"]["use_for_ai"] is False
    assert AccountsRepository(accounts.path).get_user(user["id"])["profile"]["household_size"] == 2
    with pytest.raises(AccountError):
        accounts.complete_onboarding(user["id"], {"display_name": "坏厨师", "role": "admin"})
    assert accounts.complete_onboarding(user["id"], {"display_name": "小厨师", "use_for_ai": True})["profile"]["use_for_ai"] is True
    assert accounts.complete_onboarding(user["id"], {"display_name": "小厨师", "use_for_ai": False})["profile"]["use_for_ai"] is False
    with pytest.raises(AccountError):
        accounts.complete_onboarding(user["id"], {"display_name": "小厨师", "use_for_ai": "true"})


def test_tenant_paths_are_opaque_and_contained(accounts):
    user = accounts.register("cook", PASSWORD)
    path = accounts.tenant_database(user["id"])
    assert path == accounts.path.parent / "users" / user["id"] / "sessions.db"
    with pytest.raises(AccountError):
        accounts.tenant_database("../../legacy")


def test_reservations_are_atomic_across_concurrent_admission(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": 100})
    def admit(index):
        try:
            accounts.reserve_usage(user["id"], f"turn-{index}", tokens=60, cost_micros=60)
            return True
        except QuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(admit, range(4)))
    assert sum(results) == 1
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 60


def test_resize_excludes_its_previous_bound_but_includes_other_turns(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": 100})
    accounts.reserve_usage(user["id"], "first", tokens=20, cost_micros=20)
    accounts.reserve_usage(user["id"], "second", tokens=30, cost_micros=30)
    accounts.resize_usage(user["id"], "first", tokens=70, cost_micros=70)
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 100
    with pytest.raises(QuotaExceeded):
        accounts.resize_usage(user["id"], "first", tokens=71, cost_micros=71)
    accounts.release_usage(user["id"], "second")
    accounts.resize_usage(user["id"], "first", tokens=90, cost_micros=90)


def test_concurrent_reservation_growth_cannot_overbook(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": 100})
    accounts.reserve_usage(user["id"], "first")
    accounts.reserve_usage(user["id"], "second")
    def resize(turn):
        try:
            accounts.resize_usage(user["id"], turn, tokens=60, cost_micros=60)
            return True
        except QuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resize, ("first", "second")))
    assert sum(results) == 1
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 61


def test_observed_provider_overrun_is_recorded_but_cannot_admit_the_next_call(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": 10})
    accounts.reserve_usage(user["id"], "first", tokens=5, cost_micros=5)
    accounts.resize_usage(user["id"], "first", tokens=20, cost_micros=20, observed=True)
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["reserved_tokens"] == quota["reserved_cost_micros"] == 20
    with pytest.raises(QuotaExceeded):
        accounts.reserve_usage(user["id"], "second")
    with pytest.raises(QuotaExceeded):
        accounts.resize_usage(user["id"], "first", tokens=21, cost_micros=21)


def test_ledger_uses_admission_price_snapshot_and_replay_is_not_double_charged(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_pricing(admin["id"], {"input_per_million": "1.5", "output_per_million": "3",
                                            "cached_input_per_million": "0.5"})
    accounts.reserve_usage(user["id"], "turn", tokens=1000)
    accounts.update_pricing(admin["id"], {"input_per_million": "100"})
    usage = {"attempted_calls": 1, "calls": [{"input_tokens": 100, "output_tokens": 50,
                                                  "cached_tokens": 20, "total_tokens": 150}]}
    first = accounts.settle_usage(user["id"], "turn", usage, model="test")
    assert first["cost_micros"] == 280
    assert not first["incomplete"]
    assert first["estimated_tokens"] == 0
    accounts.settle_usage(user["id"], "turn", usage, model="test")
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["used_tokens"] == 150
    assert quota["used_cost_micros"] == 280
    assert quota["reserved_tokens"] == 0
    assert len(accounts.get_usage(user["id"])) == 1


def test_unknown_usage_retains_separate_quota_estimate(accounts):
    _, user = admin_and_user(accounts)
    accounts.reserve_usage(user["id"], "turn", tokens=1000, cost_micros=2000)
    result = accounts.settle_usage(user["id"], "turn", {"attempted_calls": 1, "calls": []})
    assert result["incomplete"]
    assert result["total_tokens"] is None
    assert result["estimated_tokens"] == 1000
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["used_tokens"] == 0
    assert quota["quota_used_tokens"] == 1000
    assert quota["estimated_cost_micros"] == 2000
    assert quota["reserved_tokens"] == 0
    summary = accounts.usage_summary()["summary"]
    assert summary["incomplete_turns"] == 1


def test_zero_attempt_failure_releases_admission_without_making_unknown_usage(accounts):
    user = accounts.register("cook", PASSWORD)
    accounts.reserve_usage(user["id"], "turn", tokens=100)
    accounts.settle_usage(user["id"], "turn", {"attempted_calls": 0, "calls": []}, status="error")
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["reserved_tokens"] == quota["quota_used_tokens"] == 0
    assert not accounts.get_usage(user["id"])


def test_restart_and_expiry_recovery_keep_unknown_admitted_costs(tmp_path):
    instant = [datetime(2026, 10, 27, tzinfo=timezone.utc)]
    store = AccountsRepository(tmp_path / "accounts.db", now=lambda: instant[0])
    configure_fixture_prices(store)
    user = store.register("cook", PASSWORD)
    store.reserve_usage(user["id"], "crashed", tokens=500, cost_micros=300, ttl_seconds=60)
    assert store.reservation_pricing(user["id"], "crashed")["currency"] == "CNY"
    assert AccountsRepository(store.path, now=lambda: instant[0]).recover_reservations() == 1
    recovered = store.get_usage(user["id"])[0]
    assert recovered["status"] == "interrupted"
    assert recovered["estimated_tokens"] == 500
    assert recovered["estimated_cost_micros"] == 300
    assert store.recover_reservations() == 0
    store.reserve_usage(user["id"], "expired", tokens=100, ttl_seconds=60)
    instant[0] += timedelta(minutes=2)
    assert store.get_user(user["id"])["quota"]["reserved_tokens"] == 100
    store.reserve_usage(user["id"], "fresh")
    quota = store.get_user(user["id"])["quota"]
    assert quota["estimated_tokens"] == 600
    assert quota["reserved_tokens"] == 1
    with pytest.raises(AccountError):
        store.reservation_pricing(user["id"], "expired")


def test_missing_attempt_metadata_is_never_labeled_complete(accounts):
    user = accounts.register("cook", PASSWORD)
    accounts.reserve_usage(user["id"], "turn", tokens=1000)
    result = accounts.settle_usage(user["id"], "turn", {"attempted_calls": 2,
        "calls": [{"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}]})
    assert result["incomplete"]
    assert result["estimated_tokens"] == 980


def test_partial_provider_usage_keeps_known_cost_and_estimates_only_the_remainder(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_pricing(admin["id"], {"input_per_million": "2", "output_per_million": "4",
                                         "cached_input_per_million": "1"})
    accounts.reserve_usage(user["id"], "partial", tokens=1000, cost_micros=2000)
    result = accounts.settle_usage(user["id"], "partial", {"attempted_calls": 2, "calls": [
        {"input_tokens": 20, "output_tokens": 10, "cached_tokens": 5, "total_tokens": 30}, {}]})
    assert result["total_tokens"] == 30
    assert result["cost_micros"] == 75
    assert result["incomplete"]
    assert result["estimated_tokens"] == 970
    assert result["estimated_cost_micros"] == 1925
    assert accounts.get_user(user["id"])["quota"]["quota_used_tokens"] == 1000


def test_different_calls_cannot_lend_missing_total_operands_or_cache_discounts(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_pricing(admin["id"], {"input_per_million": "2", "output_per_million": "4",
                                         "cached_input_per_million": "0"})
    accounts.reserve_usage(user["id"], "partial", tokens=1000, cost_micros=2000)
    result = accounts.settle_usage(user["id"], "partial", {"attempted_calls": 2, "calls": [
        {"input_tokens": 20}, {"output_tokens": 10, "cached_tokens": 20}]})
    assert result["input_tokens"] == 20 and result["output_tokens"] == 10
    assert result["total_tokens"] is None
    assert result["cost_micros"] == 80  # Cache metadata cannot discount another call's input.
    assert result["incomplete"] and result["estimated_tokens"] == 1000


def test_zero_monetary_budget_refuses_model_admission(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": 0})
    with pytest.raises(QuotaExceeded):
        accounts.reserve_usage(user["id"], "turn")


def test_login_rate_limit_is_persistent_and_unknown_accounts_get_same_message(accounts):
    accounts.register("cook", PASSWORD)
    for username in ("cook", "missing"):
        with pytest.raises(AccountError) as error:
            accounts.login(username, "wrong", client_key="one-ip")
        assert error.value.status_code == 401
        assert error.value.detail == "账号或密码不正确，或账号暂不可用。"
    for _ in range(9):
        with pytest.raises(AccountError):
            accounts.login("cook", "wrong", client_key="one-ip")
    with pytest.raises(AccountError) as error:
        AccountsRepository(accounts.path).login("cook", PASSWORD, client_key="one-ip")
    assert error.value.status_code == 429


def test_admin_password_recovery_clears_account_lockout_but_keeps_ip_throttle(accounts):
    admin, user = admin_and_user(accounts)
    for _ in range(10):
        with pytest.raises(AccountError):
            accounts.login("cook", "forgotten-password", client_key="recovery-ip")
    with pytest.raises(AccountError) as error:
        accounts.login("cook", PASSWORD, client_key="recovery-ip")
    assert error.value.status_code == 429
    accounts.reset_password(admin["id"], user["id"], "recovered-password-123")
    assert accounts.login("cook", "recovered-password-123", client_key="recovery-ip")["id"] == user["id"]
    # IP admission was not cleared by the administrator's reset.
    for _ in range(48):
        accounts.check_rate_limit("login-ip:recovery-ip", count=60)
    with pytest.raises(AccountError) as error:
        accounts.login("cook", "recovered-password-123", client_key="recovery-ip")
    assert error.value.status_code == 429


def test_auth_http_cookie_session_and_admin_authorization(accounts):
    admin = accounts.bootstrap_admin("admin", PASSWORD)
    with client_for(accounts) as client:
        assert client.get("/api/auth/me").json()["user"] is None
        response = client.post("/api/auth/register", json={"username": "cook", "password": PASSWORD})
        assert response.status_code == 201
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
        assert COOKIE_NAME in client.cookies
        assert client.get("/api/auth/me").json()["user"]["username"] == "cook"
        assert client.get("/api/admin/users").status_code == 403
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").json()["user"] is None
        assert client.patch("/api/auth/onboarding", json={"display_name": "小厨师"}).status_code == 401
        response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert response.json()["user"]["id"] == admin["id"]
        assert len(client.get("/api/admin/users").json()["items"]) == 2
        assert client.get("/api/admin/usage").status_code == 200


def test_http_csrf_and_credential_validation_never_echo_secrets(accounts):
    with client_for(accounts) as client:
        response = client.post("/api/auth/register", json={"username": "cook", "password": PASSWORD},
                               headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        response = client.post("/api/auth/login", json={"username": "cook", "password": "SECRET" * 100})
        assert response.status_code == 422
        assert "SECRET" not in response.text
        assert response.json() == {"detail": "账号请求无效，请检查输入内容。"}


def test_http_password_change_rotates_this_cookie_and_revokes_other_sessions(accounts):
    with client_for(accounts) as client:
        user = client.post("/api/auth/register", json={"username": "cook", "password": PASSWORD}).json()["user"]
        first = client.cookies.get(COOKIE_NAME)
        other = accounts.issue_session(user["id"])
        assert client.patch("/api/auth/password", json={"current_password": "wrong",
            "new_password": "another secure password 123"}).status_code == 400
        response = client.patch("/api/auth/password", json={"current_password": PASSWORD,
            "new_password": "another secure password 123"})
        assert response.status_code == 200
        assert response.json()["ok"]
        assert client.cookies.get(COOKIE_NAME) != first
        assert accounts.authenticate_session(first) is None
        assert accounts.authenticate_session(other) is None
        assert client.get("/api/auth/me").json()["user"]["id"] == user["id"]
        assert "another secure password" not in response.text


def test_admin_http_quota_and_price_updates_and_usage_drilldown(accounts):
    _, user = admin_and_user(accounts)
    with client_for(accounts) as client:
        client.post("/api/auth/login", json={"username": "administrator", "password": PASSWORD})
        response = client.patch(f"/api/admin/users/{user['id']}", json={
            "cost_limit_micros": 1_000_000, "developer_allowed": True})
        assert response.json()["user"]["quota"]["cost_limit_micros"] == 1_000_000
        prices = client.patch("/api/admin/pricing", json={"currency": "CNY", "input_per_million": "1.25",
            "output_per_million": "3.5", "cached_input_per_million": "0.5"}).json()
        assert prices["input_per_million"] == "1.250000"
        assert client.patch("/api/admin/pricing", json={"input_per_million": "NaN"}).status_code == 422
        assert client.patch("/api/admin/pricing", json={"revision": 1,
            "input_per_million": "2"}).status_code == 409
        assert client.get(f"/api/admin/users/{user['id']}/usage").json()["items"] == []
        assert client.get("/api/admin/audit").json()["items"][0]["action"] == "update_pricing"


def test_registration_can_be_disabled_and_secure_cookie_enabled(accounts):
    accounts.bootstrap_admin("admin", PASSWORD)
    with client_for(accounts, registration_enabled=False, secure_cookie=True) as client:
        assert not client.get("/api/auth/me").json()["registration_enabled"]
        assert client.post("/api/auth/register", json={"username": "cook", "password": PASSWORD}).status_code == 403
        response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert "Secure" in response.headers["set-cookie"]


def test_registration_policy_changes_only_future_users_and_survives_configuration_restart(accounts):
    admin, old = admin_and_user(accounts)
    first = accounts.policy()
    policy = accounts.update_policy(admin["id"], {"revision": first["revision"], "default_cost_limit_micros": 2_000_000})
    assert policy["default_cost_limit_micros"] == 2_000_000
    future = accounts.register("future-user", PASSWORD)
    assert accounts.get_user(old["id"])["quota"]["cost_limit_micros"] == 5_000_000
    assert future["quota"]["token_limit"] is None
    assert future["quota"]["cost_limit_micros"] == 2_000_000
    assert accounts.get_user(admin["id"])["quota"]["token_limit"] is None
    restarted = AccountsRepository(accounts.path, default_cost_limit_micros=9_000_000)
    assert restarted.policy() == policy
    with pytest.raises(AccountError) as error:
        accounts.update_policy(admin["id"], {"revision": first["revision"], "registration_enabled": False})
    assert error.value.status_code == 409
    with pytest.raises(AccountError):
        accounts.update_policy(old["id"], {"revision": policy["revision"], "default_cost_limit_micros": None})


def test_registration_policy_is_rechecked_after_hashing_inside_creation_transaction(accounts, monkeypatch):
    from chefmate_playground import accounts as module
    admin, _ = admin_and_user(accounts)
    original = module.hash_password
    def hash_then_close_registration(password):
        encoded = original(password)
        current = accounts.policy()
        accounts.update_policy(admin["id"], {"revision": current["revision"], "registration_enabled": False})
        return encoded
    monkeypatch.setattr(module, "hash_password", hash_then_close_registration)
    with pytest.raises(AccountError) as error:
        accounts.register("racing-user", PASSWORD)
    assert error.value.status_code == 403
    assert not any(user["username"] == "racing-user" for user in accounts.list_users())


def test_admin_http_policy_cas_dynamic_registration_and_unlimited_defaults(accounts):
    admin, _ = admin_and_user(accounts)
    with client_for(accounts) as owner, client_for(accounts) as anonymous:
        owner.post("/api/auth/login", json={"username": "administrator", "password": PASSWORD})
        current = owner.get("/api/admin/policy").json()
        assert current == {"revision": 1, "registration_enabled": True, "default_cost_limit_micros": 5_000_000}
        response = owner.patch("/api/admin/policy", json={"revision": current["revision"], "registration_enabled": False})
        assert response.status_code == 200
        assert anonymous.get("/api/auth/me").json()["registration_enabled"] is False
        assert anonymous.post("/api/auth/register", json={"username": "closed-user", "password": PASSWORD}).status_code == 403
        assert owner.patch("/api/admin/policy", json={"revision": 1, "registration_enabled": True}).status_code == 409
        assert anonymous.get("/api/admin/policy").status_code == 401
        reopened = owner.patch("/api/admin/policy", json={"revision": response.json()["revision"],
            "registration_enabled": True, "default_cost_limit_micros": 0})
        assert reopened.status_code == 200
        registered = anonymous.post("/api/auth/register", json={"username": "unlimited-user", "password": PASSWORD}).json()["user"]
        assert registered["quota"]["token_limit"] is None and registered["quota"]["cost_limit_micros"] == 0
        assert owner.patch("/api/admin/policy", json={"default_cost_limit_micros": 100}).status_code == 422
        assert accounts.get_user(admin["id"])["role"] == "admin"


def test_concurrent_registration_policy_changes_do_not_overwrite_each_other(accounts):
    admin, _ = admin_and_user(accounts)
    revision = accounts.policy()["revision"]
    def change(limit):
        try:
            return accounts.update_policy(admin["id"], {"revision": revision, "default_cost_limit_micros": limit})
        except AccountError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, (200_000, 300_000)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 409 in results


def test_finite_money_requires_explicit_pricing_and_legacy_token_limits_never_admit_or_block(tmp_path):
    store = AccountsRepository(tmp_path / "accounts.db")
    admin, user = admin_and_user(store)
    assert store.pricing()["configured"] is False
    with pytest.raises(AccountError) as error:
        store.reserve_usage(user["id"], "unpriced", tokens=1, cost_micros=0)
    assert error.value.status_code == 503 and "尚未配置模型计价" in error.value.detail
    store.reserve_usage(admin["id"], "admin-setup")  # Unlimited admin can configure/probe first.
    store.release_usage(admin["id"], "admin-setup")
    assert store.update_pricing(admin["id"], {"input_per_million": "0", "output_per_million": "0",
        "cached_input_per_million": "0"})["configured"] is True
    with store._connect() as connection:
        connection.execute("UPDATE accounts SET token_limit=1 WHERE id=?", (user["id"],))
    store.reserve_usage(user["id"], "money-only", tokens=1_000_000_000, cost_micros=1)
    assert store.get_user(user["id"])["quota"]["balance_micros"] == 4_999_999
    assert store.get_user(user["id"])["quota"]["token_limit"] is None
    with pytest.raises(AccountError):
        store.update_user(admin["id"], user["id"], {"token_limit": 100})
    with pytest.raises(AccountError):
        store.update_policy(admin["id"], {"revision": store.policy()["revision"], "default_token_limit": 100})


def test_money_balance_counts_known_estimated_and_reserved_without_double_charging(accounts):
    admin, user = admin_and_user(accounts)
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": 1000})
    accounts.reserve_usage(user["id"], "known", tokens=100, cost_micros=100)
    assert accounts.get_user(user["id"])["quota"]["balance_micros"] == 900
    observed = {"attempted_calls": 1, "calls": [{"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}]}
    accounts.settle_usage(user["id"], "known", observed)
    assert accounts.get_user(user["id"])["quota"]["balance_micros"] == 985
    accounts.reserve_usage(user["id"], "unknown", tokens=200, cost_micros=200)
    accounts.settle_usage(user["id"], "unknown", {"attempted_calls": 1, "calls": []})
    accounts.settle_usage(user["id"], "known", observed)
    accounts.settle_usage(user["id"], "unknown", {"attempted_calls": 1, "calls": []})
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["used_cost_micros"] == 15 and quota["estimated_cost_micros"] == 200
    assert quota["balance_micros"] == quota["remaining_cost_micros"] == 785
    assert len(accounts.get_usage(user["id"])) == 2
    with pytest.raises(QuotaExceeded):
        accounts.reserve_usage(user["id"], "too-much", cost_micros=786)
    accounts.reserve_usage(user["id"], "exact-balance", cost_micros=785)
    assert accounts.get_user(user["id"])["quota"]["balance_micros"] == 0
    accounts.release_usage(user["id"], "exact-balance")
    assert accounts.get_user(user["id"])["quota"]["balance_micros"] == 785


def test_token_only_legacy_users_migrate_once_to_site_money_without_numeric_conversion(accounts):
    admin, limited = admin_and_user(accounts)
    already_money = accounts.register("money-user", PASSWORD)
    unlimited = accounts.register("unlimited-user", PASSWORD)
    blocked = accounts.register("blocked-user", PASSWORD)
    with accounts._connect() as connection:
        connection.execute("UPDATE account_settings SET body=? WHERE name='policy'", (json.dumps({
            "revision": 4, "registration_enabled": True, "default_token_limit": 100_000,
            "default_cost_limit_micros": None}),))
        connection.execute("UPDATE accounts SET token_limit=?,cost_limit_micros=NULL WHERE id=?", (12345, limited["id"]))
        connection.execute("UPDATE accounts SET token_limit=?,cost_limit_micros=? WHERE id=?", (100, 8_000_000, already_money["id"]))
        connection.execute("UPDATE accounts SET token_limit=NULL,cost_limit_micros=NULL WHERE id=?", (unlimited["id"],))
        connection.execute("UPDATE accounts SET token_limit=0,cost_limit_micros=NULL WHERE id=?", (blocked["id"],))
    migrated = AccountsRepository(accounts.path, default_cost_limit_micros=7_000_000)
    assert migrated.get_user(limited["id"])["quota"]["cost_limit_micros"] == 7_000_000
    assert migrated.get_user(already_money["id"])["quota"]["cost_limit_micros"] == 8_000_000
    assert migrated.get_user(unlimited["id"])["quota"]["cost_limit_micros"] is None
    assert migrated.get_user(admin["id"])["quota"]["cost_limit_micros"] is None
    assert migrated.get_user(blocked["id"])["quota"]["cost_limit_micros"] == 0
    assert migrated.policy()["default_cost_limit_micros"] == 7_000_000
    assert "default_token_limit" not in migrated.policy()
    assert migrated.audit_log()[0]["action"] == "migrate_monetary_budgets"
    migrated.update_user(admin["id"], limited["id"], {"cost_limit_micros": None})
    assert AccountsRepository(accounts.path).get_user(limited["id"])["quota"]["cost_limit_micros"] is None


def test_pre_policy_legacy_accounts_are_bounded_and_old_zero_prices_need_confirmation(accounts):
    _, user = admin_and_user(accounts)
    with accounts._connect() as connection:
        connection.execute("DELETE FROM account_settings WHERE name='policy'")
        connection.execute("UPDATE accounts SET token_limit=100,cost_limit_micros=NULL WHERE id=?", (user["id"],))
        connection.execute("UPDATE account_settings SET body=? WHERE name='pricing'", (json.dumps({
            "currency": "CNY", "revision": 1, "input_per_million": "0", "output_per_million": "0",
            "cached_input_per_million": "0"}),))
    migrated = AccountsRepository(accounts.path)
    assert migrated.get_user(user["id"])["quota"]["cost_limit_micros"] == 5_000_000
    assert migrated.pricing()["configured"] is False
