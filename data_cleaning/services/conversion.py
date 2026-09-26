"""Full Markdown -> full JSON in ONE model call; optional USDA enrichment follows."""

import json

from data_cleaning.models.conversion import ConversionRepository
from data_cleaning.models.repository import now
from data_cleaning.schemas.batch import USDAChoice
from data_cleaning.schemas.conversion import ConvertedRecipe, ConversionSettings
from data_cleaning.services.conversion_nutrition import food_key, keep_snapshots
from data_cleaning.services.openai_service import OpenAIService
from data_cleaning.services.usda import USDARequestError, USDAService, parse_nutrients


def configured_ai(repo: ConversionRepository, settings: ConversionSettings) -> OpenAIService:
    ai = OpenAIService(repo)
    ai.settings.update(retries=settings.retries, timeout=settings.timeout, max_output_tokens=settings.max_output_tokens)
    return ai


def convert_one(
    repo: ConversionRepository,
    recipe_id: str,
    *,
    settings: ConversionSettings | None = None,
    feedback: str = "",
    current_json: str | None = None,
    force: bool = False,
    ai=None,
    with_nutrition: bool = False,
    usda=None,
    progress=None,
) -> str:
    report = progress or (lambda message: None)
    report("读取原始 Markdown，准备提示词与 Schema")
    row = repo.get_recipe(recipe_id)
    old = repo.conversion(recipe_id)
    settings = settings or repo.conversion_settings()
    if not force and old and old["source_hash"] == row["content_hash"] and old["status"] in ("approved", "rejected"):
        return "skipped"
    if not force and old and old["source_hash"] == row["content_hash"] and not old["error_message"]:
        if with_nutrition:
            enrich_nutrition(repo, recipe_id, settings=settings, ai=ai, usda=usda)
        return "skipped"
    if not row["raw_text"]:
        raise ValueError("原文无法解码，请先修复源文件")
    # Fixed instructions, enums and schema precede per-recipe content for cache reuse.
    enums = [
        {"kind": e["kind"], "code": e["code"], "label": e["label"]} for e in repo.enums() if e["status"] == "approved"
    ]
    prompt = settings.prompt + "\n启用词表（只选以下代码）：\n" + json.dumps(enums, ensure_ascii=False, sort_keys=True)
    payload = {"markdown": row["raw_text"], "feedback": feedback}
    if current_json:
        payload["current_json"] = current_json
    model = ai or configured_ai(repo, settings)
    if progress is not None:
        model.progress = progress
    try:
        document = model.request(
            "convert", payload, ConvertedRecipe, recipe_id, repo.validate_document, system_prompt=prompt
        )
        repo.save_conversion(
            recipe_id, document, source_hash=row["content_hash"], expected=old.get("updated_at"), prompt=settings.prompt
        )
        report(f"菜谱 JSON 已保存：{len(document.ingredients)} 项食材、{len(document.steps)} 个步骤")
    except Exception as exc:
        repo.conversion_failure(
            recipe_id, old.get("updated_at"), f"{type(exc).__name__}：转换失败，旧JSON保留；请查看服务日志"
        )
        raise ValueError("AI 转换失败，原文和已有 JSON 均保留，请重试或人工修改") from None
    if with_nutrition:
        enrich_nutrition(repo, recipe_id, settings=settings, ai=model, usda=usda, progress=progress)
    report("本次处理完成，等待人工审阅")
    return "pending_review"


def enrich_nutrition(repo: ConversionRepository, recipe_id: str, *, settings=None, ai=None, usda=None, failed_only=False, progress=None) -> None:
    report = progress or (lambda message: None)
    settings = settings or repo.conversion_settings()
    row = repo.conversion(recipe_id)
    if not row:
        raise ValueError("请先转换菜谱")
    source = repo.get_recipe(recipe_id)
    if row["source_hash"] != source["content_hash"]:
        raise ValueError("原文已变化，请重新转换")
    document = ConvertedRecipe.model_validate_json(row["data_json"])
    snapshots = keep_snapshots(document, json.loads(row["nutrition_json"]))
    model = ai or configured_ai(repo, settings)
    service = usda or USDAService(repo)
    if progress is not None:
        model.progress = progress
        service.progress = progress
    if not usda:
        service.settings.update(retries=settings.retries, timeout=settings.timeout)
    changed = False
    for index, ingredient in enumerate(document.ingredients, 1):
        report(f"营养匹配 {index}/{len(document.ingredients)}：{ingredient.canonical_name}")
        key = food_key(ingredient)
        if failed_only and snapshots.get(key, {}).get("status") != "request_failed":
            continue
        if snapshots.get(key, {}).get("per_100g"):
            report("已有营养快照，直接复用")
            continue
        changed = True
        try:
            confirmed = repo.lookup_confirmed_usda(ingredient)
            if confirmed:
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
                snapshot.update(
                    source="USDA_FDC",
                    fdc_id=choice.fdc_id,
                    name=food["description"],
                    per_100g=parse_nutrients(food).model_dump(),
                    food=food,
                    fetched_at=now(),
                )
            else:
                report("无合适候选，保留未匹配状态")
                snapshot["status"] = "unmatched"
                snapshot["error"] = choice.reason or "无可靠 USDA 匹配"
            snapshots[key] = snapshot
        except USDARequestError as exc:
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
    if changed:
        repo.save_conversion(
            recipe_id,
            document,
            source_hash=row["source_hash"],
            expected=row["updated_at"],
            prompt=row["prompt"],
            nutrition=snapshots,
            note=row["review_note"],
        )
