"""Quoted recipe inputs and conversion into the shared recipe contract."""

import hashlib
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError
from chefmate_contracts import RecipeDocument
from chefmate_recipe_processing import validate_recipe


def _quantity_in(evidence, expected, kind):
    """Quantitative source fields must match a quoted amount, including units."""
    units = {
        "servings": r"人份?|份|位|口|servings?|people|persons?|portions?",
        "grams": r"千克|公斤|克|kg|g",
        "minutes": r"小时|分钟?|秒钟?|hours?|minutes?|seconds?|min",
    }
    chinese = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "半": 0.5,
    }
    for match in re.finditer(
        r"(\d+(?:\.\d+)?|\.\d+|[一二两三四五六七八九十半])\s*(" + units[kind] + r")(?![A-Za-z])", evidence, re.I
    ):
        token, unit = match.groups()
        number = chinese[token] if token in chinese else float(token)
        if kind == "grams" and unit.lower() in {"kg", "千克", "公斤"}:
            number *= 1000
        if kind == "minutes":
            if unit.lower() in {"小时", "hour", "hours"}:
                number *= 60
            elif unit.lower() in {"秒", "秒钟", "second", "seconds"}:
                number /= 60
        if abs(number - expected) < 0.000001:
            return True
    return False


class DraftIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    amount: str = Field(default="适量", max_length=200)
    grams: float | None = Field(
        default=None, ge=0, le=100000, description="原文明确的精确克重；个数、体积、约量或范围不自行换算，留null。"
    )
    amount_evidence: str = Field(
        default="",
        max_length=300,
        description="逐字复制source_text中的连续原文，保留表格竖线和标点；不能改写成另一种格式。",
    )
    scalable: bool = True
    usda_query: str = Field(default="", max_length=150)
    allergens: list[str] = Field(default_factory=list, max_length=15)


class RecipeDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    source_text: str = Field(
        min_length=1,
        max_length=20000,
        description="文字来源直接复制用户消息的完整原文或连续片段，保留Markdown表格与标点，不能摘要改写。图片来源为可辨认原文转写。",
    )
    source_kind: Literal["text", "image"] = "text"
    servings: float | None = Field(default=None, gt=0, le=100)
    servings_evidence: str = Field(default="", max_length=300, description="逐字引用source_text中人数/份数依据。")
    prep_time_min: float | None = Field(
        default=None, ge=0, le=2880, description="原文明确的备菜总耗时；未明确时为null，不能从总时间倒推。"
    )
    cook_time_min: float | None = Field(
        default=None, ge=0, le=2880, description="原文明确的烹饪总耗时；未明确时为null，不能从总时间减去备菜时间倒推。"
    )
    total_time_min: float | None = Field(
        default=None, ge=0, le=2880, description="只有原文明确总耗时才填写；单一步骤计时不等于整道菜总耗时。"
    )
    time_evidence: str = Field(
        default="",
        max_length=500,
        description="逐字引用source_text的时间依据；没有原文则时间保持null，可在warnings说明建议估时。",
    )
    difficulty: Literal["beginner", "normal", "advanced"] | None = None
    difficulty_evidence: str = Field(default="", max_length=300, description="逐字引用原文的明确难度说明；未提供难度时保留null，不按菜名或步骤猜测。")
    taste_tags: list[str] = Field(default_factory=list, max_length=30)
    cooking_methods: list[str] = Field(default_factory=list, max_length=30)
    required_tools: list[str] = Field(default_factory=list, max_length=30)
    ingredients: list[DraftIngredient] = Field(min_length=1, max_length=80)
    steps: list[str] = Field(
        min_length=1, max_length=80, description="可直接执行的步骤，保留火候和原文时间；推断的安全建议需明确标识。"
    )
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def grounded(self):
        # Stable codes and precise locations survive the agent's safe error filter.
        # Only static repair instructions are exposed; never echo input or exceptions.
        errors = []

        def reject(location, code, message):
            errors.append({"type": PydanticCustomError(code, message), "loc": location})

        normalized_source = re.sub(r"\s+", "", self.source_text)
        evidence_fields = [(("servings_evidence",), self.servings_evidence), (("time_evidence",), self.time_evidence)]
        evidence_fields.append((("difficulty_evidence",), self.difficulty_evidence))
        evidence_fields.extend(
            (("ingredients", index, "amount_evidence"), item.amount_evidence)
            for index, item in enumerate(self.ingredients)
            if item.grams is not None
        )
        for location, evidence in evidence_fields:
            if evidence and re.sub(r"\s+", "", evidence) not in normalized_source:
                reject(location, "recipe_evidence_not_quoted", "依据必须逐字引用原文，保留表格和标点")
        if self.difficulty is not None:
            choices = {"beginner": r"新手|简单|入门|beginner|easy", "normal": r"普通|中等|normal|medium",
                       "advanced": r"进阶|困难|advanced|hard"}
            evidence = re.sub(r"[*_#]", "", self.difficulty_evidence)
            if not re.search(r"(?:难度|difficulty)\s*[:：]?\s*(?:为|是)?\s*(?:" + choices[self.difficulty] + r")", evidence, re.I):
                reject(("difficulty",), "recipe_difficulty_unquoted", "难度需要原文明确说明，未提供时保留未知")
        if self.servings is not None:
            if not self.servings_evidence:
                reject(("servings_evidence",), "recipe_evidence_required", "人数需要原文依据；未写明时保留未知")
            elif not _quantity_in(self.servings_evidence, self.servings, "servings"):
                reject(("servings",), "recipe_servings_mismatch", "份数与原文人数或份数不一致")
        for index, item in enumerate(self.ingredients):
            if item.grams is None:
                continue
            if not item.amount_evidence:
                reject(("ingredients", index, "amount_evidence"), "recipe_evidence_required", "克重需要原文依据")
            elif not _quantity_in(item.amount_evidence, item.grams, "grams"):
                reject(("ingredients", index, "grams"), "recipe_grams_mismatch", "克重与原文不一致，不能换算个数或体积")
        for field in ("prep_time_min", "cook_time_min", "total_time_min"):
            value = getattr(self, field)
            if value is not None:
                if not self.time_evidence:
                    reject(("time_evidence",), "recipe_evidence_required", "耗时需要原文依据")
                    break
                if not _quantity_in(self.time_evidence, value, "minutes"):
                    reject((field,), "recipe_time_mismatch", "耗时与原文不一致，不能倒推或补造")
        # Recipe metadata commonly says "制作时间" without the word "总".
        # A step heading alone must still never establish the whole recipe time.
        explicit_total = re.search(r"总|合计|全程|total", self.time_evidence, re.I)
        recipe_time_heading = re.search(r"制作(?:时间|耗时)", self.time_evidence)
        step_heading = re.search(r"步骤|第.{0,5}步", self.time_evidence)
        if self.total_time_min is not None and not (explicit_total or recipe_time_heading and not step_heading):
            reject(("total_time_min",), "recipe_total_time_ambiguous", "单一步骤时长不能当作整道菜总耗时")
        for index, step in enumerate(self.steps):
            if not step.strip() or len(step) > 2000:
                reject(("steps", index), "recipe_step_invalid", "步骤应为可阅读的非空文本")
        if errors:
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self


class CustomSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=8, ge=1, le=30)
    offset: int = Field(default=0, ge=0, le=100000)
    status: Literal["saved", "draft", "all"] = Field(
        default="saved", description="saved找已保存菜谱；draft找待核对草稿；all同时搜索两者。"
    )


class RecipeDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    recipe_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)


class ConfirmRecipeDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmation_id: str = Field(min_length=1, max_length=100)


class RecipeDraftReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    recipe_id: str = Field(min_length=1, max_length=100)


class RecipeDraftRevisionRequest(RecipeDeletionRequest):
    recipe: RecipeDraftRequest


def _document(draft: RecipeDraftRequest) -> RecipeDocument:
    ingredients = []
    for item in draft.ingredients:
        amount, unit = _amount_parts(item.amount)
        ingredients.append(
            {
                "raw_name": item.name,
                "raw_amount": amount,
                "raw_unit": unit,
                "raw_text": f"{item.name} {item.amount}",
                "ingredient_id": "user_" + hashlib.sha256(item.name.encode()).hexdigest()[:16],
                "canonical_name": item.name,
                "category": "other",
                "aliases": [],
                "allergens": item.allergens,
                "grams": item.grams,
                "amount_evidence": item.amount_evidence,
                "scalable": item.scalable,
                "usda_query": item.usda_query,
            }
        )
    return validate_recipe(
        {
            "name": draft.name,
            "description": draft.description or None,
            "original_servings": draft.servings_evidence or None,
            "servings": draft.servings,
            "servings_evidence": draft.servings_evidence,
            "prep_time_min": draft.prep_time_min,
            "cook_time_min": draft.cook_time_min,
            "total_time_min": draft.total_time_min,
            "difficulty": draft.difficulty,
            "ingredients": ingredients,
            "steps": draft.steps,
            "taste_tags": draft.taste_tags,
            "cooking_methods": draft.cooking_methods,
            "required_tools": draft.required_tools,
            "warnings": draft.warnings,
        }
    )


def _amount_parts(amount):
    """Split an explicit scalar and unit; keep ranges/approximation as source text."""
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?|\.\d+|[一二两三四五六七八九十半])\s*([A-Za-z\u4e00-\u9fff]{1,10})\s*", amount
    )
    if not match:
        return amount, None
    number, unit = match.groups()
    chinese = dict(zip("一二两三四五六七八九十半", (1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0.5)))
    return str(chinese[number]) if number in chinese else number, unit
