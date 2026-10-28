"""Narrow metadata edits preserve ingredients, source material and unrequested time splits."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.recipe_drafts import RecipeDraftRevisionRequest
from test_app import Agent, Service
from test_cookbook import draft
from test_recipe_metadata_provenance import answer_metadata, metadata_recipe, revise
from test_recipe_review import PublicationFlow


def prepare_suggestions(flow):
    text = "请给难度份数总时间，以及备菜和烹饪分项合理建议"
    flow.current = replace(flow.current, messages=[{"role": "user", "content": text}])
    flow.read()
    values = {**metadata_recipe(text), "total_time_min": 24, "prep_time_min": 12, "cook_time_min": 12}
    values["metadata_provenance"].update({field: {"kind": "suggested", "evidence": text}
                                          for field in ("prep_time_min", "cook_time_min")})
    flow.record = revise(flow, values)["draft"]
    return flow.record


def patch(flow, metadata_patch):
    return flow.tools["revise_recipe_draft"].handler({"recipe_id": flow.record["recipe_id"],
        "revision": flow.record["revision"], "metadata_patch": metadata_patch}, flow.current)


def test_grouped_natural_metadata_patch_preserves_twelve_plus_twelve_and_all_other_content(tmp_path):
    flow = PublicationFlow(tmp_path)
    before = prepare_suggestions(flow)
    text = "按建议：普通、1人份、30分钟。请更新同一份草稿。"
    flow.current = replace(flow.current, messages=[{"role": "user", "content": text}])
    flow.read()
    request = {"difficulty": "normal", "servings": 1, "total_time_min": 30,
               "metadata_provenance": {field: {"kind": "user_confirmed", "evidence": text}
                                       for field in ("difficulty", "servings", "total_time_min")}}
    result = patch(flow, request)
    assert result["status"] == "ok"
    record = result["draft"]
    assert record["recipe_id"] == before["recipe_id"] and record["revision"] == before["revision"] + 1
    assert record["document"]["total_time_min"] == 30
    assert record["document"]["prep_time_min"] == record["document"]["cook_time_min"] == 12
    for field in ("ingredients", "steps", "name", "description", "taste_tags", "required_tools", "cooking_methods"):
        assert record["document"][field] == before["document"][field]
    for field in ("source_text", "source_kind", "source_image_ids", "source_metadata_evidence"):
        assert record[field] == before[field]
    for field in ("prep_time_min", "cook_time_min"):
        assert record["metadata_provenance"][field] == before["metadata_provenance"][field]
    assert all(record["metadata_provenance"][field]["kind"] == "user_confirmed"
               for field in ("difficulty", "servings", "total_time_min"))
    assert flow.repository.get(flow.session_id)["workspace"].get("pending_question") is None
    assert record["status"] == "draft"


def test_patch_can_clear_one_value_without_touching_other_values_or_their_sources(tmp_path):
    flow = PublicationFlow(tmp_path)
    before = prepare_suggestions(flow)
    flow.current = replace(flow.current, messages=[{"role": "user", "content": "把总耗时清空，其他保留"}])
    flow.read()
    result = patch(flow, {"total_time_min": None, "clear_fields": ["total_time_min"]})
    assert result["status"] == "ok"
    record = result["draft"]
    assert record["document"]["total_time_min"] is None
    assert "total_time_min" not in record["metadata_provenance"]
    for field in ("servings", "difficulty", "prep_time_min", "cook_time_min"):
        assert record["document"][field] == before["document"][field]
        assert record["metadata_provenance"][field] == before["metadata_provenance"][field]


def test_native_strict_nullable_fields_preserve_unrequested_values_and_provenance(tmp_path):
    flow = PublicationFlow(tmp_path)
    before = prepare_suggestions(flow)
    text = "只把总耗时改为30分钟，份数难度与其他内容保持原样"
    flow.current = replace(flow.current, messages=[{"role": "user", "content": text}])
    flow.read()
    # Responses strict tools materialize optional nullable properties. This
    # exact shape used to save servings/difficulty=null, then require repair.
    result = patch(flow, {"servings": None, "difficulty": None, "total_time_min": 30,
        "clear_fields": [], "metadata_provenance": {"servings": None, "difficulty": None,
            "total_time_min": {"kind": "user_confirmed", "evidence": text, "question_id": None}}})
    assert result["status"] == "ok"
    after = result["draft"]
    assert after["document"]["total_time_min"] == 30
    for field in before["document"]:
        if field not in {"total_time_min", "warnings"}:
            assert after["document"][field] == before["document"][field]
    for field, evidence in before["metadata_provenance"].items():
        if field != "total_time_min":
            assert after["metadata_provenance"][field] == evidence
    for field in ("source_text", "source_kind", "source_image_ids", "source_metadata_evidence"):
        assert after[field] == before[field]
    assert after["revision"] == before["revision"] + 1


@pytest.mark.parametrize("field", ["servings", "difficulty", "total_time_min"])
def test_explicit_clear_fields_clear_only_the_named_nullable_metadata(tmp_path, field):
    flow = PublicationFlow(tmp_path)
    before = prepare_suggestions(flow)
    flow.current = replace(flow.current, messages=[{"role": "user", "content": "清空这项，其他保留"}])
    flow.read()
    result = patch(flow, {"servings": None, "difficulty": None, "total_time_min": None,
        "clear_fields": [field], "metadata_provenance": {"servings": None, "difficulty": None, "total_time_min": None}})
    assert result["status"] == "ok"
    after = result["draft"]
    assert after["document"][field] is None and field not in after["metadata_provenance"]
    for other in {"servings", "difficulty", "total_time_min", "prep_time_min", "cook_time_min"} - {field}:
        assert after["document"][other] == before["document"][other]
        assert after["metadata_provenance"][other] == before["metadata_provenance"][other]


def test_full_recipe_http_editor_still_clears_an_explicit_null_without_agent_mask(tmp_path):
    flow = PublicationFlow(tmp_path)
    before = prepare_suggestions(flow)
    values = {**metadata_recipe("unused"), "total_time_min": None, "prep_time_min": 12, "cook_time_min": 12,
        "metadata_provenance": {field: evidence for field, evidence in before["metadata_provenance"].items()
                                if field != "total_time_min"}}
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=flow.repository.path), service=Service(), agent=Agent())
    uri = "/api/cookbook/" + before["recipe_id"]
    with TestClient(app) as client:
        response = client.put(uri, json={"revision": before["revision"], "recipe": values})
        assert response.status_code == 200
        after = client.get(uri).json()
    assert after["document"]["total_time_min"] is None
    assert "total_time_min" not in after["metadata_provenance"]
    for field in ("servings", "difficulty", "prep_time_min", "cook_time_min"):
        assert after["document"][field] == before["document"][field]
        assert after["metadata_provenance"][field] == before["metadata_provenance"][field]


def test_patch_uses_real_bound_answer_and_keeps_stale_version_and_missing_basis_guards(tmp_path):
    flow = PublicationFlow(tmp_path)
    identity = answer_metadata(flow)
    label = "按建议：普通、1人份、30分钟"
    request = {"difficulty": "normal", "servings": 1, "total_time_min": 30,
               "metadata_provenance": {field: {"kind": "user_confirmed", "evidence": label, "question_id": identity}
                                       for field in ("difficulty", "servings", "total_time_min")}}
    assert patch(flow, {"total_time_min": 30})["status"] == "invalid_recipe_metadata_patch"
    assert flow.cookbook.get(flow.record["recipe_id"])["revision"] == 1
    saved = patch(flow, request)
    assert saved["status"] == "ok" and saved["draft"]["document"]["total_time_min"] == 30
    assert patch(flow, request)["status"] == "recipe_changed"


@pytest.mark.parametrize("extra", [
    {"recipe": draft(), "metadata_patch": {"total_time_min": None}}, {},
    {"metadata_patch": {}}, {"metadata_patch": {"prep_time_min": 15}},
    {"metadata_patch": {"servings": None, "difficulty": None, "total_time_min": None, "clear_fields": []}},
    {"metadata_patch": {"clear_fields": ["name"]}},
    {"metadata_patch": {"clear_fields": ["difficulty", "difficulty"]}},
    {"metadata_patch": {"difficulty": "normal", "clear_fields": ["difficulty"]}},
    {"metadata_patch": {"clear_fields": ["total_time_min"], "metadata_provenance": {"total_time_min": {
        "kind": "suggested", "evidence": "可以给一个时间建议"}}}},
    {"metadata_patch": {"total_time_min": 30, "metadata_provenance": {"difficulty": {
        "kind": "suggested", "evidence": "请更新难度"}}}},
])
def test_patch_rejects_ambiguous_empty_or_unrelated_changes(extra):
    with pytest.raises(ValidationError):
        RecipeDraftRevisionRequest.model_validate({"recipe_id": "custom-test", "revision": 1, **extra})
