"""Membership presets persist independently of grants and use an admin-only CAS."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.accounts import AccountError, AccountsRepository
from chefmate_playground.database import Database
from chefmate_playground.membership_templates import MembershipTemplates, SETTING_NAME
from chefmate_playground.routes.accounts import build_router as authentication
from chefmate_playground.routes.membership_templates import build_router


PASSWORD = "template-fixture-password-123"


@pytest.fixture(params=["sqlite", "postgres"])
def templates(request, tmp_path):
    schema = "templates_" + uuid4().hex[:23]
    dsn = os.environ.get("CHEFMATE_TEST_APP_DSN", "") if request.param == "postgres" else ""
    if request.param == "postgres" and not dsn:
        pytest.skip("Set CHEFMATE_TEST_APP_DSN for isolated PostgreSQL checks")
    accounts = AccountsRepository(Database(tmp_path / "accounts.db", url=dsn, schema=schema))
    admin = accounts.bootstrap_admin("template_owner", PASSWORD)
    user = accounts.register("template_member", PASSWORD)
    try:
        yield accounts, MembershipTemplates(accounts), admin, user
    finally:
        if dsn:
            with psycopg.connect(dsn) as db:
                db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def values(**changes):
    return {"name": "标准月卡", "label": "ChefMate 会员", "duration_days": 30,
            "weekly_limit_micros": 10_000_000, "five_hour_limit_micros": 2_000_000, **changes}


def test_empty_reads_do_not_write_settings_or_grant_anything(templates):
    accounts, registry, admin, user = templates
    assert registry.list(admin["id"]) == {"revision": 1, "items": []}
    with accounts._connect() as db:
        assert db.execute("SELECT body FROM account_settings WHERE name=?", (SETTING_NAME,)).fetchone() is None
    assert accounts.get_entitlements(user["id"])["entitlements"]["memberships"] == []


def test_multiple_templates_crud_persists_after_reopening(templates):
    accounts, registry, admin, _ = templates
    first = registry.change(admin["id"], 1, "create", values=values())
    second = registry.change(admin["id"], first["revision"], "create", values=values(name="体验周卡", duration_days=7))
    identifier = first["items"][0]["id"]
    updated = registry.change(admin["id"], second["revision"], "update", identifier, {"weekly_limit_micros": 15_000_000})
    assert updated["items"][0]["weekly_limit_micros"] == 15_000_000
    assert updated["items"][0]["created_at"] == first["items"][0]["created_at"]
    assert MembershipTemplates(AccountsRepository(accounts.database)).list(admin["id"]) == updated
    deleted = registry.change(admin["id"], updated["revision"], "delete", identifier)
    assert [item["name"] for item in deleted["items"]] == ["体验周卡"]
    with accounts._connect() as db:
        raw = json.loads(db.execute("SELECT body FROM account_settings WHERE name=?", (SETTING_NAME,)).fetchone()[0])
        assert raw["templates"] == deleted["items"] and raw["format_version"] == 1
        assert [row[0] for row in db.execute("SELECT action FROM account_audit WHERE action LIKE 'membership_templates.%' ORDER BY id")] == [
            "membership_templates.create", "membership_templates.create", "membership_templates.update", "membership_templates.delete"]


def test_stale_revision_and_concurrent_creation_do_not_overwrite(templates):
    _, registry, admin, _ = templates
    def create(index):
        try:
            registry.change(admin["id"], 1, "create", values=values(name=f"并发模板 {index}"))
            return True
        except AccountError as error:
            assert error.status_code == 409
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(create, (1, 2))) == 1
    result = registry.list(admin["id"])
    assert len(result["items"]) == 1 and result["revision"] == 2
    with pytest.raises(AccountError) as stale:
        registry.change(admin["id"], 1, "delete", result["items"][0]["id"])
    assert stale.value.status_code == 409


@pytest.mark.parametrize("change", [{"duration_days": True}, {"duration_days": 0}, {"weekly_limit_micros": 1.2},
    {"five_hour_limit_micros": -1}, {"name": "  "}, {"label": "Bad\x00label"}, {"unexpected": "forbidden"}])
def test_invalid_values_cannot_write_a_template(templates, change):
    _, registry, admin, _ = templates
    with pytest.raises(AccountError) as invalid:
        registry.change(admin["id"], 1, "create", values=values(**change))
    assert invalid.value.status_code == 422
    assert registry.list(admin["id"])["items"] == []


def test_template_edits_and_deletion_do_not_change_an_issued_membership(templates):
    accounts, registry, admin, user = templates
    created = registry.change(admin["id"], 1, "create", values=values())
    preset = created["items"][0]
    issued = accounts.grant_membership(admin["id"], user["id"], label=preset["label"], duration_days=preset["duration_days"],
        weekly_limit_micros=preset["weekly_limit_micros"], five_hour_limit_micros=preset["five_hour_limit_micros"], request_id="issued-preset")
    before = issued["entitlements"]["memberships"][0]
    edited = registry.change(admin["id"], created["revision"], "update", preset["id"], {"duration_days": 7, "weekly_limit_micros": 1})
    registry.change(admin["id"], edited["revision"], "delete", preset["id"])
    assert accounts.get_entitlements(user["id"])["entitlements"]["memberships"][0] == before


def test_duplicate_names_and_corrupt_settings_preserve_existing_data(templates):
    accounts, registry, admin, _ = templates
    created = registry.change(admin["id"], 1, "create", values=values(name="Monthly"))
    with pytest.raises(AccountError) as duplicate:
        registry.change(admin["id"], 2, "create", values=values(name="ＭＯＮＴＨＬＹ"))
    assert duplicate.value.status_code == 409 and registry.list(admin["id"]) == created
    with accounts._connect() as db:
        db.execute("UPDATE account_settings SET body=? WHERE name=?", ("{invalid", SETTING_NAME))
    with pytest.raises(AccountError) as corrupt:
        registry.change(admin["id"], 2, "create", values=values())
    assert corrupt.value.status_code == 503
    with accounts._connect() as db:
        assert db.execute("SELECT body FROM account_settings WHERE name=?", (SETTING_NAME,)).fetchone()[0] == "{invalid"


def test_http_is_admin_only_same_origin_and_strict(templates):
    accounts, _, admin, user = templates
    app = FastAPI()
    app.include_router(authentication(accounts))
    app.include_router(build_router(accounts))
    url = "/api/admin/membership-templates"
    with TestClient(app) as client:
        assert client.get(url).status_code == 401
        client.post("/api/auth/login", json={"username": user["username"], "password": PASSWORD})
        assert client.get(url).status_code == 403
        client.post("/api/auth/login", json={"username": admin["username"], "password": PASSWORD})
        assert client.post(url, headers={"Origin": "https://foreign.invalid"}, json={"revision": 1, **values()}).status_code == 403
        assert client.post(url, json={"revision": 1, **values(duration_days=True)}).status_code == 422
        created = client.post(url, json={"revision": 1, **values()})
        assert created.status_code == 201
        payload = created.json()
        identifier = payload["items"][0]["id"]
        assert client.patch(url + "/" + identifier, json={"revision": payload["revision"], "values": {"name": "新月卡"}}).status_code == 200
        assert client.delete(url + "/" + identifier + "?revision=2").status_code == 409
        assert client.delete(url + "/" + identifier + "?revision=3").json() == {"revision": 4, "items": []}
    with accounts._connect() as db:
        db.execute("UPDATE accounts SET disabled=1 WHERE id=?", (admin["id"],))
    with pytest.raises(AccountError):
        MembershipTemplates(accounts).list(admin["id"])


def test_real_application_mount_and_account_switch_boundary(tmp_path):
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "mounted" / "sessions.db",
        admin_username="template_owner", admin_password=PASSWORD))
    url = "/api/admin/membership-templates"
    with TestClient(app) as client:
        assert client.get(url).status_code == 401
        response = client.post("/api/auth/login", json={"username": "template_owner", "password": PASSWORD})
        assert response.status_code == 200
        assert client.get(url).json() == {"revision": 1, "items": []}
        blocked = client.post(url, json={"revision": 1, **values()}, headers={"X-ChefMate-Account": "f" * 32})
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "account_changed"
        assert client.get(url).json()["items"] == []
        assert client.post(url, json={"revision": 1, **values()}).status_code == 201
        assert len(client.get(url).json()["items"]) == 1
