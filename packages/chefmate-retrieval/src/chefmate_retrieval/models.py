"""Framework-neutral, strict JSON contracts exposed directly as agent tool schemas."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Text = Annotated[str, Field(min_length=1, max_length=200)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
NutrientName = Literal["calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg"]
SortField = Literal[
    "recipe_id", "name", "total_time_min", "difficulty",
    "total_calories_kcal", "total_protein_g", "total_fat_g", "total_carbohydrate_g", "total_sugar_g", "total_sodium_mg",
    "per_serving_calories_kcal", "per_serving_protein_g", "per_serving_fat_g", "per_serving_carbohydrate_g",
    "per_serving_sugar_g", "per_serving_sodium_mg",
]


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, frozen=True)


class DatasetSnapshot(ToolRequest):
    data_version: Text
    imported_at: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("imported_at")
    @classmethod
    def timestamp(cls, value):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("imported_at 必须是带时区的 ISO 8601 时间") from None
        if timestamp.tzinfo is None:
            raise ValueError("imported_at 必须包含时区")
        return timestamp.astimezone(timezone.utc).isoformat()


class IngredientSelection(ToolRequest):
    ingredient_id: Text = Field(description="从 find_ingredients 返回的真实食材 ID 选择；不得猜测或创建 ID。")
    canonical_name: Text | None = Field(default=None, description="限制同一 ID 的具体形态，如虾仁；为空则匹配该 ID 的所有名称。")


class IngredientMatchGroup(ToolRequest):
    any_of: Annotated[list[IngredientSelection], Field(min_length=1, max_length=50)]


class IngredientLookupRequest(ToolRequest):
    queries: Annotated[list[Text], Field(max_length=10)] = Field(
        default_factory=list, description="中文标准名、已确认别名或英文食材 ID 查询词。不会自动推断上位类别或搜索原文。")
    usda_query: Text | None = Field(default=None, description="可选的 USDA 英文查询词；仅辅助找候选，共享营养参考不代表同义。")
    fdc_ids: Annotated[list[Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")]], Field(max_length=30)] = Field(
        default_factory=list, description="按已知 FDC 营养参考寻找本库候选，仍需检查名称和原文。")
    limit: Annotated[int, Field(ge=1, le=100)] = 30
    expected_snapshot: DatasetSnapshot | None = None

    @field_validator("fdc_ids")
    @classmethod
    def valid_fdc_ids(cls, values):
        if any(int(value) > 2**63 - 1 for value in values):
            raise ValueError("FDC ID 必须是有效正整数")
        return values

    @model_validator(mode="after")
    def has_query(self):
        if not self.queries and self.usda_query is None and not self.fdc_ids:
            raise ValueError("至少提供 queries、usda_query 或 fdc_ids")
        return self


class NutrientConstraint(ToolRequest):
    name: NutrientName
    minimum: Nonnegative | None = None
    maximum: Nonnegative | None = None
    basis: Literal["total", "per_serving"] = "per_serving"
    allow_estimated: bool = True

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("营养条件至少包含一个上下限")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("营养下限不能大于上限")
        return self


class SearchSort(ToolRequest):
    field: SortField = "recipe_id"
    direction: Literal["asc", "desc"] = "asc"


class RecipeSearchRequest(ToolRequest):
    ingredient_groups: Annotated[list[IngredientMatchGroup], Field(max_length=20)] = Field(
        default_factory=list, description="组内 OR、组间 AND。虾和番茄必须放在两个组内。")
    exclude_ingredients: Annotated[list[IngredientSelection], Field(max_length=100)] = Field(default_factory=list)
    name: Text | None = None
    min_total_time_min: Nonnegative | None = None
    max_total_time_min: Nonnegative | None = None
    difficulties: Annotated[list[Literal["beginner", "normal", "advanced"]], Field(max_length=3)] = Field(
        default_factory=list)
    taste_tags_all: Annotated[list[Text], Field(max_length=30)] = Field(default_factory=list)
    taste_tags_any: Annotated[list[Text], Field(max_length=30)] = Field(default_factory=list)
    exclude_taste_tags: Annotated[list[Text], Field(max_length=30)] = Field(
        default_factory=list, description="排除已记录的标签；缺少辣标签不构成不辣的证明。")
    cooking_methods_any: Annotated[list[Text], Field(max_length=30)] = Field(default_factory=list)
    available_tools: Annotated[list[Text], Field(max_length=50)] | None = Field(
        default=None, description="已记录的全部必需厨具必须是该集合的子集；null 不限制，[] 表示无厨具。")
    nutrition_tags_all: Annotated[list[Text], Field(max_length=30)] = Field(default_factory=list)
    exclude_allergens: Annotated[list[Text], Field(max_length=30)] = Field(
        default_factory=list, description="排除已记录的过敏原，无法证明未记录过敏原的菜谱安全。")
    nutrients: Annotated[list[NutrientConstraint], Field(max_length=12)] = Field(default_factory=list)
    ready_for_recommendation: bool | None = True
    sort: SearchSort = Field(default_factory=SearchSort)
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0, le=100000)] = 0
    expected_snapshot: DatasetSnapshot | None = None

    @model_validator(mode="after")
    def time_bounds(self):
        if (self.min_total_time_min is not None and self.max_total_time_min is not None
                and self.min_total_time_min > self.max_total_time_min):
            raise ValueError("耗时下限不能大于上限")
        return self


class RecipeDetailRequest(ToolRequest):
    recipe_id: Text
    expected_snapshot: DatasetSnapshot | None = None
