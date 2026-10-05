"""One AI response is the entire editable recipe; nutrition is never AI-authored."""

from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from typing import Literal
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ConvertedIngredient(StrictModel):
    raw_name: str = Field(min_length=1)
    raw_amount: str | None
    raw_unit: str | None
    raw_text: str = Field(min_length=1)
    ingredient_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    canonical_name: str = Field(min_length=1)
    category: str
    aliases: list[str]
    allergens: list[str]
    grams: float | None = Field(ge=0)
    amount_evidence: str
    scalable: bool
    usda_query: str

    @model_validator(mode="after")
    def evidence(self):
        aliases = [alias.strip() for alias in self.aliases]
        if any(not alias for alias in aliases):
            raise ValueError("别名不能为空")
        if self.canonical_name.strip() in aliases or len(set(aliases)) != len(aliases):
            raise ValueError("别名不能重复，也不能包含标准名称本身")
        if self.grams is not None and not self.amount_evidence.strip():
            raise ValueError("克重需要原文或人工称量依据")
        return self


class RecipeReference(StrictModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or any(character.isspace() for character in value)
        ):
            raise ValueError("参考链接必须是无凭据的完整 HTTP/HTTPS URL")
        return value


class ConvertedRecipe(StrictModel):
    name: str = Field(min_length=1)
    description: str | None
    original_servings: str | None
    servings: float | None = Field(gt=0)
    servings_evidence: str
    prep_time_min: float | None = Field(ge=0)
    cook_time_min: float | None = Field(ge=0)
    total_time_min: float | None = Field(ge=0)
    difficulty: str | None
    ingredients: list[ConvertedIngredient] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    taste_tags: list[str]
    cooking_methods: list[str]
    required_tools: list[str]
    warnings: list[str]
    references: list[RecipeReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence(self):
        if self.servings is not None and not self.servings_evidence.strip():
            raise ValueError("人数需要原文依据")
        return self


class ConversionSettings(StrictModel):
    estimate_missing: bool = False
    prompt: str = Field(default="", max_length=60000)
    retries: int = Field(default=2, ge=0, le=5)
    timeout: float = Field(default=90.0, ge=5, le=300)
    max_output_tokens: int = Field(default=12000, ge=1000, le=64000)


class Nutrients(StrictModel):
    calories_kcal: float | None = Field(default=None, ge=0)
    protein_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    carbohydrate_g: float | None = Field(default=None, ge=0)
    sugar_g: float | None = Field(default=None, ge=0)
    sodium_mg: float | None = Field(default=None, ge=0)


class TagThresholds(StrictModel):
    basis: Literal["per_serving", "total"] = "per_serving"
    high_fat: float = Field(default=20, gt=0)
    high_sodium: float = Field(default=800, gt=0)
    high_sugar: float = Field(default=20, gt=0)
    high_calorie: float = Field(default=700, gt=0)
    high_protein: float = Field(default=30, gt=0)


class USDAChoice(StrictModel):
    fdc_id: str | None
    confidence: float = Field(ge=0, le=1)
    reason: str
