# ChefMate Agent

独立、可注入的智能体核心。LangChain 标准 `create_agent` 承担模型循环、ToolNode 并行工具执行与流式生命周期；一个 middleware 和 JSON/事件适配器负责业务边界。四个菜谱检索工具保持只读；界面与工作流工具由应用显式注入。Web 会话、SQLite、HTTP、业务工作区与界面属于应用。

## 调用接口

```python
import asyncio
import os

from chefmate_agent import AgentConfig, ChefMateAgent
from chefmate_retrieval import RetrievalService
from chefmate_storage import RecipeStore

service = RetrievalService(RecipeStore(os.environ["CHEFMATE_DATABASE_URL"]))
agent = ChefMateAgent(
    service,
    AgentConfig(
        model=os.environ["OPENAI_MODEL"],
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL"),
    ),
)

async def main():
    result = await agent.run_turn([{"role": "user", "content": "我想吃虾，半小时以内"}])
    if result.status == "completed":
        print(result.assistant_text)
        # 完整的 OpenAI 风格 JSON 历史，可以直接保存为 JSON。
        following = await agent.run_turn(result.messages + [{"role": "user", "content": "第一道怎么做"}])
        print(following.assistant_text)
    else:
        print(result.error.message if result.error else "本轮已停止")

asyncio.run(main())
```

核心不读取 `.env` 或清洗平台配置文件。调用方提供模型和数据库配置。未配置真实模型 API Key 时构造失败，生产没有假模型降级。OpenAI 兼容接口通过 `ChatOpenAI` 的 `base_url` 连接，`stream_usage=False` 避免要求供应商实现使用量流扩展。工具传输默认使用完整非流式响应（`disable_streaming="tool_calling"`），每个供应商需要实测后再启用 `AgentConfig(stream_tool_calls=True)`。旧含 `$ref` 的 schema 曾出现 DSML 泄漏；展开 schema 后，已使用生产四工具和真实13个食材候选验证续轮流式返回标准 `search_recipes` 调用，未出现 DSML，本机测试应用可明确启用流式。该次实测约72秒才收到首块、87秒完成，支持单次120秒预算；这属于一次验证结果，不保证每次延迟。工具运行状态和结果事件继续实时更新，回答通过 `assistant_delta` 事件返回。`model_kwargs` 只传调用方明确给出的 JSON 参数，不按模型名猜测 thinking 或其他能力。

默认工具 schema 来自 `chefmate-retrieval` 的 Pydantic 请求模型，本地强制校验未知字段、类型、枚举、范围和嵌套参数。工具包括 `find_ingredients`、`search_recipes`、`get_recipe`、`get_search_options`，没有任意 SQL、文件或外部写入能力。检索服务通过同步协议注入，异步调用用 `asyncio.to_thread` 包装。

发送给模型的 schema 使用 LangChain 官方 `convert_to_openai_tool(PydanticModel)` 生成，递归内联嵌套 `$ref` 并移除 `$defs`，避免把预格式化 function dict 原样发送而跳过适配。内部请求仍由原始 Pydantic 模型验证，嵌套字段和参数约束保持完整。实际四工具与完整提示词已验证能返回标准 `find_ingredients` 调用；端到端多轮效果继续由测试应用验证，单个请求成功不代表所有模型行为都已经验证。

当前实现将 Pydantic 模型交给 `StructuredTool`，标准 runner 绑定模型时走同一官方 schema 转换。middleware 使用 `abefore_model/aafter_model/awrap_model_call/awrap_tool_call` 检查预算、完整调用批次与数据证据，并按当前阶段筛选应用工具。没有手写模型—工具循环，也不另启智能体框架或隐藏 checkpointer；应用持有唯一的持久化历史。

## 界面和工作流注入

```python
from chefmate_agent import InjectedToolSpec, ToolExecutionContext

def show_preferences(request: dict, context: ToolExecutionContext):
    # 应用拥有此服务及所有UI/业务模型；调用标识可用于幂等操作。
    return context.host_context.execute_tool(
        "show_preference_form", request, operation_id=context.call_id
    )

spec = InjectedToolSpec(
    name="show_preference_form",
    description="展示已保存值的偏好表单",
    args_schema=ApplicationPreferenceRequest,
    handler=show_preferences,
    response_model=ApplicationToolResult,
    allowed_stages=("search",),
)
agent = ChefMateAgent(
    service, config, extra_tools=[spec],
    ui_context_model=ApplicationUISnapshot,
    action_model=ApplicationUIAction,
)
result = await agent.run_turn(
    messages, on_event=callback,
    ui_context=server_verified_snapshot,
    tool_context=scoped_workflow_service,
)
```

以上 `Application*` 模型由应用提供，核心不定义菜谱选择、备菜或计时器业务字段。`ToolExecutionContext` 为 frozen dataclass，含本次工具调用的 `ui_context` 副本、`host_context`、标准 JSON `messages` 和 `call_id`。host object 只进入工具 handler，不序列化到模型提示、历史或结果，不使用跨会话 contextvar 或共享业务单例。同步 handler 用 `to_thread`，异步 handler 直接 await。

成功工具返回的 `actions` 必须通过外部 `action_model` 验证；完整动作通过 `ui_action` 事件与 `result.actions` 交给界面。模型只接收紧凑 `ui_snapshot` 和组件回执，不接收完整 workspace/views 渲染对象。新快照通过 `ui_context_model` 验证后更新本轮状态，下一模型轮的工具可见性根据最新 `stage` 变化。`allowed_stages=None` 表示所有阶段可见；该字段是推荐和输入量控制，业务服务仍必须执行自己的权限、revision 与阶段检查。

已确认餐单的备菜与烹饪用量优先使用最新快照中已换算的 `amount_text/amount_value/unit`，并区分当前 `target_servings` 与原始 `original_servings`。原始菜谱 `raw_text` 和步骤中的数字仅作来源引用，不能覆盖当前用量或再次乘人数倍率。应用必须把当前用量保留在紧凑快照中；缺失时智能体先读取工作区，仍无法可靠换算时说明未知。提示词约束不保证模型每次都遵守，真实流程仍需检查回答与工作区一致性。

本轮初始快照保存到相应 `user.ui_context`，用户原始 `content` 不变。LangChain `HumanMessage.additional_kwargs` 承载该元数据，序列化时恢复标准 JSON；当前快照以单独 system context 传入，明确属于数据。历史只附少量用户操作摘要，单个界面快照上限24000字符。

## 渐进 Skills

`SkillRegistry([explicit_skill_directory, ...])` 仅发现显式启用目录中的 `SKILL.md`。初始化读取并校验名称/简介，system prompt 只列元数据；`list_skills` 列元数据，`load_skill(name)` 才把正文放入工具结果。默认没有启用目录。

名称限制为小写字母、数字、下划线或连字符；路径解析必须保持在已启用目录内，不接受文件路径，不读取辅助文件，不运行脚本。YAML frontmatter 使用 `safe_load`，文件、元数据和整轮正文分别有预算；文件变更后返回 `skill_changed`，要求刷新配置。Skills 仅提供指导，不添加 shell、任意 SQL 或任意文件工具，也不能授予额外权限。

## 历史与结果

`run_turn(messages, on_event=None, cancel_event=None)` 返回可 `model_dump(mode="json")` 的 `AgentTurnResult`：

| 字段 | 含义 |
| --- | --- |
| `status` | `completed`、`cancelled` 或 `error` |
| `assistant_text` | 本轮完整回答；中断或失败为空 |
| `messages` | 完整、可序列化的对话历史，assistant/tool 消息完整成对 |
| `tool_calls` | 本轮已完成工具调用，包括 `call_id/name/arguments/result` |
| `recipes` | 最新成功查询的实际菜谱摘要，数据快照失效或查询错误时清空 |
| `selected_recipe` | 实际 `get_recipe` 返回的详情；新的搜索成功、零结果或失败都会清空旧详情 |
| `snapshot` | 对应 `data_version/imported_at`，可跟踪数据替换 |
| `error` | 稳定的 `code/message/retryable`，不暴露原始供应商错误或凭证 |
| `actions` | 已由应用动作模型严格验证的实际界面动作，失败/停止时仍可描述已经完成的业务效果 |

调用方传入当前用户消息，成功后保存 `messages`。失败、超时和取消返回原有输入历史，不加入未完成的 assistant/tool 片段。每轮新输入历史超出预算时仅移除完整的旧用户轮次，绝不把工具调用与结果拆开；当前轮增长超过预算会明确报错。

食材选择必须出现在先前真实 `find_ingredients` 返回中，菜谱详情 ID 必须来自先前真实搜索或详情，并且传入相同的 `expected_snapshot`。历史可以提供这些证据，因此“第一道怎么做”和“再限制到半小时”能延续上一轮。换数据快照后清空旧引用，工具返回 `snapshot_changed` 时必须重新查候选。

自然语言执行策略：用户说“我想吃虾”或“找番茄做的菜”时，先获取候选，再默认覆盖符合常见意图的真实食材并执行 `search_recipes`，展示实际菜谱。用户无需先选数据库食材、虾种或补全全部可选条件；取得结果后再允许收窄口味或时间。只有候选不足、实质歧义、条件冲突等情况才先追问。问候和普通解释可以直接回复，不通过代码强制所有回答先查菜谱。该策略由真实模型端到端场景验收，离线脚本测试只能验证工具与历史协议。

初始推荐在用户和历史没有明确排序、数量要求时，智能体默认按 `total_time_min` 升序取8条；工具请求模型的默认值和最大范围保持完整。回答编号与最新搜索 `items` 顺序一致，让“第二道”对应同一张卡片。更多结果使用相同条件和排序分页，新筛选条件从第一页开始。

## 事件

`on_event` 可为同步或异步回调。每个事件都是 JSON 对象：

```json
{"type":"status","status":"thinking","message":"正在理解需求"}
{"type":"tool_start","call_id":"call_1","name":"find_ingredients","arguments":{"queries":["虾"]}}
{"type":"tool_end","call_id":"call_1","name":"find_ingredients","arguments":{"queries":["虾"]},"result":{"status":"ok"}}
{"type":"assistant_delta","text":"查到","delta":"查到"}
{"type":"status","status":"completed","message":"查询完成"}
```

`assistant_delta` 属于临时流式内容；失败或停止后界面应撤销临时回答，使用最终 `AgentTurnResult` 决定是否保存。`tool_end` 包含输出预算内的真实结果；巨大的原始导出对象和连接秘密字段不会送给模型或事件消费方。被截短的字段带标记，过大到无法合理裁剪时返回 `output_too_large`，要求缩小查询。

## 生命周期和边界

- 默认每轮最多 10 次模型请求、16 次工具调用，单个模型请求 120 秒、整轮 300 秒；SDK 默认不自动重试，避免慢请求连续重试耗尽整轮预算。调用方可用 `model_timeout_s/max_model_retries/turn_timeout_s` 在验证范围内明确配置。
- 工具结束后下一次模型请求开始时发出“正在分析查询结果”，让界面正确展示等待阶段。
- 流式工具参数必须是完整 JSON，整批名称/标识/参数结构验证通过后才执行；未知工具或半截参数不会执行任何调用。
- 模型以 `finish_reason="tool_calls"` 结束但没有提供完整标准调用时，返回 `incomplete_tool_batch`，不把引导语当作完成回答。
- DSML 等已知工具协议泄漏到普通文本时返回 `tool_protocol_leak`，不执行文本中的工具、不提交回答历史；流式缓冲防止拆分标记泄漏到界面。不会以宽松文本解析冒充标准工具协议。
- 食材和营养查询由数据库执行；候选选择与自然语言理解由模型承担。系统提示要求保留多轮硬条件、区分 USDA 参考与食材身份、说明估算、可选配料和原文依据。
- 本地 schema 或引用校验失败作为工具结果反馈模型进行修正；持续重试仍受预算控制。
- `cancel_event.set()` 关闭当前异步模型流并停止本轮推进。已启动的同步数据库读取无法通过 Python 线程强制停止，仍由存储包的只读事务和 statement timeout 收束；中断结果不会写入对话历史。
- 外部任务取消保留 `asyncio.CancelledError`，由调用方处理。共享 agent 不持有会话状态；应用负责同一会话单运行和并发保护。
- 工具输出与用户内容都属于不可信数据。提示词要求忽略数据里的指令；模型回答的语义仍应由真实模型端到端测试检验，离线脚本测试不能证明真实模型选择效果。

## 验证

在仓库测试应用已安装本地包的环境中：

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider packages/chefmate-agent/tests
apps/chefmate-playground/.venv/Scripts/python.exe -m ruff check packages/chefmate-agent
```

测试通过显式注入 scripted model 检查完整批次、schema 修复、引用证据、多轮 JSON 历史、超时、取消、预算和脱敏；这只是确定性离线测试路径。实际菜谱和模型选择应使用测试应用连接当前 PostgreSQL 与已配置模型验证。

实现参考最新 [LangChain create_agent](https://docs.langchain.com/oss/python/langchain/agents)、[middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)、[工具与运行上下文](https://docs.langchain.com/oss/python/langchain/tools)、[渐进 Skills](https://docs.langchain.com/oss/python/langchain/multi-agent/skills-sql-assistant)、[流式消息](https://docs.langchain.com/oss/python/langchain/streaming)。标准 runner 管理执行，middleware 与应用边界适配控制完整批次验证和事务性历史。
