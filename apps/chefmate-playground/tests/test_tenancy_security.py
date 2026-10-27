"""Adversarial tenant boundary checks and PostgreSQL HTTP isolation acceptance."""

import json
import os
import re
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from chefmate_playground import PlaygroundConfig, create_app
from test_tenancy import MeteredAgent, PASSWORD, PROFILE, login, register


def application(tmp_path, **values):
    return create_app(PlaygroundConfig(sessions_database=tmp_path / "sessions.db",
        admin_username="owner", admin_password=PASSWORD, **values))


def test_decoded_paths_and_debug_redirects_cannot_bypass_permissions(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        for path in ("/api%2Fadmin%2Fusers", "/api/admin/users/", "/api/sessions", "/api/auth/../admin/users"):
            assert client.get(path, follow_redirects=True).status_code == 401
        user = register(client, "boundary-user")
        session = client.post("/api/sessions").json()
        for path in ("/api/debug/catalog/", f"/api/sessions/{session['id']}/debug/",
                     f"/api/sessions/{session['id']}/debug%2F"):
            response = client.get(path, headers={"X-ChefMate-Developer": "1"}, follow_redirects=True)
            assert response.status_code == 403, (path, response.text)
        assert client.get("/api/admin/users", headers={"X-ChefMate-User": user["id"],
                                                      "X-ChefMate-Role": "admin"}).status_code == 403


def test_chunked_body_limit_and_same_host_cross_scheme_origin_are_enforced(tmp_path):
    with TestClient(application(tmp_path)) as client:
        response = client.post("/api/auth/register", content=iter((b"x" * 9000, b"x" * 9000)),
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        assert client.post("/api/auth/register", json={"username": "scheme-user", "password": PASSWORD},
                           headers={"Origin": "https://testserver"}).status_code == 403


def test_session_image_paths_remain_private_even_when_identifiers_are_known(tmp_path):
    app = application(tmp_path, supports_images=True)
    with TestClient(app) as alice, TestClient(app) as bob:
        register(alice, "image-alice")
        register(bob, "image-bob")
        session = alice.post("/api/sessions").json()
        uri = f"/api/sessions/{session['id']}/images"
        output = BytesIO()
        Image.new("RGB", (8, 8), "tomato").save(output, format="PNG")
        response = alice.post(uri + "?name=tomato.png", content=output.getvalue())
        assert response.status_code == 200, response.text
        image_id = response.json()["id"]
        assert alice.get(uri + "/" + image_id).status_code == 200
        assert bob.get(uri + "/" + image_id).status_code == 404
        assert bob.post(uri, content=output.getvalue()).status_code == 404
        assert bob.get("/static/../.runtime/users/" + image_id).status_code != 200


@pytest.mark.parametrize("change", ["optout", "clear", "disable", "edit"])
def test_active_turn_stops_before_next_provider_round_when_profile_consent_changes(tmp_path, change):
    from chefmate_agent.boundary import _AgentFailure
    class ChangingProfileAgent(MeteredAgent):
        provider_entries = 0
        revoke = None

        async def run_turn(self, messages, *, on_event, memory_context=None, **kwargs):
            assert memory_context["kitchen_profile"]["display_name"] == PROFILE["display_name"]
            usage = {"attempted_calls": 1, "calls": [{"round": 1, "input_tokens": 10,
                "output_tokens": 2, "total_tokens": 12}]}
            for round_number in (1, 2):
                try:
                    await on_event({"type": "context_usage", "context_usage": {
                        "round": round_number, "utf8_bytes": 100, "chars": 100}})
                except _AgentFailure as error:
                    return {"status": "error", "messages": messages, "usage": usage,
                            "error": error.error.model_dump()}
                self.provider_entries += 1  # The boundary precedes provider entry.
                await on_event({"type": "model_usage", "usage": usage})
                if round_number == 1:
                    self.revoke()
            return {"status": "completed", "messages": messages, "usage": usage, "assistant_text": "guard failed"}
    agent = ChangingProfileAgent()
    app = application(tmp_path)
    app.state.pool.injected_agent = agent
    owner = app.state.accounts.bootstrap_admin("owner", PASSWORD)
    app.state.accounts.update_pricing(owner["id"], {"input_per_million": "1", "output_per_million": "1"})
    with TestClient(app) as client:
        user = register(client, "profile-user")
        assert client.patch("/api/auth/onboarding", json={**PROFILE, "use_for_ai": True}).status_code == 200
        session = client.post("/api/sessions").json()
        memory = app.state.pool.workspaces[user["id"]].state.memory
        if change == "optout":
            agent.revoke = lambda: app.state.accounts.complete_onboarding(user["id"], {**PROFILE, "use_for_ai": False})
        elif change == "clear":
            agent.revoke = lambda: memory.clear(memory.snapshot()["revision"])
        elif change == "disable":
            agent.revoke = lambda: memory.update_user("", memory.snapshot()["revision"], enabled=False)
        else:
            agent.revoke = lambda: app.state.accounts.complete_onboarding(user["id"], {
                **PROFILE, "household_size": 3, "use_for_ai": True})
        response = client.post(f"/api/sessions/{session['id']}/turn", json={"message": "先核对本轮的授权"})
        assert response.status_code == 200
        assert '"code": "profile_consent_changed"' in response.text
        assert agent.provider_entries == 1
        quota = client.get("/api/auth/quota").json()["quota"]
        assert quota["used_tokens"] == 12
        assert quota["estimated_tokens"] == quota["reserved_tokens"] == 0


@pytest.fixture
def postgres_application(tmp_path):
    dsn = os.environ.get("CHEFMATE_TEST_APP_DSN")
    if not dsn:
        pytest.skip("CHEFMATE_TEST_APP_DSN is required for PostgreSQL HTTP acceptance")
    import psycopg
    from psycopg import sql
    prefix = "security_" + uuid4().hex[:10]
    app = application(tmp_path, app_database_url=dsn, app_database_schema=prefix,
                      api_key="fixture-key-never-sent", model="fixture-model")
    yield app
    with psycopg.connect(dsn, autocommit=True) as connection:
        names = connection.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (prefix + "%",)).fetchall()
        for manifest in (tmp_path / "archives").glob("*/manifest.json"):
            record = json.loads(manifest.read_text(encoding="utf-8"))
            archived = record.get("archived_schema") or ""
            if record.get("original_schema", "").startswith(prefix + "_") and re.fullmatch(r"chefmate_archive_[0-9a-f]{32}", archived):
                names.append((archived,))
        for row in names:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(row[0])))


def test_postgres_http_tenant_storage_global_refresh_and_archive(postgres_application, tmp_path):
    app = postgres_application
    # The test injects an offline agent explicitly; no provider request is made.
    app.state.pool.injected_agent = MeteredAgent()
    with TestClient(app) as owner, TestClient(app) as alice, TestClient(app) as bob:
        login(owner)
        assert owner.patch("/api/admin/pricing", json={"input_per_million": "1", "output_per_million": "1"}).status_code == 200
        first = register(alice, "postgres-alice")
        second = register(bob, "postgres-bob")
        session = alice.post("/api/sessions").json()
        assert bob.get("/api/sessions/" + session["id"]).status_code == 404
        assert alice.post(f"/api/sessions/{session['id']}/turn", json={"message": "离线存储验收"}).status_code == 200
        tenant = app.state.pool.workspaces[first["id"]]
        schema = app.state.pool.config.app_database_schema + "_user_" + first["id"]
        assert tenant.state.context.repository.database.schema == schema
        assert tenant.state.manager.usage.database.is_postgres
        assert alice.get("/api/auth/quota").json()["quota"]["used_tokens"] == 30
        settings = owner.get("/api/admin/settings").json()
        response = owner.patch("/api/admin/settings", json={"revision": settings["revision"], "section": "context",
            "values": {"max_history_chars": 90000, "max_tool_result_chars": 12000}})
        assert response.status_code == 200, response.text
        assert alice.get("/api/settings").json()["context"]["max_history_chars"] == 90000
        assert tenant.state.context.config.app_database_schema == schema
        assert app.state.pool.accounts.database.is_postgres
        assert not (tmp_path / "accounts.db").exists()
        assert not (tmp_path / "sessions.db").exists()
        assert not app.state.pool.accounts.tenant_database(first["id"]).exists()
        # A private key rotation participates in refresh without becoming public.
        control = app.state.pool.control.state.context
        control.config = replace(control.config, api_key="rotated-fixture-never-sent")
        assert alice.get("/api/settings").status_code == 200
        assert tenant.state.context.config.api_key == "rotated-fixture-never-sent"
        assert tenant.state.context.config.app_database_schema == schema
        response = owner.post(f"/api/admin/users/{first['id']}/reset-data", json={"confirm_username": "postgres-alice"})
        assert response.status_code == 200, response.text
        assert alice.get("/api/sessions").status_code == 401
        assert login(alice, "postgres-alice")["id"] == first["id"]
        assert alice.get("/api/sessions/" + session["id"]).status_code == 404
        assert owner.get("/api/admin/usage").json()["summary"]["turns"] == 1
        assert alice.get("/api/auth/me").json()["user"]["id"] == first["id"]
        assert bob.get("/api/auth/me").json()["user"]["id"] == second["id"]
