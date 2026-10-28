"""The real HTTP contract keeps reviewable food drafts separate from production."""

import copy

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from test_app import Agent, Service
from test_journal import cooking_state
from test_meal_logging import NOW, photo


def application(tmp_path):
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "http-food.db"), service=Service(), agent=Agent())
    app.state.journal._now = lambda: NOW
    app.state.journal.food_logs._now = lambda: NOW
    return app


def test_http_review_update_publish_unknowns_and_cooking_production_are_separate(tmp_path):
    app = application(tmp_path)
    journal = app.state.journal
    journal.sync_session(cooking_state(completed=True))
    draft = journal.food_logs.create_draft(photo(), session_id="photo-chat", image_ids=["image"])
    uri = "/api/food-logs/" + draft["id"]
    with TestClient(app) as client:
        response = client.get("/api/journal")
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["nutrition"]["meal_total"]["calories_kcal"] == 12.5
        assert data["summary"]["intake"]["total"]["calories_kcal"] is None
        assert data["summary"]["intake"]["record_count"] == 0
        assert data["drafts"]["total"] == 1 and data["food_logs"]["total"] == 0
        assert client.get(uri).json()["source_image_ids"] == ["image"]
        assert client.post(uri + "/publish", json={"revision": 99}).status_code == 409
        modified = copy.deepcopy(draft["document"])
        modified["items"][0]["portion"] = "用户核对后约半碗"
        modified["items"][0]["nutrition"]["calories_kcal"] = 120.0
        changed = client.put(uri, json={"revision": 1, "record": modified})
        assert changed.status_code == 200 and changed.json()["revision"] == 2
        assert client.put(uri, json={"revision": 1, "record": modified}).status_code == 409
        assert client.post(uri + "/publish", json={"revision": 1}).status_code == 409
        saved = client.post(uri + "/publish", json={"revision": 2})
        assert saved.status_code == 200 and saved.json()["status"] == "saved"
        assert client.post(uri + "/publish", json={"revision": 2}).json()["revision"] == 3
        summary = client.get("/api/journal").json()["summary"]
        assert summary["today"]["intake"]["total"]["calories_kcal"] == 120.0
        assert summary["intake"]["total"]["sodium_mg"] is None
        assert summary["intake"]["estimated"]
        assert summary["production"]["meal_total"]["calories_kcal"] == 12.5


def test_http_soft_delete_restore_and_repeated_confirmation_do_not_duplicate_totals(tmp_path):
    app = application(tmp_path)
    logs = app.state.journal.food_logs
    draft = logs.create_draft(photo(), image_ids=["image"])
    logs.publish(draft["id"], 1)
    uri = "/api/food-logs/" + draft["id"]
    with TestClient(app) as client:
        assert client.delete(uri).status_code == 422
        assert client.delete(uri + "?revision=99").status_code == 409
        removed = client.delete(uri + "?revision=2")
        assert removed.status_code == 200 and removed.json()["revision"] == 3
        assert client.get(uri).status_code == 404
        assert client.get("/api/food-logs").json()["total"] == 0
        assert client.get("/api/food-logs?status=deleted").json()["total"] == 1
        assert client.get("/api/journal").json()["summary"]["intake"]["record_count"] == 0
        assert client.delete(uri + "?revision=2").json()["revision"] == 3
        assert client.post(uri + "/restore", json={"revision": 2}).status_code == 409
        restored = client.post(uri + "/restore", json={"revision": 3})
        assert restored.status_code == 200 and restored.json()["status"] == "saved"
        assert restored.json()["document"] == draft["document"]
        summary = client.get("/api/journal").json()["summary"]["intake"]
        assert summary["record_count"] == 1 and summary["total"]["calories_kcal"] == 195.0


def test_http_unknown_portion_can_be_recorded_without_fabricated_calories(tmp_path):
    app = application(tmp_path)
    draft = app.state.journal.food_logs.create_draft({"title": "看不清份量的午饭", "items": [{"name": "炖菜"}]})
    uri = "/api/food-logs/" + draft["id"]
    with TestClient(app) as client:
        invalid = copy.deepcopy(draft["document"])
        invalid["items"][0]["nutrition"]["calories_kcal"] = 0.0
        assert client.put(uri, json={"revision": 1, "record": invalid}).status_code == 422
        assert client.post(uri + "/publish", json={"revision": 1}).status_code == 200
        data = client.get("/api/journal").json()
        assert data["summary"]["intake"]["record_count"] == 1
        assert data["summary"]["intake"]["total"]["calories_kcal"] is None
        assert data["summary"]["today"]["intake"]["coverage"]["calories_kcal"] == {"known": 0, "total": 1}


def test_http_rejects_invalid_fields_and_ranges(tmp_path):
    app = application(tmp_path)
    draft = app.state.journal.food_logs.create_draft(photo(), image_ids=["image"])
    uri = "/api/food-logs/" + draft["id"]
    with TestClient(app) as client:
        assert client.get("/api/food-logs?days=0").status_code == 422
        assert client.get("/api/food-logs?status=unexpected").status_code == 422
        assert client.get("/api/food-logs?limit=101").status_code == 422
        assert client.get("/api/food-logs/missing").status_code == 404
        assert client.put(uri, json={"revision": True, "record": draft["document"]}).status_code == 422
        assert client.put(uri, json={"revision": 1, "record": draft["document"], "trusted": True}).status_code == 422
        assert client.post(uri + "/publish", json={"revision": True}).status_code == 422
        assert client.post(uri + "/publish", json={"revision": 0}).status_code == 422
        wrong_source = copy.deepcopy(draft["document"])
        wrong_source["source_kind"] = "text"
        assert client.put(uri, json={"revision": 1, "record": wrong_source}).status_code == 422
