"""Every personal repository remains private through the authenticated HTTP API."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from test_cookbook import draft
from test_journal import cooking_state
from test_meal_logging import text_record
from test_personal_kitchen import Source
from test_tenancy import PASSWORD, register


@pytest.fixture(params=["sqlite", "postgresql"])
def isolated_app(tmp_path, request):
    url = os.environ.get("CHEFMATE_TEST_APP_DSN", "") if request.param == "postgresql" else ""
    if request.param == "postgresql" and not url:
        pytest.skip("CHEFMATE_TEST_APP_DSN required for PostgreSQL privacy acceptance")
    prefix = "private_" + uuid4().hex[:10]
    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "sessions.db", app_database_url=url,
        app_database_schema=prefix, admin_username="owner", admin_password=PASSWORD), service=Source())
    yield app
    if url:
        import psycopg
        from psycopg import sql
        with psycopg.connect(url) as db:
            names = [row[0] for row in db.execute("SELECT schema_name FROM information_schema.schemata")
                     if row[0].startswith(prefix + "_")]
            for name in names:
                db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def test_cookbook_favorites_memory_food_logs_and_journal_are_user_scoped(isolated_app):
    app = isolated_app
    with TestClient(app) as alice, TestClient(app) as bob:
        user = register(alice, "private-alice")
        register(bob, "private-bob")
        session = alice.post("/api/sessions").json()
        context = app.state.pool.workspaces[user["id"]].state.context
        pending = context.cookbook.create_draft(draft(), session_id=session["id"])
        recipe = context.cookbook.publish(pending["recipe_id"], pending["revision"])
        recipe_uri = "/api/cookbook/" + recipe["recipe_id"]
        assert alice.put("/api/favorites/" + recipe["recipe_id"], json={"favorite": True}).status_code == 200
        food = context.journal.food_logs.create_draft(text_record())
        food = context.journal.food_logs.publish(food["id"], food["revision"])
        memory = context.memory.snapshot()
        context.memory.update_user("仅Alice可见：不吃香菜", memory["revision"])
        state = cooking_state(completed=True)
        state["id"] = session["id"]
        context.journal.sync_session(state)
        entries = alice.get("/api/journal").json()["entries"]
        assert len(entries) == 1
        assert alice.get(recipe_uri).json()["document"]["name"] == "番茄炒蛋"
        assert bob.get(recipe_uri).status_code == 404
        assert bob.put(recipe_uri, json={"revision": 1, "recipe": draft()}).status_code == 404
        assert bob.delete(recipe_uri, params={"revision": 2}).status_code == 404
        assert bob.post(recipe_uri + "/publish", json={"revision": 1}).status_code == 404
        assert bob.post(recipe_uri + "/restore", json={"revision": 2}).status_code == 404
        assert bob.get("/api/recipes/" + recipe["recipe_id"]).status_code == 404
        assert bob.put("/api/favorites/" + recipe["recipe_id"], json={"favorite": True}).status_code == 404
        assert bob.get("/api/cookbook?source=user").json()["items"] == []
        assert bob.get("/api/cookbook?source=favorites").json()["items"] == []
        assert bob.get("/api/memory").json()["user_notes"] == ""
        assert bob.get("/api/food-logs").json()["total"] == 0
        food_uri = "/api/food-logs/" + food["id"]
        assert bob.get(food_uri).status_code == 404
        assert bob.put(food_uri, json={"revision": 2, "record": text_record()}).status_code == 404
        assert bob.delete(food_uri, params={"revision": 2}).status_code == 404
        assert bob.post(food_uri + "/restore", json={"revision": 2}).status_code == 404
        assert bob.get("/api/journal").json()["entries"] == []
        assert bob.patch("/api/journal/" + entries[0]["id"], json={"notes": "篡改"}).status_code == 404
        assert alice.get("/api/memory").json()["user_notes"].startswith("仅Alice")
        assert alice.get(food_uri).json()["document"]["title"] == food["document"]["title"]
