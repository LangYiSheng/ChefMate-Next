"""A cold user's synchronous setup must not stall unrelated health/auth requests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from test_tenancy import build, register


def test_cold_workspace_does_not_block_health_or_session_authentication(tmp_path, monkeypatch):
    import chefmate_playground.app as app_module

    app = build(tmp_path)
    entered, release = Event(), Event()
    factory = app_module.create_workspace_app
    def cold_factory(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Test did not release cold setup")
        return factory(*args, **kwargs)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=3) as workers:
        register(client, "cold-workspace")
        monkeypatch.setattr(app_module, "create_workspace_app", cold_factory)
        cold = workers.submit(client.get, "/api/sessions")
        try:
            assert entered.wait(2)
            health = workers.submit(client.get, "/healthz")
            identity = workers.submit(client.get, "/api/auth/me")
            assert health.result(timeout=2).status_code == 200
            assert identity.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert cold.result(timeout=10).status_code == 200
