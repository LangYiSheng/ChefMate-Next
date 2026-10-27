"""Exercise mounted multi-model and legacy settings HTTP paths without a provider."""

import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from chefmate_playground import PlaygroundConfig, create_app


PASSWORD = "integrated-model-test-password-123"
SECRET = "integration-private-model-key"


@pytest.fixture(params=["sqlite", "postgresql"])
def mounted(request, tmp_path):
    url = os.environ.get("CHEFMATE_TEST_APP_DSN", "") if request.param == "postgresql" else ""
    if request.param == "postgresql" and not url:
        pytest.skip("Set CHEFMATE_TEST_APP_DSN for mounted PostgreSQL acceptance")
    prefix = "mounted_" + uuid4().hex[:10]
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "sessions.db", app_database_url=url,
        app_database_schema=prefix, admin_username="owner", admin_password=PASSWORD,
        model="legacy-fixture-model", api_key="legacy-integration-key", base_url="https://legacy.invalid/v1"))
    with TestClient(app) as client:
        try:
            yield app, client
        finally:
            client.cookies.clear()
    if url:
        with psycopg.connect(url) as db:
            names = [row[0] for row in db.execute("SELECT schema_name FROM information_schema.schemata")
                     if row[0].startswith(prefix + "_")]
            for name in names:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def login(client):
    response = client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def add_model(client):
    before = client.get("/api/admin/models").json()
    created = client.post("/api/admin/models", json={"revision": before["revision"], "name": "备用连接", "provider": "测试供应商",
        "model": "second-fixture-model", "base_url": "https://second.invalid/v1", "api_key": SECRET,
        "api_format": "responses", "pricing": {"input_per_million": "2", "cached_input_per_million": "1",
            "output_per_million": "4", "configured": True}})
    assert created.status_code == 201, created.text
    report = created.json()
    assert SECRET not in created.text
    second = next(row for row in report["items"] if row["id"] != report["active_id"])
    return report, second


def test_mounted_routes_enforce_admin_for_every_profile_operation(mounted):
    _, client = mounted
    assert client.get("/api/admin/models").status_code == 401
    assert client.post("/api/admin/models", json={}).status_code == 401
    registered = client.post("/api/auth/register", json={"username": "cook", "password": PASSWORD})
    assert registered.status_code == 201
    assert client.patch("/api/auth/onboarding", json={"display_name": "厨友"}).status_code == 200
    for method, path, body in (("get", "/api/admin/models", None), ("post", "/api/admin/models", {}),
        ("patch", "/api/admin/models/" + "a" * 32, {}), ("delete", "/api/admin/models/" + "a" * 32 + "?revision=1", None),
        ("post", "/api/admin/models/" + "a" * 32 + "/activate", {}), ("post", "/api/admin/models/" + "a" * 32 + "/test", {})):
        options = {} if body is None else {"json": body}
        assert getattr(client, method)(path, **options).status_code == 403


def test_http_crud_legacy_model_and_price_hooks_share_one_canonical_profile(mounted):
    app, client = mounted
    login(client)
    report, second = add_model(client)
    original = report["active_id"]
    activated = client.post(f"/api/admin/models/{second['id']}/activate", json={"revision": report["revision"]})
    assert activated.status_code == 200, activated.text
    assert activated.json()["active_id"] == second["id"]
    assert app.state.pool.control.state.config.model == "second-fixture-model"
    settings = client.get("/api/admin/settings").json()
    legacy = client.patch("/api/admin/settings", json={"revision": settings["revision"], "section": "model", "values": {"model": "legacy-route-updated"}})
    assert legacy.status_code == 200, legacy.text
    current = client.get("/api/admin/models").json()
    selected = next(row for row in current["items"] if row["active"])
    assert selected["id"] == second["id"] and selected["model"] == "legacy-route-updated"
    stale = client.patch(f"/api/admin/models/{second['id']}", json={"revision": activated.json()["revision"], "values": {"name": "过期草稿"}})
    assert stale.status_code == 409
    old_price = client.get("/api/admin/pricing").json()
    price = client.patch("/api/admin/pricing", json={"revision": old_price["revision"], "input_per_million": "9"})
    assert price.status_code == 200, price.text
    current = client.get("/api/admin/models").json()
    assert next(row for row in current["items"] if row["active"])["pricing"]["input_per_million"] == "9.000000"
    assert client.delete(f"/api/admin/models/{second['id']}?revision={current['revision']}").status_code == 409
    switched = client.post(f"/api/admin/models/{original}/activate", json={"revision": current["revision"]})
    assert switched.status_code == 200, switched.text
    removed = client.delete(f"/api/admin/models/{second['id']}?revision={switched.json()['revision']}")
    assert removed.status_code == 200 and len(removed.json()["items"]) == 1
    assert SECRET not in str(client.get("/api/admin/audit").json())


def test_runtime_patch_replays_json_body_and_preserves_model_cas(mounted):
    app, client = mounted
    login(client)
    models = client.get("/api/admin/models").json()
    settings = client.get("/api/admin/settings").json()
    changed = client.patch("/api/admin/settings", json={"revision": settings["revision"], "section": "runtime",
        "values": {**settings["runtime"], "max_tool_calls": 17, "model_max_retries": 1}})
    assert changed.status_code == 200, changed.text
    assert changed.json()["runtime"]["max_tool_calls"] == 17
    assert app.state.pool.control.state.config.max_tool_calls == 17
    assert client.get("/api/admin/models").json()["revision"] == models["revision"]
    invalid = client.patch("/api/admin/settings", json={"revision": changed.json()["revision"], "section": "runtime",
        "values": {**changed.json()["runtime"], "max_tool_calls": 500}})
    assert invalid.status_code == 422
    assert client.get("/api/admin/settings").json()["runtime"]["max_tool_calls"] == 17


def test_legacy_model_route_rejects_unknown_fields_and_invalid_json_without_echo(mounted):
    _, client = mounted
    login(client)
    before = client.get("/api/admin/models").json()
    settings = client.get("/api/admin/settings").json()
    for body in ({"revision": settings["revision"], "section": "model", "values": {}, "extra": SECRET},
                 {"revision": settings["revision"], "section": "model", "values": {"unexpected": SECRET}}, []):
        response = client.patch("/api/admin/settings", json=body)
        assert response.status_code == 422 and SECRET not in response.text
    invalid = client.patch("/api/admin/settings", content=b"{not-json", headers={"content-type": "application/json"})
    assert invalid.status_code == 422
    assert client.get("/api/admin/models").json() == before


def test_inactive_profile_http_probe_uses_selected_price_without_switching(mounted, monkeypatch):
    from chefmate_playground import model_profiles
    app, client = mounted
    owner = login(client)
    report, second = add_model(client)
    seen = []

    async def offline_probe(config, *, on_usage):
        seen.append(config)
        on_usage({"attempted_calls": 1, "calls": [{"input_tokens": 10, "cached_tokens": 3,
            "output_tokens": 5, "total_tokens": 15}]})
        return {"ok": True, "message": "离线工具协议验收完成", "api_format": config.api_format}

    monkeypatch.setattr(model_profiles, "probe_model", offline_probe)
    response = client.post(f"/api/admin/models/{second['id']}/test", json={"revision": report["revision"]})
    assert response.status_code == 200 and response.json()["ok"], response.text
    assert seen[0].api_key == SECRET and seen[0].model == "second-fixture-model"
    assert client.get("/api/admin/models").json()["active_id"] == report["active_id"]
    usage = app.state.accounts.get_usage(owner["id"])[0]
    assert usage["cost_micros"] == 37  # 7*2 + 3*1 + 5*4, using the inactive profile.
    assert usage["pricing"]["model_profile_id"] == second["id"]
