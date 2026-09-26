import hashlib
import json

import pytest

from data_cleaning.config import ROOT
from data_cleaning.models.batch import BatchRepository
from data_cleaning.schemas.batch import BatchOptions
from data_cleaning.schemas.cleaning import StandardIngredient
from data_cleaning.services.batch import execute, preflight
from data_cleaning.services.batch_catalog import match_usda
from data_cleaning.services.batch_lock import worker_lock
from data_cleaning.services.pipeline import extract_all


@pytest.fixture
def repo(tmp_path):
    repo = BatchRepository(tmp_path / "batch.db")
    for index, name in enumerate(["土豆", "马铃薯"]):
        text = f"# 菜谱{index}\n## 原料\n- {name} 100g\n## 操作\n- 煮熟"
        repo.ingest(str(index), f"test/{index}.md", hashlib.sha256(text.encode()).hexdigest(), text.encode(), text)
    extract_all(repo)
    return repo


class AI:
    def __init__(self, repo):
        self.repo = repo
        self.calls = []
        self.fail = None
        self.stop = None

    def request(self, purpose, payload, schema, entity, validator=None):
        self.calls.append((purpose, entity))
        if self.fail == (purpose, entity):
            raise ValueError("simulated failure")
        if self.stop:
            self.repo.request_stop(self.stop)
            self.stop = None
        if purpose == "extract":
            source = json.loads(self.repo.get_recipe(entity)["extracted_json"] or "{}")
            # Retry after a failed extraction still uses the original source.
            if not source:
                from data_cleaning.services.howtocook import parse

                source = parse(payload["raw_text"], entity, "test").model_dump()
            value = {k: source[k] for k in schema.model_fields}
            value["ingredients"] = [
                {k: i[k] for k in ("raw_name", "raw_amount", "raw_unit", "raw_text")} for i in source["ingredients"]
            ]
        elif purpose == "catalog":
            existing = payload["candidates"][0]["ingredient_id"] if payload["candidates"] else None
            value = dict(
                existing_id=existing,
                new_id=None if existing else "potato",
                canonical_name=None if existing else "土豆",
                category=None if existing else "vegetable",
                usda_query="potato raw",
                confidence=0.9,
                reason="测试候选",
            )
        elif purpose == "usda_match":
            value = dict(fdc_id="123", confidence=0.9, reason="测试匹配")
        elif purpose == "usda_query":
            value = dict(query="potato raw")
        else:
            value = dict(taste_tags=[], cooking_methods=["boil"], required_tools=["pot"], difficulty="beginner")
        result = schema.model_validate(value)
        if validator:
            validator(result)
        return result


class USDA:
    def __init__(self, repo):
        self.repo = repo
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        return [{"fdcId": 123, "description": "SYNTHETIC potato raw", "dataType": "Foundation"}]

    def food(self, fdc_id):
        food = {"fdcId": 123, "description": "SYNTHETIC potato raw", "dataType": "Foundation", "foodNutrients": []}
        self.repo.cache_food(food)
        return food


def test_batch_end_to_end_drafts_resume_and_no_auto_approval(repo):
    ai, usda = AI(repo), USDA(repo)
    run = repo.create_run(BatchOptions())
    execute(repo, run, ai=ai, usda=usda)
    assert repo.run(run)["status"] == "completed"
    assert len(repo.standards()) == 1
    standard = repo.standard("potato")
    assert standard["status"] == standard["nutrition_match_status"] == "pending_review"
    assert standard["nutrition_source_id"] == "123"
    assert "马铃薯" in standard["aliases"]
    assert all(
        r["canonical_ingredient_id"] == "potato" and r["status"] == "pending_review" for r in repo.raw_scope(["0", "1"])
    )
    assert repo.export_snapshot()["recipes"] == []
    count = len(ai.calls)
    repo.resume(run)
    execute(repo, run, ai=ai, usda=usda)
    assert len(ai.calls) == count
    assert len(usda.calls) == 1
    second = repo.create_run(BatchOptions())
    execute(repo, second, ai=ai, usda=usda)
    assert len(ai.calls) == count
    with pytest.raises(ValueError):
        repo.review_nutrition("potato", standard["updated_at"], True, "must review standard first")
    repo.clear("CLEAR")
    assert not repo.runs() and not repo.standards()


def test_failure_isolated_and_resume_retries(repo):
    ai = AI(repo)
    ai.fail = ("extract", "0")
    run = repo.create_run(BatchOptions(catalog=False, usda=False, tags=False))
    execute(repo, run, ai=ai, usda=USDA(repo))
    assert repo.run(run)["status"] == "completed_with_errors"
    assert len([i for i in repo.items(run) if i["status"] == "done"]) == 1
    ai.fail = None
    repo.resume(run)
    execute(repo, run, ai=ai, usda=USDA(repo))
    assert repo.run(run)["status"] == "completed"
    assert ai.calls.count(("extract", "1")) == 1
    assert ai.calls.count(("extract", "0")) == 2


def test_pause_lock_clear_guard_and_scope(repo):
    ai = AI(repo)
    run = repo.create_run(BatchOptions(catalog=False, usda=False, tags=False))
    ai.stop = run
    with pytest.raises(ValueError):
        repo.create_run(BatchOptions())
    with pytest.raises(ValueError):
        repo.clear("CLEAR")
    with worker_lock(repo.path):
        with pytest.raises(ValueError):
            with worker_lock(repo.path):
                pass
    execute(repo, run, ai=ai, usda=USDA(repo))
    assert repo.run(run)["status"] == "paused"
    scope = repo.run(run)["recipe_ids_json"]
    repo.resume(run)
    execute(repo, run, ai=ai, usda=USDA(repo))
    assert repo.run(run)["status"] == "completed"
    assert repo.run(run)["recipe_ids_json"] == scope
    assert len(ai.calls) == 2


def test_usda_outside_candidates_rejected(repo):
    repo.save_standard(StandardIngredient(ingredient_id="potato", canonical_name="土豆", category="vegetable"))

    class BadAI(AI):
        def request(self, purpose, payload, schema, entity, validator=None):
            if purpose == "usda_match":
                return schema(fdc_id="99999", confidence=1, reason="invalid")
            return super().request(purpose, payload, schema, entity, validator)

    with pytest.raises(ValueError, match="候选"):
        match_usda(repo, "potato", BadAI(repo), USDA(repo))
    assert not repo.standard("potato")["nutrition_json"]


def test_human_recipe_decisions_are_preserved(repo):
    from data_cleaning.schemas.recipe import Recipe, Status

    first = repo.get_recipe("0")
    repo.save_extraction(
        Recipe.model_validate_json(first["extracted_json"]), first["updated_at"], review_status=Status.APPROVED
    )
    second = repo.get_recipe("1")
    repo.reject("1", second["updated_at"], "human rejection")
    ai = AI(repo)
    run = repo.create_run(BatchOptions(catalog=False, usda=False, tags=False))
    execute(repo, run, ai=ai, usda=USDA(repo))
    assert not ai.calls
    assert repo.get_recipe("0")["status"] == "approved"
    assert repo.get_recipe("1")["status"] == "rejected"


def test_preflight_and_batch_ui_do_not_call_api(repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(ValueError, match="密钥"):
        preflight(BatchOptions())
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setenv("USDA_API_KEY", "")
    with pytest.raises(ValueError, match="USDA"):
        preflight(BatchOptions())
    preflight(BatchOptions(usda=False))
    monkeypatch.setenv("CLEANING_DB_PATH", str(repo.path))
    from streamlit.testing.v1 import AppTest

    # Override path in-process because pytest DB lies outside production workspace.
    import data_cleaning.config as config

    monkeypatch.setattr(config, "database_path", lambda: repo.path)
    app = AppTest.from_file(str(ROOT / "app/main.py")).run()
    app.sidebar.radio[0].set_value("批量转换").run()
    assert not app.exception
    next(c for c in app.checkbox if c.label == "转换后自动查询 USDA 并计算营养").check()
    next(b for b in app.button if b.label == "一键开始批量 AI 处理").click().run()
    assert any("USDA" in e.value for e in app.error)
    assert not repo.runs()
