"""Narrow metadata edits preserve ingredients, source material and unrequested time splits."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from chefmate_playground.recipe_drafts import RecipeDraftRevisionRequest
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
    result = patch(flow, {"total_time_min": None})
    assert result["status"] == "ok"
    record = result["draft"]
    assert record["document"]["total_time_min"] is None
    assert "total_time_min" not in record["metadata_provenance"]
    for field in ("servings", "difficulty", "prep_time_min", "cook_time_min"):
        assert record["document"][field] == before["document"][field]
        assert record["metadata_provenance"][field] == before["metadata_provenance"][field]


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
    {"metadata_patch": {"total_time_min": 30, "metadata_provenance": {"difficulty": {
        "kind": "suggested", "evidence": "请更新难度"}}}},
])
def test_patch_rejects_ambiguous_empty_or_unrelated_changes(extra):
    with pytest.raises(ValidationError):
        RecipeDraftRevisionRequest.model_validate({"recipe_id": "custom-test", "revision": 1, **extra})
