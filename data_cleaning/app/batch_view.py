import json
from collections import Counter

import streamlit as st

from data_cleaning.config import service_settings
from data_cleaning.models.batch import BatchRepository
from data_cleaning.schemas.batch import BatchOptions
from data_cleaning.services.batch import launch, preflight
from data_cleaning.services.batch_lock import worker_lock


@st.fragment(run_every="3s")
def batch_progress(repo: BatchRepository) -> None:
    try:
        _batch_progress(repo)
    except ValueError as exc:
        st.error(str(exc))


def _batch_progress(repo: BatchRepository) -> None:
    runs = [r for r in repo.runs() if BatchOptions.model_validate_json(r["options_json"]).pipeline == "unified"]
    if not runs:
        st.info("尚无新版转换任务。旧版批次保留，可通过命令行继续。")
        return
    selected = st.selectbox(
        "查看批次",
        [r["id"] for r in runs],
        format_func=lambda key: next(
            f"{r['created_at'][:19]} · {r['status']} · {key[:8]}" for r in runs if r["id"] == key
        ),
    )
    run = repo.run(selected)
    items = repo.items(selected)
    counts = Counter(i["status"] for i in items)
    st.write(
        {
            "状态": run["status"],
            "当前阶段": run["stage"],
            "当前记录": run["current_item"],
            "菜谱范围": len(json.loads(run["recipe_ids_json"])),
            "完成": counts["done"],
            "跳过": counts["skipped"],
            "失败": counts["failed"],
            "处理中": counts["processing"],
        }
    )
    options = BatchOptions.model_validate_json(run["options_json"])
    st.caption(
        "每条记录对应一篇菜谱。关闭页面不会停止后台进程。"
        if options.pipeline == "unified"
        else "这是旧版分阶段批次，完成数为各阶段记录数之和。"
    )
    if run["error_message"]:
        st.error(run["error_message"])
    active = run["status"] in ("queued", "running", "stopping")
    if active and st.button("停止任务（当前请求结束后）"):
        repo.request_stop(selected)
        st.rerun(scope="fragment")
    if not active and st.button("继续此批次 / 重试失败项"):
        preflight(BatchOptions.model_validate_json(run["options_json"]))
        repo.resume(selected)
        launch(repo, selected)
        st.rerun(scope="fragment")
    if active and st.button("恢复已中断进程的任务状态"):
        with worker_lock(repo.path):
            repo.interrupted()
        st.rerun(scope="fragment")
    st.dataframe(items, hide_index=True, width="stretch")


def batch_page(repository) -> None:
    repo = BatchRepository(repository.path)
    st.header("批量转换")
    st.write("每篇完整 Markdown 一次生成 JSON，包含食材、用量、标签和 USDA 检索词。完成后逐篇审阅即可。")
    settings = service_settings()
    st.caption(
        f"模型：{settings['model']} · AI 密钥：{'已配置' if settings['openai_key'] else '未配置'} · USDA 密钥：{'已配置' if settings['usda_key'] else '未配置'}"
    )
    st.info("已有人工通过/拒绝结果受保护。新 AI 结果为待审核；无匹配不猜测。任务会产生 API 用量，密钥缺失时不会启动。")
    with st.form("batch_start"):
        config = repo.conversion_settings()
        prompt = st.text_area("本批提示词", config.prompt, height=220)
        retries = st.number_input("本批额外重试次数", 0, 5, config.retries)
        redo = st.checkbox("按本批提示词重做已有待审阅 JSON", value=False)
        usda = st.checkbox("转换后自动查询 USDA 并计算营养", value=bool(settings["usda_key"]))
        limit = st.number_input("本批最多菜谱数（0 = 全部）", min_value=0, max_value=100000, value=0, step=1)
        start = st.form_submit_button("一键开始批量 AI 处理", type="primary")
    if start:
        options = BatchOptions(
            pipeline="unified",
            redo_existing=redo,
            conversion=config.model_copy(update={"prompt": prompt, "retries": int(retries)}),
            usda=usda,
            recipe_limit=int(limit),
        )
        preflight(options)
        run_id = repo.create_run(options)
        launch(repo, run_id)
        st.success("任务已启动，下方每 3 秒刷新进度。")
    batch_progress(repo)
