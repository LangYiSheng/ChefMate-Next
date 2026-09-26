import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_cleaning.models.repository import Repository
from data_cleaning.schemas.recipe import Recipe, Status
from data_cleaning.services import exporter
from data_cleaning.services.howtocook import parse
from data_cleaning.services.pipeline import extract_all, import_directory

FIXTURES = Path(__file__).parent / "fixtures/howtocook"


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "cleaning.db")


def seed(repo):
    assert import_directory(repo, FIXTURES)["imported"] == 2
    assert extract_all(repo)["extracted"] == 2
    return repo.list_recipes()[0]["recipe_id"]


def approve(repo, recipe_id):
    row = repo.get_recipe(recipe_id)
    recipe = Recipe.model_validate_json(row["extracted_json"])
    repo.save_extraction(recipe, row["updated_at"], action="human_review", review_status=Status.APPROVED)


def test_faithful_extraction(repo):
    seed(repo)
    rows = repo.list_recipes()
    row = repo.get_recipe(next(r["recipe_id"] for r in rows if "土豆小炒" in r["name"]))
    recipe = Recipe.model_validate_json(row["extracted_json"])
    assert recipe.ingredients[0].raw_name == "马铃薯"
    assert recipe.ingredients[0].raw_amount == "约两个"
    assert recipe.ingredients[0].normalized_amount is None
    assert recipe.ingredients[3].raw_amount == "1 个"
    assert recipe.original_servings == "按照 4 人份准备："
    assert recipe.total_time_min == 15
    assert recipe.difficulty == "★★"
    assert recipe.required_tools == ["炒锅"]
    assert "补充说明" in recipe.steps[0]
    assert "附加内容" in recipe.raw_sections
    assert "calories_kcal" not in recipe.model_dump()


def test_idempotence_and_counts(repo):
    recipe_id = seed(repo)
    approve(repo, recipe_id)
    assert import_directory(repo, FIXTURES)["skipped"] == 2
    assert extract_all(repo)["skipped"] == 2
    assert repo.get_recipe(recipe_id)["status"] == "approved"
    potato = next(r for r in repo.ingredient_list() if r["raw_name"] == "马铃薯")
    assert potato["occurrences"] == potato["recipe_count"] == 2
    assert len(repo.ingredient_recipes(potato["id"])) == 2
    assert len(repo.export_snapshot()["recipes"]) == 1


def test_changed_source_preserves_history_revokes_approval(repo):
    recipe_id = seed(repo)
    approve(repo, recipe_id)
    row = repo.get_recipe(recipe_id)
    changed = row["raw_text"] + "\n新版本备注\n"
    repo.ingest(recipe_id, row["source"], hashlib.sha256(changed.encode()).hexdigest(), changed.encode(), changed)
    current = repo.get_recipe(recipe_id)
    assert current["revision"] == 2
    assert current["status"] == "pending"
    assert current["extracted_json"] is None
    assert repo.export_snapshot()["recipes"] == []
    assert len(repo.source_history(recipe_id)) == 2
    assert any(e["action"] == "human_review" for e in repo.history(recipe_id))


def test_failure_isolation_and_retry(repo, tmp_path):
    seed(repo)
    (tmp_path / "bad.md").write_text("# 无操作内容", encoding="utf-8")
    (tmp_path / "invalid.md").write_bytes(b"\xff\xfe\x80")
    result = import_directory(repo, tmp_path, "broken")
    assert result["failed"] == 1
    assert extract_all(repo)["failed"] == 2
    assert repo.dashboard()["已提取"] == 2
    assert extract_all(repo, failed_only=True)["failed"] == 2
    with repo.connection() as db:
        assert (
            db.execute("SELECT raw_bytes FROM source_versions WHERE raw_text IS NULL").fetchone()[0] == b"\xff\xfe\x80"
        )


def test_clear_requires_confirmation_and_reinitializes(repo):
    seed(repo)
    with pytest.raises(ValueError):
        repo.clear("clear")
    assert repo.dashboard()["菜谱总数"] == 2
    repo.clear("CLEAR")
    assert repo.path.exists()
    assert repo.dashboard()["菜谱总数"] == 0
    assert repo.ingredient_list() == []
    assert len(repo.enums()) > 40
    Repository(repo.path)
    seed(repo)


def test_stale_write_is_rejected(repo):
    recipe_id = seed(repo)
    row = repo.get_recipe(recipe_id)
    recipe = Recipe.model_validate_json(row["extracted_json"])
    approve(repo, recipe_id)
    with pytest.raises(ValueError, match="其他任务"):
        repo.save_extraction(recipe, row["updated_at"])
    assert repo.get_recipe(recipe_id)["status"] == "approved"


def test_export_consistent_approved_only(repo, tmp_path, monkeypatch):
    recipe_id = seed(repo)
    assert repo.export_snapshot()["recipes"] == []
    approve(repo, recipe_id)
    monkeypatch.setattr(exporter, "ROOT", tmp_path)
    target = exporter.export_json(repo)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["approved_only"] is True
    assert manifest["ready_for_recommendation"] is False
    assert manifest["recipe_count"] == 1
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
    exported = json.loads((target / "recipes.json").read_text(encoding="utf-8"))
    assert exported[0]["raw_source_text"] == repo.get_recipe(recipe_id)["raw_text"]
    assert exported[0]["provenance"]["status"] == "approved"
    assert len(repo.export_snapshot(False)["recipes"]) == 2


def test_validation_and_ambiguity():
    text = "# 测试\n## 必备原料和工具\n- 鸡蛋 1个\n- 小葱挽成结\n## 操作\n- 做熟\n"
    result = parse(text, "id", "source")
    assert result.original_servings is None
    assert result.ingredients[1].raw_name == "小葱挽成结"
    assert result.ingredients[1].raw_amount is None
    with pytest.raises(ValidationError):
        Recipe.model_validate({**result.model_dump(), "calories_kcal": 100})
    with pytest.raises(ValidationError):
        Recipe.model_validate({**result.model_dump(), "total_time_min": -1})


def test_newer_db_rejected(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="版本较新"):
        Repository(path)


def test_force_reextract_revokes_approval(repo):
    recipe_id = seed(repo)
    approve(repo, recipe_id)
    assert extract_all(repo, force=True)["extracted"] == 2
    assert repo.get_recipe(recipe_id)["status"] == "pending_review"
    assert repo.export_snapshot()["recipes"] == []


def test_reject_failed_recipe_without_fabricating_data(repo, tmp_path):
    (tmp_path / "bad.md").write_text("# 缺少食材的记录", encoding="utf-8")
    import_directory(repo, tmp_path)
    extract_all(repo)
    recipe_id = repo.list_recipes()[0]["recipe_id"]
    row = repo.get_recipe(recipe_id)
    repo.reject(recipe_id, row["updated_at"], "原文信息不足")
    assert extract_all(repo, failed_only=True)["skipped"] == 1
    current = repo.get_recipe(recipe_id)
    assert current["status"] == "rejected"
    assert current["extracted_json"] is None
    assert repo.export_snapshot()["recipes"] == []
