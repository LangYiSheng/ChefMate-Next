import hashlib
import json

from chefmate_contracts import RecipeDocument, VocabularySnapshot, validate_recipe_vocabulary
from chefmate_core import ConvertedRecipe
from data_cleaning.models.conversion import ConversionRepository
from data_cleaning.services import exporter
from data_cleaning.tests.test_conversion import document


def test_shared_seed_does_not_overwrite_local_vocabulary_and_export_is_self_contained(tmp_path, monkeypatch):
    path = tmp_path / "cleaning.db"
    repo = ConversionRepository(path)
    repo.save_enum("taste", "light", "自定义清淡", True)
    repo.save_enum("tool", "oven", "烤箱", False)
    repo = ConversionRepository(path)
    entries = {(e["kind"], e["code"]): e for e in repo.enums()}
    assert entries["taste", "light"]["label"] == "自定义清淡"
    assert entries["tool", "oven"]["status"] == "rejected"
    text = "土豆汤"
    digest = hashlib.sha256(text.encode()).hexdigest()
    repo.ingest("r", "source.md", digest, text.encode(), text)
    repo.save_conversion("r", document(), source_hash=digest, expected=None, prompt="test")
    monkeypatch.setattr(exporter, "ROOT", tmp_path)
    exported = exporter.export_json(repo, approved_only=False, stage="unified")
    raw = (exported / "vocabulary.json").read_bytes()
    snapshot = VocabularySnapshot.model_validate_json(raw)
    manifest = json.loads((exported / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"]["vocabulary.json"] == hashlib.sha256(raw).hexdigest()
    recipes = json.loads((exported / "recipes.json").read_text(encoding="utf-8"))
    # Persisted historical vocabulary survives changes in the live application.
    repo.save_enum("taste", "light", "停用", False)
    validate_recipe_vocabulary(document(), snapshot)
    assert recipes[0]["taste_tags"] == ["light"]
    assert RecipeDocument is ConvertedRecipe


def test_ui_conversion_nutrition_and_review_use_new_packages(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from data_cleaning.config import ROOT
    from data_cleaning.services import conversion
    from data_cleaning.tests.test_conversion import AI, USDA

    path = tmp_path / "ui.db"
    monkeypatch.setenv("CLEANING_DB_PATH", str(path))
    repo = ConversionRepository(path)
    text = "土豆200g，2人份，煮熟"
    digest = hashlib.sha256(text.encode()).hexdigest()
    repo.ingest("r", "source.md", digest, text.encode(), text)
    model = AI()
    monkeypatch.setattr(conversion, "configured_ai", lambda *args: model)
    monkeypatch.setattr(conversion, "USDAService", lambda *args: USDA())
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=20).run()
    next(button for button in app.button if button.label == "AI 转换 / 按意见重做").click().run()
    assert not app.exception and not app.error
    assert repo.conversion("r")["status"] == "pending_review"
    next(button for button in app.button if button.label == "补齐 / 重试 USDA 并重新计算").click().run()
    assert not app.exception and not app.error
    assert json.loads(repo.conversion("r")["nutrition_json"])
    next(button for button in app.button if button.label == "通过审阅").click().run()
    assert not app.exception and not app.error
    assert repo.conversion("r")["status"] == "approved"
