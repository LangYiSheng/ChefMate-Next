"""Synthetic nutrient fixtures test arithmetic, never used as production nutrition."""

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from data_cleaning.config import ROOT
from data_cleaning.models.workflow import WorkflowRepository
from data_cleaning.schemas.cleaning import MatchChoice, RecipeProfile, StandardIngredient, TagThresholds
from data_cleaning.schemas.recipe import Recipe, Status
from data_cleaning.services.matching import match_all, match_one
from data_cleaning.services.normalization import ai_extract, normalize_one, profile_template
from data_cleaning.services.openai_service import OpenAIService
from data_cleaning.services.pipeline import extract_all
from data_cleaning.services.usda import USDAService, parse_nutrients


@pytest.fixture
def repo(tmp_path):
    return WorkflowRepository(tmp_path / "full.db")


def standard(repo, ingredient_id="chicken_breast", name="鸡胸肉", aliases=None):
    repo.save_standard(
        StandardIngredient(
            ingredient_id=ingredient_id, canonical_name=name, category="meat", aliases=aliases or [], status="approved"
        )
    )


def source(repo, text=None):
    text = text or "# 测试菜谱\n按照 4 人份准备\n## 必备原料和工具\n- 鸡脯肉 400g\n- 盐 1g\n## 操作\n1. 做熟。\n"
    repo.ingest("r1", "test/recipe.md", hashlib.sha256(text.encode()).hexdigest(), text.encode(), text)
    extract_all(repo)
    row = repo.get_recipe("r1")
    repo.save_extraction(
        Recipe.model_validate_json(row["extracted_json"]), row["updated_at"], review_status=Status.APPROVED
    )


def food(fdc_id=123, salt=False, missing=None):
    values = [
        (1008, "KCAL", 0 if salt else 200),
        (1003, "G", 0 if salt else 30),
        (1004, "G", 0 if salt else 8),
        (1005, "G", 0 if salt else 2),
        (2000, "G", 0),
        (1093, "MG", 40000 if salt else 80),
    ]
    return {
        "fdcId": fdc_id,
        "description": "SYNTHETIC TEST ONLY",
        "dataType": "Foundation",
        "foodNutrients": [
            {"nutrient": {"id": key, "unitName": unit}, "amount": value}
            for key, unit, value in values
            if key != missing
        ],
    }


def map_nutrition(repo, ingredient_id, payload):
    repo.cache_food(payload)
    repo.stage_nutrition(ingredient_id, str(payload["fdcId"]), repo.standard(ingredient_id)["updated_at"])
    repo.review_nutrition(ingredient_id, repo.standard(ingredient_id)["updated_at"], True, "synthetic fixture")


def approve_profile(repo):
    old = repo.profile("r1")
    row = repo.get_recipe("r1")
    profile = (
        RecipeProfile.model_validate_json(old["data_json"])
        if old
        else profile_template(Recipe.model_validate_json(row["extracted_json"]))
    )
    repo.save_profile(
        "r1", profile, source_expected=row["updated_at"], expected=old.get("updated_at"), status="approved"
    )


def complete(repo):
    source(repo)
    standard(repo, aliases=["鸡脯肉"])
    standard(repo, "salt", "盐")
    assert match_all(repo, use_ai=False)["approved"] == 2
    map_nutrition(repo, "chicken_breast", food())
    map_nutrition(repo, "salt", food(124, True))
    approve_profile(repo)


class FakeAI:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.responses = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(output_parsed=output)


def test_complete_pipeline_math_and_final_export(repo):
    complete(repo)
    result = repo.result("r1")
    assert result["ready_for_recommendation"]
    assert result["nutrition"]["total"]["calories_kcal"] == 800
    assert result["nutrition"]["total"]["sodium_mg"] == 720
    assert result["nutrition"]["per_serving"]["protein_g"] == 30
    assert result["nutrition"]["tags"] == ["high_protein"]
    assert result["ingredients"][0]["raw_name"] == "鸡脯肉"
    assert result["ingredients"][0]["canonical_name"] == "鸡胸肉"
    assert result["ingredients"][0]["per_serving_amount"] == 100
    assert len(repo.export_snapshot()["recipes"]) == 1
    repo.save_thresholds(TagThresholds(high_protein=31))
    assert repo.result("r1")["nutrition"]["tags"] == []


def test_missing_nutrient_is_null_not_zero(repo):
    complete(repo)
    map_nutrition(repo, "salt", food(124, True, missing=2000))
    approve_profile(repo)
    result = repo.result("r1")
    assert result["nutrition"]["total"]["sugar_g"] is None
    assert result["nutrition"]["total"]["protein_g"] == 120
    assert result["nutrition"]["tags"] == []
    assert not result["ready_for_recommendation"]
    assert repo.export_snapshot()["recipes"] == []
    assert len(repo.export_snapshot(False)["recipes"]) == 1


def test_alias_before_ai_and_fixed_management(repo):
    source(repo)
    standard(repo, aliases=["鸡脯肉"])
    standard(repo, "salt", "盐")
    raw = next(r for r in repo.ingredient_list() if r["raw_name"] == "鸡脯肉")
    assert match_one(repo, raw["id"], ai=object()) == "approved"
    assert repo.raw_detail(raw["id"])["match_method"] == "alias"
    with pytest.raises(ValueError, match="冲突"):
        repo.save_fixed("鸡脯肉", "salt", True)
    repo.save_fixed("鸡胸脯", "chicken_breast", True)
    assert repo.fixed_mappings()[0]["status"] == "approved"
    repo.save_fixed("鸡胸脯", "salt", False)
    assert repo.fixed_mappings()[0]["ingredient_id"] == "salt"
    assert repo.fixed_mappings()[0]["status"] == "rejected"
    repo.delete_fixed("鸡胸脯")
    assert repo.fixed_mappings() == []


def test_fixed_reused_without_ai_and_disable_invalidates(repo):
    source(repo, "# 测试\n## 必备原料和工具\n- 胸脯部位 100g\n## 操作\n- 做熟\n")
    standard(repo)
    repo.save_fixed("胸脯部位", "chicken_breast", True)
    raw = repo.ingredient_list()[0]
    assert match_one(repo, raw["id"], ai=object()) == "approved"
    assert repo.raw_detail(raw["id"])["match_method"] == "fixed"
    repo.save_fixed("胸脯部位", "chicken_breast", False)
    assert repo.raw_detail(raw["id"])["status"] == "pending_review"


def test_ai_out_of_candidates_retries_and_requires_human(repo, monkeypatch):
    source(repo, "# 测试\n## 必备原料和工具\n- 鸡胸脯 100g\n## 操作\n- 做熟\n")
    standard(repo)
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "2")
    invalid = {"selected_ingredient_id": "invented", "confidence": 0.99, "reason": "bad", "suggested_name": None}
    client = FakeAI([invalid] * 3)
    ai = OpenAIService(repo, client, sleeper=lambda _: None)
    raw = repo.ingredient_list()[0]
    assert match_one(repo, raw["id"], ai=ai) == "failed"
    assert len(client.calls) == 3
    assert repo.raw_detail(raw["id"])["canonical_ingredient_id"] is None
    valid = {**invalid, "selected_ingredient_id": "chicken_breast"}
    ai = OpenAIService(repo, FakeAI([valid]), sleeper=lambda _: None)
    assert match_one(repo, raw["id"], ai=ai) == "pending_review"
    assert repo.raw_detail(raw["id"])["status"] == "pending_review"


def test_schema_refusal_and_redaction(repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-123456789")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "2")
    good = {"selected_ingredient_id": None, "confidence": 0.4, "reason": "none", "suggested_name": "新食材"}
    fake = FakeAI([None, {**good, "confidence": 7}, good])
    ai = OpenAIService(repo, fake, sleeper=lambda _: None)
    ai.request("match", {"raw_name": "sk-secret-123456789"}, MatchChoice, "raw")
    assert len(fake.calls) == 3
    assert "sk-secret-123456789" not in json.dumps(repo.service_history())
    assert "[REDACTED]" in json.dumps(repo.service_history())


def test_merge_preserves_raw_and_updates_history_links(repo):
    complete(repo)
    raw = next(r for r in repo.ingredient_list() if r["raw_name"] == "鸡脯肉")
    before = repo.get_recipe("r1")["raw_text"]
    repo.merge_raw([raw["id"]], "chicken_breast", "确认同义词")
    assert repo.fixed_mappings()[0]["raw_name"] == "鸡脯肉"
    standard(repo, "chicken", "鸡肉")
    repo.merge_standards(["chicken_breast"], "chicken", "合并测试")
    assert repo.raw_detail(raw["id"])["canonical_ingredient_id"] == "chicken"
    assert "鸡胸肉" in repo.standard("chicken")["aliases"]
    assert repo.standard("chicken_breast")["status"] == "rejected"
    assert repo.standard("chicken")["nutrition_match_status"] == "pending_review"
    assert repo.get_recipe("r1")["raw_text"] == before
    assert not repo.result("r1")["ready_for_recommendation"]


def test_enums_disable_persist_and_invalid_choices_rejected(repo):
    complete(repo)
    repo.save_enum("taste", "custom_taste", "自定义", True)
    repo.save_enum("taste", "spicy", "辣味", False)
    reopened = WorkflowRepository(repo.path)
    assert next(e for e in reopened.enums() if e["code"] == "spicy")["status"] == "rejected"
    assert repo.profile("r1")["status"] == "pending_review"
    old = repo.profile("r1")
    profile = RecipeProfile.model_validate_json(old["data_json"])
    profile.taste_tags = ["spicy"]
    with pytest.raises(ValueError, match="未启用"):
        repo.save_profile(
            "r1", profile, source_expected=repo.get_recipe("r1")["updated_at"], expected=old["updated_at"]
        )
    with pytest.raises(ValueError):
        repo.save_enum("difficulty", "expert", "大师", True)


def test_approval_conflict_and_clear_full_schema(repo):
    complete(repo)
    before = repo.profile("r1")
    repo.save_fixed("胸肉", "chicken_breast", True)
    with pytest.raises(ValueError, match="变化"):
        repo.save_profile(
            "r1",
            RecipeProfile.model_validate_json(before["data_json"]),
            source_expected=repo.get_recipe("r1")["updated_at"],
            expected=before["updated_at"],
            status="approved",
        )
    repo.clear("CLEAR")
    assert repo.standards() == []
    assert repo.fixed_mappings() == []
    assert repo.cached_food("123") is None
    assert repo.service_history() == []
    complete(repo)
    assert repo.result("r1")["ready_for_recommendation"]


def test_nutrition_changes_invalidate_final_approval(repo):
    complete(repo)
    old = repo.standard("chicken_breast")
    repo.stage_nutrition("chicken_breast", "123", old["updated_at"])
    assert repo.export_snapshot()["recipes"] == []
    assert repo.profile("r1")["status"] == "pending_review"


def test_usda_local_cache_and_unit_validation(repo, local_catalog):
    local, _, _ = local_catalog
    client = USDAService(repo, path=local.path)
    assert client.food("123")["fdcId"] == 123
    assert repo.cached_food("123")["fdcId"] == 123
    assert len(client.search("potato raw")) >= 2
    wrong = food()
    wrong["foodNutrients"][0]["nutrient"]["unitName"] = "kJ"
    assert parse_nutrients(wrong).calories_kcal is None
    with pytest.raises(ValueError):
        parse_nutrients({**food(), "dataType": "Branded"})


def test_no_guessed_weight_or_fractional_eggs(repo):
    source(repo, "# 测试\n按照 4 人份\n## 必备原料和工具\n- 鸡蛋 1个\n- 盐 少许\n## 操作\n- 做熟\n")
    recipe = Recipe.model_validate_json(repo.get_recipe("r1")["extracted_json"])
    template = profile_template(recipe)
    assert all(i.grams is None and i.normalized_amount is None for i in template.items)
    normalize_one(repo, "r1", use_ai=False)
    assert repo.result("r1")["nutrition"]["total"]["sodium_mg"] is None
    assert repo.result("r1")["ingredients"][0]["per_serving_amount"] is None


def test_ai_extraction_is_pydantic_validated_and_traced(repo):
    source(repo)
    payload = {
        "name": "AI 测试",
        "description": None,
        "original_servings": None,
        "prep_time_min": None,
        "cook_time_min": None,
        "total_time_min": None,
        "difficulty": None,
        "ingredients": [{"raw_name": "鸡脯肉", "raw_amount": "400g", "raw_unit": "g", "raw_text": "鸡脯肉 400g"}],
        "steps": ["做熟"],
        "taste_tags": [],
        "cooking_methods": [],
        "required_tools": [],
    }
    ai_extract(repo, "r1", OpenAIService(repo, FakeAI([payload]), sleeper=lambda _: None))
    row = repo.get_recipe("r1")
    assert row["status"] == "pending_review"
    assert json.loads(row["extracted_json"])["ingredients"][0]["raw_name"] == "鸡脯肉"
    assert "按照 4 人份" in row["raw_text"]


def test_ai_enum_guard_retries(repo, monkeypatch):
    source(repo)
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "1")
    invalid = {"taste_tags": ["fake"], "cooking_methods": [], "required_tools": [], "difficulty": None}
    valid = {**invalid, "taste_tags": ["savory"]}
    client = FakeAI([invalid, valid])
    assert normalize_one(repo, "r1", ai=OpenAIService(repo, client, sleeper=lambda _: None)) == "pending_review"
    assert len(client.calls) == 2
    assert json.loads(repo.profile("r1")["data_json"])["taste_tags"] == ["savory"]


def test_v1_migration_preserves_existing_raw(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript((ROOT / "models/migrations/001_initial.sql").read_text(encoding="utf-8"))
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO raw_ingredients(raw_name,created_at,updated_at) VALUES('马铃薯','t','t')")
    repo = WorkflowRepository(path)
    assert repo.raw_detail(1)["raw_name"] == "马铃薯"
    assert repo.raw_detail(1)["candidates_json"] == "[]"
    with repo.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_ambiguous_servings_stay_unknown(repo):
    source(repo)
    recipe = Recipe.model_validate_json(repo.get_recipe("r1")["extracted_json"])
    for raw in ("按照 4~6 人份", "约 4 人份", "4 人份左右", "一盘"):
        recipe.original_servings = raw
        assert profile_template(recipe).servings is None


def test_disabled_alias_does_not_keep_automatic_approval(repo):
    complete(repo)
    old = repo.standard("chicken_breast")
    repo.save_standard(
        StandardIngredient(
            ingredient_id="chicken_breast", canonical_name="鸡胸肉", category="meat", aliases=[], status="approved"
        ),
        old["updated_at"],
    )
    raw = next(r for r in repo.ingredient_list() if r["raw_name"] == "鸡脯肉")
    assert raw["status"] == "pending_review"
    assert repo.export_snapshot()["recipes"] == []


def test_openai_actual_sdk_structured_contract(repo, monkeypatch):
    import httpx
    from openai import OpenAI

    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    choice = {"selected_ingredient_id": None, "confidence": 0.2, "reason": "none", "suggested_name": "test"}
    requests_seen = []

    def handle(request):
        requests_seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_test",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(choice), "annotations": []}],
                    }
                ],
            },
        )

    with OpenAI(
        api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(handle)), max_retries=0
    ) as client:
        result = OpenAIService(repo, client).request(
            "match", {"raw_name": "test", "candidates": []}, MatchChoice, "test"
        )
    assert result.confidence == 0.2
    assert requests_seen[0]["text"]["format"]["type"] == "json_schema"
    assert requests_seen[0]["text"]["format"]["strict"] is True
    assert requests_seen[0]["store"] is False


def test_usda_missing_catalog_has_local_diagnostic(repo, tmp_path):
    from data_cleaning.services.usda import USDARequestError
    with pytest.raises(USDARequestError, match="本地库") as caught:
        USDAService(repo, path=tmp_path / "missing.db").food("123")
    assert caught.value.details["backend"] == "local"


def test_full_ui_pages_and_manual_decisions(repo, monkeypatch, local_catalog):
    from streamlit.testing.v1 import AppTest
    from data_cleaning.models.conversion import ConversionRepository
    from data_cleaning.tests.test_conversion import document
    from data_cleaning.services.conversion import convert_one
    from data_cleaning.tests.test_conversion import AI

    complete(repo)
    monkeypatch.setenv("CLEANING_DB_PATH", str(repo.path))
    monkeypatch.setenv("USDA_DB_PATH", str(local_catalog[0].path))
    converted = ConversionRepository(repo.path)
    convert_one(converted, "r1", ai=AI())
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=30).run()
    for page in ("菜谱转换", "批量转换", "数据库菜谱", "标准食材", "数据与设置"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception, page
        assert not app.error, page
    assert any("5 条食物" in message.value for message in app.success)
    app.sidebar.radio[0].set_value("菜谱转换").run()
    next(b for b in app.button if b.label == "通过审阅").click().run()
    assert not app.exception
    assert converted.conversion("r1")["status"] == "approved"
    assert json.loads(converted.conversion("r1")["data_json"])["name"] == document().name


def test_final_export_manifest_and_review_filter(repo, tmp_path, monkeypatch):
    from data_cleaning.services import exporter

    complete(repo)
    monkeypatch.setattr(exporter, "ROOT", tmp_path)
    batch = exporter.export_json(repo)
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    recipes = json.loads((batch / "recipes.json").read_text(encoding="utf-8"))
    assert manifest["phase"] == 3
    assert manifest["ready_for_recommendation"] is True
    assert manifest["recipe_count"] == 1
    assert recipes[0]["ingredients"][0]["nutrition_source"]["fdc_id"] == "123"
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((batch / name).read_bytes()).hexdigest() == digest
    repo.save_enum("taste", "light", "清淡", False)
    next_batch = exporter.export_json(repo)
    blocked = json.loads((next_batch / "manifest.json").read_text(encoding="utf-8"))
    assert blocked["recipe_count"] == 0
    assert blocked["filter_report"]["excluded"][0]["recipe_id"] == "r1"


def test_countable_units_never_emit_fraction(repo):
    complete(repo)
    old = repo.profile("r1")
    profile = RecipeProfile.model_validate_json(old["data_json"])
    profile.items[0].normalized_amount = 1
    profile.items[0].normalized_unit = "个"
    profile.items[0].scalable = True
    repo.save_profile(
        "r1",
        profile,
        source_expected=repo.get_recipe("r1")["updated_at"],
        expected=old["updated_at"],
        status="approved",
    )
    result = repo.result("r1")
    assert result["ingredients"][0]["per_serving_amount"] is None
    assert result["nutrition"]["per_serving"]["protein_g"] == 30
