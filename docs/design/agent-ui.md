# 智能体界面与厨房任务规范

用户通过自然语言和可操作界面共同表达需求。模型选择工具与展示意图，服务端核对组件结构、业务事实、权限与状态变化，React 前端按已知协议渲染受控组件。
从完整使用流程理解这些规则，见[项目导览](../guide/overview.md)；字段与端点分别见[工作流](../reference/workflow.md)和[核心 App](../reference/workbench.md)。

## 职责与实现位置

| 层 | 负责什么 | 主要源码 |
| --- | --- | --- |
| 检索 | 四个只读工具、真实候选、查询条件、营养标记与数据快照 | [retrieval/service.py](../../packages/chefmate-retrieval/src/chefmate_retrieval/service.py) |
| 工作流 | 冻结原件、餐单、份量、备料、步骤、计时与版本 | [workflow/service.py](../../packages/chefmate-workflow/src/chefmate_workflow/service.py) |
| Agent | 标准运行时、动态工具、证据与预算、完整历史与事件 | [agent/core.py](../../packages/chefmate-agent/src/chefmate_agent/core.py) |
| Web 应用 | 依赖组装、单会话轮次、SQLite、HTTP/SSE 与个人资料 | [App](../../apps/chefmate-playground/src/chefmate_playground/app.py) · [routes](../../apps/chefmate-playground/src/chefmate_playground/routes/) |
| 浏览器 | 草稿、流式显示、受控操作、导航与焦点 | [App.tsx](../../apps/chefmate-playground/frontend/src/App.tsx) · [会话状态](../../apps/chefmate-playground/frontend/src/runtime/store.ts) · [厨房组件](../../apps/chefmate-playground/frontend/src/features/workspace/views.tsx) |

入口负责组装；拆分后的领域规则仍各有唯一归属：

- Agent 的 [history.py](../../packages/chefmate-agent/src/chefmate_agent/history.py) 维护历史与增量快照，[evidence.py](../../packages/chefmate-agent/src/chefmate_agent/evidence.py) 检查引用，[tool_execution.py](../../packages/chefmate-agent/src/chefmate_agent/tool_execution.py) 检查技能、阶段、批次与结果，[provider.py](../../packages/chefmate-agent/src/chefmate_agent/provider.py) 守住供应商参数完整性。
- Workflow 的 [service.py](../../packages/chefmate-workflow/src/chefmate_workflow/service.py) 保留原子命令与版本检查，[kitchen.py](../../packages/chefmate-workflow/src/chefmate_workflow/kitchen.py) 管理领域命令，[projection.py](../../packages/chefmate-workflow/src/chefmate_workflow/projection.py) 生成用量与组件，[tool_commands.py](../../packages/chefmate-workflow/src/chefmate_workflow/tool_commands.py) 和 [user_actions.py](../../packages/chefmate-workflow/src/chefmate_workflow/user_actions.py) 分别接收模型与用户意图。
- App 的路由按会话、工作区、个人资料、菜谱库、系统与诊断分开；浏览器对话按网络、会话、轮次、草稿和渲染分开，具体地图见[开发指南](../guide/development.md)。

## 标准 Agent 的边界

使用 `langchain.agents.create_agent`，标准 LangGraph 运行时负责模型与工具循环、工具执行及流式生命周期。
`ChefMateMiddleware` 在模型请求和工具调用边界核对预算、原生调用批次、状态与数据证据，按最新任务阶段筛选应用工具。
应用持有唯一的已提交成功历史，框架没有另建一份隐藏会话数据库。[Agent middleware](../../packages/chefmate-agent/src/chefmate_agent/runner.py)

提示词表达查询策略、角色与领域指导；备料门槛、步骤完成、版本冲突、时间来源和可缩放数值由程序执行。
接口分工可对照官方 [Agent](https://docs.langchain.com/oss/python/langchain/agents)、[自定义 middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)、[Tools](https://docs.langchain.com/oss/python/langchain/tools) 与 [Streaming](https://docs.langchain.com/oss/python/langchain/streaming)。

## 组件和展示协议

界面工具有闭合参数，例如 `show_preference_form`、`show_recipe_choices`、`show_recipe_details`、`show_prep_checklist`、`show_cooking_guide`。
菜谱 ID 和快照来自真实工具结果或已保存任务；模型不能提供任意 HTML、JavaScript、数据库查询或把自写配方当成库中事实。

| 类别 | 当前结构 | 渲染与状态归属 |
| --- | --- | --- |
| 厨房视图 | `preference_form/recipe_choices/recipe_detail/meal_plan/prep_checklist/cooking_guide/timers/task_progress` | 工作流 `ViewComponent` 与厨房 registry |
| 对话饮食 | `food_log/nutrition_summary` | 已验证饮食工具结果、个人记录和独立渲染入口 |
| 个人菜谱审核 | 个人菜谱记录（工具结果 `draft`） | 权威读取与独立审核卡，不扩充厨房视图枚举 |
| 提问等待 | `pending_question/last_answer` | 持久工作区中的问题与答案，独立于厨房视图枚举 |

厨房 registry 的枚举只覆盖对应领域，不能把其数量当作全应用组件总数。
饮食内容使用 [FoodCards.tsx](../../apps/chefmate-playground/frontend/src/components/FoodCards.tsx) 等入口，产品归属由[组件注册表](../../apps/chefmate-playground/src/chefmate_playground/components.py)声明。
模型历史只接收工具回执、必要个人记录和短任务状态，完整厨房 `views` 用于界面渲染。

工作流工具的 `display=inline|workspace|none` 决定展示位置，`UIAction.placement` 承接意图。
读取工作区返回的 `views` 不自动变成本轮聊天动作。前端限制一次展开的核心内容，按当前组件 ID 绑定交互，避免重复铺满工作区。
新增交互内容在模型本轮完整输出结束后展示；保存确认成功后才自动续轮，保存失败或版本冲突保留草稿。

<a id="修改表单后模型如何知道"></a>

## 从表单到可信上下文

1. 每个会话拥有独立工作区，持久保存 `revision`、组件引用和任务状态。
2. 用户动作携带 `action_id/base_revision/view_id?/type/payload`；局部表单只发送真正修改字段。
3. 服务端核对参数、引用、来源、阶段和版本，再用一次原子 mutation 保存。重复操作幂等，冲突要求复核。
4. 下一条自然语言先保存偏好表单的局部修改，再发送消息；选菜仍须明确确认。失败时保留草稿与待发消息。
5. 后端从自身聚合重新构造模型状态，客户端快照不能把虚构菜谱或未保存勾选变成事实。原用户文字保留，可信状态作为明确的数据上下文进入请求。

例如表单填写“虾、30 分钟、2 人”，下一句“按刚填的找几个”可引用已保存条件。
备料勾选和步骤确认直接保存，下一轮“还有什么没准备好”读取的是实际进度。[宿主工作区边界](../../apps/chefmate-playground/src/chefmate_playground/interactions.py)

首次提供完整快照，后续状态可用差异表达变化与删除；当前用量、目标份量和可执行动作必须保留。
历史预算只裁剪完整旧人类轮次，不能拆散助手工具调用和对应结果。[历史投影](../../packages/chefmate-agent/src/chefmate_agent/history.py)

## 阶段、动作和版本

阶段为 `idea/planning/prep/cooking/completed`。模型根据用户意图调用业务能力，服务端检查相邻转换和前置条件。
页面只显示阶段和执行入口；HTTP 明确拒绝用户直接 `change_stage`，用户对真实食材、步骤和整餐完成的确认继续独立保存。
选菜使用 `select_recipes`，可直接引用真实检索结果，无须先展示候选；建立餐单后进入 planning。

`TOOL_CONTRACTS` 统一声明工具说明、schema、阶段、前置条件、副作用、可见性与自动展示。
快照的 `current_actions/allowed_transitions/blockers` 由相同规则计算，模型绑定可执行子集，执行器再次校验。[工具合同](../../packages/chefmate-workflow/src/chefmate_workflow/contracts.py)

业务 `revision` 只随真实业务字段变化增加；`presentation_revision` 管理纯视图替换和展示变化。
展示菜谱可能登记候选，展示步骤可能切换活动位置，必须按实际效果判断是否改变业务版本。
用户动作核对业务版本与当前 `view_id`；相同 `action_id` 和相同内容不重复修改，同 ID 不同内容冲突。

模型批量写入使用必填 `expected_revision`。偏好使用 `changed_fields` 掩码，未列字段即使被框架填入也不保存。
省略表示保留，清空按字段契约使用 `null`、`[]` 或空串；不支持的 null 和陌生 ID 在保存前拒绝。
修改、展示和等待操作按依赖逐次调用，先获得真实结果与新版本；独立只读查询可由运行时并行执行。

## 待回答问题

`ask_user` 必须单独调用，保存题目、至多六个不同选项、是否允许自填和多选。
通过 `StructuredTool(return_direct=True)` 在完整 `ToolMessage` 后结束本轮，返回 `awaiting_user`，不维持一个等待中的后台模型请求。

用户提交 `answer_question`，按问题 ID、业务版本、当前选项与自填规则校验，保存 `last_answer` 并清除待答状态。
应用随后使用稳定 `answer:<question_id>` 请求标识自动续轮；动作与消息分别幂等。
未回答时拒绝新的普通轮次，刷新、切换或断线仍可恢复问题。[提问与动作测试](../../apps/chefmate-playground/tests/test_interactions.py)

## 并发、历史与取消

业务操作和轮次进度使用同一会话聚合的原子 mutation。每轮 handler 使用明确 `ToolExecutionContext` 与私有 scoped service，宿主对象不进入模型提示或历史。
用户在模型执行中修改界面后，旧版本写入被拒绝；模型先读取新工作区再按用户意图继续。
取消和轮次身份在写入事务中再次检查，迟到线程不能写回已经结束的任务。

成功的业务动作独立保留。生成失败或取消不会保存半截 assistant/tool 历史，也不会撤销已提交的用户操作和业务写入。
下一轮依据当前状态解释已经发生的事情，避免把历史回退描述成整轮外部效果回滚。

历史消息保留当时展示意图，仍对应当前组件的交互使用服务端新版本，被替换内容只保留打开当前任务的入口。
源库候选详情检查数据快照；已进入任务的菜谱使用冻结原件，源库重导入不能替换正在执行的配方。

SSE 活跃事件带 `session_id/turn_id/sequence`。离开视图只断开订阅，显式停止才取消任务；重新打开读取持久状态。
活跃任务属于当前服务进程，服务重启将未完成轮次标为中断；页面脱离不能被当作持久作业故障恢复保证。[轮次管理](../../apps/chefmate-playground/src/chefmate_playground/runtime.py)

浏览器使用 React 19.3.1 和 TypeScript 7，`ChefStore` 独立持有请求与会话状态，组件订阅不可变快照。切换会话和页面不会取消任务；流结束后重新 GET 已保存会话，断流后按各自会话轮询恢复。厨房状态与计时由独立 [WorkspaceStore](../../apps/chefmate-playground/frontend/src/features/workspace/store.ts) 管理，经适配器提供真实快照和已保存确认的下一轮请求。图片 Blob 保存在 IndexedDB，文字与续轮标识保存在浏览器本地存储。

<a id="系统组件"></a>

## 系统组件、Skills 与个人能力

不可变 `ComponentSpec` 统一声明组件 ID、中文名称、指引、工具归属、目标阶段、UI 类型与页面、依赖和工厂。
设置、调试、构建和执行权限从同一清单派生；新增工具漏归属、重复归属、阶段冲突或工厂漏工具会由构建与合同测试拒绝。

0.2.1 当前有七个系统组件：[组件清单](../../apps/chefmate-playground/src/chefmate_playground/components.py)。

| 组件 ID | 能力 |
| --- | --- |
| `meal-planning` | 选菜与餐单 |
| `pantry-preparation` | 备菜与准备 |
| `cooking-guide` | 烹饪引导 |
| `recipe-authoring` | 个人菜谱草稿、对话审核、修改与保存确认 |
| `app-preferences` | 白名单普通应用偏好的读取与修改 |
| `personal-memory` | 长期记忆与个人历史 |
| `food-journal` | 饮食草稿、个人摄入与营养统计 |

`SkillRegistry` 只负责按需读取显式允许的指南：先列简介，再按目录、内容版本和预算读取正文。
指南正文提供指导，执行权限由组件启用状态、阶段与业务条件核对。共用阶段工具按请求的目标阶段检查所属技能。
新能力建立自己的请求、工具、记录和 UI 边界，先用隔离依赖验证，再进行真实模型验收。[组件合同测试](../../apps/chefmate-playground/tests/test_components.py)

个人菜谱、收藏、制作历史、个人饮食和长期记忆保存在应用独立表，基础 PostgreSQL 库保持只读。
个人菜谱先生成带来源依据的草稿，用户核对发布后进入相同的检索引用和冻结流程。
长期记忆分为用户原文与助手摘要，当前明确需求优先，助手不能覆盖用户原文；按预算注入本轮，历史按需检索。

个人菜谱的 `create_recipe_draft/get_recipe_draft/revise_recipe_draft` 返回真实记录，修改保留同一 `recipe_id` 并核对 `revision`；份数、耗时、难度与精确克重受引用来源校验，未知项不补造。
对话审核卡按最高版本和最新所属消息去重，展示前 GET 当前记录；修改按钮只填写输入草稿，用户发送后才执行修订。直接保存携带已审核版本，冲突后显式重读，不自动重试发布。[草稿工具](../../apps/chefmate-playground/src/chefmate_playground/recipe_tools.py) · [审核卡](../../apps/chefmate-playground/frontend/src/components/RecipeReview.tsx)

`request_recipe_publication` 保存与草稿版本、会话和问题绑定的确认回执；`confirm_recipe_publication` 必须核对服务端真实用户答案，选择继续修改不会发布。
会话权威记录或工作区存在待答问题时，审核卡隐藏直接保存入口；已回答的问题按 `last_answer.question_id` 排除旧缓存，读取、保存或续轮未同步时仍禁用直接发布。发布准备失败只撤回仍匹配本次 ID 的新问题，不清除其他问题。[确认事务](../../apps/chefmate-playground/src/chefmate_playground/recipe_publication.py)

## 制作量与个人摄入

整餐 `meal_diners` 与每道菜 `target_servings` 分开保存，默认共享菜量可以逐菜调整。
`meal_id/started_at/completed_at` 描述真实烹饪；所有步骤完成后才允许 `complete_meal`，无真实时间的旧任务不补造日期。
业务保存后再同步制作历史，同步失败不能把已成功任务动作伪报为失败；读取可重试制作记录同步，不能借此补登记个人摄入。

照片或文字记餐先产生草稿，用户确认后才计入个人统计。
启用饮食组件并发生真实完成事件时，已知单人餐按明确产品规则将本次冻结配方整份自动登记，保留估算标记并允许更正。
多人或人数未知时等待本人逐菜比例；不能凭人数均分，也不能把整桌制作量当作个人摄入。

`get_cooked_meal_intake` 在本轮读取来源与登记版本，`record_cooked_meal_intake` 核对逐菜身份、本人原话、实际比例和当前版本。
营养由服务端按冻结资料计算，未知值为空；同餐更正更新稳定记录 ID。查看历史不回填，关闭组件停用工具与自动登记。[登记实现](../../apps/chefmate-playground/src/chefmate_playground/cooked_intake.py) · [登记回归](../../apps/chefmate-playground/tests/test_cooked_intake.py)

## 设置与工作区导航

设置通过 `/settings` 路由在主工作区显示，直接打开该路径使用相同页面、字段、主题与保存协议。
设置、菜谱中心和食光手记切换时保持对话与输入草稿；所有离开路径先经过未保存修改保护。
中心中的发起聊天动作创建新会话，原会话草稿可恢复；查看、收藏和编辑保持所属中心行为。

设置内容使用顶部页签，宽内容区按当前可见节点生成右侧索引；设置、个人中心和诊断的选择控件共用 [Select](../../apps/chefmate-playground/frontend/src/components/ui/Select.tsx)，普通输入由共享样式管理。[视觉与控件规范](ui.md#控件和保存反馈)

模型、组件与运行设置核对完整配置版本，校验、构建新 Agent 并原子写入后应用；活跃对话限制相关配置修改，计时策略允许实时保存。
密钥不可回读，同端点可沿用，更换端点必须明确提供；分类分别保留草稿与读取版本，其他分类刷新不能解除已发生的冲突。
普通偏好走独立 `/api/ui-preferences` 版本协议，服务端保存并由浏览器投影到本地外观；本机开发者设置继续单独保留。[设置参考](../reference/workbench.md#配置来源与默认值)

`app-preferences` 仅提供 `get_app_preferences/update_app_preferences`，修改白名单为 `theme/light_palette/dark_palette/font_pairing/density/inline_limit/show_estimates/reply_suggestions/reply_suggestion_count`。
修改只应用明确传入字段并检查版本，未知字段拒绝；不能通过这个工具读取或修改模型、密钥、组件、开发者、系统或运行参数。前端对当前执行与服务恢复重新 GET 权威值，历史工具快照不能覆盖当前偏好。[偏好合同与服务](../../apps/chefmate-playground/src/chefmate_playground/ui_preferences.py) · [浏览器同步](../../apps/chefmate-playground/frontend/src/features/preferences/client.ts)

专注厨房由常驻普通场景承载，进入与退出时主区、已打开右栏和厨房场景协同平移；非活动场景 `inert/aria-hidden`，输入草稿与后台任务保持原归属，减少动态效果时即时切换。[场景实现](../../apps/chefmate-playground/frontend/src/features/workspace/index.tsx) · [应用框架](../../apps/chefmate-playground/frontend/src/App.tsx)

## 参数完整性、恢复与观测

供应商适配层在发布调用前检查完整 JSON、身份和成功终态，不用部分 JSON 容错执行业务。
相同失败有界恢复，持续无进展后进入无工具收尾；推荐回复是非阻塞辅助能力，只尝试一次，失败使用保守草稿。
调试保存脱敏参数完整性摘要，不记录原始 SSE 参数片段、图片字节、密钥或隐藏推理。

用量账本按会话与轮次幂等记录，独立于可删除聊天，保留失败和取消前已观测的供应商调用。
在实际工具绑定边界捕获系统、对话、工具回调、工具定义、可信 UI、记忆和指南；字符数对应捕获内容，token 分类明确为估算，计费用量以供应商报告为准。
公开过程与最终回答分开；开发者诊断只展示真实留存请求，缺失历史不得用当前配置重建冒充。[观测实现](../../apps/chefmate-playground/src/chefmate_playground/debug.py)

普通模式仅显示助手位置的简短活动文字与必要错误、停止和重试。开发者模式才显示回复执行时间线、侧栏上下文分类估算彩条、调试入口和带版本时间的开发水印。
调试目录提供中文工具分组、搜索与可用状态，以及组件启用状态、依赖与工具归属；会话轮次和模型请求分别选择，按时间线或树视图展示系统、用户、助手、调用及结果，完整参数和内容按需展开。
供应商报告、部分已知用量与请求大小粗估分别标明；缺失指标不当作零，隐藏推理正文不进入展示。[开发者界面](../../apps/chefmate-playground/frontend/src/components/developer/Diagnostics.tsx)

## 来源与验证

旧版厨房任务经验与迁移范围保留在[迁移考察](../engineering/records/legacy-feature-matrix.md)，当前规则以这里的源码与回归为准。
账号和语音仍属扩展方向，现有图片能力来自配置的外部多模态模型。
工作流、Agent、宿主交互和个人记录分别有测试入口；完整浏览器与真实模型验收按日期记录在[工程档案](../engineering/README.md)。
