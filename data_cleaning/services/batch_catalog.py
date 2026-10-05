"""AI catalog drafts and candidate-only USDA matching; never approves AI output."""

from data_cleaning.models.batch import BatchRepository
from data_cleaning.schemas.batch import CatalogChoice, USDAChoice, USDAQuery
from data_cleaning.schemas.cleaning import StandardIngredient
from data_cleaning.services.matching import candidates, match_one
from data_cleaning.services.openai_service import OpenAIService
from data_cleaning.services.usda import USDAService


def build_catalog(repo: BatchRepository, raw_id: int, ai: OpenAIService) -> str:
    raw = repo.raw_detail(raw_id)
    if raw["status"] in ("approved", "rejected"):
        return "skipped"
    if raw["canonical_ingredient_id"] and not raw["error_message"]:
        return "skipped"
    # Exact approved rules retain their established priority and avoid AI calls.
    if match_one(repo, raw_id, use_ai=False) == "approved":
        return "approved"
    raw = repo.raw_detail(raw_id)
    standards = [s for s in repo.standards() if s["status"] != "rejected"]
    exact = next((s for s in standards if raw["raw_name"] in [s["canonical_name"], *s["aliases"]]), None)
    if exact:
        repo.match_raw(
            raw_id,
            exact["ingredient_id"],
            expected=raw["updated_at"],
            status="pending_review",
            method="draft_exact",
            reason="复用标准草稿，仍需人工审核",
        )
        return "pending_review"
    options = candidates(raw["raw_name"], standards, limit=30)
    categories = [e["code"] for e in repo.enums() if e["kind"] == "category" and e["status"] == "approved"]

    def validate(value: CatalogChoice) -> None:
        if value.existing_id:
            if value.existing_id not in {s["ingredient_id"] for s in options} or value.new_id:
                raise ValueError("标准 ID 不在候选内或同时创建与复用")
        elif value.new_id:
            if not value.canonical_name or not value.canonical_name.strip() or value.category not in categories:
                raise ValueError("新标准食材名称或类别无效")
            existing = repo.standard(value.new_id)
            if existing and (existing["canonical_name"] != value.canonical_name or existing["status"] == "rejected"):
                raise ValueError("新 ID 已被其他标准食材使用")

    choice = ai.request(
        "catalog",
        {"raw_name": raw["raw_name"], "candidates": options, "categories": categories},
        CatalogChoice,
        str(raw_id),
        validate,
    )
    validate(choice)
    target = choice.existing_id
    if not target and choice.new_id:
        same = next((s for s in standards if choice.canonical_name in [s["canonical_name"], *s["aliases"]]), None)
        target = same["ingredient_id"] if same else choice.new_id
        if not repo.standard(target):
            repo.save_standard(
                StandardIngredient(
                    ingredient_id=target,
                    canonical_name=choice.canonical_name,
                    category=choice.category,
                    status="pending_review",
                    review_note="AI 草稿：" + choice.reason,
                )
            )
    if target:
        standard = repo.standard(target)
        if standard["status"] == "pending_review" and raw["raw_name"] not in [
            standard["canonical_name"],
            *standard["aliases"],
        ]:
            repo.save_standard(
                StandardIngredient(
                    ingredient_id=target,
                    canonical_name=standard["canonical_name"],
                    category=standard["category"],
                    aliases=[*standard["aliases"], raw["raw_name"]],
                    allergens=standard["allergens"],
                    status="pending_review",
                    review_note=standard["review_note"] or "AI 别名草稿",
                ),
                standard["updated_at"],
            )
            standard = repo.standard(target)
        if not standard["usda_query"]:
            repo.set_query(target, choice.usda_query, standard["updated_at"])
    repo.match_raw(
        raw_id,
        target,
        expected=raw["updated_at"],
        status="pending_review",
        method="ai_catalog",
        confidence=choice.confidence,
        reason=choice.reason,
        candidates=options,
    )
    return "pending_review"


def match_usda(repo: BatchRepository, ingredient_id: str, ai: OpenAIService, usda: USDAService) -> str:
    row = repo.standard(ingredient_id)
    if row["status"] == "rejected" or row["nutrition_match_status"] in ("approved", "rejected"):
        return "skipped"
    if row["nutrition_json"] and not row["nutrition_error"]:
        return "skipped"
    query = row["usda_query"]
    if not query:
        query = ai.request(
            "usda_query", {"name": row["canonical_name"], "id": ingredient_id}, USDAQuery, ingredient_id
        ).query
        repo.set_query(ingredient_id, query, row["updated_at"])
    options = usda.search(query)
    choice = USDAChoice(fdc_id=None, confidence=0, reason="USDA 未返回候选，请人工调整搜索词")
    if options:

        def validate(value: USDAChoice) -> None:
            if value.fdc_id and value.fdc_id not in {str(s["fdcId"]) for s in options}:
                raise ValueError("AI 选择的 FDC ID 不在候选内")

        choice = ai.request(
            "usda_match",
            {"name": row["canonical_name"], "query": query, "candidates": options},
            USDAChoice,
            ingredient_id,
            validate,
        )
        validate(choice)
    repo.save_usda_candidates(ingredient_id, options, choice.confidence, choice.reason, row["updated_at"])
    if choice.fdc_id:
        usda.food(choice.fdc_id)
        repo.stage_nutrition(ingredient_id, choice.fdc_id, row["updated_at"],
                             dataset=usda.provenance(choice.fdc_id) if hasattr(usda, "provenance") else None)
    else:
        repo.nutrition_error(ingredient_id, row["updated_at"], choice.reason)
    return "pending_review"
