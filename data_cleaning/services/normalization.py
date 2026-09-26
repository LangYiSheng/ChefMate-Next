import re

from data_cleaning.models.workflow import WorkflowRepository
from data_cleaning.schemas.cleaning import AIRecipe, NormalizedItem, RecipeProfile, RecipeTags
from data_cleaning.schemas.recipe import Ingredient, Recipe
from data_cleaning.services.logging_service import log_event
from data_cleaning.services.openai_service import OpenAIService


def profile_template(recipe: Recipe) -> RecipeProfile:
    items = []
    for ordinal, item in enumerate(recipe.ingredients):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(kg|g|千克|公斤|克)\s*", item.raw_amount or "", re.I)
        grams = float(match[1]) * (1000 if match[2].lower() in ("kg", "千克", "公斤") else 1) if match else None
        if grams is not None and grams <= 0:
            grams = None
        items.append(
            NormalizedItem(
                ordinal=ordinal,
                normalized_amount=grams,
                normalized_unit="g" if grams else None,
                grams=grams,
                scalable=True if grams else None,
                evidence=item.raw_text if grams else "",
            )
        )
    serving = re.search(r"(?<![\d.])(\d+)\s*人份", recipe.original_servings or "")
    if re.search(r"约|大概|左右|~|～|至|到|-", recipe.original_servings or ""):
        serving = None
    servings = float(serving[1]) if serving and int(serving[1]) > 0 else None
    return RecipeProfile(
        taste_tags=[],
        cooking_methods=[],
        required_tools=[],
        difficulty=None,
        items=items,
        servings=servings,
        servings_evidence=recipe.original_servings if servings else "",
    )


def normalize_one(
    repo: WorkflowRepository,
    recipe_id: str,
    *,
    use_ai: bool = True,
    force: bool = False,
    ai: OpenAIService | None = None,
) -> str:
    row = repo.get_recipe(recipe_id)
    old = repo.profile(recipe_id)
    if row["status"] == "rejected" or (
        not force
        and old
        and old["status"] in ("approved", "rejected")
        and old["source_updated_at"] == row["updated_at"]
    ):
        return "skipped"
    if not row["extracted_json"]:
        return "skipped"
    recipe = Recipe.model_validate_json(row["extracted_json"])
    profile = (
        RecipeProfile.model_validate_json(old["data_json"])
        if old and old["source_updated_at"] == row["updated_at"]
        else profile_template(recipe)
    )
    error = None
    if use_ai:
        enums = [e for e in repo.enums() if e["status"] == "approved"]

        def validate(tags: RecipeTags) -> None:
            values = tags.model_dump()
            for field, kind in (
                ("taste_tags", "taste"),
                ("cooking_methods", "cooking_method"),
                ("required_tools", "tool"),
                ("difficulty", "difficulty"),
            ):
                selected = ([values[field]] if values[field] else []) if field == "difficulty" else values[field]
                if any(c not in {e["code"] for e in enums if e["kind"] == kind} for c in selected):
                    raise ValueError("AI 返回未启用的枚举")

        try:
            tags = (ai or OpenAIService(repo)).request(
                "tags", {"recipe": recipe.model_dump(), "enums": enums}, RecipeTags, recipe_id, validate
            )
            profile = RecipeProfile.model_validate({**profile.model_dump(), **tags.model_dump()})
        except Exception as exc:
            log_event("normalization_ai_failure", recipe_id, exc)
            error = "AI 标签处理失败，请人工选择固定选项或重试"
    repo.save_profile(
        recipe_id, profile, source_expected=row["updated_at"], expected=old.get("updated_at"), error=error
    )
    return "failed" if error else "pending_review"


def normalize_all(repo: WorkflowRepository, *, use_ai: bool = True, failed_only: bool = False) -> dict:
    counts = {"pending_review": 0, "skipped": 0, "failed": 0}
    ai = OpenAIService(repo) if use_ai else None
    for row in repo.list_recipes():
        if failed_only and not repo.profile(row["recipe_id"]).get("error_message"):
            continue
        try:
            counts[normalize_one(repo, row["recipe_id"], use_ai=use_ai, ai=ai)] += 1
        except Exception as exc:
            counts["failed"] += 1
            log_event("normalization_failed", row["recipe_id"], exc)
    return counts


def ai_extract(repo: WorkflowRepository, recipe_id: str, ai: OpenAIService | None = None) -> None:
    row = repo.get_recipe(recipe_id)
    try:
        if not row["raw_text"]:
            raise ValueError("原文不可解码")

        def to_recipe(value: AIRecipe) -> Recipe:
            data = value.model_dump()
            data["ingredients"] = [
                Ingredient(**i.model_dump(), source_section="AI 原文提取") for i in value.ingredients
            ]
            return Recipe(
                **data,
                recipe_id=recipe_id,
                source=row["source"],
                raw_sections={"完整原文": row["raw_text"]},
                extraction_warnings=["AI 提取，必须对照原文核实所有字段"],
            )

        output = (ai or OpenAIService(repo)).request(
            "extract", {"raw_text": row["raw_text"]}, AIRecipe, recipe_id, lambda v: to_recipe(v)
        )
        repo.save_extraction(to_recipe(output), row["updated_at"], action="ai_extract")
    except Exception as exc:
        log_event("ai_extraction_failed", recipe_id, exc)
        repo.fail_extraction(recipe_id, row["updated_at"], "AI 提取失败，请检查配置/服务调用记录后重试或人工处理")
        raise ValueError("AI 提取失败，已记录错误并转人工处理") from None
