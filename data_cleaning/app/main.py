import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from data_cleaning.app.batch_view import batch_page
from data_cleaning.app.conversion_view import conversion_page, simple_data_page
from data_cleaning.app.browse_view import recipes_page, ingredients_page
from data_cleaning.config import database_path
from data_cleaning.models.conversion import ConversionRepository as Repository

st.set_page_config(page_title="ChefMate 数据清洗", page_icon="🥬", layout="wide")
st.title("🥬 ChefMate · 数据清洗工作台")
st.caption("Markdown → AI 完整 JSON → 修改与审阅 → 营养计算")
page = st.sidebar.radio("工作区", ["菜谱转换", "批量转换", "数据库菜谱", "标准食材", "数据与设置"])
st.sidebar.caption("本地内部工具 · USDA 本地查询 · AI 在 .env 配置")
try:
    repository = Repository(database_path())
    {
        "菜谱转换": conversion_page,
        "批量转换": batch_page,
        "数据与设置": simple_data_page,
        "数据库菜谱": recipes_page,
        "标准食材": ingredients_page,
    }[page](repository)
except Exception as exc:
    st.error(f"操作未完成：{exc}")
