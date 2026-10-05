"""Full Markdown -> full JSON in ONE model call; optional USDA enrichment follows."""

import json

from data_cleaning.models.conversion import ConversionRepository
from data_cleaning.schemas.conversion import ConvertedRecipe, ConversionSettings
from data_cleaning.services.openai_service import OpenAIService
from data_cleaning.services.usda import USDAService
from chefmate_recipe_processing import RecipeProcessor


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
        if with_nutrition or (settings and settings.estimate_missing):
            enrich_nutrition(repo, recipe_id, settings=settings, ai=ai, usda=usda)
        return "skipped"
    if not row["raw_text"]:
        raise ValueError("原文无法解码，请先修复源文件")
    # Fixed instructions, enums and schema precede per-recipe content for cache reuse.
    enums = [
        {"kind": e["kind"], "code": e["code"], "label": e["label"]} for e in repo.enums() if e["status"] == "approved"
    ]
    model = ai or configured_ai(repo, settings)
    try:
        document = RecipeProcessor(model, progress=report).extract(
            row["raw_text"], vocabulary=enums, prompt=settings.prompt, feedback=feedback,
            current_json=current_json, entity_id=recipe_id, validator=repo.validate_document,
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
    if with_nutrition or settings.estimate_missing:
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
    model = ai or configured_ai(repo, settings)
    service = usda or USDAService(repo)
    result = RecipeProcessor(model, service, progress=report).enrich(
        document, snapshots=json.loads(row["nutrition_json"]),
        confirmed_lookup=repo.lookup_confirmed_usda, entity_id=recipe_id, failed_only=failed_only,
    )
    snapshots, changed = result["snapshots"], result["changed"]
    from chefmate_recipe_processing.estimation import ESTIMATE_KEY, estimate_missing
    estimation_error = None
    if settings.estimate_missing:
        report("AI 估算缺失用量与营养，结果将单独标记")
        try:
            estimate = estimate_missing(document, snapshots, model, recipe_id, progress=progress)
            if estimate:
                snapshots[ESTIMATE_KEY] = estimate
                changed = True
        except Exception as exc:
            estimation_error = exc
    elif ESTIMATE_KEY in snapshots and not failed_only:
        snapshots.pop(ESTIMATE_KEY)
        changed = True
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

    if estimation_error is not None:
        raise ValueError("AI 估算失败，已获得的 USDA 结果已保留；请重试估算") from estimation_error
