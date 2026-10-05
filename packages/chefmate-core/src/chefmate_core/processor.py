"""Recipe processing without UI, persistence or environment loading."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .schemas import ConvertedRecipe, USDAChoice
from .fooddata import DATA_TYPES, FoodDataError
from .nutrition import food_key, keep_snapshots, parse_nutrients


class RecipeProcessor:
    def __init__(self, ai, food_data=None, *, progress=None):
        self.ai = ai
        self.food_data = food_data
        self.progress = progress or (lambda message: None)
        if progress is not None:
            self.ai.progress = progress

    def extract(self, text: str, *, vocabulary, prompt=None, feedback="", current_json=None,
                entity_id="", validator=None) -> ConvertedRecipe:
        if not text.strip():
            raise ValueError("菜谱原文不能为空")
        if prompt is None:
            prompt = (Path(__file__).parent / "prompts/convert.txt").read_text(encoding="utf-8")
        prompt += "\n启用词表（只选以下代码）：\n" + json.dumps(vocabulary, ensure_ascii=False, sort_keys=True)
        payload = {"markdown": text, "feedback": feedback}
        if current_json:
            payload["current_json"] = current_json

        def validate(document):
            allowed = {}
            for entry in vocabulary:
                allowed.setdefault(entry["kind"], set()).add(entry["code"])
            groups = [("taste", document.taste_tags), ("tool", document.required_tools),
                      ("cooking_method", document.cooking_methods),
                      ("difficulty", [document.difficulty] if document.difficulty else [])]
            for item in document.ingredients:
                groups.extend([("category", [item.category]), ("allergen", item.allergens)])
            for kind, codes in groups:
                if any(code not in allowed.get(kind, set()) for code in codes):
                    raise ValueError(f"菜谱包含未启用的词表选项：{kind}")
            if validator:
                validator(document)

        result = self.ai.request("convert", payload, ConvertedRecipe, entity_id, validate, system_prompt=prompt)
        result = ConvertedRecipe.model_validate(result)
        validate(result)
        return result

    def enrich(self, document: ConvertedRecipe, *, snapshots=None, confirmed_lookup=None,
               entity_id="", failed_only=False) -> dict:
        document = ConvertedRecipe.model_validate(document)
        snapshots = keep_snapshots(document, snapshots or {})
        model, service, report, recipe_id = self.ai, self.food_data, self.progress, entity_id
        if service is None:
            raise ValueError("请配置本地 USDA 数据源")
        changed = False
        for index, ingredient in enumerate(document.ingredients, 1):
            report(f"营养匹配 {index}/{len(document.ingredients)}：{ingredient.canonical_name}")
            key = food_key(ingredient)
            if failed_only and snapshots.get(key, {}).get("status") not in ("request_failed", "processing_failed"):
                continue
            if snapshots.get(key, {}).get("per_100g"):
                report("已有营养快照，直接复用")
                continue
            changed = True
            try:
                confirmed = confirmed_lookup(ingredient) if confirmed_lookup else None
                if confirmed:
                    if confirmed.get("food", {}).get("dataType") in DATA_TYPES:
                        report("命中已确认共享映射，无需外部请求")
                        snapshots[key] = confirmed
                        continue
                if not ingredient.usda_query.strip():
                    raise ValueError("缺少 USDA 检索词")
                options = service.search(ingredient.usda_query)
                report(f"检索得到 {len(options)} 个候选")
                if not options:
                    snapshots[key] = {
                        "status": "unmatched",
                        "error": "USDA 无候选，请修改 JSON 中的 usda_query 后重试",
                        "query": ingredient.usda_query,
                    }
                    continue

                def validate(choice: USDAChoice) -> None:
                    if choice.fdc_id is not None and choice.fdc_id not in {str(o["fdcId"]) for o in options}:
                        raise ValueError("FDC ID 不在候选中")

                choice = model.request(
                    "usda_match",
                    {
                        "name": ingredient.canonical_name,
                        "query": ingredient.usda_query,
                        "raw_name": ingredient.raw_name,
                        "raw_text": ingredient.raw_text,
                        "amount_evidence": ingredient.amount_evidence,
                        "recipe_warnings": document.warnings,
                        "candidates": options,
                    },
                    USDAChoice,
                    recipe_id,
                    validate,
                )
                choice = USDAChoice.model_validate(choice)
                validate(choice)
                snapshot = {
                    "query": ingredient.usda_query,
                    "candidates": options,
                    "confidence": choice.confidence,
                    "reason": choice.reason,
                }
                if choice.fdc_id:
                    report(f"AI 选择 USDA ID {choice.fdc_id}，读取营养详情")
                    food = service.food(choice.fdc_id)
                    if str(food.get("fdcId")) != choice.fdc_id or food.get("dataType") not in DATA_TYPES:
                        raise ValueError("本地食物来源与所选候选不一致")
                    snapshot.update(
                        source="USDA_FDC",
                        fdc_id=choice.fdc_id,
                        name=food["description"],
                        per_100g=parse_nutrients(food).model_dump(),
                        food=food,
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        dataset=service.provenance(choice.fdc_id) if hasattr(service, "provenance") else None,
                    )
                else:
                    report("无合适候选，保留未匹配状态")
                    snapshot["status"] = "unmatched"
                    snapshot["error"] = choice.reason or "无可靠 USDA 匹配"
                snapshots[key] = snapshot
            except FoodDataError as exc:
                snapshots[key] = {
                    "query": ingredient.usda_query,
                    "status": "request_failed",
                    "error": str(exc),
                    "diagnostics": exc.details,
                }
            except Exception as exc:
                snapshots[key] = {
                    "status": "processing_failed",
                    "query": ingredient.usda_query,
                    "error": f"{type(exc).__name__}：USDA 处理失败，请查看日志后重试",
                }
        return {"snapshots": snapshots, "changed": changed}
