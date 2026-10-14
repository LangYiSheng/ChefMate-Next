"""Closed kitchen domain inputs and trusted UI projections; no HTML or arbitrary forms."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from chefmate_retrieval import DatasetSnapshot

Text = Annotated[str, Field(min_length=1, max_length=200)]
Positive = Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)]
Stage = Literal["idea", "planning", "shopping", "prep", "cooking", "completed"]
PrepStatus = Literal["pending", "ready", "skipped"]
StepStatus = Literal["pending", "current", "done"]
PreferenceName = Literal["servings", "max_total_time_min", "taste_tags", "available_tools",
                         "ingredient_terms", "exclude_ingredient_terms", "dietary_notes", "allow_estimated"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PreferenceValues(StrictModel):
    servings: Positive | None = None
    max_total_time_min: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    taste_tags: Annotated[list[Text], Field(max_length=30)] = Field(default_factory=list)
    available_tools: Annotated[list[Text], Field(max_length=50)] | None = None
    ingredient_terms: Annotated[list[Text], Field(max_length=50)] = Field(default_factory=list)
    exclude_ingredient_terms: Annotated[list[Text], Field(max_length=50)] = Field(default_factory=list)
    dietary_notes: Annotated[str, Field(max_length=2000)] = ""
    allow_estimated: bool = True


class PreferencePatch(StrictModel):
    servings: Positive | None = None
    max_total_time_min: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    taste_tags: Annotated[list[Text], Field(max_length=30)] | None = None
    available_tools: Annotated[list[Text], Field(max_length=50)] | None = None
    ingredient_terms: Annotated[list[Text], Field(max_length=50)] | None = None
    exclude_ingredient_terms: Annotated[list[Text], Field(max_length=50)] | None = None
    dietary_notes: Annotated[str, Field(max_length=2000)] | None = None
    allow_estimated: bool | None = None


class ApprovedRecipeRef(StrictModel):
    recipe_id: Text
    data_snapshot: DatasetSnapshot


class FrozenRecipe(StrictModel):
    recipe_id: Text
    data_snapshot: DatasetSnapshot
    recipe: dict[str, Any]
    target_servings: Positive | None = None


class TimerState(StrictModel):
    id: Text
    label: Text
    status: Literal["running", "paused", "finished", "cancelled"] = "running"
    seconds: Annotated[int, Field(ge=1, le=86400)]
    remaining_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    deadline_at: str | None = None
    recipe_id: Text | None = None
    step_id: Text | None = None
    basis: Literal["user", "recipe_text"] = "user"


class ActionRecord(StrictModel):
    action_id: Text
    type: Text
    payload: dict[str, Any]
    revision: Annotated[int, Field(ge=0)]
    created_at: str
    fingerprint: str


class SourceInfo(StrictModel):
    source_recipe_id: Text
    data_snapshot: DatasetSnapshot


class IngredientView(StrictModel):
    id: Text
    recipe_id: Text
    ingredient_id: Text
    canonical_name: Text
    amount_text: str
    grams: float | None = None
    estimated: bool = False
    scaled: bool = False
    status: PrepStatus = "pending"
    raw_text: str = ""
    unit: str | None = None
    amount_value: float | None = None


class StepView(StrictModel):
    id: Text
    ordinal: Annotated[int, Field(ge=0)]
    instruction: str
    status: StepStatus = "pending"
    timer_seconds: int | None = None


class RecipeView(StrictModel):
    recipe_id: Text
    name: Text
    description: str | None = None
    servings: float | None = None
    target_servings: float | None = None
    original_servings: float | None = None
    estimated_servings: bool = False
    total_time_min: float | None = None
    difficulty: str | None = None
    nutrition: dict[str, Any] = Field(default_factory=dict)
    ingredients: list[IngredientView] = Field(default_factory=list)
    steps: list[StepView] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: SourceInfo


class PreferenceField(StrictModel):
    name: PreferenceName
    label: Text
    input_type: Literal["number", "multiselect", "text", "textarea", "checkbox"]
    value: Any = None
    options: list[dict[str, str]] = Field(default_factory=list)


class PreferenceFormData(StrictModel):
    fields: list[PreferenceField]
    values: PreferenceValues


class RecipeListData(StrictModel):
    recipes: list[RecipeView]


class RecipeDetailData(StrictModel):
    recipe: RecipeView


class ShoppingItem(StrictModel):
    id: Text
    ingredient_id: Text
    canonical_name: Text
    unit: str | None
    amount_value: float | None
    amount_text: str
    estimated: bool
    checked: bool
    source_recipe_ids: list[str]


class ShoppingData(StrictModel):
    items: list[ShoppingItem]
    notes: list[str] = Field(default_factory=list)


class PrepData(StrictModel):
    items: list[IngredientView]
    notes: list[str] = Field(default_factory=list)


class CookingData(StrictModel):
    recipe_id: str | None
    recipe_name: str | None
    steps: list[StepView]
    active_step_id: str | None


class TimersData(StrictModel):
    timers: list[TimerState]


class ProgressData(StrictModel):
    stage: Stage
    recipe_count: int
    prep_ready: int
    prep_total: int
    steps_done: int
    steps_total: int


class ComponentBase(StrictModel):
    id: Text
    title: Text
    revision: Annotated[int, Field(ge=0)]
    editable: bool = True


class PreferenceForm(ComponentBase):
    kind: Literal["preference_form"] = "preference_form"
    data: PreferenceFormData


class RecipeChoices(ComponentBase):
    kind: Literal["recipe_choices"] = "recipe_choices"
    data: RecipeListData


class RecipeDetail(ComponentBase):
    kind: Literal["recipe_detail"] = "recipe_detail"
    data: RecipeDetailData


class MealPlan(ComponentBase):
    kind: Literal["meal_plan"] = "meal_plan"
    data: RecipeListData


class ShoppingList(ComponentBase):
    kind: Literal["shopping_list"] = "shopping_list"
    data: ShoppingData


class PrepChecklist(ComponentBase):
    kind: Literal["prep_checklist"] = "prep_checklist"
    data: PrepData


class CookingGuide(ComponentBase):
    kind: Literal["cooking_guide"] = "cooking_guide"
    data: CookingData


class Timers(ComponentBase):
    kind: Literal["timers"] = "timers"
    data: TimersData


class TaskProgress(ComponentBase):
    kind: Literal["task_progress"] = "task_progress"
    data: ProgressData


ViewComponent = Annotated[PreferenceForm | RecipeChoices | RecipeDetail | MealPlan | ShoppingList | PrepChecklist
                          | CookingGuide | Timers | TaskProgress, Field(discriminator="kind")]


class CompactComponentSnapshot(StrictModel):
    id: Text
    kind: Literal["preference_form", "recipe_choices", "recipe_detail", "meal_plan", "shopping_list",
                  "prep_checklist", "cooking_guide", "timers", "task_progress"]
    title: Text
    revision: Annotated[int, Field(ge=0)]
    editable: bool
    data: dict[str, Any]


class UISnapshot(StrictModel):
    revision: Annotated[int, Field(ge=0)]
    stage: Stage
    preferences: PreferenceValues = Field(default_factory=PreferenceValues)
    selected_recipe_ids: list[str] = Field(default_factory=list)
    active_recipe_id: str | None = None
    active_step_id: str | None = None
    components: list[CompactComponentSnapshot] = Field(default_factory=list)
    recent_actions: list[ActionRecord] = Field(default_factory=list)


class WorkspaceState(StrictModel):
    schema_version: Literal[1] = 1
    revision: Annotated[int, Field(ge=0)] = 0
    stage: Stage = "idea"
    preferences: PreferenceValues = Field(default_factory=PreferenceValues)
    candidates: list[FrozenRecipe] = Field(default_factory=list)
    meal_plan: list[FrozenRecipe] = Field(default_factory=list)
    prep_status: dict[str, PrepStatus] = Field(default_factory=dict)
    step_status: dict[str, StepStatus] = Field(default_factory=dict)
    shopping_checked: list[str] = Field(default_factory=list)
    active_recipe_id: str | None = None
    active_step_id: str | None = None
    timers: list[TimerState] = Field(default_factory=list)
    components: list[ViewComponent] = Field(default_factory=list)
    recent_actions: list[ActionRecord] = Field(default_factory=list)


class UIAction(StrictModel):
    kind: Literal["show_component"] = "show_component"
    component: ViewComponent
    placement: Literal["inline", "workspace"] = "inline"


class WorkflowToolResult(StrictModel):
    status: str
    workspace: UISnapshot
    views: list[ViewComponent]
    ui_snapshot: UISnapshot
    actions: list[UIAction] = Field(default_factory=list)
    message: str | None = None


class ToolInput(StrictModel):
    base_revision: Annotated[int, Field(ge=0)] | None = None
    display: Literal["inline", "workspace", "none"] = Field(
        default="inline", description="仅展示这轮必要的核心内容。inline在聊天内交互，workspace仅在工作区展示，none仅更新状态。")


class EmptyInput(ToolInput):
    pass


class PreferenceFormInput(ToolInput):
    fields: Annotated[list[PreferenceName], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: ["ingredient_terms", "servings", "max_total_time_min", "taste_tags", "available_tools"])
    title: Text = "这次想怎么吃"


class RecipeChoicesInput(ToolInput):
    recipe_ids: Annotated[list[Text], Field(min_length=1, max_length=6)]
    data_snapshot: DatasetSnapshot
    title: Text = "挑选这次要做的菜"


class RecipeDetailInput(ToolInput):
    recipe_id: Text
    data_snapshot: DatasetSnapshot | None = None


class MealPlanInput(ToolInput):
    recipe_ids: Annotated[list[Text], Field(min_length=1, max_length=4)]
    servings: dict[str, Positive] = Field(default_factory=dict)
    data_snapshot: DatasetSnapshot


class CookingGuideInput(ToolInput):
    recipe_id: Text | None = None


class TimerInput(ToolInput):
    seconds: Annotated[int, Field(ge=1, le=86400)]
    label: Text
    recipe_id: Text | None = None
    step_id: Text | None = None
    basis: Literal["user", "recipe_text"] = Field(default="user", description="必须依据用户指定时长或已确认菜谱原文，不能猜测。")


class StageInput(ToolInput):
    stage: Stage


class ActionBase(StrictModel):
    action_id: Text
    base_revision: Annotated[int, Field(ge=0)]
    view_id: Text | None = None


class PreferencePayload(StrictModel):
    values: PreferencePatch


class RecipeIdsPayload(StrictModel):
    recipe_ids: Annotated[list[Text], Field(min_length=1, max_length=4)]


class RecipeIdPayload(StrictModel):
    recipe_id: Text


class ServingsPayload(RecipeIdPayload):
    servings: Positive


class ShoppingPayload(StrictModel):
    item_id: Text
    checked: bool


class PrepPayload(StrictModel):
    item_id: Text
    status: PrepStatus


class StepPayload(RecipeIdPayload):
    step_id: Text


class CompleteStepPayload(StepPayload):
    completed: bool = True


class TimerPayload(StrictModel):
    seconds: Annotated[int, Field(ge=1, le=86400)]
    label: Text
    recipe_id: Text | None = None
    step_id: Text | None = None


class TimerIdPayload(StrictModel):
    timer_id: Text


class StagePayload(StrictModel):
    stage: Stage


def _action_model(name, payload_model):
    from pydantic import create_model
    return create_model(name + "Action", __base__=ActionBase, type=(Literal[name], name),
                        payload=(payload_model, ...))


ACTION_PAYLOAD_MODELS = {
    "update_preferences": PreferencePayload, "select_recipes": RecipeIdsPayload,
    "open_recipe_detail": RecipeIdPayload, "change_servings": ServingsPayload,
    "check_shopping_item": ShoppingPayload, "check_prep_item": PrepPayload,
    "set_active_recipe": RecipeIdPayload, "set_active_step": StepPayload,
    "complete_step": CompleteStepPayload, "change_stage": StagePayload,
    "start_timer": TimerPayload, "pause_timer": TimerIdPayload,
    "resume_timer": TimerIdPayload, "cancel_timer": TimerIdPayload,
}
_action_types = tuple(_action_model(name, model) for name, model in ACTION_PAYLOAD_MODELS.items())
from typing import Union  # noqa: E402


class UserActionRequest(RootModel[Annotated[Union[_action_types], Field(discriminator="type")]]):
    """A strict discriminated action envelope; model_dump returns the plain request object."""

    @model_validator(mode="after")
    def has_payload(self):
        return self


TOOL_REQUEST_MODELS = {
    "show_preference_form": PreferenceFormInput, "show_recipe_choices": RecipeChoicesInput,
    "show_recipe_details": RecipeDetailInput, "set_meal_plan": MealPlanInput,
    "show_meal_plan": EmptyInput, "show_shopping_list": EmptyInput, "show_prep_checklist": EmptyInput,
    "show_cooking_guide": CookingGuideInput, "set_cooking_timer": TimerInput,
    "show_timers": EmptyInput, "get_cooking_workspace": EmptyInput, "advance_cooking_stage": StageInput,
}
