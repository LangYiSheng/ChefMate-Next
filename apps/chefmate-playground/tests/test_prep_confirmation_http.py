"""Explicit preparation buttons work through the HTTP action boundary without a model turn."""

from uuid import uuid4

from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from test_app import Agent
from test_personal_kitchen import SNAPSHOT, Source, apply


def test_select_prepare_and_start_cooking_through_http(tmp_path):
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "prep.db"), service=Source(), agent=Agent())
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["id"]
        uri = "/api/sessions/" + session_id
        service = app.state.workspace.service(session_id)
        service.execute_tool("select_recipes", {"recipe_ids": ["r1"], "data_snapshot": SNAPSHOT,
            "expected_revision": service.model_snapshot()["revision"]}, approved_recipes=[{"recipe_id": "r1", "data_snapshot": SNAPSHOT}])
        selected = apply(client, uri, "select_recipes", {"recipe_ids": ["r1"], "start_prep": True})
        assert selected.status_code == 200 and selected.json()["workspace"]["stage"] == "prep"
        view = next(view for view in selected.json()["views"] if view["kind"] == "prep_checklist")
        item_id = view["data"]["items"][0]["id"]
        assert apply(client, uri, "check_prep_item", {"item_id": item_id, "status": "pending", "availability": "missing"}).json()["status"] == "ok"

        def confirm():
            return client.post(uri + "/actions", json={"type": "confirm_prep", "payload": {}, "view_id": view["id"],
                "action_id": str(uuid4()), "base_revision": service.model_snapshot()["revision"]})

        assert confirm().json()["status"] == "ingredients_missing"
        apply(client, uri, "check_prep_item", {"item_id": item_id, "status": "ready"})
        started = confirm()
        assert started.status_code == 200 and started.json()["workspace"]["stage"] == "cooking"
        assert client.get(uri).json()["conversation_state"]["stage"] == "cooking"
        assert client.get("/api/journal").json()["entries"][0]["status"] == "attempted"
        # The generic phase-switch endpoint still requires assistant-managed intent.
        assert apply(client, uri, "change_stage", {"stage": "completed"}).status_code == 403
