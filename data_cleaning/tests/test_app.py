from pathlib import Path

from streamlit.testing.v1 import AppTest

from data_cleaning.config import ROOT
from data_cleaning.models.repository import Repository
from data_cleaning.services.pipeline import extract_all, import_directory


def test_dashboard_pages_review_clear(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    # Tests run with --basetemp inside data_cleaning, preserving the DB path boundary.
    monkeypatch.setenv("CLEANING_DB_PATH", str(path))
    repo = Repository(path)
    fixtures = Path(__file__).parent / "fixtures/howtocook"
    import_directory(repo, fixtures)
    extract_all(repo)
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=20).run()
    app.sidebar.checkbox[0].check().run()
    app.sidebar.radio[0].set_value("Dashboard").run()
    assert not app.exception
    assert not app.error
    assert app.metric[0].value == "2"
    for page in ("食材列表", "固定选项", "菜谱列表与审核"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception
        assert not app.error
    next(s for s in app.selectbox if s.label == "处理方式").set_value("通过")
    next(b for b in app.button if b.label == "保存审核结果").click().run()
    assert not app.exception
    assert not app.error
    assert repo.dashboard()["已审核菜谱"] == 1
    app.sidebar.radio[0].set_value("数据管理").run()
    next(b for b in app.button if b.label == "清空数据库").click().run()
    assert repo.dashboard()["菜谱总数"] == 2
    next(c for c in app.checkbox if c.label == "我确认清空当前数据库中的全部数据").check()
    next(t for t in app.text_input if t.label == "再次确认：请输入 CLEAR").set_value("CLEAR")
    next(b for b in app.button if b.label == "清空数据库").click().run()
    assert not app.exception
    assert repo.dashboard()["菜谱总数"] == 0
