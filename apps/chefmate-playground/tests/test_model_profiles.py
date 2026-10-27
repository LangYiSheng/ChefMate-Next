"""Saved connections keep secrets private and apply one connection/tariff together."""

import asyncio
import json
import os
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from langchain_core.messages import AIMessage, ToolMessage

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.accounts import AccountError
from chefmate_playground.model_profiles import ModelProfilesRegistry
from chefmate_playground.routes.model_profiles import build_router
from chefmate_playground.settings import public_settings, settings_path


PASSWORD = "profile-fixture-password-123"
SECRET = "private-profile-fixture-key-never-echo"


@pytest.fixture(params=["sqlite", "postgresql"])
def registry(request, tmp_path):
    url = ""
    prefix = "models_" + uuid4().hex[:12]
    if request.param == "postgresql":
        url = os.environ.get("CHEFMATE_TEST_APP_DSN", "")
        if not url:
            pytest.skip("Set CHEFMATE_TEST_APP_DSN for isolated model-profile PostgreSQL checks")
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "sessions.db", app_database_url=url,
        app_database_schema=prefix, admin_username="owner", admin_password=PASSWORD,
        model="legacy-model", api_key="legacy-fixture-key", base_url="https://legacy.invalid/v1"))
    pool = app.state.pool
    models = getattr(pool, "model_profiles", None) or ModelProfilesRegistry(pool)
    pool.model_profiles = models
    owner = next(user for user in pool.accounts.list_users() if user["role"] == "admin")
    try:
        yield app, models, owner
    finally:
        asyncio.run(pool.close())
        if url:
            with psycopg.connect(url) as db:
                names = [row[0] for row in db.execute("SELECT schema_name FROM information_schema.schemata")
                         if row[0].startswith(prefix + "_")]
                for name in names:
                    db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def connection(**changes):
    return {"name": "第二连接", "provider": "测试提供商", "model": "model-b", "base_url": "https://second.invalid/v1",
        "api_key": SECRET, "api_format": "responses", "supports_images": True, "max_images": 4,
        "pricing": {"input_per_million": "2", "cached_input_per_million": "1", "output_per_million": "4", "configured": True}, **changes}


def create(models, owner, **changes):
    before = models.list(owner["id"])
    accepted = asyncio.run(models.change(owner["id"], before["revision"], "create", values=connection(**changes)))
    return accepted, next(item for item in accepted["items"] if item["id"] != before["active_id"])


def test_bootstrap_imports_legacy_without_exposing_secrets_and_is_idempotent(registry):
    app, models, owner = registry
    before = models.list(owner["id"])
    assert len(before["items"]) == 1 and before["items"][0]["model"] == "legacy-model"
    assert before["items"][0]["api_key_set"] and "legacy-fixture-key" not in json.dumps(before)
    again = ModelProfilesRegistry(app.state.pool).list(owner["id"])
    assert again == before
    assert app.state.accounts.pricing()["model_profile_id"] == before["active_id"]


def test_inactive_save_then_activate_updates_config_and_price_together(registry):
    app, models, owner = registry
    before = models.list(owner["id"])
    accepted, second = create(models, owner)
    assert app.state.pool.control.state.config.model == "legacy-model"
    assert accepted["active_id"] == before["active_id"] and SECRET not in json.dumps(accepted)
    active = asyncio.run(models.change(owner["id"], accepted["revision"], "activate", second["id"]))
    config = app.state.pool.control.state.config
    assert config.model == "model-b" and config.api_key == SECRET and config.api_format == "responses"
    assert config.supports_images and config.max_images == 4
    tariff = app.state.accounts.pricing()
    assert tariff["input_per_million"] == "2.000000" and tariff["model_profile_id"] == second["id"]
    assert sum(item["active"] for item in active["items"]) == 1
    assert json.loads(settings_path(config).read_text(encoding="utf-8"))["model"] == "model-b"


def test_cas_and_busy_guard_allow_editing_only_inactive_connections(registry):
    app, models, owner = registry
    accepted, second = create(models, owner)
    app.state.pool.inflight["busy-fixture"] = 1
    try:
        with pytest.raises(AccountError, match="正在运行"):
            asyncio.run(models.change(owner["id"], accepted["revision"], "activate", second["id"]))
        with pytest.raises(AccountError, match="正在运行"):
            asyncio.run(models.change(owner["id"], accepted["revision"], "update", accepted["active_id"], {"model": "new-model"}))
        updated = asyncio.run(models.change(owner["id"], accepted["revision"], "update", second["id"], {"name": "保留草稿"}))
        assert next(item for item in updated["items"] if item["id"] == second["id"])["name"] == "保留草稿"
        with pytest.raises(AccountError, match="已变化"):
            asyncio.run(models.change(owner["id"], accepted["revision"], "update", second["id"], {"model": "stale"}))
    finally:
        app.state.pool.inflight.clear()


def test_key_reuse_requires_same_provider_and_origin_and_clear_is_explicit(registry):
    _, models, owner = registry
    accepted, second = create(models, owner)
    for changes in ({"provider": "另一个提供商"}, {"base_url": "https://third.invalid/v1"}):
        with pytest.raises(AccountError, match="专用新密钥"):
            asyncio.run(models.change(owner["id"], accepted["revision"], "update", second["id"], changes))
    retained = asyncio.run(models.change(owner["id"], accepted["revision"], "update", second["id"], {"api_key": "", "model": "model-b2"}))
    assert next(item for item in retained["items"] if item["id"] == second["id"])["api_key_set"]
    cleared = asyncio.run(models.change(owner["id"], retained["revision"], "update", second["id"], {"clear_api_key": True}))
    assert not next(item for item in cleared["items"] if item["id"] == second["id"])["api_key_set"]
    with pytest.raises(AccountError, match="专用密钥"):
        asyncio.run(models.change(owner["id"], cleared["revision"], "activate", second["id"]))
    with pytest.raises(AccountError, match="同时"):
        asyncio.run(models.change(owner["id"], cleared["revision"], "update", second["id"], {"clear_api_key": True, "api_key": SECRET}))


def test_active_delete_is_refused_and_failed_price_write_restores_file_and_live_state(registry, monkeypatch):
    app, models, owner = registry
    accepted, second = create(models, owner)
    with pytest.raises(AccountError, match="先切换"):
        asyncio.run(models.change(owner["id"], accepted["revision"], "delete", accepted["active_id"]))
    context = app.state.pool.control.state.context
    path = settings_path(context.config)
    original = path.read_bytes() if path.exists() else None
    old_config = context.config
    def failure(db, profile, **kwargs):
        raise OSError("private-connection-secret-must-not-escape")
    monkeypatch.setattr(models, "_mirror_price", failure)
    with pytest.raises(OSError):
        asyncio.run(models.change(owner["id"], accepted["revision"], "activate", second["id"]))
    assert models.list(owner["id"]) == accepted
    assert context.config == old_config
    assert (path.read_bytes() if path.exists() else None) == original


def test_legacy_model_update_hook_preserves_profile_and_can_recover_from_file_drift(registry):
    app, models, owner = registry
    context = app.state.pool.control.state.context
    revision = public_settings(context.config, components=context.components)["revision"]
    updated = asyncio.run(models.update_legacy_active(owner["id"], {"model": "legacy-updated"}, revision))
    assert updated["model"]["model"] == "legacy-updated"
    canonical = models.list(owner["id"])
    context.config = replace(context.config, model="drifted-file")
    ModelProfilesRegistry(app.state.pool)
    assert context.config.model == "legacy-updated" and models.list(owner["id"]) == canonical


def test_legacy_price_update_binds_active_profile_and_invalidates_stale_editor(registry):
    app, models, owner = registry
    before = models.list(owner["id"])
    price = app.state.accounts.pricing()
    app.state.pool.inflight["existing-turn"] = 1
    try:
        changed = models.update_legacy_pricing(owner["id"], {"revision": price["revision"], "input_per_million": "7"})
        assert changed["model_profile_id"] == before["active_id"] and changed["input_per_million"] == "7.000000"
        latest = models.list(owner["id"])
        assert latest["revision"] == before["revision"] + 1 and latest["items"][0]["pricing"]["input_per_million"] == "7.000000"
        with pytest.raises(AccountError, match="已变化"):
            asyncio.run(models.change(owner["id"], before["revision"], "update", before["active_id"], {"name": "过期编辑"}))
        with pytest.raises(AccountError, match="已变化"):
            models.update_legacy_pricing(owner["id"], {"revision": price["revision"], "input_per_million": "8"})
    finally:
        app.state.pool.inflight.clear()


def test_http_admin_boundary_and_validation_never_return_profile_keys(registry):
    app, models, _ = registry
    if not any(getattr(route, "path", None) == "/api/admin/models" for route in app.routes):
        app.include_router(build_router(app.state.pool))
    with TestClient(app) as admin, TestClient(app) as member:
        assert member.get("/api/admin/models").status_code == 401
        assert member.post("/api/auth/register", json={"username": "ordinary-model-user", "password": PASSWORD}).status_code == 201
        assert member.get("/api/admin/models").status_code == 403
        assert admin.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
        listing = admin.get("/api/admin/models").json()
        response = admin.post("/api/admin/models", json={"revision": listing["revision"], **connection()})
        assert response.status_code == 201 and SECRET not in response.text
        invalid = admin.post("/api/admin/models", json={"revision": response.json()["revision"], **connection(api_key={"secret": SECRET})})
        assert invalid.status_code == 422 and SECRET not in invalid.text
        assert models.list(next(user for user in app.state.accounts.list_users() if user["role"] == "admin")["id"])["active_id"] == listing["active_id"]


def test_probe_runs_native_tools_with_selected_profile_tariff_without_activation(registry, monkeypatch):
    app, models, owner = registry
    accepted, second = create(models, owner)
    original_price = app.state.accounts.pricing()
    class FakeModel:
        def __init__(self, **kwargs):
            assert kwargs["model"] == "model-b" and kwargs["api_key"] == SECRET
            assert kwargs["base_url"] == "https://second.invalid/v1" and kwargs["use_responses_api"]
            assert kwargs["max_tokens"] == 256 and kwargs["max_retries"] == 0
        def bind_tools(self, *args, **kwargs):
            return self
        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="OK", usage_metadata={"input_tokens": 7, "output_tokens": 1, "total_tokens": 8})
            return AIMessage(content="", tool_calls=[{"name": "connection_echo", "args": {"value": "chefmate"}, "id": "echo-profile"}],
                usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    monkeypatch.setattr("chefmate_agent.provider.IntegrityChatOpenAI", FakeModel)
    result = asyncio.run(models.test(owner["id"], second["id"], accepted["revision"]))
    assert result["ok"] and SECRET not in json.dumps(result)
    assert app.state.pool.control.state.config.model == "legacy-model" and app.state.accounts.pricing() == original_price
    row = app.state.accounts.get_usage(owner["id"])[0]
    assert row["total_tokens"] == 20 and row["cost_micros"] == 46 and not row["incomplete"]
    assert row["pricing"]["model_profile_id"] == second["id"] and row["model"] == "model-b"
    last_test = next(item for item in models.list(owner["id"])["items"] if item["id"] == second["id"])["last_test"]
    assert last_test["ok"] and not last_test["outdated"]


def test_probe_missing_usage_retains_selected_profile_bound(registry, monkeypatch):
    app, models, owner = registry
    accepted, second = create(models, owner)
    async def fail(config, *, on_usage):
        on_usage({"attempted_calls": 1, "calls": []})
        return {"ok": False, "message": "测试失败"}
    monkeypatch.setattr("chefmate_playground.model_profiles.probe_model", fail)
    result = asyncio.run(models.test(owner["id"], second["id"], accepted["revision"]))
    assert result["ok"] is False
    row = app.state.accounts.get_usage(owner["id"])[0]
    assert row["incomplete"] and row["estimated_cost_micros"] == 18432
    assert row["pricing"]["model_profile_id"] == second["id"] and row["status"] == "error"


def test_probe_keeps_original_price_when_inactive_profile_is_edited_during_request(registry, monkeypatch):
    app, models, owner = registry
    accepted, second = create(models, owner)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(config, *, on_usage):
            entered.set()
            await release.wait()
            on_usage({"attempted_calls": 1, "calls": [{"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}]})
            return {"ok": True, "message": "通过"}
        monkeypatch.setattr("chefmate_playground.model_profiles.probe_model", slow)
        task = asyncio.create_task(models.test(owner["id"], second["id"], accepted["revision"]))
        await entered.wait()
        await models.change(owner["id"], accepted["revision"], "update", second["id"], {"pricing": {"input_per_million": "9"}})
        release.set()
        await task
    asyncio.run(scenario())
    row = app.state.accounts.get_usage(owner["id"])[0]
    assert row["cost_micros"] == 28 and row["pricing"]["input_per_million"] == "2.000000"
    current = next(item for item in models.list(owner["id"])["items"] if item["id"] == second["id"])
    assert current["pricing"]["input_per_million"] == "9.000000" and current["last_test"]["outdated"]
