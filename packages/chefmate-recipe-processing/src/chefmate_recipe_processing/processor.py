"""Recipe processing without UI, persistence or environment loading."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .schemas import ConvertedRecipe, USDAChoice
from chefmate_food_data import DATA_TYPES, FoodDataError
from chefmate_contracts import validate_vocabulary, validate_recipe_vocabulary
from .execution import io, model_call, run, arun, bind_model
from .nutrition import food_key, keep_snapshots, parse_nutrients


class RecipeProcessor:
    def __init__(self, ai, food_data=None, *, progress=None):
        self.ai = ai
        self.food_data = food_data
        self.progress = progress

    def _extract(self, text: str, *, vocabulary, prompt=None, feedback="", current_json=None,
                entity_id="", validator=None, context=None, progress=None):
        report = progress or (context.progress if context and context.progress else self.progress)
        model = bind_model(self.ai, context=context, progress=report)
        vocabulary = [e.model_dump(mode="json") for e in validate_vocabulary(vocabulary) if e.enabled]
        if not text.strip():
            raise ValueError("菜谱原文不能为空")
        if prompt is None:
            prompt = yield io((Path(__file__).parent / "prompts/convert.txt").read_text, encoding="utf-8")
        prompt += "\n启用词表（只选以下代码）：\n" + json.dumps(vocabulary, ensure_ascii=False, sort_keys=True)
        payload = {"markdown": text, "feedback": feedback}
        if current_json:
            payload["current_json"] = current_json

        def validate(document):
            validate_recipe_vocabulary(document, vocabulary)
            if validator:
                validator(document)

        result = yield model_call(model, "convert", payload, ConvertedRecipe, entity_id, validate, system_prompt=prompt)
        result = ConvertedRecipe.model_validate(result)
        yield io(validate, result)
        return result

    def _enrich(self, document: ConvertedRecipe, *, snapshots=None, confirmed_lookup=None,
               entity_id="", failed_only=False, context=None, progress=None):
        document = ConvertedRecipe.model_validate(document)
        snapshots = keep_snapshots(document, snapshots or {})
        report = progress or (context.progress if context and context.progress else self.progress)
        report = report or getattr(self.ai, "progress", None) or (lambda message: None)
        model = bind_model(self.ai, context=context, progress=report)
        service, recipe_id = self.food_data, entity_id
        if service is None:
            raise ValueError("请配置本地 USDA 数据源")
        changed = False
        for index, ingredient in enumerate(document.ingredients, 1):
            yield io(report, f"营养匹配 {index}/{len(document.ingredients)}：{ingredient.canonical_name}")
            key = food_key(ingredient)
            if failed_only and snapshots.get(key, {}).get("status") not in ("request_failed", "processing_failed"):
                continue
            if snapshots.get(key, {}).get("per_100g"):
                yield io(report, "已有营养快照，直接复用")
                continue
            changed = True
            try:
                confirmed = (yield io(confirmed_lookup, ingredient)) if confirmed_lookup else None
                if confirmed:
                    if confirmed.get("food", {}).get("dataType") in DATA_TYPES:
                        yield io(report, "命中已确认共享映射，无需外部请求")
                        snapshots[key] = confirmed
                        continue
                if not ingredient.usda_query.strip():
                    raise ValueError("缺少 USDA 检索词")
                options = yield io(service.search, ingredient.usda_query)
                yield io(report, f"检索得到 {len(options)} 个候选")
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

                choice = yield model_call(model,
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
                    yield io(report, f"AI 选择 USDA ID {choice.fdc_id}，读取营养详情")
                    food = yield io(service.food, choice.fdc_id)
                    if str(food.get("fdcId")) != choice.fdc_id or food.get("dataType") not in DATA_TYPES:
                        raise ValueError("本地食物来源与所选候选不一致")
                    snapshot.update(
                        source="USDA_FDC",
                        fdc_id=choice.fdc_id,
                        name=food["description"],
                        per_100g=parse_nutrients(food).model_dump(),
                        food=food,
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        dataset=(yield io(service.provenance, choice.fdc_id)) if hasattr(service, "provenance") else None,
                    )
                else:
                    yield io(report, "无合适候选，保留未匹配状态")
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

    def extract(self, *args, **kwargs):
        return run(self._extract(*args, **kwargs))

    async def aextract(self, *args, **kwargs):
        return await arun(self._extract(*args, **kwargs))

    def enrich(self, *args, **kwargs):
        return run(self._enrich(*args, **kwargs))

    async def aenrich(self, *args, **kwargs):
        return await arun(self._enrich(*args, **kwargs))
