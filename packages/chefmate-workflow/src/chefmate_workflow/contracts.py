"""One source for tool availability, mutation effects and model-facing guidance."""

from dataclasses import dataclass
from typing import Literal

from .models import TOOL_REQUEST_MODELS


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    allowed_stages: tuple[str, ...]
    effect: Literal["read", "presentation", "mixed", "write", "wait"]
    auto_display: bool = False
    side_effects: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    model_visible: bool = True


ALL_STAGES = ("idea", "planning", "prep", "cooking", "completed")
PLAN_STAGES = ("planning", "prep", "cooking", "completed")
SELECT_STAGES = ("idea", "planning", "completed")


def _tool(name, description, stages, effect, auto_display=False, side_effects=(), preconditions=(), *, model_visible=True):
    return ToolContract(name, description, stages, effect, auto_display, side_effects, preconditions, model_visible)


TOOL_CONTRACTS = {item.name: item for item in (
    _tool("get_cooking_workspace", "读取最新业务版本、当前可用动作、阶段转换与具体阻塞原因；依赖上次修改的调用以最新成功回调为准。",
          ALL_STAGES, "read"),
    _tool("show_preference_form", "展示当前已保存的偏好供用户填写或核对；只展示，不修改偏好，也不提升业务版本。",
          ALL_STAGES, "presentation", True, ("替换当前偏好视图",)),
    _tool("update_preferences", "changed_fields必须只列用户要求修改的字段，values提供对应值；未列字段一律保留。列表用[]、文字用空串清空；人数/时间/厨具可用null清空，其他字段禁止null。expected_revision来自最新ui_snapshot.revision。保存后已自动展示真实值，无需重复show。",
          ALL_STAGES, "write", True, ("保存偏好", "展示保存后的偏好"), ("用户已授权修改提供的字段",)),
    _tool("show_recipe_choices", "展示真实检索候选；recipe_ids与data_snapshot必须来自同一次可信来源。只展示已登记候选不提升业务版本；登记新候选会修改业务版本。展示不等于选择餐单。",
          SELECT_STAGES, "mixed", True, ("登记真实候选（若有变化）", "替换候选视图"), ("真实检索引用",)),
    _tool("show_recipe_details", "展示当前冻结菜谱或真实检索详情。执行中只可用当前餐单；新候选需来源快照并会登记候选。浏览详情不会确认备菜或完成步骤。",
          ALL_STAGES, "mixed", True, ("登记新候选（如必要）", "替换详情视图"), ("可信菜谱引用；新候选须data_snapshot",)),
    _tool("select_recipes", "按用户授权把真实检索或当前候选ID选为餐单，最多4道；可直接引用检索结果，无需先show。新来源建议带data_snapshot，expected_revision用最新业务版本。自动进入planning并展示餐单；重复相同餐单保留进度，改变餐单会清理旧备菜/步骤/计时。",
          SELECT_STAGES, "write", True, ("冻结真实餐单", "进入planning", "餐单变化时清理旧执行状态", "展示餐单"), ("用户已授权选择", "可信菜谱引用")),
    _tool("set_meal_plan", "历史兼容入口，与select_recipes使用同一选择事务；新模型应使用select_recipes。",
          SELECT_STAGES, "write", True, ("与select_recipes相同",), ("真实检索引用",), model_visible=False),
    _tool("show_meal_plan", "展示已选餐单、目标人数与真实来源；只展示，不提升业务版本。select_recipes已自动展示，勿重复调用。",
          PLAN_STAGES, "presentation", True, ("替换餐单视图",), ("已有餐单",)),
    _tool("show_prep_checklist", "展示当前真实备菜状态。recipe_ids省略或null显示整餐，非空数组只可包含当前餐单ID；不改变阶段或勾选。已有清单/工具自动展示时无需重复调用。",
          PLAN_STAGES, "presentation", True, ("替换备菜视图",), ("已有餐单",)),
    _tool("update_prep_items", "按用户明确授权逐ID批量更新备菜状态，全部ID先验证再原子保存。item_id必须来自真实清单；不能凭名称猜ID或硬划分小料。expected_revision须匹配最新业务版本，自动展示保存结果。",
          ("planning", "prep"), "write", True, ("更新准备确认", "展示备菜清单"), ("已有餐单", "用户授权对应食材及状态")),
    _tool("show_cooking_guide", "展示已选菜谱的真实步骤。指定recipe_id会切换当前菜谱/步骤（业务修改）；省略仅展示当前步骤。浏览不等于完成步骤。",
          PLAN_STAGES, "mixed", True, ("指定菜谱时可能切换当前步骤", "替换步骤视图"), ("已有餐单",)),
    _tool("update_cooking_guidance", "按用户条件为当前任务补充备菜/步骤建议，保留冻结原件与真实完成进度；保存后自动展示，无需再次show。",
          ("planning", "prep", "cooking"), "write", True, ("保存任务建议", "可能切换当前步骤", "展示建议"), ("已有餐单", "真实菜谱/步骤ID")),
    _tool("set_cooking_timer", "设置持久化计时器；仅cooking可用。秒数必须来自用户本轮明确指定，或源步骤唯一明确时长；不猜范围与时长。自动展示计时器。",
          ("cooking",), "write", True, ("创建运行中计时器", "展示计时器"), ("明确时长来源", "最多12个运行/暂停计时器")),
    _tool("show_timers", "展示现有计时器；依据截止时间计算剩余时间，不创建计时器、不提升业务版本。",
          ALL_STAGES, "presentation", True, ("替换计时视图",)),
    _tool("advance_cooking_stage", "按用户意图明确切换阶段，目标须在allowed_transitions中。idea无餐单；选菜自动到planning；准备进入prep；食材全部ready/skipped才可cooking；全部步骤done才可completed。回到idea会清空当前餐单、备菜、步骤与计时。自动展示对应阶段。",
          ALL_STAGES, "write", True, ("切换阶段", "回到idea清空当前任务", "完成时取消未结束计时器", "展示阶段视图"), ("目标合法且blockers为空", "用户已授权阶段变化")),
    _tool("ask_user", "仅在实质歧义阻塞任务时提一个问题；至多6个短选项，可允许自填或多选。必须单独调用，成功后立即等待回答；不能自己选答案或替换未回答问题。",
          ALL_STAGES, "wait", False, ("保存待答问题", "暂停本轮等待用户"), ("当前无待回答问题",)),
)}

if TOOL_CONTRACTS.keys() != TOOL_REQUEST_MODELS.keys():
    raise RuntimeError("Every workflow request must have exactly one tool contract")

TOOL_DESCRIPTIONS = {name: contract.description for name, contract in TOOL_CONTRACTS.items()}
