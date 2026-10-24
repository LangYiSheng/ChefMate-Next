"""Closed kitchen domain inputs and trusted UI projections; no HTML or arbitrary forms."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from chefmate_retrieval import DatasetSnapshot

Text = Annotated[str, Field(min_length=1, max_length=200)]
Positive = Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)]
Stage = Literal["idea", "planning", "prep", "cooking", "completed"]
PrepStatus = Literal["pending", "ready", "skipped"]
StepStatus = Literal["pending", "current", "done"]
PreferenceName = Literal["servings", "max_total_time_min", "taste_tags", "available_tools",
                         "ingredient_terms", "exclude_ingredient_terms", "dietary_notes", "allow_estimated"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class CookingSettings(StrictModel):
    timer_limit: Literal[1, 3] = 3
    clear_timer_on_step_change: bool = False

    @field_validator("timer_limit", mode="before")
    @classmethod
    def timer_limit_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Timer limit must be the integer 1 or 3")
        return value


class TimerCapacity(StrictModel):
    limit: Literal[1, 3] = 3
    active_count: Annotated[int, Field(ge=0)] = 0
    available_slots: Annotated[int, Field(ge=0)] = 3
    over_limit: bool = False


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
    servings: Positive | None = Field(default=None, description="省略保留原值；null清空人数限制。")
    max_total_time_min: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = Field(
        default=None, description="省略保留原值；null清空耗时限制。")
    taste_tags: Annotated[list[Text], Field(max_length=30)] = Field(default_factory=list,
        description="省略保留原值；[]清空；不能为null。")
    available_tools: Annotated[list[Text], Field(max_length=50)] | None = Field(
        default=None, description="省略保留；null表示未限制厨具，[]表示没有厨具；按用户明确要求修改。")
    ingredient_terms: Annotated[list[Text], Field(max_length=50)] = Field(default_factory=list,
        description="省略保留原值；[]清空；不能为null。")
    exclude_ingredient_terms: Annotated[list[Text], Field(max_length=50)] = Field(default_factory=list,
        description="省略保留原值；[]清空；不能为null。")
    dietary_notes: Annotated[str, Field(max_length=2000)] = Field(default="",
        description="省略保留原值；空字符串清空；不能为null。")
    allow_estimated: bool = Field(default=True, description="省略保留原值；修改时只接受true或false，不能为null。")


class ApprovedRecipeRef(StrictModel):
    recipe_id: Text
    data_snapshot: DatasetSnapshot


class FrozenRecipe(StrictModel):
    recipe_id: Text
    data_snapshot: DatasetSnapshot
    recipe: dict[str, Any]
    target_servings: Positive | None = None
    portion_override: bool = False


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
    adapted_instruction: str | None = None
    adaptation_reason: str | None = None


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


QuestionText = Annotated[str, Field(min_length=1, max_length=500, pattern=r"^[^<>\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+$")]


class QuestionOption(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")]
    label: Annotated[QuestionText, Field(max_length=80)]


class PendingQuestion(StrictModel):
    id: Text
    title: Annotated[QuestionText, Field(max_length=80)] = "想再确认一下"
    question: QuestionText
    options: Annotated[list[QuestionOption], Field(max_length=6)] = Field(default_factory=list)
    allow_custom: bool = True
    multiple: bool = False


class QuestionAnswer(StrictModel):
    question_id: Text
    question: QuestionText
    option_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    text: Annotated[str, Field(max_length=2000)] = ""


class RecipeListData(StrictModel):
    recipes: list[RecipeView]
    meal_diners: Positive | None = None
    portion_mode: Literal["shared", "per_dish"] = "shared"
    portion_note: str = ""


class RecipeDetailData(StrictModel):
    recipe: RecipeView


class PrepData(StrictModel):
    items: list[IngredientView]
    recipe_ids: list[str] | None = None
    notes: list[str] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)


class CookingData(StrictModel):
    recipe_id: str | None
    recipe_name: str | None
    steps: list[StepView]
    active_step_id: str | None
    guidance: list[str] = Field(default_factory=list)
    timers: list[TimerState] = Field(default_factory=list)
    server_now: str | None = None


class TimersData(StrictModel):
    timers: list[TimerState]
    server_now: str | None = None


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
    presentation_revision: Annotated[int, Field(ge=0)] = 0
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


ViewComponent = Annotated[PreferenceForm | RecipeChoices | RecipeDetail | MealPlan | PrepChecklist
                          | CookingGuide | Timers | TaskProgress, Field(discriminator="kind")]


class CompactComponentSnapshot(StrictModel):
    id: Text
    kind: Literal["preference_form", "recipe_choices", "recipe_detail", "meal_plan",
                  "prep_checklist", "cooking_guide", "timers", "task_progress"]
    title: Text
    revision: Annotated[int, Field(ge=0)]
    presentation_revision: Annotated[int, Field(ge=0)] = 0
    editable: bool
    data: dict[str, Any]


class UISnapshot(StrictModel):
    revision: Annotated[int, Field(ge=0)]
    presentation_revision: Annotated[int, Field(ge=0)] = 0
    stage: Stage
    preferences: PreferenceValues = Field(default_factory=PreferenceValues)
    meal_diners: Positive | None = None
    portion_mode: Literal["shared", "per_dish"] = "shared"
    portion_note: str = ""
    selected_recipe_ids: list[str] = Field(default_factory=list)
    active_recipe_id: str | None = None
    active_step_id: str | None = None
    components: list[CompactComponentSnapshot] = Field(default_factory=list)
    recent_actions: list[ActionRecord] = Field(default_factory=list)
    pending_question: PendingQuestion | None = None
    last_answer: QuestionAnswer | None = None
    current_actions: dict[str, Any] = Field(default_factory=dict)
    allowed_transitions: list[Stage] = Field(default_factory=list)
    blockers: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    cooking_settings: CookingSettings = Field(default_factory=CookingSettings)
    timer_capacity: TimerCapacity = Field(default_factory=TimerCapacity)

    @model_validator(mode="before")
    @classmethod
    def migrate_purchasing_snapshot(cls, value):
        from .migration import migrate_snapshot
        return migrate_snapshot(value)


class CookingAdaptation(StrictModel):
    recipe_id: Text
    step_id: Text | None = None
    instruction: Annotated[str, Field(min_length=1, max_length=2000)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class WorkspaceState(StrictModel):
    schema_version: Literal[2] = 2
    revision: Annotated[int, Field(ge=0)] = 0
    presentation_revision: Annotated[int, Field(ge=0)] = 0
    stage: Stage = "idea"
    preferences: PreferenceValues = Field(default_factory=PreferenceValues)
    meal_diners: Positive | None = None
    portion_mode: Literal["shared", "per_dish"] = "shared"
    meal_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    candidates: list[FrozenRecipe] = Field(default_factory=list)
    meal_plan: list[FrozenRecipe] = Field(default_factory=list)
    prep_status: dict[str, PrepStatus] = Field(default_factory=dict)
    step_status: dict[str, StepStatus] = Field(default_factory=dict)
    active_recipe_id: str | None = None
    active_step_id: str | None = None
    timers: list[TimerState] = Field(default_factory=list)
    components: list[ViewComponent] = Field(default_factory=list)
    recent_actions: list[ActionRecord] = Field(default_factory=list)
    adaptations: list[CookingAdaptation] = Field(default_factory=list)
    pending_question: PendingQuestion | None = None
    last_answer: QuestionAnswer | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_purchasing_workspace(cls, value):
        from .migration import migrate_workspace
        return migrate_workspace(value)


class ShowComponentAction(StrictModel):
    kind: Literal["show_component"] = "show_component"
    component: ViewComponent
    placement: Literal["inline", "workspace"] = "inline"


DisplayViewKind = Literal["preference_form", "recipe_choices", "recipe_detail", "meal_plan",
                          "prep_checklist", "cooking_guide", "timers"]


class KitchenDisplayAction(StrictModel):
    kind: Literal["set_kitchen_display"] = "set_kitchen_display"
    action_id: Text
    mode: Literal["focus", "conversation"]
    view_kind: DisplayViewKind | None = None


class UIAction(RootModel[Annotated[ShowComponentAction | KitchenDisplayAction, Field(discriminator="kind")]]):
    """Typed UI events; display requests carry a stable, session-local operation ID."""


class WorkflowToolResult(StrictModel):
    status: str
    workspace: UISnapshot
    views: list[ViewComponent]
    ui_snapshot: UISnapshot
    actions: list[UIAction] = Field(default_factory=list)
    message: str | None = None
    pending_question: PendingQuestion | None = None
    last_answer: QuestionAnswer | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    recovery: list[str] = Field(default_factory=list)


class ToolInput(StrictModel):
    display: Literal["inline", "workspace", "none"] = Field(
        default="inline", description="仅展示这轮必要的核心内容。inline在聊天内交互，workspace仅在工作区展示，none仅更新状态。")


class EmptyInput(ToolInput):
    pass


class KitchenDisplayInput(StrictModel):
    mode: Literal["focus", "conversation"] = Field(
        description="focus进入厨房全屏专注模式，conversation返回普通对话；只切换展示，不修改做菜进度。")
    view_kind: DisplayViewKind | None = Field(default=None,
        description="focus可指定当前已打开的卡片类型；省略跟随当前做菜阶段。conversation时省略。")

    @model_validator(mode="after")
    def conversation_has_no_view(self):
        if self.mode == "conversation" and self.view_kind is not None:
            raise ValueError("Conversation mode does not select a kitchen view")
        return self


class ChecklistInput(ToolInput):
    recipe_ids: Annotated[list[Text], Field(min_length=1, max_length=4)] | None = Field(
        default=None, description="只看指定菜品的可勾选食材；必须来自当前餐单。null展示整餐合并清单。")


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
    diners: Positive | None = None
    portion_mode: Literal["shared", "per_dish"] = "shared"
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


class AdaptCookingInput(ToolInput):
    recipe_id: Text
    step_id: Text | None = Field(default=None, description="当前任务里的真实步骤ID；null表示这道菜的通用备菜/烹饪建议。")
    instruction: Annotated[str, Field(min_length=1, max_length=2000)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class ExpectedRevisionInput(ToolInput):
    expected_revision: Annotated[int, Field(ge=0)] = Field(
        description="最新成功工具返回ui_snapshot.revision（业务版本）。依赖前次写入的调用必须等结果；不要使用展示版本。")


class NavigateCookingStepInput(ExpectedRevisionInput):
    direction: Literal["next", "previous", "current", "step"] = Field(
        description="next下一步，previous上一步，current展示当前步骤，step跳到step_number指定的步骤；都不会标记完成。")
    step_number: Annotated[int, Field(ge=1)] | None = Field(default=None,
        description="仅direction=step时提供，从1开始的原菜谱步骤编号。")
    recipe_id: Text | None = Field(default=None,
        description="当前餐单的真实菜谱ID；省略使用当前正在做的菜。")

    @model_validator(mode="after")
    def numbered_target(self):
        if (self.direction == "step") != (self.step_number is not None):
            raise ValueError("Only step navigation requires a step_number")
        return self


class UpdatePreferencesInput(ExpectedRevisionInput):
    values: PreferencePatch
    changed_fields: Annotated[list[PreferenceName], Field(min_length=1, max_length=8)] = Field(
        description="本次用户明确要求修改的字段名。只保存这些字段；即使严格格式补齐values中的其他值，也不会修改未列出的字段。")

    @model_validator(mode="after")
    def supplied_values(self):
        if (len(self.changed_fields) != len(set(self.changed_fields))
                or set(self.changed_fields) - self.values.model_fields_set):
            raise ValueError("Each changed field must be distinct and supplied in values")
        return self


class SelectRecipesInput(ExpectedRevisionInput):
    recipe_ids: Annotated[list[Text], Field(min_length=1, max_length=4)] = Field(
        description="来自真实检索或当前工作区的ID；可直接选择检索结果，无需先展示候选。")
    data_snapshot: DatasetSnapshot | None = Field(default=None,
        description="新检索候选建议提供对应来源snapshot；省略时只在服务器有唯一可信来源时解析，不猜来源。")
    servings: dict[str, Positive] = Field(default_factory=dict,
        description="仅覆盖某盘的配方等效份量，通常省略让系统分配共享菜量；不是整餐人数。只有用户明确要求某道的量时才填写。")
    diners: Positive | None = Field(default=None,
        description="整餐实际用餐人数；省略沿用偏好中的用餐人数。5人共享4菜应填5，通常不要填写servings。")
    portion_mode: Literal["shared", "per_dish"] = Field(default="shared",
        description="shared按整餐人数与菜数分配每盘共享量；仅用户明确每道都要足额人数份时用per_dish。")


class PrepItemUpdate(StrictModel):
    item_id: Text
    status: PrepStatus


class UpdatePrepItemsInput(ExpectedRevisionInput):
    items: Annotated[list[PrepItemUpdate], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def distinct_items(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("Item IDs must be distinct")
        return self


class AskUserInput(ToolInput):
    title: Annotated[QuestionText, Field(max_length=80)] = "想再确认一下"
    question: QuestionText
    options: Annotated[list[QuestionOption], Field(max_length=6)] = Field(default_factory=list)
    allow_custom: bool = True
    multiple: bool = False

    @model_validator(mode="after")
    def answerable(self):
        if (not self.options and not self.allow_custom
                or len({option.id for option in self.options}) != len(self.options)
                or len({option.label for option in self.options}) != len(self.options)):
            raise ValueError("Question must have distinct options or allow a custom answer")
        return self


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


class ChecklistScopePayload(StrictModel):
    recipe_ids: Annotated[list[Text], Field(min_length=1, max_length=4)] | None = None


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


class CompleteMealPayload(StrictModel):
    pass


class AnswerQuestionPayload(StrictModel):
    question_id: Text
    option_ids: Annotated[list[str], Field(max_length=6)] = Field(default_factory=list)
    text: Annotated[str, Field(max_length=2000)] = ""


def _action_model(name, payload_model):
    from pydantic import create_model
    return create_model(name + "Action", __base__=ActionBase, type=(Literal[name], name),
                        payload=(payload_model, ...))


ACTION_PAYLOAD_MODELS = {
    "update_preferences": PreferencePayload, "select_recipes": RecipeIdsPayload,
    "open_recipe_detail": RecipeIdPayload, "change_servings": ServingsPayload,
    "check_prep_item": PrepPayload, "answer_question": AnswerQuestionPayload,
    "set_checklist_scope": ChecklistScopePayload,
    "set_active_recipe": RecipeIdPayload, "set_active_step": StepPayload,
    "complete_step": CompleteStepPayload, "change_stage": StagePayload,
    "complete_meal": CompleteMealPayload,
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
    "set_kitchen_display": KitchenDisplayInput,
    "show_preference_form": PreferenceFormInput, "show_recipe_choices": RecipeChoicesInput,
    "show_recipe_details": RecipeDetailInput, "set_meal_plan": MealPlanInput,
    "show_meal_plan": EmptyInput, "show_prep_checklist": ChecklistInput,
    "show_cooking_guide": CookingGuideInput, "set_cooking_timer": TimerInput,
    "navigate_cooking_step": NavigateCookingStepInput,
    "show_timers": EmptyInput, "get_cooking_workspace": EmptyInput, "advance_cooking_stage": StageInput,
    "update_cooking_guidance": AdaptCookingInput,
    "update_preferences": UpdatePreferencesInput, "select_recipes": SelectRecipesInput,
    "update_prep_items": UpdatePrepItemsInput, "ask_user": AskUserInput,
}
