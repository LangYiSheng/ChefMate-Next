from pydantic import Field
from typing import Literal
from data_cleaning.schemas.conversion import ConversionSettings

from data_cleaning.schemas.recipe import StrictModel


class BatchOptions(StrictModel):
    pipeline: Literal["legacy", "unified"] = "legacy"
    conversion: ConversionSettings | None = None
    redo_existing: bool = False
    extract: bool = True
    catalog: bool = True
    usda: bool = True
    tags: bool = True
    recipe_limit: int = Field(default=0, ge=0, le=100000)


class CatalogChoice(StrictModel):
    existing_id: str | None
    new_id: str | None = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    canonical_name: str | None
    category: str | None
    usda_query: str
    confidence: float = Field(ge=0, le=1)
    reason: str


from chefmate_core.schemas import USDAChoice as USDAChoice


class USDAQuery(StrictModel):
    query: str = Field(min_length=1, max_length=300)
