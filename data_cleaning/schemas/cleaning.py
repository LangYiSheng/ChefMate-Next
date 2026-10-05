"""Shared validated contracts. AI schemas deliberately cannot contain nutrition values."""

from typing import Literal

from pydantic import Field, model_validator

from data_cleaning.schemas.recipe import StrictModel


class StandardIngredient(StrictModel):
    ingredient_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    canonical_name: str = Field(min_length=1, max_length=200)
    category: str
    aliases: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    status: Literal["pending_review", "approved", "rejected"] = "pending_review"
    review_note: str = ""


class MatchChoice(StrictModel):
    selected_ingredient_id: str | None
    confidence: float = Field(ge=0, le=1)
    reason: str
    suggested_name: str | None


class AIIngredient(StrictModel):
    raw_name: str
    raw_amount: str | None
    raw_unit: str | None
    raw_text: str


class AIRecipe(StrictModel):
    name: str
    description: str | None
    original_servings: str | None
    prep_time_min: float | None
    cook_time_min: float | None
    total_time_min: float | None
    difficulty: str | None
    ingredients: list[AIIngredient]
    steps: list[str]
    taste_tags: list[str]
    cooking_methods: list[str]
    required_tools: list[str]


class RecipeTags(StrictModel):
    taste_tags: list[str]
    cooking_methods: list[str]
    required_tools: list[str]
    difficulty: str | None


class NormalizedItem(StrictModel):
    ordinal: int = Field(ge=0)
    normalized_amount: float | None = Field(default=None, gt=0)
    normalized_unit: str | None = None
    grams: float | None = Field(default=None, gt=0)
    scalable: bool | None = None
    evidence: str = ""

    @model_validator(mode="after")
    def paired(self):
        if (self.normalized_amount is None) != (self.normalized_unit is None):
            raise ValueError("标准用量与单位必须同时填写或同时留空")
        if self.grams is not None and not self.evidence.strip():
            raise ValueError("克重必须注明原文或人工称量依据")
        if self.normalized_unit == "g" and self.grams is not None and self.normalized_amount != self.grams:
            raise ValueError("g 单位的标准用量必须与克重一致")
        return self


class RecipeProfile(RecipeTags):
    servings: float | None = Field(default=None, gt=0)
    servings_evidence: str = ""
    items: list[NormalizedItem]

    @model_validator(mode="after")
    def validate_servings(self):
        if self.servings is not None and not self.servings_evidence.strip():
            raise ValueError("人数需注明依据；盘、碗不能直接当人数")
        return self


from chefmate_contracts import Nutrients as Nutrients
from chefmate_recipe_processing import TagThresholds as TagThresholds
