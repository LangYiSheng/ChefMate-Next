"""A legacy model-evaluation run must never inherit daily application schemas."""

import runpy
from pathlib import Path

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app


def test_model_evaluation_uses_only_its_output_database(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts" / "evaluate_agent_quality.py"
    isolated_config = runpy.run_path(str(script))["isolated_config"]
    daily = PlaygroundConfig(sessions_database=tmp_path / "daily" / "sessions.db",
        app_database_url="postgresql://fixture:unreachable@127.0.0.1:1/daily",
        app_database_schema="daily")
    output = tmp_path / "evaluation"
    candidate = isolated_config(daily, output)
    with TestClient(create_app(candidate)) as client:
        created = client.post("/api/sessions")
        assert created.status_code == 200
        assert client.get("/api/sessions/" + created.json()["id"]).status_code == 200
    assert (output / "sessions.db").is_file()
    assert not daily.sessions_database.parent.exists()
    assert daily.auth_enabled and daily.app_database_schema == "daily" and daily.app_database_url
