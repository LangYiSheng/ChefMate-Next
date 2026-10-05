from pathlib import Path
from streamlit.testing.v1 import AppTest
from data_cleaning.config import ROOT
from data_cleaning.models.conversion import ConversionRepository
from data_cleaning.services.pipeline import import_directory


def test_dashboard_pages_and_guarded_clear(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setenv("CLEANING_DB_PATH", str(path))
    repo = ConversionRepository(path)
    import_directory(repo, Path(__file__).parent / "fixtures/howtocook")
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=20).run()
    for page in ("菜谱转换", "批量转换", "数据库菜谱", "标准食材", "数据与设置"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception
        assert not app.error
    assert any("本地库" in warning.value for warning in app.warning)
    clear = next(b for b in app.button if b.label == "清空全部数据")
    assert clear.disabled
    assert len(repo.conversions()) == 2
    next(t for t in app.text_input if "CLEAR" in t.label).set_value("CLEAR").run()
    next(b for b in app.button if b.label == "清空全部数据").click().run()
    assert not app.exception
    assert len(repo.conversions()) == 0
