"""Read-only views over the current conversion documents, not legacy ingredient tables."""
import json

import streamlit as st

from data_cleaning.schemas.conversion import ConvertedRecipe
from data_cleaning.services.conversion_nutrition import food_key

STATUS = {None: "未转换", "pending_review": "待审阅", "approved": "已通过", "rejected": "已拒绝"}


def row_status(row: dict) -> str:
    if row["source_hash"] and row["source_hash"] != row["content_hash"]:
        return "原文已变更"
    return STATUS.get(row["status"], row["status"])


def recipes_page(repo) -> None:
    st.header("数据库菜谱")
    rows = repo.browse_recipes()
    st.caption(f"数据库共 {len(rows)} 条菜谱；只读查看，修改请到菜谱转换。")
    search = st.text_input("搜索菜名、来源或 ID").strip().casefold()
    state = st.selectbox("审核状态", ["全部", *STATUS.values(), "原文已变更"])
    rows = [r for r in rows if (not search or search in " ".join([r["name"], r["source"], r["recipe_id"]]).casefold())
            and (state == "全部" or row_status(r) == state)]
    st.dataframe([{"菜名": r["name"], "状态": row_status(r), "来源": r["source"],
                   "ID": r["recipe_id"], "更新时间": r["updated_at"]} for r in rows], width="stretch", hide_index=True)
    if not rows:
        st.info("没有符合条件的菜谱。")
        return
    selected = st.selectbox("查看菜谱", range(len(rows)), format_func=lambda i: f"{rows[i]['name']} · {rows[i]['source']}")
    row = rows[selected]
    st.caption(f"审核备注：{row['review_note'] or '无'}；错误：{row['error_message'] or '无'}")
    left, right = st.columns(2)
    with left:
        st.subheader("数据库原文")
        st.code(repo.get_recipe(row["recipe_id"])["raw_text"] or "", language="markdown")
    with right:
        st.subheader("保存的菜谱 JSON")
        if row["data_json"]:
            st.json(json.loads(row["data_json"]), expanded=False)
            st.download_button("下载此条数据库 JSON", row["data_json"], "recipe.json", "application/json")
        else:
            st.info("尚未转换。")
    with st.expander("USDA 来源快照"):
        st.json(json.loads(row["nutrition_json"] or "{}"), expanded=False)


def ingredient_records(repo) -> list[dict]:
    groups = {}
    for row in repo.browse_recipes():
        if not row["data_json"] or row["source_hash"] != row["content_hash"]:
            continue
        document = ConvertedRecipe.model_validate_json(row["data_json"])
        snapshots = json.loads(row["nutrition_json"] or "{}")
        for item in document.ingredients:
            snapshot = snapshots.get(food_key(item), {})
            # Keep identities distinct even when the same nutrition substitute is used.
            key = (item.canonical_name, item.usda_query)
            group = groups.setdefault(key, {"name": item.canonical_name, "query": item.usda_query,
                "aliases": set(), "raw_names": set(), "ids": set(), "categories": set(), "uses": []})
            group["aliases"].update(item.aliases)
            group["raw_names"].add(item.raw_name)
            group["ids"].add(item.ingredient_id)
            group["categories"].add(item.category)
            group["uses"].append({"菜谱": row["name"], "菜谱ID": row["recipe_id"], "审核": row_status(row),
                "原始名称": item.raw_name, "USDA ID": snapshot.get("fdc_id"),
                "USDA 名称": snapshot.get("name"), "置信度": snapshot.get("confidence"),
                "匹配说明": snapshot.get("reason") or snapshot.get("error"),
                "近似": str(snapshot.get("reason", "")).startswith("近似匹配：")})
    return list(groups.values())


def ingredients_page(repo) -> None:
    st.header("标准食材")
    st.caption("汇总当前版本菜谱 JSON；包含待审阅结果，不等于全部已确认。按标准名和检索词分组，同一 USDA ID 的近似替代不会强行合并。")
    search = st.text_input("搜索标准名、原名、别名或 USDA ID").strip().casefold()
    mode = st.selectbox("匹配筛选", ["全部", "有 USDA 匹配", "无 USDA 匹配", "包含近似匹配"])
    groups = ingredient_records(repo)
    groups = [g for g in groups if not search or search in " ".join(
        [g["name"], *g["aliases"], *g["raw_names"], *[str(u["USDA ID"] or "") for u in g["uses"]]]).casefold()]
    groups = [g for g in groups if mode == "全部"
              or (mode == "有 USDA 匹配" and any(u["USDA ID"] for u in g["uses"]))
              or (mode == "无 USDA 匹配" and not any(u["USDA ID"] for u in g["uses"]))
              or (mode == "包含近似匹配" and any(u["近似"] for u in g["uses"]))]
    groups.sort(key=lambda g: (-len(g["uses"]), g["name"]))
    st.dataframe([{"标准名": g["name"], "别名": "、".join(sorted(g["aliases"])),
                   "类别": "、".join(sorted(g["categories"])), "出现次数": len(g["uses"]),
                   "菜谱数": len({u["菜谱ID"] for u in g["uses"]}),
                   "USDA IDs": ", ".join(sorted({str(u["USDA ID"]) for u in g["uses"] if u["USDA ID"]}))}
                  for g in groups], width="stretch", hide_index=True)
    if not groups:
        st.info("暂无符合条件的食材。先转换菜谱后即可查看。")
        return
    selected = st.selectbox("查看食材及使用菜谱", range(len(groups)), format_func=lambda i: f"{groups[i]['name']} · {groups[i]['query']}")
    group = groups[selected]
    st.write({"原始名称": sorted(group["raw_names"]), "AI 食材 IDs": sorted(group["ids"]), "USDA 检索词": group["query"]})
    st.dataframe(group["uses"], width="stretch", hide_index=True)
