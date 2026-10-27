"""Identity, preference consent and memory share an atomic HTTP boundary."""

import json

import pytest

from fastapi.testclient import TestClient
from chefmate_playground.accounts import AccountError

from test_personal_tenant_isolation import isolated_app as _isolated_app
from test_tenancy import PROFILE, register

isolated_app = _isolated_app


def test_account_revoked_during_preference_sync_has_auth_boundary(isolated_app, monkeypatch):
    with TestClient(isolated_app) as client:
        user = register(client, "prefs-revoked")
        assert client.get("/api/personal-preferences").status_code == 200
        service = isolated_app.state.pool.workspaces[user["id"]].state.context.personal_preferences
        def revoked():
            raise AccountError(401, "请重新登录。")
        monkeypatch.setattr(service, "sync", revoked)
        assert client.get("/api/sessions").status_code == 401
        assert not isolated_app.state.pool.inflight


def test_preferences_single_consent_cas_and_identity_independent(isolated_app):
    with TestClient(isolated_app) as client:
        user = register(client, "preferences-qa")
        uri = "/api/personal-preferences"
        before = client.get(uri).json()
        saved = client.patch(uri, json={"revision": before["revision"], "enabled": True,
            "user_notes": "不吃香菜", "cooking_level": "confident", "household_size": 3,
            "preferences": ["清淡", "清淡", "家常菜"]})
        assert saved.status_code == 200, saved.text
        saved = saved.json()
        assert saved["preferences"] == ["清淡", "家常菜"]
        assert saved["user"]["profile"]["use_for_ai"] is True
        memory = client.get("/api/memory").json()
        assert memory["enabled"] is True
        assert memory["kitchen_profile"] == {"cooking_level": "confident", "household_size": 3, "preferences": ["清淡", "家常菜"]}
        assert client.patch(uri, json={"revision": before["revision"], "user_notes": "stale"}).status_code == 409
        changed = client.patch("/api/auth/profile", json={"display_name": "阿青"})
        assert changed.status_code == 200
        assert changed.json()["user"]["profile"] == saved["user"]["profile"]
        latest = client.get(uri).json()
        assert latest["revision"] != saved["revision"]
        disabled = client.patch(uri, json={"revision": latest["revision"], "enabled": False}).json()
        assert disabled["user"]["display_name"] == "阿青"
        assert disabled["user"]["profile"]["use_for_ai"] is False
        context = isolated_app.state.pool.workspaces[user["id"]].state.context
        assert context.memory.context() == {}
        assert isolated_app.state.pool.workspaces[user["id"]].state.manager.account_identity == {"display_name": "阿青"}
        # The old endpoint must update the same switch rather than leave two flags.
        old = client.get("/api/memory").json()
        assert client.patch("/api/memory", json={"revision": old["revision"], "user_notes": "不吃香菜", "enabled": True}).status_code == 200
        assert client.get(uri).json()["enabled"] is True
        assert client.get("/api/auth/me").json()["user"]["profile"]["use_for_ai"] is True


def test_clear_summary_retains_preferences_and_legacy_clear_survives_migration(isolated_app):
    with TestClient(isolated_app) as client:
        user = register(client, "memory-clear-qa")
        uri = "/api/personal-preferences"
        before = client.get(uri).json()
        current = client.patch(uri, json={"revision": before["revision"], "enabled": True, "user_notes": "常做两人份"}).json()
        context = isolated_app.state.pool.workspaces[user["id"]].state.context
        snapshot = context.memory.snapshot()
        context.memory.update_summary("最近在学煎蛋", snapshot["revision"])
        current = client.get(uri).json()
        assert current["summary"]
        cleared = client.delete(uri + "/summary", params={"revision": current["revision"]}).json()
        assert cleared["summary"] == ""
        assert cleared["user_notes"] == "常做两人份"
        assert cleared["preferences"] == PROFILE["preferences"]
        assert context.memory.context()["kitchen_profile"]["household_size"] == PROFILE["household_size"]
        # Recreate the pre-unification shape after the user explicitly cleared memory.
        context.memory.clear(context.memory.snapshot()["revision"])
        with isolated_app.state.accounts._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            profile = json.loads(db.execute("SELECT profile FROM accounts WHERE id=?", (user["id"],)).fetchone()[0])
            profile.pop("memory_enabled", None)
            db.execute("UPDATE accounts SET profile=? WHERE id=?", (json.dumps(profile), user["id"]))
        assert client.get(uri).status_code == 200
        assert context.memory.snapshot()["kitchen_profile"] == {}
        assert client.patch("/api/auth/profile", json={"display_name": "新称呼"}).status_code == 200
        assert client.get(uri).status_code == 200
        assert context.memory.snapshot()["kitchen_profile"] == {}


def test_validation_is_atomic_and_users_cannot_select_another_profile(isolated_app):
    with TestClient(isolated_app) as alice, TestClient(isolated_app) as bob:
        first = register(alice, "prefs-alice")
        register(bob, "prefs-bob")
        uri = "/api/personal-preferences"
        before = alice.get(uri).json()
        for changes in ({"household_size": True}, {"household_size": 21}, {"enabled": 1},
                        {"preferences": [""]}, {"target_id": "other"}, {"user_notes": "x" * 6001}):
            assert alice.patch(uri, json={"revision": before["revision"], **changes}).status_code == 422
            assert alice.get(uri).json() == before
        assert bob.patch(uri, json={"revision": before["revision"], "user_notes": "other"}).status_code == 409
        assert bob.patch(uri, json={"revision": before["revision"], "user_notes": "other"}, headers={"X-ChefMate-Account": first["id"]}).status_code == 409


@pytest.mark.parametrize("memory_kind", ["user_notes", "summary"])
@pytest.mark.parametrize("next_round", [1, 2])
def test_disabling_note_or_summary_only_context_stops_next_provider_entry(isolated_app, memory_kind, next_round):
    """Consent is checked even without a kitchen projection or on a retry round."""
    from chefmate_agent.boundary import _AgentFailure
    from test_tenancy import MeteredAgent

    class MemoryOnlyAgent(MeteredAgent):
        provider_entries = 0
        disable = None

        async def run_turn(self, messages, *, on_event, memory_context=None, user_identity=None, **kwargs):
            assert user_identity["display_name"] == PROFILE["display_name"]
            assert memory_context[memory_kind] == "private-fixture-memory"
            assert memory_context["kitchen_profile"] == {}
            usage = {"attempted_calls": 1, "calls": [{"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}]}
            for number in (1, next_round):
                try:
                    await on_event({"type": "context_usage", "context_usage": {"round": number, "utf8_bytes": 100, "chars": 100}})
                except _AgentFailure as error:
                    return {"status": "error", "messages": messages, "usage": usage, "error": error.error.model_dump()}
                self.provider_entries += 1
                await on_event({"type": "model_usage", "usage": usage})
                self.disable()
            return {"status": "completed", "messages": messages, "usage": usage, "assistant_text": "guard failed"}

    app, agent = isolated_app, MemoryOnlyAgent()
    app.state.pool.injected_agent = agent
    owner = next(user for user in app.state.accounts.list_users() if user["role"] == "admin")
    app.state.accounts.update_pricing(owner["id"], {"input_per_million": "1", "output_per_million": "1"})
    with TestClient(app) as client:
        user = register(client, "memory-consent-qa")
        before = client.get("/api/personal-preferences").json()
        assert client.patch("/api/personal-preferences", json={"revision": before["revision"], "enabled": True}).status_code == 200
        session = client.post("/api/sessions").json()
        context = app.state.pool.workspaces[user["id"]].state.context
        memory = context.memory
        cleared = memory.clear(memory.snapshot()["revision"])
        if memory_kind == "summary":
            memory.update_summary("private-fixture-memory", cleared["revision"])
        else:
            # A legacy notes-only memory or a tool-written summary may survive
            # with an explicitly cleared kitchen projection.
            memory.update_user("private-fixture-memory", cleared["revision"])
        service = context.personal_preferences
        agent.disable = lambda: service.update(service.snapshot()["revision"], {"enabled": False})
        response = client.post(f"/api/sessions/{session['id']}/turn", json={"message": "offline consent boundary"})
        assert response.status_code == 200
        assert '"code": "profile_consent_changed"' in response.text
        assert agent.provider_entries == 1
        assert memory.snapshot()["enabled"] is False
        quota = client.get("/api/auth/quota").json()["quota"]
        assert quota["used_tokens"] == 12 and quota["reserved_tokens"] == 0


def test_unhashable_cooking_level_is_rejected_atomically(isolated_app):
    with TestClient(isolated_app) as client:
        register(client, "invalid-level-qa")
        before = client.get("/api/personal-preferences").json()
        for value in ([], {}):
            response = client.patch("/api/personal-preferences", json={"revision": before["revision"], "cooking_level": value})
            assert response.status_code == 422
            assert client.get("/api/personal-preferences").json() == before


def test_history_tool_consent_measurement_stops_initially_empty_memory_turn(isolated_app):
    from chefmate_agent.boundary import _AgentFailure
    from test_tenancy import MeteredAgent

    class HistoryAgent(MeteredAgent):
        provider_entries = 0
        disable = None

        async def run_turn(self, messages, *, on_event, memory_context=None, **kwargs):
            assert memory_context is None
            usage = {"attempted_calls": 1, "calls": [{"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}]}
            await on_event({"type": "context_usage", "context_usage": {"round": 1, "utf8_bytes": 100, "memory_attached": False}})
            self.provider_entries += 1
            await on_event({"type": "model_usage", "usage": usage})
            # The core middleware marks a successful history-tool result as
            # memory access even when it contains items rather than memory.
            self.disable()
            try:
                await on_event({"type": "context_usage", "context_usage": {"round": 2, "utf8_bytes": 150, "memory_attached": True}})
            except _AgentFailure as error:
                return {"status": "error", "messages": messages, "usage": usage, "error": error.error.model_dump()}
            self.provider_entries += 1
            return {"status": "completed", "messages": messages, "usage": usage, "assistant_text": "guard failed"}

    app, agent = isolated_app, HistoryAgent()
    app.state.pool.injected_agent = agent
    owner = next(user for user in app.state.accounts.list_users() if user["role"] == "admin")
    app.state.accounts.update_pricing(owner["id"], {"input_per_million": "1", "output_per_million": "1"})
    with TestClient(app) as client:
        user = register(client, "history-consent-qa")
        before = client.get("/api/personal-preferences").json()
        assert client.patch("/api/personal-preferences", json={"revision": before["revision"], "enabled": True}).status_code == 200
        session = client.post("/api/sessions").json()
        context = app.state.pool.workspaces[user["id"]].state.context
        context.memory.clear(context.memory.snapshot()["revision"])
        service = context.personal_preferences
        agent.disable = lambda: service.update(service.snapshot()["revision"], {"enabled": False})
        response = client.post(f"/api/sessions/{session['id']}/turn", json={"message": "offline history boundary"})
        assert response.status_code == 200 and '"code": "profile_consent_changed"' in response.text
        assert agent.provider_entries == 1
        assert client.get("/api/auth/quota").json()["quota"]["used_tokens"] == 12
