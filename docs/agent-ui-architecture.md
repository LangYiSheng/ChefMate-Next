# 智能体界面与厨房任务架构

用户使用自然语言和可操作的界面共同表达需求。模型选择展示哪一种界面、调用哪项业务能力；组件定义、业务事实和状态变更由服务端验证。前端以白名单 registry 渲染安全 DOM。

2026-10-14：总体阶段改为模型根据用户需求管理的会话状态。页面只显示阶段标记，HTTP 层拒绝客户端 `change_stage`；食材确认和实际步骤完成仍是独立的用户事实输入。工作区分类切换、分页清单、逐步阅读和摘要入口避免重复整张卡片。执行过程有单行/展开两种视图，只使用公开操作文本和真实工具事件，不展示隐藏推理。

## LangChain 的实际职责

第一版仅使用 `ChatOpenAI`、LangChain 消息类型和 schema 转换，模型/工具循环是手写的。当前使用标准 `langchain.agents.create_agent`，其 LangGraph runner 负责模型与 ToolNode 循环、并行工具执行和流式生命周期。

`chefmate-agent` 保留一个有界 middleware 与 JSON/SSE 适配器：校验完整原生调用、执行预算、食材和菜谱引用、界面动作、取消及失败历史；每次模型决策按最新任务阶段筛选应用工具。应用继续持有唯一的成功历史，未增加一份隐藏的框架会话数据库。

提示词负责角色、查询策略、工具使用和数据边界。备菜完成、步骤完成、版本冲突、时间来源和可缩放数值等规则由 `chefmate-workflow` 执行，不依赖模型遵守一句文字约定。

参考官方 [Agent](https://docs.langchain.com/oss/python/langchain/agents)、[Middleware](https://docs.langchain.com/oss/python/langchain/middleware)、[Tools](https://docs.langchain.com/oss/python/langchain/tools) 和 [Streaming](https://docs.langchain.com/oss/python/langchain/streaming)。本次通过 Context7 CLI 核对这些当前接口。

## 四层能力

| 层 | 职责 |
|---|---|
| 检索 | `find_ingredients/search_recipes/get_recipe/get_search_options` 只读真实源库，保留营养估算、未知值与数据快照 |
| 工作流 | 独立任务菜谱原件、餐单、份量、购物/备菜、步骤和计时；存储由应用注入 |
| Agent | 标准框架执行，按阶段绑定业务工具和 Skills，提供完整 JSON 历史及事件 |
| Web 应用 | 单会话运行、SQLite 原子聚合、HTTP/SSE、界面动作和组件 registry |

界面工具有明确参数，如 `show_preference_form`、`show_recipe_choices`、`show_recipe_details`、`show_prep_checklist`、`show_cooking_guide`。菜谱 ID 和源快照必须来自真实工具结果或已保存任务，模型不能生成任意 HTML、JS、配方或数据库查询。

支持的组件为 `preference_form`、`recipe_choices`、`recipe_detail`、`meal_plan`、`shopping_list`、`prep_checklist`、`cooking_guide`、`timers` 和 `task_progress`。工具输出完整的已验证展示动作给界面，模型历史只接收组件回执和短任务快照。

## 修改表单后模型如何知道

1. 每个会话拥有独立工作区，带 `revision`。组件有稳定引用和所属版本。
2. 界面操作携带 `action_id/base_revision/view_id/type/payload`。表单提交只发送改过的字段。
3. 服务端校验动作、菜谱/步骤/食材引用、来源、阶段和版本，以单次原子更新保存任务。重复操作幂等，冲突要求复核，不覆盖新数据。
4. 下一条自然语言先自动保存偏好表单的局部修改（菜品选择仍须明确确认），再取得最新短 `UISnapshot`，附当前偏好、已选菜谱、勾选和步骤/计时状态、最近操作。保存失败或版本冲突时保留草稿和消息，待用户复核。
5. 后端重新从自己的聚合构造模型上下文，客户端不能通过伪造快照把虚构菜谱变成事实。原用户文字保持原样，快照作为结构化元数据和明确的数据上下文进入模型。

例如用户在表单中填写“虾、30分钟、2人”，下一条只说“按刚填的找几个”即可。模型读取结构化条件后查询并展示菜品选择。用户确认菜谱以后，备菜勾选与步骤按钮直接保存状态；随后说“还有什么没准备好”时，模型读取最新的实际进度。

## 并发、历史和取消

业务操作与 Agent 进度都使用同一个会话聚合的原子 mutation，避免旧进度快照覆盖新表单。每轮业务 handler 使用明确的 `ToolExecutionContext` 和私有 scoped service；host object 不进入模型提示或历史，不使用跨会话共享的可变任务对象。

用户在模型运行中修改界面后，本轮旧版本写入会被拒绝。模型需读取新工作区后继续。停止取消后，迟到的工具线程不能继续写任务；已经原子保存的用户操作或完整业务操作仍保留，下一轮快照反映真实结果。

历史消息中的组件保存当时的展示结果，以摘要入口呈现并打开当前工作区。当前工作区渲染当前版本。候选从当前源库取详情时校验数据快照；已经进入任务的菜谱以冻结原件继续执行，源库重导入不能悄悄改变正在做的配方。

SSE 事件带 `session_id/turn_id/sequence`。断开视图不停止任务，恢复时读持久化状态。中间过程文字与最终回答分别呈现，输入法、草稿、手动折叠和会话切换由前端控制器维护。

## Skills

应用显式启用三个厨房 `SKILL.md` 目录：选菜规划、备菜和烹饪指导。框架提示只列名称和简介；模型调用 `load_skill(name)` 时才读取正文。页面区分可用技能与当前轮实际加载记录。

加载器校验名称、目录、内容版本和读取预算。Skills 提供操作指南，不能授予任意文件、shell、SQL 或额外副作用能力。业务 schema、版本与阶段检查始终执行。

## 对参考项目的采用

旧版 ChefMate 提供任务阶段、任务菜谱原件与备料/步骤的业务经验；已验证和待迁移功能列在[旧版迁移矩阵](../packages/chefmate-workflow/docs/legacy-feature-matrix.md)。其客户端覆盖式勾选、非持久计时和初始阶段固定工具集等限制在新版处理。

LanCher Code / WebUI 提供过程与结果分离、工具调用配对、历史绑定快照、原子状态、明确停止与 Skills 状态展示的设计参考。新版不依赖它们的 TUI、文件权限、MCP 进程或自定义智能体执行引擎。

本轮聚焦可运行的界面工具、快照反馈和厨房主流程。旧版账号、图片食材识别、语音输入和受控长期记忆有清晰接入位置，尚未在这轮复制。
