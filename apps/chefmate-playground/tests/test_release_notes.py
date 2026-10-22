"""Release information stays readable without a model or recipe database."""

import json
import tomllib
from datetime import date

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.config import APP_ROOT


def test_release_page_uses_the_same_version_as_about_and_openapi_without_configuration(tmp_path):
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "sessions.db"))
    expected = tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with TestClient(app) as client:
        response = client.get("/api/changelog")
        assert response.status_code == 200
        body = response.json()
        assert body["current_version"] == expected == "0.2.0"
        assert client.get("/api/about").json()["version"] == expected
        assert client.get("/openapi.json").json()["info"]["version"] == expected
        assert body["releases"] == json.loads((APP_ROOT / "releases.json").read_text(encoding="utf-8"))
        release = body["releases"][0]
        assert release["version"] == expected
        assert date.fromisoformat(release["date"]) == date(2026, 10, 22)
        assert release["title"] and release["summary"]
        assert all(section["title"] and all(section["items"]) for section in release["sections"])
        assert client.get("/api/sessions").json()["items"] == []
        assert client.put("/api/changelog", json={"current_version": "9.9.9"}).status_code == 405
        assert client.get("/api/changelog").json() == body


def test_changelog_never_queries_an_unavailable_recipe_service(tmp_path):
    class UnavailableRecipes:
        def get_search_options(self):
            raise AssertionError("Reading release notes must not query the recipe database")

    app = create_app(
        PlaygroundConfig(sessions_database=tmp_path / "sessions.db"), service=UnavailableRecipes()
    )
    with TestClient(app) as client:
        assert client.get("/api/changelog").status_code == 200
