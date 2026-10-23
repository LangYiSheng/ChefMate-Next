"""The React application has one shell and no legacy entrypoint fallback."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chefmate_playground.frontend import mount_frontend


def test_all_ui_routes_serve_the_same_build_and_hashed_assets(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        '<div id="root"></div><script type="module" src="/static/assets/main-abc.js"></script>', encoding="utf-8"
    )
    (tmp_path / "assets" / "main-abc.js").write_text("export const app = 'React';", encoding="utf-8")
    app = FastAPI()
    mount_frontend(app, tmp_path)
    with TestClient(app) as client:
        for path in ("/", "/settings", "/recipes", "/journal"):
            response = client.get(path)
            assert response.status_code == 200
            assert 'id="root"' in response.text
            assert "settings.html" not in response.text
        assert client.get("/static/assets/main-abc.js").status_code == 200
        assert client.get("/static/app.js").status_code == 404
        assert client.get("/static/settings.html").status_code == 404
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/static/../secret").status_code == 404


def test_missing_build_has_a_clear_error_without_a_legacy_fallback(tmp_path):
    app = FastAPI()
    mount_frontend(app, tmp_path / "missing")
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 503
        assert "前端尚未构建" in response.text
        assert client.get("/static/app.js").status_code == 404
