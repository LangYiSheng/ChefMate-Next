"""Personal kitchen HTTP flows through real workflow persistence and journal hooks."""

import asyncio
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.interactions import WorkspaceBinding
from chefmate_playground.journal import JournalRepository
from chefmate_playground.runtime import TurnManager
from chefmate_playground.sessions import SessionRepository
from test_app import Agent

SNAPSHOT = {"data_version": "personal-kitchen-fixture", "imported_at": "2026-10-21T00:00:00+00:00"}


class Source:
    def get_search_options(self):
        return {"status": "ok", "snapshot": SNAPSHOT, "recipe_count": 4}

    def find_ingredients(self, request):
        return {"status": "ok", "snapshot": SNAPSHOT, "items": []}

    def search_recipes(self, request):
        return {"status": "ok", "snapshot": SNAPSHOT, "total": 4,
                "items": [self.get_recipe({"recipe_id": f"r{n}"})["recipe"] for n in range(1, 5)]}

    def get_recipe(self, request):
        totals = {"calories_kcal": 200.0, "protein_g": 10.0, "fat_g": 5.0, "carbohydrate_g": 25.0,
                  "sugar_g": None, "sodium_mg": 100.0}
        identity = request["recipe_id"]
        return {"status": "ok", "snapshot": SNAPSHOT, "recipe": {
            "recipe_id": identity, "name": f"共享菜 {identity}", "servings": 2.0, "estimated_servings": False,
            "total_time_min": 10.0, "difficulty": "beginner", "warnings": [],
            "nutrition": {"total": totals, "estimated": False},
            "ingredients": [{"ingredient_id": "tomato", "canonical_name": "番茄", "raw_text": "番茄200克",
                             "raw_amount": "200", "raw_unit": "g", "grams": 200.0, "scalable": True,
                             "nutrition": {"contribution": totals}}], "steps": ["煮熟番茄。"]}}


def apply(client, uri, kind, payload, *, revision=None, action_id=None):
    snapshot = client.get(uri + "/workspace").json()["ui_snapshot"]
    return client.post(uri + "/actions", json={"action_id": action_id or str(uuid4()),
        "base_revision": snapshot["revision"] if revision is None else revision, "type": kind, "payload": payload})


def begin_meal(app, client, uri, *, ids=("r1", "r2", "r3", "r4")):
    session_id = uri.rsplit("/", 1)[1]
    apply(client, uri, "update_preferences", {"values": {"servings": 5}})
    service = app.state.workspace.service(session_id)
    approved = [{"recipe_id": identity, "data_snapshot": SNAPSHOT} for identity in ids]
    assert service.execute_tool("select_recipes", {"recipe_ids": list(ids), "data_snapshot": SNAPSHOT,
        "expected_revision": service.model_snapshot()["revision"]}, approved_recipes=approved)["status"] == "ok"
    assert service.execute_tool("advance_cooking_stage", {"stage": "prep"})["status"] == "ok"
    checklist = next(view for view in service.get_workspace()["views"] if view["kind"] == "prep_checklist")
    for item in checklist["data"]["items"]:
        assert apply(client, uri, "check_prep_item", {"item_id": item["id"], "status": "ready"}).json()["status"] == "ok"
    assert service.execute_tool("advance_cooking_stage", {"stage": "cooking"})["status"] == "ok"
    return service


def test_four_shared_dishes_complete_to_journal_and_survive_chat_deletion(tmp_path):
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "personal.db"), service=Source(), agent=Agent())
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        service = begin_meal(app, client, uri)
        plan = next(view for view in client.get(uri + "/workspace").json()["views"] if view["kind"] == "meal_plan")
        assert plan["data"]["meal_diners"] == 5
        assert [recipe["target_servings"] for recipe in plan["data"]["recipes"]] == [2.5] * 4
        assert "共享4道菜" in plan["data"]["portion_note"]
        blocked = apply(client, uri, "complete_meal", {})
        assert blocked.json()["status"] == "steps_incomplete"
        assert client.get("/api/journal").json()["summary"]["completed_meals"] == 0
        for identity in ("r1", "r2", "r3", "r4"):
            result = apply(client, uri, "complete_step", {"recipe_id": identity, "step_id": identity + ":step:0"})
            assert result.json()["status"] == "ok"
        revision = service.model_snapshot()["revision"]
        final = apply(client, uri, "complete_meal", {}, action_id="finish", revision=revision)
        assert final.status_code == 200 and final.json()["status"] == "ok"
        replay = apply(client, uri, "complete_meal", {}, action_id="finish", revision=revision)
        assert replay.json()["status"] == "ok"
        journal = client.get("/api/journal").json()
        assert journal["summary"]["completed_meals"] == 1 and journal["summary"]["completed_dishes"] == 4
        assert journal["summary"]["nutrition"]["meal_total"]["calories_kcal"] == 1000
        assert journal["summary"]["nutrition"]["per_diner_reference"]["calories_kcal"] == 200
        assert journal["summary"]["nutrition"]["meal_total"]["sugar_g"] is None
        assert len(journal["entries"]) == 1
        entry_id = journal["entries"][0]["id"]
        feedback = client.patch("/api/journal/" + entry_id, json={"rating": 4, "notes": "大家都吃得挺好"})
        assert feedback.status_code == 200 and feedback.json()["rating"] == 4
        assert client.delete(uri).status_code == 200
        remaining = client.get("/api/journal").json()["entries"]
        assert len(remaining) == 1 and remaining[0]["notes"] == "大家都吃得挺好"


def test_complete_meal_rejects_stale_revision_without_false_completion(tmp_path):
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "cas.db"), service=Source(), agent=Agent())
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        service = begin_meal(app, client, uri, ids=("r1",))
        revision = service.model_snapshot()["revision"]
        assert apply(client, uri, "complete_step", {"recipe_id": "r1", "step_id": "r1:step:0"}).json()["status"] == "ok"
        stale = apply(client, uri, "complete_meal", {}, revision=revision)
        assert stale.json()["status"] == "revision_conflict"
        journal = client.get("/api/journal").json()
        assert journal["summary"]["completed_meals"] == 0
        assert journal["entries"][0]["status"] == "attempted"
        assert client.get(uri).json()["conversation_state"]["stage"] == "cooking"


def test_personal_features_enabled_by_default_and_settings_skill_cas(tmp_path):
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "skills.db"), service=Source(), agent=Agent())
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        assert {"recipe-authoring", "personal-memory"} <= set(settings["enabled_skills"])
        metadata = client.get("/api/skills").json()["items"]
        assert {"recipe-authoring", "personal-memory"} <= {item["name"] for item in metadata}
        # Revision conflicts must leave the currently active skill set untouched.
        response = client.patch("/api/settings", json={"revision": "stale", "section": "skills", "values": {"enabled_skills": []}})
        assert response.status_code == 409
        assert client.get("/api/settings").json()["enabled_skills"] == settings["enabled_skills"]


def test_journal_io_failure_cannot_hide_persisted_answer_or_done_event(tmp_path, monkeypatch):
    async def exercise():
        repository = SessionRepository(tmp_path / "outage.db")
        journal = JournalRepository(repository.path)
        binding = WorkspaceBinding(repository, Source(), journal=journal)
        manager = TurnManager(repository, Agent(), binding)
        session_id = repository.create()["id"]
        def unavailable(state):
            raise sqlite3.OperationalError("temporary journal write failure")
        monkeypatch.setattr(journal, "sync_session", unavailable)
        turn, _ = await manager.begin(session_id, "这顿饭完成啦")
        await asyncio.wait_for(turn.task, timeout=3)
        output = []
        while not turn.events.empty():
            output.append(turn.events.get_nowait())
        assert output[-1]["type"] == "done" and output[-1]["status"] == "completed"
        assert next(event for event in output if event["type"] == "result")["text"]
        assert repository.get(session_id)["last_status"] == "completed"
        assert session_id not in manager.active
        await manager.close()
    asyncio.run(exercise())
