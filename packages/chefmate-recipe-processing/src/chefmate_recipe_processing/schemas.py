from chefmate_contracts.schemas import (
    StrictModel as StrictModel, ConvertedRecipe as ConvertedRecipe, ConvertedIngredient as ConvertedIngredient,
    RecipeReference as RecipeReference, RecipeDocument as RecipeDocument, Nutrients as Nutrients,
)
from pydantic import Field
from typing import Literal


class RecipeSource(StrictModel):
    kind: Literal["text", "markdown"] = "text"
    content: str = Field(min_length=1)
    source_id: str | None = None


class ProcessingOptions(StrictModel):
    with_nutrition: bool = False
    estimate_missing: bool = False


class ProcessingIssue(StrictModel):
    stage: Literal["match", "estimate"]
    status: str
    message: str
    ingredient_key: str | None = None


class MatchingResult(StrictModel):
    snapshots: dict
    changed: bool
    issues: list[ProcessingIssue] = Field(default_factory=list)


class ProcessingResult(StrictModel):
    recipe: RecipeDocument
    ingredient_matches: dict = Field(default_factory=dict)
    estimates: dict | None = None
    nutrition: dict | None = None
    issues: list[ProcessingIssue] = Field(default_factory=list)
    processing_metadata: dict = Field(default_factory=dict)

class ConversionSettings(StrictModel):
    estimate_missing: bool = False
    prompt: str = Field(default="", max_length=60000)
    retries: int = Field(default=2, ge=0, le=5)
    timeout: float = Field(default=90.0, ge=5, le=300)
    max_output_tokens: int = Field(default=12000, ge=1000, le=64000)


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
