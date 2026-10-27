"""Timer discovery restores background chats after a full browser refresh."""

import copy
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.sessions import SessionRepository
from test_app import Agent, Service


def timer(status="running"):
    return {"id": "timer-1", "label": "后台炖汤", "status": status, "seconds": 300,
            "remaining_seconds": 200.0, "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
            if status == "running" else None, "basis": "user"}


def test_background_timer_chats_discoverable_without_loading_or_mutating_them(tmp_path):
    repo = SessionRepository(tmp_path / "timers.db")
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=repo.path), service=Service(), agent=Agent(), repository=repo)
    current, background, paused, finished = [repo.create() for _ in range(4)]
    background.update(title="另一段对话的汤", workspace={"timers": [timer()]})
    paused.update(title="稍后继续蒸", workspace={"timers": [timer("paused")]})
    finished.update(title="刷新期间已经到时", workspace={"timers": [timer("finished")]})
    for state in (background, paused, finished):
        repo.save(state)
    before = copy.deepcopy(repo.list())
    with TestClient(app) as client:
        # Loading the current chat must not be required for background discovery.
        assert client.get("/api/sessions/" + current["id"]).status_code == 200
        result = client.get("/api/timer-sessions")
        assert result.status_code == 200
        assert {item["id"] for item in result.json()["items"]} == {background["id"], paused["id"], finished["id"]}
        assert {item["id"]: item["title"] for item in result.json()["items"]}[background["id"]] == "另一段对话的汤"
        assert all(set(item) == {"id", "title"} for item in result.json()["items"])
    assert repo.list() == before


def test_cancelled_empty_stale_finished_or_invalid_workspaces_are_not_reported(tmp_path):
    repo = SessionRepository(tmp_path / "excluded.db")
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=repo.path), service=Service(), agent=Agent(), repository=repo)
    cancelled, empty, stale, invalid = [repo.create() for _ in range(4)]
    cancelled["workspace"] = {"timers": [timer("cancelled")]}
    empty["workspace"] = {"timers": []}
    stale.update(updated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                 workspace={"timers": [timer("finished")]})
    invalid["workspace"] = {"timers": [{**timer(), "seconds": -1}]}
    for state in (cancelled, empty, stale, invalid):
        repo.save(state)
    before = copy.deepcopy(repo.list())
    with TestClient(app) as client:
        assert client.get("/api/timer-sessions").json() == {"items": []}
    assert repo.list() == before
