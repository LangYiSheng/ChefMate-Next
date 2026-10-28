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
    _tool("set_kitchen_display", "按用户要求进入厨房全屏专注模式或返回普通对话。mode为focus或conversation；focus的view_kind可指定当前已打开的卡片，省略时跟随当前阶段。只改变当前会话的展示，不推进阶段、不确认食材或步骤。",
          ALL_STAGES, "presentation", False, ("请求切换当前会话的显示模式",), ("用户已要求切换显示模式",)),
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
    _tool("select_recipes", "按用户授权把真实检索或当前候选ID选为餐单，最多4道；可直接引用检索结果，无需先show。已有餐单收到新的用餐人数时，也必须先对同一recipe_ids调用本工具并传diners，再按最新回执进入prep；只改偏好或推进阶段不更新已选餐单量。diners是整餐人数，shared默认按菜数分配每盘共享量（2菜每盘人数×0.75、3菜×0.6、4菜×0.5），属可调整的初始配方量，不是吃饭人数。通常省略servings；仅用户明确指定某盘配方份量才覆盖，明确每道都要足额人数份时用per_dish。新来源建议带data_snapshot，expected_revision用最新业务版本。自动进入planning并展示餐单；重复相同餐单保留进度，改变人数/餐单会清理旧备菜/步骤/计时。prep/cooking须先按allowed_transitions回planning再更新。",
          SELECT_STAGES, "write", True, ("冻结真实餐单", "进入planning", "餐单变化时清理旧执行状态", "展示餐单"), ("用户已授权选择", "可信菜谱引用")),
    _tool("set_meal_plan", "历史兼容入口，与select_recipes使用同一选择事务；新模型应使用select_recipes。",
          SELECT_STAGES, "write", True, ("与select_recipes相同",), ("真实检索引用",), model_visible=False),
    _tool("show_meal_plan", "展示已选餐单、目标人数与真实来源；只展示，不提升业务版本。select_recipes已自动展示，勿重复调用。",
          PLAN_STAGES, "presentation", True, ("替换餐单视图",), ("已有餐单",)),
    _tool("show_prep_checklist", "只预览或切换当前真实备菜清单范围，不改变阶段或勾选。用户要求整理、打开本餐备料或核对食材时，应先advance_cooking_stage进入prep（已有清单无需重复show）；缺少食材不阻止进入prep。一次准备整餐时recipe_ids省略或null；逐道准备时传[某一道当前餐单ID]，也可传当前餐单ID子集。availability=unknown是尚未确认，missing是用户明确缺少；未勾选不代表缺货。",
          PLAN_STAGES, "presentation", True, ("替换备菜视图",), ("已有餐单",)),
    _tool("update_prep_items", "先以最新餐单人数和换算量为准。记录用户明确拥有/已备齐或确认题选中‘齐/可用’的真实项，无需再问是否代勾选。用户点名的具体食材与清单canonical_name字面不一致、且上下文无可信alias证据时，标ready前必须先find_ingredients查询用户叫法，只有真实结果命中清单同一ingredient_id才可确认；未查或无相同ID一律保持pending+unknown，即使凭经验认为能替代也不能勾选。相近名称、同USDA/FDC都不是同物证据。疑似替代不阻塞其他明确项或进入prep，不能自行推为缺货。食谱用料/推荐食材不算拥有。明确缺少记pending+availability=missing；未提及/未选择保持unknown，不连续追问。全部ID先验证再原子保存，item_id必须来自真实清单；不硬划分小料。expected_revision须匹配最新业务版本，成功已展示结果。",
          ("planning", "prep"), "write", True, ("更新准备确认", "展示备菜清单"), ("已有餐单", "用户授权对应食材及状态")),
    _tool("show_cooking_guide", "展示已选菜谱的真实步骤。指定recipe_id会切换当前菜谱/步骤（业务修改）；省略仅展示当前步骤。浏览不等于完成步骤。",
          PLAN_STAGES, "mixed", True, ("指定菜谱时可能切换当前步骤", "替换步骤视图"), ("已有餐单",)),
    _tool("navigate_cooking_step", "用户说下一步、上一步、回到当前步骤或第N步时，必须调用此工具让烹饪卡片同步切换，不能只用文字描述或要求用户先点完成。direction为next/previous/current/step，只有step填写从1开始的step_number。省略recipe_id使用当前菜，指定ID须来自当前餐单；切换菜时以该菜首个未完成步骤为基准。expected_revision来自最新ui_snapshot.revision。只改变当前显示步骤，保留所有done状态；下一步不代表当前步已完成。首尾不会循环、完成步骤或推进阶段。遵循用户已有的切换步骤计时器设置，成功后已刷新卡片，不必再show。",
          ("cooking",), "write", True, ("切换当前步骤", "刷新步骤卡片", "按既有设置处理旧步骤计时器"),
          ("已有餐单", "用户要求切换或查看步骤")),
    _tool("complete_cooking_step", "用户明确说这一步已做好/搞定/完成，或要求帮我标记完成时，记录单个真实步骤，不让用户另点按钮。仅说下一步/看看后面不构成完成授权，应改用navigate_cooking_step。省略recipe_id和step_id只标当前正在做的步骤；指定ID须属于当前餐单。completed=true确认完成后自动定位该菜首个未完成步，false撤销该步完成；都不自动完成其他步骤或结束阶段。expected_revision须为最新业务版本，成功已刷新烹饪卡片，不重复show。",
          ("cooking",), "write", True, ("记录或撤销一个步骤的完成状态", "定位该菜首个未完成步骤", "刷新步骤卡片"),
          ("已有餐单和真实步骤", "用户明确确认该步完成或要求撤销该步完成")),
    _tool("update_cooking_guidance", "按用户条件为当前任务补充备菜/步骤建议，保留冻结原件与真实完成进度；保存后自动展示，无需再次show。",
          ("planning", "prep", "cooking"), "write", True, ("保存任务建议", "可能切换当前步骤", "展示建议"), ("已有餐单", "真实菜谱/步骤ID")),
    _tool("set_cooking_timer", "设置持久化计时器；仅cooking可用。秒数必须来自用户本轮明确指定，或源步骤唯一明确时长；不猜范围与时长。同一步骤已有未结束计时器时，相同时长复用原计时器而不重新开始，不同时长须先取消旧计时器。自动展示计时器。",
          ("cooking",), "write", True, ("创建运行中计时器", "展示计时器"), ("明确时长来源", "运行/暂停计时器受cooking_settings.timer_limit限制，默认3个；满额须先结束已有计时器")),
    _tool("show_timers", "展示现有计时器；依据截止时间计算剩余时间，不创建计时器、不提升业务版本。",
          ALL_STAGES, "presentation", True, ("替换计时视图",)),
    _tool("advance_cooking_stage", "按用户意图明确切换阶段，目标须在allowed_transitions中。已有餐单的‘回到选菜’对应planning；completed后保留原菜谱/人数/份量再做一餐只需本工具到planning，无需重新检索或select，会保留冻结餐单与份量、建立新餐身份并清除旧执行进度；未完成的同一餐回planning保留进度。只要求回选菜时到此停止。planning消息新给人数时先select_recipes同一餐单并传diners，等最新回执再prep；本工具不改用餐人数或份量。idea无餐单，只有明确要求丢弃当前餐单、从无餐单重新开始才使用，不能将‘再做一餐’或保留配方的‘从零做’当作清空授权；回idea会清空餐单、备菜、步骤与计时。选菜自动到planning；准备进入prep；食材全部ready/skipped才可cooking；全部步骤done才可completed。自动展示对应阶段。",
          ALL_STAGES, "write", True, ("切换阶段", "已完成回planning保留餐单并开始新餐", "回到idea清空当前任务", "完成时取消未结束计时器", "展示阶段视图"), ("目标合法且blockers为空", "用户已授权阶段变化")),
    _tool("ask_user", "仅在实质歧义阻塞当前动作时提一个问题；已有餐单的备料清单可直接展示，不先逐组询问是否都有。回答中的‘拥有/齐/可用’可直接记录对应真实食材，无需再问代勾选；多选未选项只是未知，不代表缺少，也不需要为展示清单继续追问。至多6个短选项，可允许自填或多选。必须单独调用，成功后立即等待回答；不能自己选答案或替换未回答问题。",
          ALL_STAGES, "wait", False, ("保存待答问题", "暂停本轮等待用户"), ("当前无待回答问题",)),
)}

if TOOL_CONTRACTS.keys() != TOOL_REQUEST_MODELS.keys():
    raise RuntimeError("Every workflow request must have exactly one tool contract")

TOOL_DESCRIPTIONS = {name: contract.description for name, contract in TOOL_CONTRACTS.items()}
