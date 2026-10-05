import json
import time

import streamlit as st

from data_cleaning.config import local_path, source_path
from data_cleaning.models.conversion import ConversionRepository
from data_cleaning.schemas.conversion import ConvertedRecipe, ConversionSettings
from data_cleaning.services.conversion import convert_one, enrich_nutrition
from data_cleaning.services.conversion_nutrition import calculate
from data_cleaning.services.exporter import export_json
from data_cleaning.services.pipeline import import_directory
from data_cleaning.services.usda import local_status

LABELS = {"pending_review": "待审阅", "approved": "已通过", "rejected": "已拒绝"}


def conversion_page(repository) -> None:
    repo = ConversionRepository(repository.path)
    st.header("菜谱转换")
    st.caption("完整 Markdown → AI 一次生成 JSON → 修改与审阅。营养优先由 USDA 数据计算，可开启 AI 缺失估算。")
    rows = repo.conversions()
    search = st.text_input("查找菜谱")
    state = st.selectbox("筛选", ["全部", "未转换", "待审阅", "已通过", "已拒绝"])
    matched_recipe_ids = set()
    if search.strip():
        mappings = repo.confirmed_usda_mappings()
        wanted = {m["fdc_id"] for m in mappings
                  if search.strip().casefold() in {n.casefold() for n in [m["name"], *m["aliases"]]}}
        from data_cleaning.services.conversion_nutrition import food_key
        for row in rows:
            saved = repo.conversion(row["recipe_id"])
            if not saved or saved["source_hash"] != row["content_hash"]:
                continue
            document = ConvertedRecipe.model_validate_json(saved["data_json"])
            snapshots = json.loads(saved["nutrition_json"])
            if any(search.casefold() in n.casefold()
                   for item in document.ingredients for n in [item.raw_name, item.canonical_name, *item.aliases]):
                matched_recipe_ids.add(row["recipe_id"])
            if any(str(snapshots.get(food_key(item), {}).get("fdc_id")) in wanted for item in document.ingredients):
                matched_recipe_ids.add(row["recipe_id"])
    rows = [r for r in rows if search in r["name"] or search in r["source"] or r["recipe_id"] in matched_recipe_ids]
    rows = [
        r
        for r in rows
        if state == "全部"
        or (state == "未转换" and (not r["status"] or r["source_hash"] != r["content_hash"]))
        or LABELS.get(r["status"]) == state
    ]
    if not rows:
        st.info("没有符合条件的菜谱。可在“数据与设置”导入 Markdown。")
        return
    ids = [r["recipe_id"] for r in rows]
    labels = {r["recipe_id"]: f"{r['name']} · {LABELS.get(r['status'], '未转换')}" for r in rows}
    if st.session_state.get("conversion_selected") not in ids:
        st.session_state["conversion_selected"] = ids[0]
    selected = st.selectbox("当前菜谱", ids, format_func=labels.get, key="conversion_selected")
    index = ids.index(selected)

    def move(target):
        st.session_state["conversion_selected"] = target

    prev, middle, nxt = st.columns([1, 2, 1])
    prev.button("← 上一篇", disabled=index == 0, on_click=move, args=(ids[max(0, index - 1)],))
    middle.caption(f"第 {index + 1} / {len(ids)} 篇")
    nxt.button("下一篇 →", disabled=index == len(ids) - 1, on_click=move, args=(ids[min(len(ids) - 1, index + 1)],))
    source = repo.get_recipe(selected)
    saved = repo.conversion(selected)
    settings = repo.conversion_settings()
    if saved and saved["source_hash"] != source["content_hash"]:
        st.warning("原文已更新，旧 JSON 已失效。请重新转换或对照原文修正。")
    if saved.get("error_message"):
        st.warning(saved["error_message"])
    left, right = st.columns(2)
    with left:
        st.subheader("原始 Markdown")
        st.caption(source["source"])
        st.code(source["raw_text"] or "原文无法解码", language="markdown", wrap_lines=True)
    with right:
        st.subheader("完整菜谱 JSON")
        edited = st.text_area(
            "直接编辑 JSON",
            json.dumps(json.loads(saved["data_json"]), ensure_ascii=False, indent=2) if saved else "",
            height=500,
            key=f"json_{selected}_{saved.get('updated_at', 'new')}",
        )
        feedback = st.text_area(
            "让 AI 修改的意见",
            placeholder="例如：保留原文单位；这里应是清淡口味；不要把工具当食材。",
            key=f"feedback_{selected}",
        )
        with st.expander("本次提示词与重试设置"):
            prompt = st.text_area("本次完整提示词", settings.prompt, height=220, key=f"prompt_{selected}")
            retries = st.number_input("失败后额外重试次数", 0, 5, settings.retries, key=f"retries_{selected}")
            st.caption("超时与输出上限在“数据与设置”统一配置。修改意见放在请求末尾，固定提示词前缀保持稳定。")
        auto_nutrition = st.checkbox(
            "转换后自动查询 USDA 并计算营养", value=local_status()["ready"], key=f"nutrition_{selected}"
        )
        estimate_mode = st.checkbox(
            "AI 估算缺失数据（标记估算值）", value=settings.estimate_missing, key=f"estimate_{selected}",
            help="补估可食克重、人数及缺失营养；已有来源值优先。结果需审阅，估算不会写成原文事实。",
        )
        current_settings = settings.model_copy(update={"estimate_missing": estimate_mode})
        if st.button("AI 转换 / 按意见重做", type="primary"):
            config = current_settings.model_copy(update={"prompt": prompt, "retries": int(retries)})
            started = time.monotonic()
            events = []
            with st.status("正在处理菜谱…", expanded=True) as status:
                output = st.empty()
                def report(message):
                    events.append(f"[{time.monotonic() - started:.1f}s] {message}")
                    st.session_state[f"run_log_{selected}"] = list(events)
                    output.code("\n".join(events[-80:]), language=None)
                    status.update(label=message)
                try:
                    convert_one(
                        repo,
                        selected,
                        settings=config,
                        feedback=feedback,
                        current_json=edited or None,
                        force=True,
                        with_nutrition=auto_nutrition,
                        progress=report,
                    )
                    status.update(label="处理完成", state="complete")
                except Exception:
                    report("处理未完成，请查看错误和服务记录；已有数据保留")
                    status.update(label="处理失败", state="error")
                    raise
            st.rerun()
        if st.session_state.get(f"run_log_{selected}"):
            with st.expander("最近一次转换运行日志"):
                st.code("\n".join(st.session_state[f"run_log_{selected}"]), language=None)
                st.caption("时间为累计耗时。当前接口为非流式返回，等待响应期间无法显示模型内部进度。日志保留在当前浏览器会话，详细调用记录保存在数据库。")
        if saved or edited:
            note = st.text_input("审阅备注", saved.get("review_note", ""), key=f"note_{selected}")
            save, approve, reject = st.columns(3)
            action = "pending_review" if save.button("保存修改") else None
            if approve.button("通过审阅"):
                action = "approved"
            if reject.button("拒绝"):
                action = "rejected"
            if action:
                value = ConvertedRecipe.model_validate_json(edited)
                repo.save_conversion(
                    selected,
                    value,
                    source_hash=source["content_hash"],
                    expected=saved.get("updated_at"),
                    prompt=saved.get("prompt", prompt),
                    status=action,
                    note=note,
                )
                st.rerun()
    if saved:
        st.subheader("营养结果")
        st.caption("检查下方 USDA 来源及匹配是否正确，随整篇 JSON 一次审阅；开启 AI 估算后点击下方补齐按钮；关闭后重新计算可移除估算。")
        if st.button("仅重试本菜谱请求失败项"):
            if edited != saved["data_json"]:
                # Compare parsed content because the editor formats JSON with indentation.
                if json.loads(edited) != json.loads(saved["data_json"]):
                    st.error("请先保存 JSON 修改，再重试已保存结果中的失败项。")
                    st.stop()
            with st.spinner("仅重试请求失败项…"):
                enrich_nutrition(repo, selected, settings=current_settings, failed_only=True)
            st.rerun()
        if st.button("补齐 / 重试 USDA 并重新计算"):
            # The estimate toggle applies to this action as well as conversion.
            # Save the visible edits first, so nutrition never uses an unseen old JSON.
            value = ConvertedRecipe.model_validate_json(edited)
            repo.save_conversion(
                selected,
                value,
                source_hash=source["content_hash"],
                expected=saved["updated_at"],
                prompt=saved["prompt"],
            )
            with st.spinner("查询 USDA 并计算…"):
                enrich_nutrition(repo, selected, settings=current_settings)
            st.rerun()
        document = ConvertedRecipe.model_validate_json(saved["data_json"])
        for snapshot in json.loads(saved["nutrition_json"]).values():
            if str(snapshot.get("reason", "")).startswith("近似匹配："):
                st.warning(f"营养采用近似条目 · {snapshot.get('query', '')} · {snapshot['reason']}")
            if snapshot.get("status") == "request_failed":
                st.error(f"USDA 请求失败 · {snapshot.get('query', '')} · {snapshot['error']}")
                with st.expander("查看脱敏响应摘要"):
                    st.json(snapshot.get("diagnostics", {}))
            elif snapshot.get("status") == "unmatched":
                st.warning(f"无可靠匹配 · {snapshot.get('query', '')} · {snapshot.get('error', '')}")
        result = calculate(document, json.loads(saved["nutrition_json"]), repo.thresholds())
        if result["estimated"]:
            st.warning("含 AI 估算 · 总量、每人份及标签可能包含估算，仅供参考；估算来源随导出保留。")
            estimates = [
                {"食材": item["name"], "计算克重": item["grams"],
                 "克重为估算": item["estimated_grams"], "含估算的营养指标": ", ".join(item["estimated_nutrients"]),
                 "估算依据": (item["estimate"] or {}).get("reason", "")}
                for item in result["ingredients"] if item["estimated"]
            ]
            if estimates:
                st.dataframe(estimates, hide_index=True, width="stretch")
            if result["estimated_servings"]:
                st.caption(f"估算人数：{result['servings']}；{result['estimation']['servings_reason']}")
        st.json(result, expanded=False)
    with st.expander("历史版本与 AI 修改记录"):
        st.json([e for e in repo.history(selected) if e["action"].startswith("conversion_")], expanded=False)


def simple_data_page(repository) -> None:
    repo = ConversionRepository(repository.path)
    st.header("数据与设置")
    availability = local_status()
    st.subheader("USDA 本地数据")
    if availability["ready"]:
        st.success(f"本地库可用 · {availability['count']:,} 条食物 · 无需 USDA API 密钥")
        st.dataframe([
            {"数据类型": row["data_type"], "版本": row["version"], "食物记录": row["record_count"],
             "跳过空项": row["skipped_nulls"], "导入时间": row["imported_at"]}
            for row in availability["datasets"]
        ], hide_index=True, width="stretch")
        with st.expander("下载来源与校验信息"):
            st.json(availability["datasets"])
    else:
        st.warning(availability["error"] + "；仍可执行仅 AI 转换。")
    st.caption("仅查询 Foundation Foods 与 SR Legacy；数据更新通过命令行下载并导入，历史快照保持不变。")
    st.code('python -m chefmate_core.cli install --directory data/raw/usda --database database/usda.db', language="powershell")
    with st.expander("重试全库 USDA 处理失败项"):
        st.caption("仅处理 request_failed / processing_failed，跳过成功项、无可靠匹配和已拒绝菜谱；结果变化后需要重新审阅。逐篇处理，单篇失败不阻断后续。")
        if st.button("重试所有请求失败的词条"):
            counts = {"processed": 0, "failed": 0}
            progress = st.empty()
            for row in repo.conversions():
                saved_row = repo.conversion(row["recipe_id"])
                if not saved_row or row["status"] == "rejected" or saved_row["source_hash"] != row["content_hash"]:
                    continue
                if not any(s.get("status") in ("request_failed", "processing_failed") for s in json.loads(saved_row["nutrition_json"]).values()):
                    continue
                progress.info(f"正在重试：{row['name']}")
                try:
                    enrich_nutrition(repo, row["recipe_id"], failed_only=True)
                    counts["processed"] += 1
                except Exception:
                    counts["failed"] += 1
            progress.empty()
            st.info(f"已处理 {counts['processed']} 篇，执行异常 {counts['failed']} 篇。请求是否成功请查看各篇营养结果。")
    with st.expander("已确认的 USDA 共享映射"):
        st.caption("整篇菜谱通过审阅后建立映射。修改来源菜谱的 aliases 后重新审阅可补充别名；撤回为待审阅后停止共享。名称命中且检索词一致才复用，冲突不自动复用。")
        mappings = repo.confirmed_usda_mappings()
        st.dataframe([{k: v for k, v in m.items() if k != "snapshot"} for m in mappings], use_container_width=True)
    st.subheader("AI 转换设置")
    config = repo.conversion_settings()
    with st.form("conversion_settings"):
        prompt = st.text_area("默认提示词（单篇与批量共用）", config.prompt, height=320)
        estimate_missing = st.checkbox("默认启用 AI 缺失估算", value=config.estimate_missing)
        retries = st.number_input("额外重试次数", 0, 5, config.retries)
        timeout = st.number_input("每次请求超时（秒）", 5.0, 300.0, config.timeout)
        tokens = st.number_input("最大输出 tokens", 1000, 64000, config.max_output_tokens, step=1000)
        st.caption(
            "额外重试 2 次 = 最多请求 3 次。鉴权、无效请求等错误不反复重试；USDA 直接读取本地库，不使用网络重试。运行中的批次使用启动时的设置快照。"
        )
        if st.form_submit_button("保存 AI 设置"):
            repo.save_conversion_settings(
                ConversionSettings(prompt=prompt, retries=int(retries), timeout=timeout, max_output_tokens=int(tokens),
                                   estimate_missing=estimate_missing)
            )
            st.success("已保存，下次转换生效。")
    st.subheader("导入原文")
    with st.expander("最近 AI 调用与用量"):
        calls = []
        for row in repo.service_history(30):
            if row["service"] != "OpenAI":
                continue
            response = json.loads(row["response_json"] or "{}") or {}
            calls.append(
                {
                    "时间": row["created_at"],
                    "任务": row["purpose"],
                    "状态": row["status"],
                    "错误": row["error_message"],
                    "用量": response.get("usage") if isinstance(response, dict) else None,
                }
            )
        st.json(calls, expanded=False)
    with st.form("simple_import"):
        directory = st.text_input("Markdown 数据目录", str(source_path()))
        if st.form_submit_button("导入 Markdown"):
            st.json(import_directory(repo, local_path(directory), "HowToCook"))
            st.caption("仅保存原文，不做规则提取。去“菜谱转换”或“批量转换”开始。")
    st.subheader("导出")
    approved = st.checkbox("仅导出已审阅通过的 JSON", value=True)
    st.caption("营养不完整也保留已审阅 JSON，但 ready_for_recommendation=false；正式推荐使用前检查该标识。")
    if st.button("导出菜谱 JSON"):
        st.success(str(export_json(repo, approved, stage="unified")))
    with st.expander("清空数据库"):
        st.warning("清空原文、转换和审阅历史；磁盘上的 Markdown 和历史导出保留。")
        confirm = st.text_input("输入 CLEAR 确认")
        if st.button("清空全部数据", disabled=confirm != "CLEAR"):
            repo.clear(confirm)
            st.rerun()
