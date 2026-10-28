"""Administrator history stays searchable, bounded, and private in SQLite/PG."""

import json
import os
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql

from chefmate_playground.accounts import AccountError, AccountsRepository, COOKIE_NAME
from chefmate_playground.audit import audit_report
from chefmate_playground.database import Database
from chefmate_playground.routes.accounts import build_router


PASSWORD = "audit-fixture-password-123"


@pytest.fixture(params=["sqlite", "postgresql"])
def history(request, tmp_path):
    url = os.environ.get("CHEFMATE_TEST_APP_DSN", "") if request.param == "postgresql" else ""
    if request.param == "postgresql" and not url:
        pytest.skip("Set CHEFMATE_TEST_APP_DSN for isolated PostgreSQL audit checks")
    schema = "audit_" + uuid4().hex[:12]
    accounts = AccountsRepository(Database(tmp_path / "accounts.db", url=url, schema=schema))
    owner = accounts.bootstrap_admin("owner", PASSWORD)
    user = accounts.register("cook_1", PASSWORD)
    with accounts._connect() as db:
        db.execute("DELETE FROM account_audit")
        db.execute("UPDATE accounts SET display_name=? WHERE id=?", ("小厨师", user["id"]))
    try:
        yield accounts, owner, user
    finally:
        if url:
            with psycopg.connect(url) as db:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def add(history, *, action="update_user", actor="owner", target="cook", at="2026-10-27T12:00:00+00:00", fields=None):
    accounts, owner, user = history
    with accounts._connect() as db:
        accounts._audit(db, owner["id"] if actor == "owner" else user["id"] if actor == "cook" else actor,
                        user["id"] if target == "cook" else target, action, fields or ["developer_allowed"], at)


def test_pagination_is_stable_on_time_ties_and_metadata_is_complete(history):
    for index in range(27):
        add(history, action="grant_credit" if index % 2 else "update_user")
    accounts, owner, user = history
    first = audit_report(accounts)
    second = audit_report(accounts, page=2)
    assert first["total"] == 27 and first["total_pages"] == 2 and len(first["items"]) == 20
    assert len(second["items"]) == 7 and second["page"] == 2
    ids = [row["id"] for row in [*first["items"], *second["items"]]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 27
    assert first["items"][0]["target_username"] == "cook_1"
    assert "小厨师" in first["items"][0]["target_label"]
    assert first["items"][0]["field_labels"] == ["开发者权限"]
    assert first["actors"] == [{"value": owner["id"], "label": "@owner"}]
    assert first["targets"] == [{"value": user["id"], "label": "小厨师 (@cook_1)"}]
    assert {item["value"] for item in first["actions"]} == {"grant_credit", "update_user"}
    assert audit_report(accounts, page=999)["page"] == 2


def test_order_uses_created_at_before_id(history):
    add(history, action="grant_credit", at="2026-10-27T12:00:00+00:00")
    add(history, action="register", at="2026-10-26T12:00:00+00:00")
    assert [row["action"] for row in audit_report(history[0])["items"]] == ["grant_credit", "register"]


def test_search_is_literal_and_bound_not_a_pattern_or_sql(history):
    for action in ("100%_done\\yes", "100xx_done\\yes", "safe", "' OR 1=1 --"):
        add(history, action=action)
    accounts = history[0]
    assert audit_report(accounts, q="%_")["total"] == 1
    assert audit_report(accounts, q="\\yes")["total"] == 2
    assert audit_report(accounts, q="' OR 1=1 --")["total"] == 1
    assert audit_report(accounts, q="COOK_1")["total"] == 4
    assert audit_report(accounts, q="小厨师")["total"] == 4


def test_filters_combine_and_system_is_distinct_from_users(history):
    accounts, owner, user = history
    add(history, action="grant_credit")
    add(history, action="grant_credit", actor="cook")
    add(history, action="grant_membership", actor=None, target=None)
    result = audit_report(accounts, action="grant_credit", actor=owner["id"], target=user["id"])
    assert result["total"] == 1
    system = audit_report(accounts, actor="system", target="system")
    assert system["total"] == 1 and system["items"][0]["actor_label"] == "系统"
    assert audit_report(accounts, q="开通会员")["total"] == 1
    assert len(result["actions"]) == 2  # Filter choices do not disappear after applying a filter.


def test_local_date_boundaries_are_inclusive_without_timezone_drift(history):
    for at in ("2026-10-26T15:59:59.999999+00:00", "2026-10-26T16:00:00+00:00",
               "2026-10-27T15:59:59.999999+00:00", "2026-10-27T16:00:00+00:00"):
        add(history, at=at)
    accounts = history[0]
    result = audit_report(accounts, date_from="2026-10-27", date_to="2026-10-27")
    assert result["total"] == 2 and result["timezone"] == "Asia/Shanghai"
    assert audit_report(accounts, date_from="2026-10-28")["total"] == 1
    assert audit_report(accounts, date_to="2026-10-26")["total"] == 1


def test_model_target_labels_and_search_extract_no_private_values(history):
    accounts = history[0]
    profile_id = uuid4().hex
    private = {"profiles": [{"id": profile_id, "name": "备用厨房模型", "api_key": "never-show-this-key",
                              "base_url": "https://private.invalid/v1"}]}
    with accounts._connect() as db:
        db.execute("INSERT INTO account_settings(name,body) VALUES('model_profiles',?)", (json.dumps(private),))
    add(history, action="model_profiles.test", target=profile_id)
    report = audit_report(accounts, q="备用厨房")
    assert report["total"] == 1 and report["items"][0]["target_label"] == "备用厨房模型"
    assert "never-show" not in json.dumps(report) and "private.invalid" not in json.dumps(report)
    assert audit_report(accounts, target=profile_id)["total"] == 1


@pytest.mark.parametrize("options", [{"page": 0}, {"page_size": 101}, {"page_size": True}, {"q": "x" * 201},
    {"date_from": "2026-2-01"}, {"date_to": "2026-02-30"}, {"date_to": "9999-12-31"},
    {"date_from": "2026-10-28", "date_to": "2026-10-27"}])
def test_invalid_filters_are_rejected(history, options):
    with pytest.raises(AccountError) as error:
        audit_report(history[0], **options)
    assert error.value.status_code == 422


def test_http_requires_admin_and_paginates_without_validation_input_echo(history):
    accounts, owner, user = history
    app = FastAPI()
    app.include_router(build_router(accounts))
    add(history)
    with TestClient(app) as client:
        assert client.get("/api/admin/audit").status_code == 401
        client.cookies.set(COOKIE_NAME, accounts.issue_session(user["id"]))
        assert client.get("/api/admin/audit").status_code == 403
        client.cookies.set(COOKIE_NAME, accounts.issue_session(owner["id"]))
        response = client.get("/api/admin/audit?page_size=1&action=update_user")
        assert response.status_code == 200 and response.json()["total"] == 1
        invalid = client.get("/api/admin/audit?page=private-value-never-echo")
        assert invalid.status_code == 422 and "private-value-never-echo" not in invalid.text
        assert client.get("/api/admin/audit?page_size=101").status_code == 422
