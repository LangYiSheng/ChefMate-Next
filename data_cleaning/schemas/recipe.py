from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    AI_PROCESSED = "ai_processed"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Ingredient(StrictModel):
    raw_name: str = Field(min_length=1)
    raw_amount: str | None = None
    raw_unit: str | None = None
    raw_text: str = Field(min_length=1)
    source_section: str
    canonical_ingredient_id: str | None = None
    normalized_amount: float | None = Field(default=None, ge=0)
    normalized_unit: str | None = None
    scalable: bool | None = None


class Recipe(StrictModel):
    recipe_id: str
    name: str = Field(min_length=1)
    source: str
    description: str | None = None
    original_servings: str | None = None
    prep_time_min: float | None = Field(default=None, ge=0)
    cook_time_min: float | None = Field(default=None, ge=0)
    total_time_min: float | None = Field(default=None, ge=0)
    difficulty: str | None = None
    ingredients: list[Ingredient] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    taste_tags: list[str] = Field(default_factory=list)
    cooking_methods: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    raw_sections: dict[str, str] = Field(default_factory=dict)
    extraction_warnings: list[str] = Field(default_factory=list)
