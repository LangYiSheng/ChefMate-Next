"""Release information stays readable without a model or recipe database."""

import json
import re
import tomllib
from datetime import date

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.config import APP_ROOT
from chefmate_playground.versioning import current_commit


def test_release_page_uses_the_same_version_as_about_and_openapi_without_configuration(tmp_path):
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "sessions.db"))
    expected = tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with TestClient(app) as client:
        response = client.get("/api/changelog")
        assert response.status_code == 200
        body = response.json()
        assert body["current_version"] == expected == "0.3.0"
        assert client.get("/api/about").json()["version"] == expected
        assert client.get("/openapi.json").json()["info"]["version"] == expected
        assert body["releases"] == json.loads((APP_ROOT / "releases.json").read_text(encoding="utf-8"))
        release = body["releases"][0]
        assert release["version"] == expected
        assert release["title"] and release["summary"]
        assert all(section["title"] and all(section["items"]) for section in release["sections"])
        previous = next(entry for entry in body["releases"] if entry["version"] == "0.2.1")
        assert previous["source"]["commit"] == "bd242d99ddee51f0e760dd52cfd4736f7fc68a89"
        assert previous["date"] == "2026-10-24"
        assert {section["title"] for section in previous["sections"]} == {
            "对话与做饭", "更一致的界面与设置", "个人菜谱在聊天里完成", "开发者视图更清晰",
        }
        assert not ({section["title"] for section in release["sections"]}
                    & {section["title"] for section in previous["sections"]})
        assert not ({entry["commit"] for entry in release.get("includes", [])}
                    & {entry["commit"] for entry in previous.get("includes", [])})
        for entry in body["releases"]:
            assert date.fromisoformat(entry["date"]) >= date(2026, 10, 22)
            assert entry["source"]["branch"]
            assert re.fullmatch(r"[0-9a-f]{40}", entry["source"]["commit"])
        assert client.get("/api/sessions").json()["items"] == []
        assert client.put("/api/changelog", json={"current_version": "9.9.9"}).status_code == 405
        assert client.get("/api/changelog").json() == body


def test_changelog_never_queries_an_unavailable_recipe_service(tmp_path):
    class UnavailableRecipes:
        def get_search_options(self):
            raise AssertionError("Reading release notes must not query the recipe database")

    app = create_app(
        PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "sessions.db"), service=UnavailableRecipes()
    )
    with TestClient(app) as client:
        assert client.get("/api/changelog").status_code == 200


def test_container_build_identity_works_without_git_and_rejects_invalid_metadata(tmp_path, monkeypatch):
    commit = "a1" * 20
    monkeypatch.setenv("CHEFMATE_BUILD_COMMIT", commit)
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git is not installed")
    monkeypatch.setattr("chefmate_playground.versioning.subprocess.run", no_git)
    with TestClient(create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "sessions.db"))) as client:
        assert client.get("/api/about").json()["commit"] == commit
    monkeypatch.setenv("CHEFMATE_BUILD_COMMIT", "invalid\npassword=must-not-reflect")
    assert current_commit(APP_ROOT) is None
