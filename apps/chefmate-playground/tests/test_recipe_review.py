"""A conversation reviews and edits one draft; only its host-confirmed version publishes."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.cookbook import RecipeDraftRequest
from test_app import Agent, Service
from test_cookbook import draft
from test_recipe_deletion import ConfirmationFlow


class PublicationFlow(ConfirmationFlow):
    def read(self):
        result = self.tools["get_recipe_draft"].handler({"recipe_id": self.record["recipe_id"]}, self.current)
        assert result["status"] == "ok"
        self.current = replace(self.current, messages=[*self.current.messages, {
            "role": "tool", "name": "get_recipe_draft", "content": json.dumps(result)}])
        return result

    def request(self):
        result = self.tools["request_recipe_publication"].handler({
            "recipe_id": self.record["recipe_id"], "revision": self.record["revision"]}, self.current)
        assert result["status"] == "ok"
        self.question = result["pending_question"]
        self.token = result["receipt"]["confirmation_id"]
        return result

    def answer(self, option="publish", *, completed=True):
        super().answer(option, completed=completed)

    def confirm(self, token=None):
        return self.tools["confirm_recipe_publication"].handler({"confirmation_id": token or self.token}, self.current)


def test_publication_requires_review_then_host_answer_for_exact_draft_and_is_idempotent(tmp_path):
    flow = PublicationFlow(tmp_path)
    args = {"recipe_id": flow.record["recipe_id"], "revision": flow.record["revision"]}
    assert flow.tools["request_recipe_publication"].handler(args, flow.current)["status"] == "review_required"
    flow.read()
    result = flow.request()
    assert result["receipt"]["recipe_id"] == flow.record["recipe_id"]
    assert result["receipt"]["revision"] == 1 and result["pending_question"]["allow_custom"] is False
    assert flow.confirm()["status"] == "confirmation_required"
    assert flow.cookbook.retrieve(flow.record["recipe_id"])["status"] == "not_found"
    with pytest.raises(ValidationError):
        flow.tools["confirm_recipe_publication"].args_schema.model_validate({"confirmation_id": flow.token, "approved": True})
    flow.answer()
    saved = flow.confirm()
    assert saved["status"] == "ok" and saved["published"] is True
    assert saved["draft"]["recipe_id"] == flow.record["recipe_id"] and saved["draft"]["revision"] == 2
    assert flow.confirm()["draft"] == saved["draft"]
    assert flow.cookbook.retrieve(flow.record["recipe_id"])["status"] == "ok"


@pytest.mark.parametrize("change", ["revision", "other_question", "cancelled_origin", "keep", "cancelled_turn", "other_session"])
def test_confirmation_cannot_publish_a_changed_or_unconfirmed_draft(tmp_path, change):
    flow = PublicationFlow(tmp_path)
    flow.read()
    flow.request()
    if change == "revision":
        flow.cookbook.update_draft(flow.record["recipe_id"], {**draft(), "name": "新版本番茄炒蛋"}, 1)
    if change == "other_question":
        flow.repository.mutate(flow.session_id, lambda state: state["workspace"].update(
            pending_question=None, last_answer={"question_id": "other-question", "question": "另一个问题", "option_ids": ["publish"]}))
        flow.current = flow.start("confirm-turn")
    elif change == "other_session":
        another = flow.repository.create()["id"]
        flow.session_id = another
        flow.current = flow.start("confirm-turn")
    else:
        flow.answer("keep" if change == "keep" else "publish", completed=change != "cancelled_origin")
        if change == "cancelled_turn":
            flow.current.host_context.cancelled.set()
    result = flow.confirm()
    assert result["status"] != "ok" and not result.get("published")
    assert flow.cookbook.get(flow.record["recipe_id"])["status"] == "draft"


def test_revision_uses_the_same_read_draft_and_rejects_fabricated_sources_or_closed_turns(tmp_path):
    flow = PublicationFlow(tmp_path)
    args = {"recipe_id": flow.record["recipe_id"], "revision": 1, "recipe": {**draft(), "name": "家常番茄炒蛋"}}
    revise = flow.tools["revise_recipe_draft"]
    assert revise.handler(args, flow.current)["status"] == "read_required"
    flow.read()
    forged = {**args, "recipe": {**args["recipe"], "source_text": draft()["source_text"] + "模型虚构的新来源"}}
    assert revise.handler(forged, flow.current)["status"] == "invalid_recipe_source"
    result = revise.handler(args, flow.current)
    assert result["status"] == "ok" and result["draft"]["recipe_id"] == flow.record["recipe_id"]
    assert result["draft"]["revision"] == 2 and flow.cookbook.list(status="all")["total"] == 1
    assert revise.handler(args, flow.current)["status"] == "recipe_changed"
    flow.record = result["draft"]
    flow.read()
    flow.current.host_context.cancelled.set()
    assert revise.handler({**args, "revision": 2}, flow.current)["status"] == "recipe_changed"
    assert flow.cookbook.get(flow.record["recipe_id"])["revision"] == 2


def test_user_supplement_can_add_grounded_difficulty_without_inventing_unknown_metadata(tmp_path):
    flow = PublicationFlow(tmp_path)
    flow.read()
    assert flow.record["document"]["difficulty"] is None
    supplement = "难度：新手，菜名保留番茄炒蛋。"
    flow.current = replace(flow.current, messages=[*flow.current.messages, {"role": "user", "content": supplement}])
    values = {**draft(), "source_text": draft()["source_text"] + "\n\n" + supplement,
              "difficulty": "beginner", "difficulty_evidence": "难度：新手"}
    result = flow.tools["revise_recipe_draft"].handler({"recipe_id": flow.record["recipe_id"], "revision": 1,
        "recipe": values}, flow.current)
    assert result["status"] == "ok" and result["draft"]["document"]["difficulty"] == "beginner"
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate({**draft(), "difficulty": "beginner"})
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate({**values, "difficulty": "advanced"})


def test_personal_detail_and_catalog_preserve_common_contract_and_unknowns(tmp_path):
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "recipes.db"), service=Service(), agent=Agent())
    values = {**draft(), "source_text": draft()["source_text"] + "总用时15分钟。", "total_time_min": 15,
              "time_evidence": "总用时15分钟"}
    record = app.state.cookbook.create_draft(values)
    saved = app.state.cookbook.publish(record["recipe_id"], 1)
    with TestClient(app) as client:
        recipe = client.get("/api/recipes/" + saved["recipe_id"]).json()["recipe"]
        assert recipe["total_time_min"] == 15 and recipe["difficulty"] is None
        egg = recipe["ingredients"][1]
        assert egg["raw_amount"] == "2" and egg["raw_unit"] == "个" and egg["grams"] is None
        assert "amount_value" not in egg and "amount_text" not in egg
        assert set(egg["nutrition"]) == {"estimated", "fdc_id", "estimated_nutrients", "per_100g", "contribution"}
        assert all(value is None for value in egg["nutrition"]["contribution"].values())
        card = client.get("/api/cookbook?source=user").json()["items"][0]
        assert card["total_time_min"] == 15 and card["difficulty"] is None and card["ingredient_count"] == 2
        explicit = {**values, "source_text": values["source_text"] + "难度：普通", "difficulty": "normal",
                    "difficulty_evidence": "难度：普通"}
        pending = app.state.cookbook.create_draft(explicit)
        app.state.cookbook.publish(pending["recipe_id"], 1)
        assert client.get("/api/recipes/" + pending["recipe_id"]).json()["recipe"]["difficulty"] == "normal"
        assert {card["difficulty"] for card in client.get("/api/cookbook?source=user").json()["items"]} == {None, "normal"}


@pytest.mark.parametrize("new_question", [False, True])
def test_failed_publication_receipt_withdraws_only_its_own_still_pending_question(tmp_path, monkeypatch, new_question):
    flow = PublicationFlow(tmp_path)
    flow.read()
    prepare = flow.cookbook.publication.prepare
    created = {}

    def publish_between_question_and_receipt(*args, **kwargs):
        state = flow.repository.get(flow.session_id)
        created.update(state["workspace"]["pending_question"])
        # A separately authorized HTTP publication can commit while the tool's thread pauses.
        flow.cookbook.publish(flow.record["recipe_id"], flow.record["revision"])
        if new_question:
            def replace_question(current):
                workspace = current["workspace"]
                workspace["pending_question"] = {**created, "id": "new-question", "question": "另一轮的新问题"}
                workspace["revision"] += 1
            flow.repository.mutate(flow.session_id, replace_question)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(flow.cookbook.publication, "prepare", publish_between_question_and_receipt)
    result = flow.tools["request_recipe_publication"].handler({"recipe_id": flow.record["recipe_id"],
        "revision": flow.record["revision"]}, flow.current)
    assert result["status"] == "confirmation_expired"
    current = flow.repository.get(flow.session_id)["workspace"]
    assert current["revision"] == 2
    if new_question:
        assert current["pending_question"]["id"] == "new-question"
        assert current["pending_question"]["question"] == "另一轮的新问题"
    else:
        assert current["pending_question"] is None
        assert flow.current.host_context.expected_revision == current["revision"]
    assert flow.cookbook.get(flow.record["recipe_id"])["status"] == "saved"
    with flow.cookbook._connect() as db:
        assert db.execute("SELECT count(*) FROM recipe_publish_confirmations").fetchone()[0] == 0
