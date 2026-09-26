import hashlib
import json

import pytest

from data_cleaning.config import ROOT
from data_cleaning.models.batch import BatchRepository
from data_cleaning.schemas.batch import BatchOptions
from data_cleaning.schemas.cleaning import TagThresholds
from data_cleaning.schemas.conversion import ConvertedRecipe, ConversionSettings
from data_cleaning.services.batch import execute
from data_cleaning.services.conversion import configured_ai, convert_one, enrich_nutrition
from data_cleaning.services.conversion_nutrition import calculate, food_key


def document():
    return ConvertedRecipe.model_validate(
        dict(
            name="土豆汤",
            description=None,
            original_servings="2人份",
            servings=2,
            servings_evidence="原文2人份",
            prep_time_min=None,
            cook_time_min=None,
            total_time_min=None,
            difficulty="beginner",
            ingredients=[
                dict(
                    raw_name="马铃薯",
                    raw_amount="200g",
                    raw_unit="g",
                    raw_text="马铃薯200g",
                    ingredient_id="potato",
                    canonical_name="土豆",
                    category="vegetable",
                    aliases=["马铃薯"],
                    allergens=[],
                    grams=200,
                    amount_evidence="原文200g",
                    scalable=True,
                    usda_query="potato raw",
                )
            ],
            steps=["煮熟"],
            taste_tags=["light"],
            cooking_methods=["boil"],
            required_tools=["pot"],
            warnings=[],
        )
    )


@pytest.fixture
def repo(tmp_path):
    repo = BatchRepository(tmp_path / "convert.db")
    text = "# 自由格式菜谱\n2人份；马铃薯200g。煮熟。"
    repo.ingest("r", "raw.md", hashlib.sha256(text.encode()).hexdigest(), text.encode(), text)
    return repo


class AI:
    def __init__(self):
        self.calls = []
        self.fail = False

    def request(self, purpose, payload, schema, entity, validator=None, **kwargs):
        self.calls.append((purpose, payload, kwargs))
        if self.fail:
            raise ValueError("test")
        result = document() if purpose == "convert" else schema(fdc_id="123", confidence=0.9, reason="synthetic match")
        if validator:
            validator(result)
        return result


class USDA:
    def search(self, query):
        return [{"fdcId": 123, "description": "SYNTHETIC TEST ONLY", "dataType": "Foundation"}]

    def food(self, fdc_id):
        return {
            "fdcId": 123,
            "description": "SYNTHETIC TEST ONLY",
            "dataType": "Foundation",
            "foodNutrients": [
                {"nutrient": {"id": key, "unitName": unit}, "amount": value}
                for key, unit, value in [
                    (1008, "KCAL", 100),
                    (1003, "G", 2),
                    (1004, "G", 1),
                    (1005, "G", 20),
                    (2000, "G", 1),
                    (1093, "MG", 5),
                ]
            ],
        }


@pytest.mark.parametrize("aliases", [["土豆"], ["马铃薯", "马铃薯"], [" "]])
def test_invalid_aliases_are_rejected(aliases):
    data = document().model_dump()
    data["ingredients"][0]["aliases"] = aliases
    with pytest.raises(ValueError, match="别名"):
        ConvertedRecipe.model_validate(data)


def test_reference_roundtrip_and_legacy_default(repo):
    data = document().model_dump()
    data.pop("references")
    assert ConvertedRecipe.model_validate(data).references == []
    reference = {"title": "做法参考", "url": "https://www.bilibili.com/video/BV1Nq4y1W7K9?x=1&y=2"}
    data["references"] = [reference]
    value = ConvertedRecipe.model_validate(data)
    row = repo.get_recipe("r")
    repo.save_conversion("r", value, source_hash=row["content_hash"], expected=None, prompt="test", status="approved")
    assert repo.conversion_export()["recipes"][0]["references"] == [reference]
    data["references"][0]["url"] = "javascript:alert(1)"
    with pytest.raises(ValueError, match="HTTP/HTTPS"):
        ConvertedRecipe.model_validate(data)


def test_confirmed_usda_alias_reuse_and_revocation(repo):
    convert_one(repo, "r", ai=AI(), with_nutrition=True, usda=USDA())
    item = document().ingredients[0].model_copy(update={"canonical_name": "马铃薯", "ingredient_id": "new_ai_id"})
    assert repo.lookup_confirmed_usda(item) is None
    row = repo.conversion("r")
    repo.save_conversion("r", document(), source_hash=row["source_hash"], expected=row["updated_at"], prompt="test", status="approved")
    match = repo.lookup_confirmed_usda(item)
    assert match["fdc_id"] == "123"
    assert match["reused_from_recipe_id"] == "r"
    assert repo.lookup_confirmed_usda(item.model_copy(update={"usda_query": "potato cooked"})) is None
    assert repo.conversion_export()["usda_mappings"][0]["fdc_id"] == "123"
    row = repo.conversion("r")
    repo.save_conversion("r", document(), source_hash=row["source_hash"], expected=row["updated_at"], prompt="test")
    assert repo.lookup_confirmed_usda(item) is None


def test_shared_mapping_avoids_external_requests(repo):
    convert_one(repo, "r", ai=AI(), with_nutrition=True, usda=USDA())
    row = repo.conversion("r")
    repo.save_conversion("r", document(), source_hash=row["source_hash"], expected=row["updated_at"], prompt="test", status="approved")
    text = "第二份菜谱"
    digest = hashlib.sha256(text.encode()).hexdigest()
    repo.ingest("r2", "second.md", digest, text.encode(), text)
    repo.save_conversion("r2", document(), source_hash=digest, expected=None, prompt="test")
    class NoUSDA:
        def search(self, query):
            raise AssertionError("must reuse approved mapping")
    ai = AI()
    enrich_nutrition(repo, "r2", ai=ai, usda=NoUSDA())
    snapshot = json.loads(repo.conversion("r2")["nutrition_json"])
    assert snapshot[food_key(document().ingredients[0])]["reused_from_recipe_id"] == "r"
    assert not ai.calls


def test_failed_only_does_not_retry_unmatched(repo):
    convert_one(repo, "r", ai=AI())
    row = repo.conversion("r")
    key = food_key(document().ingredients[0])
    repo.save_conversion("r", document(), source_hash=row["source_hash"], expected=row["updated_at"], prompt="test",
                         nutrition={key: {"status": "unmatched", "error": "No suitable candidate"}})
    ai = AI()
    enrich_nutrition(repo, "r", ai=ai, usda=USDA(), failed_only=True)
    assert not ai.calls
    row = repo.conversion("r")
    repo.save_conversion("r", document(), source_hash=row["source_hash"], expected=row["updated_at"], prompt="test",
                         nutrition={key: {"status": "request_failed", "error": "HTTP 400"}})
    enrich_nutrition(repo, "r", ai=ai, usda=USDA(), failed_only=True)
    assert json.loads(repo.conversion("r")["nutrition_json"])[key]["fdc_id"] == "123"


def test_usda_receives_source_constraints(repo):
    class CapturingAI(AI):
        def request(self, purpose, payload, *args, **kwargs):
            if purpose == "usda_match":
                assert payload["raw_text"] == "马铃薯200g"
                assert payload["amount_evidence"] == "原文200g"
                assert "recipe_warnings" in payload
            return super().request(purpose, payload, *args, **kwargs)

    ai = CapturingAI()
    convert_one(repo, "r", ai=ai, with_nutrition=True, usda=USDA())
    assert any(call[0] == "usda_match" for call in ai.calls)


def test_one_call_no_parser_no_catalog_or_tags_pass(repo):
    ai = AI()
    convert_one(repo, "r", ai=ai)
    assert [c[0] for c in ai.calls] == ["convert"]
    assert ai.calls[0][1]["markdown"] == repo.get_recipe("r")["raw_text"]
    assert repo.get_recipe("r")["extracted_json"] is None
    assert repo.standards() == []
    row = repo.conversion("r")
    value = ConvertedRecipe.model_validate_json(row["data_json"])
    assert value.taste_tags == ["light"] and value.ingredients[0].grams == 200
    assert row["status"] == "pending_review"
    assert not repo.conversion_export()["recipes"]


def test_nutrition_and_one_review_export(repo):
    ai = AI()
    convert_one(repo, "r", ai=ai, with_nutrition=True, usda=USDA())
    row = repo.conversion("r")
    value = ConvertedRecipe.model_validate_json(row["data_json"])
    result = calculate(value, json.loads(row["nutrition_json"]), TagThresholds())
    assert result["total"]["calories_kcal"] == 200
    assert result["per_serving"]["calories_kcal"] == 100
    repo.save_conversion(
        "r", value, source_hash=row["source_hash"], expected=row["updated_at"], prompt=row["prompt"], status="approved"
    )
    assert repo.conversion_export()["recipes"][0]["ready_for_recommendation"]
    old = repo.conversion("r")
    enrich_nutrition(repo, "r", ai=ai, usda=USDA())
    assert repo.conversion("r")["updated_at"] == old["updated_at"]
    value.ingredients[0].usda_query = "potato cooked"
    repo.save_conversion("r", value, source_hash=old["source_hash"], expected=old["updated_at"], prompt=old["prompt"])
    assert json.loads(repo.conversion("r")["nutrition_json"]) == {}
    assert not repo.conversion_export()["recipes"]


def test_retry_settings_feedback_and_failure_preserves_json(repo):
    config = ConversionSettings(prompt="test instructions", retries=4, timeout=130, max_output_tokens=14000)
    repo.save_conversion_settings(config)
    assert repo.conversion_settings() == config
    service = configured_ai(repo, config)
    assert service.settings["retries"] == 4 and service.settings["timeout"] == 130
    ai = AI()
    convert_one(repo, "r", ai=ai, settings=config, feedback="修改口味", current_json="broken json")
    assert ai.calls[0][1]["feedback"] == "修改口味"
    assert ai.calls[0][1]["current_json"] == "broken json"
    assert ai.calls[0][2]["system_prompt"].startswith("test instructions")
    old = repo.conversion("r")
    ai.fail = True
    with pytest.raises(ValueError):
        convert_one(repo, "r", ai=ai, force=True)
    assert repo.conversion("r")["data_json"] == old["data_json"]
    with pytest.raises(ValueError):
        repo.save_conversion("r", document(), source_hash="wrong", expected=old["updated_at"], prompt="")


def test_unified_batch_resumes_and_does_not_use_legacy_stages(repo):
    ai = AI()
    run = repo.create_run(BatchOptions(pipeline="unified", conversion=repo.conversion_settings(), usda=False))
    execute(repo, run, ai=ai, usda=USDA())
    assert repo.run(run)["status"] == "completed"
    assert len(ai.calls) == 1
    assert {i["stage"] for i in repo.items(run)} == {"convert"}
    repo.resume(run)
    execute(repo, run, ai=ai, usda=USDA())
    assert len(ai.calls) == 1


def test_missing_nutrition_not_zero_and_no_ai_nutrients(repo):
    value = document()
    result = calculate(value, {}, TagThresholds())
    assert result["status"] == "incomplete" and result["total"]["calories_kcal"] is None
    with pytest.raises(ValueError):
        ConvertedRecipe.model_validate({**value.model_dump(), "calories_kcal": 100})
    value.ingredients[0].grams = None
    result = calculate(value, {food_key(value.ingredients[0]): {"per_100g": {"calories_kcal": 100}}}, TagThresholds())
    assert result["total"]["calories_kcal"] is None


def test_batch_redo_pending_but_protect_approved(repo):
    ai = AI()
    convert_one(repo, "r", ai=ai)
    run = repo.create_run(BatchOptions(pipeline="unified", usda=False, redo_existing=True))
    execute(repo, run, ai=ai, usda=USDA())
    assert len(ai.calls) == 2
    row = repo.conversion("r")
    repo.save_conversion(
        "r",
        document(),
        source_hash=row["source_hash"],
        expected=row["updated_at"],
        prompt=row["prompt"],
        status="approved",
    )
    run = repo.create_run(BatchOptions(pipeline="unified", usda=False, redo_existing=True))
    execute(repo, run, ai=ai, usda=USDA())
    assert len(ai.calls) == 2
    assert repo.conversion("r")["status"] == "approved"


def test_simple_ui_pagination_edit_review_and_settings(repo, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("CLEANING_DB_PATH", str(repo.path))
    repo.ingest("second", "z.md", "second-hash", b"# second", "# second")
    convert_one(repo, "r", ai=AI())
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=20).run()
    assert app.sidebar.radio[0].options == ["菜谱转换", "批量转换", "数据库菜谱", "标准食材", "数据与设置"]
    assert not app.error and not app.exception
    next(b for b in app.button if b.label == "下一篇 →").click().run()
    assert next(s for s in app.selectbox if s.label == "当前菜谱").value == "second"
    next(b for b in app.button if b.label == "← 上一篇").click().run()
    assert next(s for s in app.selectbox if s.label == "当前菜谱").value == "r"
    next(b for b in app.button if b.label == "通过审阅").click().run()
    assert not app.error and repo.conversion("r")["status"] == "approved"
    app.sidebar.radio[0].set_value("数据与设置").run()
    next(n for n in app.number_input if n.label == "额外重试次数").set_value(3)
    next(b for b in app.button if b.label == "保存 AI 设置").click().run()
    assert repo.conversion_settings().retries == 3


@pytest.mark.parametrize("retries,expected", [(0, 1), (2, 3)])
def test_configured_retry_rounds_are_enforced(repo, retries, expected):
    from types import SimpleNamespace

    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed={})

    service = configured_ai(repo, ConversionSettings(retries=retries))
    service.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    service.sleeper = lambda _: None
    with pytest.raises(ValueError):
        service.request("convert", {}, ConvertedRecipe, "r")
    assert len(calls) == expected


def test_cache_usage_is_not_mistaken_for_secret_tokens():
    from data_cleaning.services.redaction import redact

    value = redact(
        {
            "prompt_cache_hit_tokens": 128,
            "prompt_tokens_details": {"cached_tokens": 128, "secret_token": "secret"},
            "access_token": "credential",
            "prompt_tokens": "unexpected-secret",
        }
    )
    assert value["prompt_cache_hit_tokens"] == 128
    assert value["prompt_tokens_details"] == {"cached_tokens": 128, "secret_token": "[REDACTED]"}
    assert value["access_token"] == value["prompt_tokens"] == "[REDACTED]"
