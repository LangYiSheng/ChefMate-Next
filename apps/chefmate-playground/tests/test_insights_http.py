"""HTTP coverage for public usage ranges and installation metadata."""

import tomllib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.components import DEFAULT_COMPONENTS
from chefmate_playground.config import APP_ROOT
from test_app import Agent, Service


@pytest.fixture
def usage_client(tmp_path):
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "insights.db"),
                     service=Service(), agent=Agent())
    ledger = app.state.usage
    ledger._now = lambda: datetime(2026, 10, 21, 4, tzinfo=timezone.utc)
    observations = (
        ("today", "2026-10-20T16:05:00+00:00", 100, "current-model"),
        ("seventh-day", "2026-10-14T16:05:00+00:00", 200, "current-model"),
        ("eighth-day", "2026-10-13T16:05:00+00:00", 300, "current-model"),
        ("old", "2026-09-01T04:00:00+00:00", 400, "previous-model"),
    )
    for turn_id, created_at, input_tokens, model in observations:
        ledger.record("fixture-session", turn_id, {"model_calls": 1, "calls": [{
            "round": 1, "model": model, "input_tokens": input_tokens, "output_tokens": 10,
            "total_tokens": input_tokens + 10, "cached_tokens": None, "reasoning_tokens": None,
        }]}, created_at=created_at, status="completed")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(("days", "period_days", "start", "turns", "inputs"), [
    ("7", 7, "2026-10-15", 2, 300),
    ("30", 30, "2026-09-22", 3, 600),
    ("all", None, "2026-09-01", 4, 1000),
])
def test_usage_ranges_filter_observations_by_local_day(usage_client, days, period_days, start, turns, inputs):
    response = usage_client.get("/api/usage", params={"days": days})
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == {"from": start, "to": "2026-10-21", "days": period_days}
    assert body["summary"]["turns"] == turns
    assert body["summary"]["model_calls"] == turns
    assert body["summary"]["input_tokens"] == inputs
    assert body["summary"]["output_tokens"] == turns * 10
    assert body["summary"]["total_tokens"] == inputs + turns * 10
    assert body["today"]["turns"] == 1
    assert body["today"]["input_tokens"] == 100
    assert body["all_time"]["turns"] == 4
    assert body["all_time"]["input_tokens"] == 1000
    assert sum(day["turns"] for day in body["daily"]) == turns
    assert sum(model["input_tokens"] for model in body["models"]) == inputs
    expected_models = {"current-model", "previous-model"} if days == "all" else {"current-model"}
    assert {item["model"] for item in body["models"]} == expected_models
    assert body["summary"]["cached_tokens"] is None
    assert body["summary"]["coverage"]["cached_tokens"] == {"reported": 0, "calls": turns}


def test_usage_default_range_is_thirty_days(usage_client):
    default = usage_client.get("/api/usage")
    explicit = usage_client.get("/api/usage", params={"days": "30"})
    assert default.status_code == explicit.status_code == 200
    assert default.json() == explicit.json()


@pytest.mark.parametrize("days", ["0", "367", "-1", "1.5", "", "invalid", "ALL", "7 days", " 7", "7 ", "true"])
def test_usage_rejects_invalid_range_with_public_validation_message(usage_client, days):
    response = usage_client.get("/api/usage", params={"days": days})
    assert response.status_code == 422
    assert response.json() == {"detail": "请选择有效统计范围。"}
    assert usage_client.get("/api/usage", params={"days": "all"}).json()["summary"]["turns"] == 4


def test_reading_usage_does_not_create_model_observations(tmp_path):
    agent = Agent()
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "empty.db"), service=Service(), agent=agent)
    with TestClient(app) as client:
        for days in ("7", "30", "all"):
            response = client.get("/api/usage", params={"days": days})
            assert response.status_code == 200
            summary = response.json()
            assert summary["summary"]["turns"] == summary["all_time"]["model_calls"] == 0
            assert summary["summary"]["input_tokens"] is None
            assert summary["models"] == []
    assert agent.calls == 0


@pytest.mark.parametrize("enabled_components", [DEFAULT_COMPONENTS, ()])
def test_about_reports_project_version_database_count_and_installed_components(tmp_path, enabled_components):
    class CountedService(Service):
        def get_search_options(self):
            return {**super().get_search_options(), "recipe_count": 37}

    secret = "fixture-key-not-for-public-metadata"
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "about.db", api_key=secret,
                                     enabled_components=enabled_components),
                     service=CountedService(), agent=Agent())
    expected_version = tomllib.loads((APP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with TestClient(app) as client:
        response = client.get("/api/about")
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == expected_version
        assert body["name"] == "ChefMate"
        assert body["subtitle"] == "您的个性化烹饪助手"
        assert body["runtime"] == "本地工作台"
        assert body["recipe_count"] == client.get("/api/status").json()["database"]["recipe_count"] == 37
        installed = client.get("/api/components").json()["items"]
        assert body["component_count"] == len(installed) == len(DEFAULT_COMPONENTS)
        assert {item["id"] for item in installed if item["enabled"]} == set(enabled_components)
        assert secret not in response.text
