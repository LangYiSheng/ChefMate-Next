"""Real HTTP authorization and two-user persistence without provider requests."""

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.sessions import SessionRepository


PASSWORD = "fixture-only-password-123"
PROFILE = {"display_name": "小厨友", "cooking_level": "beginner", "household_size": 2,
           "preferences": ["清淡"], "theme": "dark"}


class MeteredAgent:
    config = SimpleNamespace(model="fixture-model", max_output_tokens=128, max_model_retries=0)

    async def run_turn(self, messages, *, on_event, **kwargs):
        await on_event({"type": "context_usage", "context_usage": {"round": 1, "chars": 50, "utf8_bytes": 75}})
        usage = {"model_calls": 1, "attempted_calls": 1, "input_tokens": 25, "output_tokens": 5,
                 "total_tokens": 30, "calls": [{"round": 1, "model": "fixture-model", "input_tokens": 25,
                                               "output_tokens": 5, "total_tokens": 30, "cached_tokens": 10}]}
        await on_event({"type": "model_usage", "usage": usage})
        return {"status": "completed", "assistant_text": "一起做饭吧", "messages": [*messages,
            {"role": "assistant", "content": "一起做饭吧"}], "usage": usage}


def build(tmp_path, **kwargs):
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "sessions.db", admin_username="owner",
        admin_password=PASSWORD), **kwargs)
    if kwargs.get("agent") is not None:
        # Explicit offline agents are fixtures; explicitly confirm their free
        # test tariff instead of bypassing production pricing readiness.
        owner = next(user for user in app.state.accounts.list_users() if user["role"] == "admin")
        app.state.accounts.update_pricing(owner["id"], {"input_per_million": "0", "output_per_million": "0"})
    return app


def login(client, username="owner"):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    user = response.json()["user"]
    assert user["role"] == "user"
    response = client.patch("/api/auth/onboarding", json=PROFILE)
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_default_app_requires_login_and_onboarding_without_claiming_legacy(tmp_path):
    legacy = SessionRepository(tmp_path / "sessions.db")
    old = legacy.create()
    with TestClient(build(tmp_path)) as client:
        assert client.get("/healthz").json() == {"status": "ok", "service": "chefmate", "authentication": True}
        assert client.get("/api/auth/me").json()["user"] is None
        for path in ("/api/status", "/api/sessions", "/api/ui-preferences", "/api/usage", "/api/admin/users"):
            assert client.get(path).status_code == 401
        response = client.post("/api/auth/register", json={"username": "newuser", "password": PASSWORD})
        assert response.status_code == 201
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        assert client.post("/api/sessions").status_code == 403
        assert client.patch("/api/auth/onboarding", json=PROFILE).status_code == 200
        assert client.get("/api/sessions").json()["items"] == []
        assert client.get("/api/sessions/" + old["id"]).status_code == 404
        assert legacy.get(old["id"]) is not None
        prefs = client.get("/api/ui-preferences").json()
        assert prefs["values"]["theme"] == "dark"


def test_two_users_cannot_read_modify_or_delete_other_workspace(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as alice, TestClient(app) as bob:
        register(alice, "alice")
        register(bob, "bobby")
        session = alice.post("/api/sessions").json()
        uri = "/api/sessions/" + session["id"]
        assert alice.patch(uri, json={"title": "Alice的私房厨房"}).status_code == 200
        assert bob.get(uri).status_code == 404
        assert bob.patch(uri, json={"title": "改名"}).status_code == 404
        assert bob.delete(uri).status_code == 404
        assert bob.get("/api/sessions").json()["items"] == []
        prefs = alice.get("/api/ui-preferences").json()
        assert alice.patch("/api/ui-preferences", json={"revision": prefs["revision"], "changes": {"theme": "light"}}).status_code == 200
        assert bob.get("/api/ui-preferences").json()["values"]["theme"] == "dark"
        assert alice.get(uri).json()["title"] == "Alice的私房厨房"
        # A caller cannot select a tenant through client-controlled IDs or headers.
        assert bob.get(uri, headers={"X-ChefMate-User": session["id"]}).status_code == 404


def test_admin_settings_developer_permission_and_disabled_sessions(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as owner, TestClient(app) as member:
        login(owner)
        user = register(member, "member")
        assert member.get("/api/admin/users").status_code == 403
        assert member.get("/api/admin/settings").status_code == 403
        assert member.patch("/api/settings", json={"section": "model"}).status_code == 403
        assert member.post("/api/settings/test-model", json={}).status_code == 403
        assert member.get("/api/debug/catalog", headers={"X-ChefMate-Developer": "1"}).status_code == 403
        assert member.post("/api/tools/search_recipes", json={}).status_code == 403
        settings = owner.get("/api/admin/settings").json()
        changed = owner.patch("/api/admin/settings", json={"revision": settings["revision"], "section": "context",
            "values": {"max_history_chars": 80000, "max_tool_result_chars": 10000}})
        assert changed.status_code == 200, changed.text
        assert member.get("/api/settings").json()["context"]["max_history_chars"] == 80000
        assert owner.patch(f"/api/admin/users/{user['id']}", json={"developer_allowed": True}).status_code == 200
        assert member.get("/api/debug/catalog", headers={"X-ChefMate-Developer": "1"}).status_code == 200
        assert owner.patch(f"/api/admin/users/{user['id']}", json={"disabled": True}).status_code == 200
        assert member.get("/api/sessions").status_code == 401
        assert member.get("/api/auth/me").json()["user"] is None


def test_csrf_host_and_secret_validation_cover_anonymous_routes(tmp_path):
    with TestClient(build(tmp_path)) as client:
        body = {"username": "csrf-user", "password": PASSWORD}
        assert client.post("/api/auth/register", json=body, headers={"Origin": "https://attacker.test"}).status_code == 403
        assert client.post("/api/auth/register", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.get("/healthz", headers={"Host": "attacker.test"}).status_code == 403
        assert client.post("/api/auth/register", json=body, headers={"Origin": "null"}).status_code == 403
        secret = "secret-must-never-be-echoed" * 50
        response = client.post("/api/auth/register", json={"username": "csrf-user", "password": secret})
        assert response.status_code == 422
        assert "secret-must" not in response.text
        assert client.post("/api/auth/register", content=b"x" * 17000).status_code == 413


def test_account_quota_admission_and_idempotent_cost_snapshot(tmp_path):
    app = build(tmp_path, agent=MeteredAgent())
    with TestClient(app) as owner, TestClient(app) as member:
        login(owner)
        user = register(member, "metered")
        assert owner.patch("/api/admin/pricing", json={"input_per_million": "2", "output_per_million": "4",
            "cached_input_per_million": "1"}).status_code == 200
        session = member.post("/api/sessions").json()
        uri = "/api/sessions/" + session["id"] + "/turn"
        assert owner.patch(f"/api/admin/users/{user['id']}", json={"cost_limit_micros": 0}).status_code == 200
        assert member.post(uri, json={"message": "你好", "request_id": "first"}).status_code == 402
        assert member.get(uri.removesuffix("/turn")).json()["messages"] == []
        assert owner.patch(f"/api/admin/users/{user['id']}", json={"cost_limit_micros": 10000}).status_code == 200
        response = member.post(uri, json={"message": "你好", "request_id": "first"})
        assert response.status_code == 200, response.text
        assert '"status": "completed"' in response.text
        quota = member.get("/api/auth/quota").json()["quota"]
        assert quota["used_tokens"] == 30
        assert quota["used_cost_micros"] == 60  # 15*2 + 10*1 + 5*4
        assert quota["reserved_tokens"] == 0
        owner.patch("/api/admin/pricing", json={"input_per_million": "200"})
        assert member.post(uri, json={"message": "你好", "request_id": "first"}).status_code == 200
        assert member.get("/api/auth/quota").json()["quota"]["used_cost_micros"] == 60
        assert owner.get("/api/admin/usage").json()["summary"]["turns"] == 1


def test_reset_archives_data_preserves_identity_ledger_and_restart(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as owner, TestClient(app) as member:
        login(owner)
        user = register(member, "archived")
        session = member.post("/api/sessions").json()
        assert member.patch("/api/sessions/" + session["id"], json={"title": "保留在归档中"}).status_code == 200
        uri = f"/api/admin/users/{user['id']}/reset-data"
        assert owner.post(uri, json={"confirm_username": "wrong"}).status_code == 422
        response = owner.post(uri, json={"confirm_username": "archived"})
        assert response.status_code == 200, response.text
        archive = tmp_path / "archives" / response.json()["archive"] / "sessions.db"
        assert SessionRepository(archive).get(session["id"])["title"] == "保留在归档中"
        assert member.get("/api/auth/me").json()["user"] is None
        assert login(member, "archived")["id"] == user["id"]
        assert member.get("/api/sessions/" + session["id"]).status_code == 404
    with TestClient(build(tmp_path)) as restarted:
        assert login(restarted, "archived")["onboarding_completed"]
        assert restarted.get("/api/sessions").json()["items"] == []


def test_personal_settings_never_store_provider_key(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as client:
        user = register(client, "private")
        prefs = client.get("/api/ui-preferences").json()
        assert client.patch("/api/ui-preferences", json={"revision": prefs["revision"], "changes": {"theme": "light"}}).status_code == 200
        settings_path = tmp_path / "users" / user["id"] / "settings.json"
        assert set(json.loads(settings_path.read_text(encoding="utf-8"))) == {
            "ui_preferences", "reply_suggestions", "reply_suggestion_count", "timer_limit", "clear_timer_on_step_change"}


def test_shutdown_cleans_managers_and_leases(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as client:
        register(client, "lifecycle")
        assert client.get("/api/status").status_code == 200
    assert not app.state.pool.busy()
    asyncio.run(app.state.pool.close())


def test_stale_browser_account_binding_cannot_write_or_logout_current_cookie_user(tmp_path):
    app = build(tmp_path)
    with TestClient(app) as alice, TestClient(app) as bob:
        first = register(alice, "bound-alice")
        second = register(bob, "bound-bob")
        old_revision = alice.get("/api/ui-preferences").json()["revision"]
        before = bob.get("/api/ui-preferences").json()
        assert old_revision == before["revision"]  # Default values can share CAS hashes.
        stale = {"X-ChefMate-Account": first["id"]}
        response = bob.patch("/api/ui-preferences", json={"revision": old_revision, "changes": {"theme": "light"}}, headers=stale)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "account_changed"
        assert bob.get("/api/ui-preferences").json() == before
        assert bob.post("/api/auth/logout", headers=stale).status_code == 409
        assert bob.get("/api/auth/me", headers=stale).json()["user"]["id"] == second["id"]
        assert bob.get("/api/sessions", headers=stale).status_code == 409
        assert bob.get("/api/admin/users", headers={"X-ChefMate-Account": second["id"]}).status_code == 403


def test_health_requires_application_database_and_recovers_without_restart(tmp_path, monkeypatch):
    app = build(tmp_path)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        login(client)
        database = app.state.accounts.database
        connect = database.connect
        def unavailable(**kwargs):
            raise OSError("password=fixture-never-expose")
        monkeypatch.setattr(database, "connect", unavailable)
        response = client.get("/healthz")
        assert response.status_code == 503 and response.json()["status"] == "unavailable"
        assert "fixture-never-expose" not in response.text
        assert client.get("/api/auth/me").status_code == 503
        page = client.get("/")
        assert page.status_code in {200, 503} and "text/html" in page.headers["content-type"]
        monkeypatch.setattr(database, "connect", connect)
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/auth/me").json()["user"]["username"] == "owner"


def test_workspace_storage_outage_has_safe_boundary_and_can_retry(tmp_path, monkeypatch):
    app = build(tmp_path)
    with TestClient(app) as client:
        register(client, "workspace-retry")
        workspace = app.state.pool.workspace
        async def unavailable(user):
            raise OSError("password=fixture-never-expose")
        monkeypatch.setattr(app.state.pool, "workspace", unavailable)
        response = client.get("/api/sessions")
        assert response.status_code == 503
        assert "fixture-never-expose" not in response.text
        assert not app.state.pool.inflight
        monkeypatch.setattr(app.state.pool, "workspace", workspace)
        assert client.get("/api/sessions").status_code == 200
