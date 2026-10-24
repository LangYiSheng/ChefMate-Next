# ChefMate Workflow

独立厨房领域包：从真实菜谱候选选择餐单，冻结本次任务原件，确认备料，推进烹饪步骤，管理持久化计时，并投影闭合的界面组件。数据库、HTTP、模型、技能加载和用户账号由应用管理；本包没有网络客户端、HTML 执行或后台通知。

整体设计先读[一顿饭导览](../guide/overview.md)，本页集中说明可复用领域接口。实现入口为[原子服务边界](../../packages/chefmate-workflow/src/chefmate_workflow/service.py)。

## 公开 API

```python
from chefmate_workflow import WorkflowService, TOOL_REQUEST_MODELS, TOOL_CONTRACTS

service = WorkflowService(
    retrieval,
    get_state=lambda: repository.get(session_id).get("workspace", {}),
    mutate=lambda callback: repository.mutate(
        session_id, lambda state: callback(state.setdefault("workspace", {}))
    ),
)
snapshot = service.model_snapshot()
response = service.execute_tool(
    "show_recipe_choices",
    {"recipe_ids": [recipe_id], "data_snapshot": source_snapshot},
    approved_recipes=[{"recipe_id": recipe_id, "data_snapshot": source_snapshot}],
    operation_id="agent:turn_uuid:call_id",
)
```

调用方的 `mutate(callback)` 必须原子地调用 `callback(workspace_dict)`，并保存该字典。业务包只在命令通过全部校验后原地替换字典。应用应在回调外检查当前回合身份、取消标记及自己的会话权限；不要让已结束模型回合继续写入新状态。

- `execute_tool(name, request, approved_recipes=(), operation_id=None, user_text=None)`：执行闭合工具。`approved_recipes` 必须由应用从真实检索结果收集，不能从模型参数拿到。`user_text` 是可信的本轮用户原文，仅核对计时来源。
- `apply_action(UserActionRequest | dict)`：执行用户界面动作。
- `get_workspace()`：读取当前界面投影，不增加修订号。
- `model_snapshot()`：返回给下一轮模型的短快照字典。
- `meal_record()`：返回具有真实开始时间的本次做菜快照，含菜品、整餐人数、来源及营养；没有观测开始时间的旧工作区返回 `None`。应用在会话事务提交后写入独立制作记录，不能在原事务内另开 SQLite 写事务。个人摄入由应用按完成政策和本人份额另行登记，见[核心 App 参考](workbench.md#制作记录个人饮食与记忆-api)。

导出 `WorkspaceState`、`UISnapshot`、`ViewComponent`、`UIAction`、`UserActionRequest` 和 `WorkflowToolResult`。`TOOL_REQUEST_MODELS` 中每个参数类支持 `model_json_schema()`。`TOOL_CONTRACTS` 是模型说明与执行阶段的唯一注册表，每项包含 `name/description/allowed_stages/effect/auto_display/side_effects/preconditions/model_visible`；`TOOL_DESCRIPTIONS` 由它派生。应用应按快照 `current_actions[name].available` 提供当前模型工具，不能另写一套阶段表。

响应格式为：

```text
status          ok 或明确的冲突/校验状态
workspace       UISnapshot，短模型上下文
ui_snapshot     同 workspace
views           完整 ViewComponent 数组，供应用渲染
actions         本次需要执行的 UIAction，至多一个 show_component 或 set_kitchen_display
message         可选说明
pending_question 待回答问题，回答前保留；无待答时为null
last_answer     最近一次已经验证并保存的答案
errors          参数失败时的安全字段错误：loc/type/hint；不回显原参数
recovery        可选的恢复动作说明
```

持久化的 `WorkspaceState` 包含冻结菜谱和执行状态，由应用保存；它不会作为重复原始数据返回到 HTTP/模型响应。完整 `views` 用于当前工作区。聊天卡片采用主动生成的 `show_component` 动作，组件工具的 `display` 参数决定 inline/workspace/none，动作携带 `placement`。`set_kitchen_display` 使用独立的显示模式参数，不接受 `display`，也不携带组件。`UIAction` 是按 `kind` 鉴别的严格 union；历史 `actions.component` 不能作为可写状态，交互必须按组件ID重新取得当前可信投影与版本，被替换的组件只保留入口。应用注册模型工具时可只返回 `status/message/actions/ui_snapshot/pending_question`，避免将完整 `views` 再次放入模型上下文。

## 工具与界面

当前注册表包含19个工具，其中18个允许模型按当前阶段与权限使用；`set_meal_plan` 仅保留为隐藏的历史别名。当前可调用子集以 `current_actions` 与组件权限为准。

| 工具 | 参数重点 | 组件 |
| --- | --- | --- |
| `set_kitchen_display` | `mode=focus|conversation`、可选当前 `view_kind` | 带稳定 `action_id` 的显示模式动作 |
| `show_preference_form` | 闭合字段名、标题 | `preference_form` |
| `show_recipe_choices` | 真实 IDs、源数据快照 | `recipe_choices` |
| `show_recipe_details` | 已选任务 ID，或新候选 ID 与快照 | `recipe_detail` |
| `set_meal_plan` | 历史兼容别名；新模型使用 `select_recipes` | `meal_plan` |
| `show_meal_plan` | 无业务参数 | `meal_plan` |
| `show_prep_checklist` | `recipe_ids` 为当前餐单子集，null整餐 | `prep_checklist` |
| `show_cooking_guide` | 可选已选菜谱 ID | `cooking_guide` |
| `navigate_cooking_step` | 必填 `expected_revision`、`direction=next|previous|current|step`；仅 `step` 传从1开始的 `step_number`，可选 `recipe_id` | 刷新当前烹饪卡片，不确认完成 |
| `complete_cooking_step` | 必填 `expected_revision`，可选真实 `recipe_id/step_id`，`completed` 默认 true、false 撤销 | 确认单步并刷新烹饪卡片 |
| `update_cooking_guidance` | 当前餐单 `recipe_id`、可选真实 `step_id`、建议与原因 | 保留原件与完成状态的备菜/烹饪建议 |
| `set_cooking_timer` | 秒数、标题、所属菜谱/步骤、来源 | `timers` |
| `show_timers` | 无业务参数 | `timers` |
| `get_cooking_workspace` | 无业务参数 | 短快照 |
| `advance_cooking_stage` | 明确目标阶段 | `task_progress` |
| `update_preferences` | 必填 `changed_fields` 修改掩码、对应 `values`、必填 `expected_revision` | 仅展示实际提交字段的保存值 |
| `select_recipes` | 真实检索/当前任务 `recipe_ids`、可选来源快照、整餐 `diners` 和单菜份量覆盖、必填 `expected_revision` | 保存后的餐单 |
| `update_prep_items` | 真实 `item_id/status` 数组，pending 项可传 `availability=missing|unknown`；必填 `expected_revision` | 保存后的统一备菜清单 |
| `ask_user` | 问题、至多6个不同选项、是否多选/允许自填 | 持久化待答问题 |

所有组件使用 `kind` 鉴别，结构为 `id/kind/title/revision/presentation_revision/editable/data`；数据来自真实领域投影。模型不能提供 HTML、任意 schema、菜谱步骤或营养内容。偏好字段为 `ingredient_terms`、`exclude_ingredient_terms`、`servings`、`max_total_time_min`、`taste_tags`、`available_tools`、`dietary_notes`、`allow_estimated`。

这里的 `update_preferences` 只修改当前厨房任务的选菜偏好。主题、字体、配色与推荐回复由工作台独立的 `app-preferences` 组件负责；个人菜谱草稿的读改与发布也由应用管理，都不是 `TOOL_CONTRACTS` 或 `UserActionRequest` 中的新工作流命令。其白名单、版本与确认接口见[核心 App 参考](workbench.md)。

短快照包含当前完整偏好、所选 IDs、当前菜谱/步骤、简短组件状态和最近20条操作。`current_actions` 逐工具给出 `available/effect/auto_display/blockers`；`allowed_transitions` 列出可进入的不同阶段，`blockers[目标阶段]` 给出具体 `code/message`。它们与执行共用注册表和阶段校验。历史偏好操作仅记改变了哪些字段，当前偏好值是唯一依据。完整用量、营养、warnings 和源快照保留在界面投影与任务原件中。

`set_kitchen_display` 仅请求切换当前会话的专注/普通对话模式，不修改烹饪状态或业务版本。focus 的 `view_kind` 必须是当前阶段可用的已有卡片，省略时按阶段选择；conversation 不指定卡片。显示动作使用 `operation_id` 作为稳定 `action_id`，同一操作重放不再次发出动作。浏览器须按会话和动作ID去重，首次载入历史不重放旧显示请求。该工具在工作台归属 `app-preferences`。

## 备菜与步骤确认

用户已有餐单并要求整理、打开或核对本餐备料时，助手先用 `advance_cooking_stage(stage="prep")` 展示清单；食材不齐不阻止进入备菜。只有明确要求预览时才单独使用 `show_prep_checklist`，它不会自动切换阶段。未知项目直接留在清单中，不为展示清单连续提问。

食材的 `status` 仍为 `pending/ready/skipped`；独立的 `availability` 会投影到完整食材和紧凑备菜快照：

| availability | 含义 | 保存方式 |
| --- | --- | --- |
| `unknown` | 尚未确认，包含多选中未选的项目 | `pending` 默认值；可显式传 `availability="unknown"` 清除旧缺货记录 |
| `available` | 用户明确说拥有或备齐 | 记录 `status="ready"` 后由服务端投影，不接受模型另传 available |
| `missing` | 用户明确说缺少或需购买 | `status="pending", availability="missing"`，界面显示“待购买” |

`availability` 修改参数只允许用于 pending 项。明确缺货存入 `WorkspaceState.prep_availability`，重读和重启后保留；改为ready/skipped、用户取消勾选回pending时清除旧缺货记录，换餐单或回到idea时清理原任务记录。全部pending（包括missing）都阻止进入cooking；跳过仍需用户明确授权。用户明确说“我家有/已备齐”，或选中确认题的“齐/可用”，助手可立即记录对应真实项，无需再问是否代勾选；食谱用料、推荐食材和未选选项不构成此授权。

`navigate_cooking_step` 和 `complete_cooking_step` 仅在cooking阶段使用，均严格校验当前餐单引用和 `expected_revision`。导航只改变活动步骤，保留done状态；首尾返回 `first_step/last_step`，不循环、不替用户完成。用户明确确认某步已做好或要求标记完成时，完成工具才记录该步，省略ID只作用于当前步；确认后自动定位该菜首个未完成步骤，不再紧接一次next跳过它。`completed=false` 撤销该步完成。没有当前步时需指定真实步骤，返回 `step_required`；完成一道菜不完成其他菜，也不自动进入completed。

两种步骤工具都会刷新同一 `CookingGuide` 组件并返回 `show_component`，计时器继续遵循已有的 `clear_timer_on_step_change` 设置。模型理解用户的实际确认意图，领域服务不靠固定自然语言词句判定完成；“下一步”本身仅对应导航。界面 `complete_step` 与模型完成工具共用同一事务语义。

## 用户动作

`UserActionRequest` 是按 `type` 鉴别的严格 union，`model_dump()` 返回普通动作对象：

```json
{
  "action_id": "unique-id",
  "base_revision": 3,
  "view_id": "current-view-id",
  "type": "update_preferences",
  "payload": {"values": {"ingredient_terms": ["鸡肉"]}}
}
```

支持 `update_preferences`、`select_recipes`、`open_recipe_detail`、`change_servings`、`set_checklist_scope`、`check_prep_item`、`answer_question`、`set_active_recipe`、`set_active_step`、`complete_step`、`complete_meal`、`change_stage`、`start_timer`、`pause_timer`、`resume_timer`、`cancel_timer`。`complete_meal` 的 payload 为 `{}`，必须全餐步骤均由用户确认完成，之后才保存完成时间；界面成功保存后可发起收尾聊天。各动作有不同的严格 payload；未知字段、任意 ID、非法数字和类型被拒绝。

偏好更新是 Partial，不会清掉未提交字段。直接传已验证的模型对象，或者使用 `model_dump(exclude_unset=True)`；不要把所有未提交默认值展开成显式 `null`。可空字段可以明确清空，列表字段使用 `[]` 清空。

`revision` 仅在实际业务状态变化时增加；`presentation_revision` 用于组件替换和纯展示排序，应用按两者排序快照。纯展示仍校验当前业务版本，并从最新工作区原子修改视图，不覆盖同时存在的业务状态。组件的 `revision` 始终是业务版本，用户动作只提交 `base_revision`。模型工具不接受 `base_revision`；支持显式版本参数的模型批量写入只接受 `expected_revision`，其他工具以执行开始时读到的业务版本校验原子事务，应用回合边界仍需拒绝用户并发变更。

同一 `action_id` 与相同内容重放不再修改；同 ID 不同内容返回 `action_id_conflict`。`base_revision` 过期返回 `revision_conflict`，即使业务版本不变，被替换的组件 ID 仍返回 `stale_view`。幂等记录保留最近20条；超出窗口的旧业务写入被旧业务版本拒绝。重复无变化写入可记录新操作，但不会提升业务版本。冲突响应携带当前可信界面，调用方应复核后重新提交，不能自动重试旧业务操作。

模型修改偏好、餐单、备菜及导航/完成步骤工具的 `expected_revision` 是必填字段，批量食材修改先校验全部ID再原子提交。工具按照真实ID执行用户指令，不根据名称自动划分“小料”，不绕过烹饪阶段门槛。模型偏好更新要求 `changed_fields`，只保存其中显式提供的字段；严格输出补齐的其他 `values` 占位值也忽略。UI偏好动作仍使用Partial，省略保留；人数/时间/厨具允许null清空，列表用[]、文字用空串，其他字段null在schema层拒绝。`show_preference_form` 始终展示当前保存值。

`select_recipes` 可直接使用应用批准的检索引用，不要求先展示候选；同一ID有多个可信来源时必须提供 `data_snapshot`。它与历史 `set_meal_plan` 共用选择事务，重复完全相同的来源、菜品及份量保留阶段和执行进度；更换餐单清理旧备菜/步骤/计时。已完成后再次选择会创建新的 `meal_id`，不沿用上一次完成时间。修改工具返回的组件已展示保存结果，无需再调用show。`show_recipe_choices` 登记候选、`show_recipe_details` 登记新来源、`show_cooking_guide` 切换当前菜谱/步骤可能实际修改业务版本；重看既有状态只改变展示版本。

`diners` 与偏好 `servings` 表示整餐人数，保存为 `meal_diners`。默认 `portion_mode="shared"`：一道菜按人数全量，两道菜每盘按人数×0.75、三道×0.6、四道×0.5 的配方等效份量起算。例如5人共享4道菜，各盘默认2.5配方份，不能标成每道各吃5人的完整一餐。这是透明可调的家常初始量，不是营养标准或实际摄入推断。模型通常省略 `servings`；用户明确某道的量时用该字典覆盖，之后换菜数仍保留该覆盖。用户明确每道都要足额人数份才用 `per_dish`。原份量未知、不可缩放与估算的限制仍保留。`meal_plan.data` 和短快照均携带整餐人数、模式及 `portion_note`。

开始烹饪时记录真实 `started_at`，完成时记录 `completed_at`；重复完成保持原时间。`meal_record()` 用同一份量/营养投影输出观察到的任务快照，供应用独立保存饮食记录。旧工作区没有观测时间时不回填虚构日期。

`ask_user` 的结构为 `{title?,question,options:[{id,label}],allow_custom:true,multiple:false}`，其中 `allow_custom/multiple` 可由调用方明确设置，示例为默认值。问题最多500字，标题与选项各80字，至多6个不同选项；没有选项时必须允许自填。待答问题不会被新的提问工具悄悄替换，调用方应停止本轮推理并等用户输入。`answer_question` 使用 `{question_id,option_ids:[],text:""}`，同时携带普通动作的 `action_id/base_revision`；不符合当前问题、多选限制、自填限制或版本的答案不保存。成功后清除 `pending_question`，将 `question_id/question/option_ids/labels/text` 存入 `last_answer`，再由应用启动下一轮。

多选中未选择的选项仅表示未确认，不推断为缺货。回答不会自动联动食材状态；下一轮助手应依据已明确的拥有或缺少事实调用 `update_prep_items`，不为同一事实重复提问。只在实质歧义阻塞用户当前要求时提问。

`last_answer` 表示服务端已验证的问题答案，本包不会因此自动执行发布或删除等应用操作。应用若把问题作为写入确认，仍须核对同一业务对象及版本、会话、问题ID和所需选项；不能把任意一次回答或模型声称“已确认”作为授权。工作台的个人菜谱确认由专用回执完成，草稿版本变化必须重新审核与确认，见[菜谱中心 API](workbench.md#菜谱中心-api)。

## 业务边界

- 阶段：idea尚无餐单，选择真实菜谱后自动进入planning；准备食材进入prep，逐项确认后进入cooking，全部步骤完成后才能completed。支持明确回退；空餐单不能advance到planning，不能跳过备料校验。只保留统一备菜清单，不再维护独立采购阶段和购物勾选。
- 每个会话一个当前厨房任务。选菜冻结完整真实原件，来源更新不会偷偷替换进行中的任务。原件只保留业务白名单，不保存 `raw_source_json`、凭据或导出整包。
- 食材稳定 ID 为 `<recipe_id>:ingredient:<原ordinal>`；步骤 ID 为 `<recipe_id>:step:<原列表序号>`，从0开始。指令与顺序沿用源菜谱。
- 源数据没有可靠的必选/可选字段，因此全部食材需要明确确认 `ready` 或用户主动 `skipped`；界面说明该资料边界，不声称已证明食材可省略。
- 浏览当前步骤不会把步骤标成完成；用户界面使用 `complete_step`，明确口头确认使用 `complete_cooking_step`。所有已选菜谱的步骤都 done 才允许 completed。
- 烹饪中不允许替换餐单、修改备料或份量。回退后改变人数会使该菜谱的准备和步骤重新待确认，并取消其关联计时。
- 份量换算仅针对已知数字、可靠人数基准和 `scalable=true` 的项；不猜单位、不把“适量”转零、不倍增不可缩放调味料。原始 `raw_amount/raw_unit` 与可食克重 `grams` 分开保留，数量“2个”不能替代未知克重。当前用量通过 `amount_text/amount_value/unit` 投影，原人数不可靠时保留未知和提示，不伪造换算基准。营养按单项贡献重算，缺信息保持未知，人数估算继续传递标记。
- `set_checklist_scope` 在统一备菜视图切换整餐/单菜，范围必须来自已选餐单；食材仍保留逐菜ID、目标人数和真实用量，不把另一道菜的同名食材推断为已经确认。
- `WorkspaceState` 当前格式为 `schema_version=2`。读取v1时将旧shopping阶段和购物组件归一成prep，旧购买勾选按实际 `source_item_ids` 转为准备确认，保留已有ready/skipped；未知归属不猜测。读取不改修订号，下一次有效命令原子保存v2。旧用户消息中的购物快照也可读取，新模型不再得到购物工具。
- 计时保存 UTC 截止时间；暂停保存剩余秒数，恢复生成新截止时间，读取时依据真实时间判断到期。UI 的直接输入属于用户指定；模型的 `basis=user` 需与可信用户文本明确时长一致，`basis=recipe_text` 需源步骤唯一明确时长，不猜范围或多阶段时间。无后台通知。
- 烹饪投影 `CookingData.timers` 按当前菜谱每步骤最多展示一个计时器，优先选最新未结束的计时器，否则选最后一条终态记录；`TimersData` 包含全任务计时器。两者的 `server_now` 为UTC时间，界面可据此校准本地倒数。到期只显示 `finished`，不自动完成步骤；GET仍只读，重启依据持久化deadline恢复，到期终态在下一次成功命令保存。同一步骤重复启动相同时长复用原计时器，不重置剩余时间；不同新时长须先取消旧计时器。结束后明确重启只替换该步骤旧记录。旧终态重复记录在读取时归一，下次成功命令保存；旧活动计时器不会因历史归一而无声丢失。记录上限30条时优先保留全部活动计时器，只清理较早终态历史。
- `CookingSettings` 默认为 `{timer_limit:3,clear_timer_on_step_change:false}`，上限只接受1或3，运行和暂停的计时器占并发名额。`WorkflowService(...,cooking_settings=getter)` 每次读取应用最新设置；`UISnapshot.cooking_settings/timer_capacity` 提供策略、活动数量、剩余名额和超限状态。降额保留全部已有计时器，新建满额明确拒绝且不替换旧计时器。开启步骤清理后，仅真实当前菜谱/步骤发生变化（包括完成后自动进入下一步）才删除离开步骤的计时器，纯浏览或翻页不受影响。

旧版功能考察与本轮边界见 [迁移矩阵](../engineering/records/legacy-feature-matrix.md)。

## 验证

测试使用真实检索详情形状的假数据和原子 host 回调，不调用模型或业务数据库。覆盖闭合输入、Partial 偏好、CAS/幂等/旧视图、源版本变化、冻结任务、并发修改、合并/换算/未知值、阶段门槛、步骤确认、计时恢复和模型上下文容量。

```powershell
python -m pytest packages/chefmate-workflow/tests -q -p no:cacheprovider
```
