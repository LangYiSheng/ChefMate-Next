"""Personal recipe drafts preserve provenance and are usable only after confirmation."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from chefmate_agent import ToolExecutionContext
from chefmate_playground.cookbook import CookbookRepository, RecipeDraftRequest, make_recipe_tools


def draft():
    return {"name": "番茄炒蛋", "source_text": "番茄炒蛋：番茄200g，鸡蛋2个。番茄切块，鸡蛋打散，炒熟。",
            "ingredients": [{"name": "番茄", "amount": "200g", "grams": 200.0, "amount_evidence": "番茄200g"},
                            {"name": "鸡蛋", "amount": "2个", "grams": None}],
            "steps": ["番茄切块，鸡蛋打散，炒熟。"]}


def context(values=None, *, cancelled=None, images=None):
    host = SimpleNamespace(session_id="chat", turn_id="turn", cancelled=cancelled)
    message = {"role": "user", "content": (values or draft())["source_text"]}
    if images:
        message["images"] = images
    return ToolExecutionContext({}, host, [message], call_id="create-1")


def test_unknown_time_servings_and_egg_weight_stay_unknown_after_existing_processor(tmp_path):
    cookbook = CookbookRepository(tmp_path / "sessions.db")
    record = cookbook.create_draft(draft())
    doc = record["document"]
    assert doc["servings"] is None and doc["total_time_min"] is None
    assert doc["ingredients"][1]["grams"] is None
    assert doc["ingredients"][1]["raw_amount"] == "2" and doc["ingredients"][1]["raw_unit"] == "个"
    assert record["origin"] == "user" and record["is_custom"] is True
    assert record["nutrition"]["total"]["calories_kcal"] is None
    assert cookbook.retrieve(record["recipe_id"])["status"] == "not_found"


def test_confirmation_creates_versioned_reference_and_repeated_confirmation_is_idempotent(tmp_path):
    cookbook = CookbookRepository(tmp_path / "sessions.db")
    pending = cookbook.create_draft(draft())
    saved = cookbook.publish(pending["recipe_id"], pending["revision"])
    assert saved["status"] == "saved" and saved["revision"] == 2
    assert cookbook.publish(pending["recipe_id"], pending["revision"])["revision"] == 2
    retrieved = cookbook.retrieve(saved["recipe_id"], cookbook.snapshot(saved))
    assert retrieved["status"] == "ok" and retrieved["recipe"]["name"] == "番茄炒蛋"
    assert retrieved["recipe"]["is_custom"]
    assert cookbook.retrieve(saved["recipe_id"], cookbook.snapshot(pending))["status"] == "snapshot_changed"
    assert len(cookbook.list()["items"]) == 1


def test_missing_or_fabricated_evidence_rejects_invented_numbers():
    for patch in ({"servings": 2.0}, {"total_time_min": 10.0},
                  {"total_time_min": 10.0, "time_evidence": "总用时10分钟"},
                  {"servings": 2.0, "servings_evidence": "鸡蛋2个"}):
        with pytest.raises(ValidationError):
            RecipeDraftRequest.model_validate({**draft(), **patch})
    values = draft()
    values["ingredients"][1].update(grams=100.0, amount_evidence="鸡蛋2个")
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate(values)


def test_exact_text_evidence_accepts_original_minutes_and_servings():
    values = draft()
    values.update(source_text=values["source_text"] + "2人份，总用时15分钟。", servings=2.0,
                  servings_evidence="2人份", total_time_min=15.0, time_evidence="总用时15分钟")
    parsed = RecipeDraftRequest.model_validate(values)
    assert parsed.total_time_min == 15.0 and parsed.servings == 2.0


@pytest.mark.parametrize("source_text", [
    "西红柿炒鸡蛋\n制作时间：约15分钟\n适用人数：2人\n西红柿：2个（约300克）\n食盐：3克",
    "# 西红柿炒鸡蛋\n\n制作时间：约15分钟\n适用人数：2人\n\n西红柿：2个（约300克）\n食盐：3克\n\n步骤1：西红柿切块。",
])
def test_recipe_conversation_failures_expose_fields_and_safe_repair_hints(source_text):
    """Replay the source/evidence mismatch and inferred time from the failed chat."""
    from chefmate_agent.failure_policy import FailurePolicy, safe_errors

    values = {"name": "西红柿炒鸡蛋", "source_text": source_text,
              "servings": 2, "servings_evidence": "适用人数：2人",
              "prep_time_min": 3, "cook_time_min": 12, "total_time_min": 15,
              "time_evidence": "制作时间：约15分钟",
              "ingredients": [{"name": "西红柿", "amount": "2个（约300克）", "grams": 300,
                               "amount_evidence": "西红柿 | 2个（约300克）"},
                              {"name": "食盐", "amount": "3克", "grams": 3,
                               "amount_evidence": "食盐 | 3克"}],
              "steps": ["西红柿切块。"]}
    with pytest.raises(ValidationError) as caught:
        RecipeDraftRequest.model_validate(values)
    safe = safe_errors(caught.value.errors())
    mapped = {tuple(error["loc"]): error for error in safe}
    assert mapped[("ingredients", 0, "amount_evidence")]["type"] == "recipe_evidence_not_quoted"
    assert "Markdown" in mapped[("ingredients", 0, "amount_evidence")]["hint"]
    assert mapped[("cook_time_min",)]["type"] == "recipe_time_mismatch"
    assert "倒推" in mapped[("cook_time_min",)]["hint"]
    assert ("total_time_min",) not in mapped
    assert FailurePolicy().observe("create_recipe_draft", values, {
        "status": "invalid_arguments", "errors": safe})["errors"] == safe


def test_recipe_metadata_time_and_verbatim_table_can_save_without_invented_subtotals(tmp_path):
    source = "# 西红柿炒鸡蛋\n- **制作时间**：约15分钟\n- **适用人数**：2人\n| 西红柿 | 300克 |\n| 鸡蛋 | 3个 |\n西红柿切块，鸡蛋打散后炒熟。"
    values = {"name": "西红柿炒鸡蛋", "source_text": source,
              "servings": 2, "servings_evidence": "**适用人数**：2人",
              "total_time_min": 15, "time_evidence": "**制作时间**：约15分钟",
              "ingredients": [{"name": "西红柿", "amount": "300克", "grams": 300,
                               "amount_evidence": "| 西红柿 | 300克 |"},
                              {"name": "鸡蛋", "amount": "3个", "grams": None}],
              "steps": ["西红柿切块，鸡蛋打散后炒熟。"]}
    record = CookbookRepository(tmp_path / "sessions.db").create_draft(values)
    assert record["source_text"] == source
    assert record["document"]["total_time_min"] == 15
    assert record["document"]["prep_time_min"] is None
    assert record["document"]["cook_time_min"] is None
    assert record["document"]["ingredients"][1]["grams"] is None
    values.update(source_text=source + "步骤1制作时间：3分钟", total_time_min=3,
                  time_evidence="步骤1制作时间：3分钟")
    with pytest.raises(ValidationError) as caught:
        RecipeDraftRequest.model_validate(values)
    assert any(error["loc"] == ("total_time_min",) and error["type"] == "recipe_total_time_ambiguous"
               for error in caught.value.errors())


def test_quoted_source_cannot_lend_wrong_numbers_or_step_duration_as_total():
    values = draft()
    values["ingredients"][0]["grams"] = 400.0
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate(values)
    values = draft()
    values.update(source_text=values["source_text"] + "2人份，蒸10分钟。", servings=5.0, servings_evidence="2人份")
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate(values)
    values.update(servings=2.0, total_time_min=10.0, time_evidence="蒸10分钟")
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate(values)


@pytest.mark.parametrize("unit", ["gallons", "grams_unknown"])
def test_quantity_units_must_not_match_a_word_prefix(unit):
    values = draft()
    evidence = f"番茄200{unit}"
    values.update(source_text=evidence)
    values["ingredients"][0]["amount_evidence"] = evidence
    with pytest.raises(ValidationError):
        RecipeDraftRequest.model_validate(values)


def test_explicit_units_can_scale_without_inventing_egg_grams(tmp_path):
    values = draft()
    values["ingredients"][1]["amount"] = "两个"
    record = CookbookRepository(tmp_path / "sessions.db").create_draft(values)
    egg = record["document"]["ingredients"][1]
    assert egg["raw_amount"] == "2" and egg["raw_unit"] == "个" and egg["grams"] is None
    values["ingredients"][1]["amount"] = "1-2个"
    record = CookbookRepository(tmp_path / "another.db").create_draft(values)
    egg = record["document"]["ingredients"][1]
    assert egg["raw_amount"] == "1-2个" and egg["raw_unit"] is None


def test_tool_text_source_must_be_real_user_material_and_skill_is_bound(tmp_path):
    cookbook = CookbookRepository(tmp_path / "sessions.db")
    create = make_recipe_tools(cookbook)[0]
    valid = create.handler(draft(), context())
    assert valid["status"] == "ok" and create.required_skill == "recipe-authoring"
    forged = {**draft(), "source_text": "模型凭空编出来的源菜谱，番茄200g"}
    invalid = create.handler(forged, context())
    assert invalid["status"] == "invalid_recipe" and "引用" in invalid["message"]
    assert cookbook.list(status="all")["total"] == 1


def test_image_requires_current_upload_and_preserves_only_metadata_reference(tmp_path):
    cookbook = CookbookRepository(tmp_path / "sessions.db")
    create = make_recipe_tools(cookbook)[0]
    values = {**draft(), "source_kind": "image"}
    assert create.handler(values, context())["status"] == "invalid_recipe"
    result = create.handler(values, context(images=[{"id": "owned-photo"}]))
    assert result["status"] == "ok"
    assert result["draft"]["source_image_ids"] == ["owned-photo"]
    assert "base64" not in str(result)


def test_operation_replay_cannot_change_draft_content(tmp_path):
    cookbook = CookbookRepository(tmp_path / "sessions.db")
    first = cookbook.create_draft(draft(), session_id="chat", operation_id="turn:1")
    again = cookbook.create_draft(draft(), session_id="chat", operation_id="turn:1")
    assert first["recipe_id"] == again["recipe_id"]
    with pytest.raises(ValueError):
        cookbook.create_draft({**draft(), "name": "另一道菜"}, session_id="chat", operation_id="turn:1")
    with pytest.raises(ValueError):
        cookbook.create_draft(draft(), session_id="another-chat", operation_id="turn:1")
    assert cookbook.list(status="all")["total"] == 1


def test_cancellation_and_stale_turn_do_not_leave_drafts(tmp_path):
    cookbook = CookbookRepository(tmp_path / "sessions.db")
    create = make_recipe_tools(cookbook)[0]
    cancelled = asyncio.Event()
    cancelled.set()
    assert create.handler(draft(), context(cancelled=cancelled))["status"] == "invalid_recipe"
    stale = context()
    stale.host_context.binding = SimpleNamespace(repository=SimpleNamespace(get=lambda identity: {
        "running": False, "active_turn": None}))
    assert create.handler(draft(), stale)["status"] == "invalid_recipe"
    assert cookbook.list(status="all")["total"] == 0


def test_cancel_between_insert_and_commit_rolls_back(tmp_path):
    cookbook = CookbookRepository(tmp_path / "sessions.db")
    calls = []
    def guard():
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("cancelled")
    with pytest.raises(ValueError):
        cookbook.create_draft(draft(), guard=guard)
    assert cookbook.list(status="all")["total"] == 0


def test_recipe_center_http_catalog_confirmation_and_favorites(tmp_path):
    from fastapi.testclient import TestClient
    from chefmate_playground import PlaygroundConfig, create_app
    from test_app import Agent, Service

    app = create_app(PlaygroundConfig(sessions_database=tmp_path / "http.db"), service=Service(), agent=Agent())
    cookbook = app.state.cookbook
    pending = cookbook.create_draft(draft())
    with TestClient(app) as client:
        assert client.get("/api/cookbook?source=drafts").json()["items"][0]["status"] == "draft"
        assert client.get("/api/cookbook?source=user").json()["total"] == 0
        uri = "/api/cookbook/" + pending["recipe_id"]
        assert client.post(uri + "/publish", json={"revision": 999}).status_code == 409
        assert client.put("/api/favorites/" + pending["recipe_id"], json={"favorite": True}).status_code == 404
        saved = client.post(uri + "/publish", json={"revision": pending["revision"]})
        assert saved.status_code == 200 and saved.json()["origin"] == "user"
        assert client.get("/api/cookbook?source=user").json()["total"] == 1
        assert client.get("/api/cookbook?source=all").json()["total"] == 2
        favorite_uri = "/api/favorites/" + pending["recipe_id"]
        assert client.put(favorite_uri, json={"favorite": True}).json()["favorite"]
        favorites = client.get("/api/cookbook?source=favorites").json()
        assert favorites["total"] == 1 and favorites["items"][0]["recipe_id"] == pending["recipe_id"]
        assert client.put(favorite_uri, json={"favorite": False}).json()["favorite"] is False
        assert client.get("/api/cookbook?source=favorites").json()["total"] == 0
        detail = client.get("/api/recipes/" + pending["recipe_id"]).json()
        assert detail["status"] == "ok" and detail["recipe"]["is_custom"]
