# ChefMate Workflow

独立厨房领域包：从真实菜谱候选选择餐单，冻结本次任务原件，确认备料，推进烹饪步骤，管理持久化计时，并投影闭合的界面组件。数据库、HTTP、模型、技能加载和用户账号由应用管理；本包没有网络客户端、HTML 执行或后台通知。

## 公开 API

```python
from chefmate_workflow import WorkflowService, TOOL_REQUEST_MODELS, TOOL_DESCRIPTIONS

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

导出 `WorkspaceState`、`UISnapshot`、`ViewComponent`、`UIAction`、`UserActionRequest` 和 `WorkflowToolResult`。`TOOL_REQUEST_MODELS` 中每个参数类支持 `model_json_schema()`。

响应格式为：

```text
status          ok 或明确的冲突/校验状态
workspace       UISnapshot，短模型上下文
ui_snapshot     同 workspace
views           完整 ViewComponent 数组，供应用渲染
actions         本次需要展示的 UIAction，至多一个 show_component
message         可选说明
```

持久化的 `WorkspaceState` 包含冻结菜谱和执行状态，由应用保存；它不会作为重复原始数据返回到 HTTP/模型响应。完整 `views` 用于当前工作区，历史 `actions.component` 应作为只读展示，不能复用旧卡片修改当前任务。应用注册模型工具时可只返回 `status/message/actions/ui_snapshot`，避免将完整 `views` 再次放入模型上下文。

## 工具与界面

| 工具 | 参数重点 | 组件 |
| --- | --- | --- |
| `show_preference_form` | 闭合字段名、标题 | `preference_form` |
| `show_recipe_choices` | 真实 IDs、源数据快照 | `recipe_choices` |
| `show_recipe_details` | 已选任务 ID，或新候选 ID 与快照 | `recipe_detail` |
| `set_meal_plan` | 至多4道、按 ID 的目标人数、快照 | `meal_plan` |
| `show_meal_plan` | 无业务参数 | `meal_plan` |
| `show_shopping_list` | 无业务参数 | `shopping_list` |
| `show_prep_checklist` | 无业务参数 | `prep_checklist` |
| `show_cooking_guide` | 可选已选菜谱 ID | `cooking_guide` |
| `set_cooking_timer` | 秒数、标题、所属菜谱/步骤、来源 | `timers` |
| `show_timers` | 无业务参数 | `timers` |
| `get_cooking_workspace` | 无业务参数 | 短快照 |
| `advance_cooking_stage` | 明确目标阶段 | `task_progress` |

所有组件使用 `kind` 鉴别，结构为 `id/kind/title/revision/editable/data`；数据来自真实领域投影。模型不能提供 HTML、任意 schema、菜谱步骤或营养内容。偏好字段为 `ingredient_terms`、`exclude_ingredient_terms`、`servings`、`max_total_time_min`、`taste_tags`、`available_tools`、`dietary_notes`、`allow_estimated`。

短快照包含当前完整偏好、所选 IDs、当前菜谱/步骤、简短组件状态和最近20条操作。历史偏好操作仅记改变了哪些字段，当前偏好值是唯一依据，避免重复长文本挤满上下文。完整用量、营养、warnings 和源快照保留在界面投影与任务原件中。

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

支持 `update_preferences`、`select_recipes`、`open_recipe_detail`、`change_servings`、`check_shopping_item`、`check_prep_item`、`set_active_recipe`、`set_active_step`、`complete_step`、`change_stage`、`start_timer`、`pause_timer`、`resume_timer`、`cancel_timer`。各动作有不同的严格 payload；未知字段、任意 ID、非法数字和类型被拒绝。

偏好更新是 Partial，不会清掉未提交字段。直接传已验证的模型对象，或者使用 `model_dump(exclude_unset=True)`；不要把所有未提交默认值展开成显式 `null`。可空字段可以明确清空，列表字段使用 `[]` 清空。

每个成功命令增加 `revision`，同一 `action_id` 与相同内容重放不再修改；同 ID 不同内容返回 `action_id_conflict`。`base_revision` 过期返回 `revision_conflict`，替换后的组件 ID 返回 `stale_view`。响应携带当前可信界面；调用方应让用户复核后重新提交，不能自动重试旧操作。幂等记录保留最近20条；超过该窗口的旧操作仍被旧修订号拒绝。

## 业务边界

- 阶段：`idea → planning → shopping → prep → cooking → completed`，支持明确回退；不能跳过选菜和备料校验。`shopping` 是缺料清单，`prep` 是逐项准备确认。
- 每个会话一个当前厨房任务。选菜冻结完整真实原件，来源更新不会偷偷替换进行中的任务。原件只保留业务白名单，不保存 `raw_source_json`、凭据或导出整包。
- 食材稳定 ID 为 `<recipe_id>:ingredient:<原ordinal>`；步骤 ID 为 `<recipe_id>:step:<原列表序号>`，从0开始。指令与顺序沿用源菜谱。
- 源数据没有可靠的必选/可选字段，因此全部食材需要明确确认 `ready` 或用户主动 `skipped`；界面说明该资料边界，不声称已证明食材可省略。
- 浏览当前步骤不会把步骤标成完成；必须 `complete_step`。所有已选菜谱的步骤都 done 才允许 completed。
- 烹饪中不允许替换餐单、修改备料或份量。回退后改变人数会使该菜谱的准备和步骤重新待确认，并取消其关联计时。
- 份量换算仅针对已知数字、可靠人数基准和 `scalable=true` 的项；不猜单位、不把“适量”转零、不倍增不可缩放调味料。营养按单项贡献重算，缺信息保持未知，人数估算继续传递标记。
- 购物清单只合并同 ID、同标准形态、同单位且数值可可靠求和的条目；未知用量分别展示。购物勾选与备料确认分开。
- 计时保存 UTC 截止时间；暂停保存剩余秒数，恢复生成新截止时间，读取时依据真实时间判断到期。UI 的直接输入属于用户指定；模型的 `basis=user` 需与可信用户文本明确时长一致，`basis=recipe_text` 需源步骤唯一明确时长，不猜范围或多阶段时间。无后台通知。

旧版功能考察与本轮边界见 [迁移矩阵](docs/legacy-feature-matrix.md)。

## 验证

测试使用真实检索详情形状的假数据和原子 host 回调，不调用模型或业务数据库。覆盖闭合输入、Partial 偏好、CAS/幂等/旧视图、源版本变化、冻结任务、并发修改、合并/换算/未知值、阶段门槛、步骤确认、计时恢复和模型上下文容量。

```powershell
python -m pytest packages/chefmate-workflow/tests -q -p no:cacheprovider
```
