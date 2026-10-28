# ChefMate Agent

独立、可注入的智能体核心。LangChain 标准 `create_agent` 承担模型循环、ToolNode 并行工具执行与流式生命周期；一个 middleware 和 JSON/事件适配器负责业务边界。四个菜谱检索工具保持只读；界面与工作流工具由应用显式注入。Web 会话、SQLite、HTTP、业务工作区与界面属于应用。

先读[智能体导览](../guide/agent.md)了解完整任务，再用本页查注入接口、数据边界和事件字段。当前入口见[核心源码](../../packages/chefmate-agent/src/chefmate_agent/core.py)。

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

核心不读取 `.env` 或清洗平台配置文件。调用方提供模型和数据库配置。未配置真实模型 API Key 时构造失败，生产没有假模型降级。OpenAI 兼容接口通过 `ChatOpenAI` 的 `base_url` 连接，`stream_usage=False` 避免要求供应商实现使用量流扩展。工具传输默认使用完整非流式响应（`disable_streaming="tool_calling"`），每个供应商需要实测后再启用 `AgentConfig(stream_tool_calls=True)`。流式可用性取决于所配置服务的原生工具协议，连接测试与真实多轮任务需分别验证；历史供应商实测只对记录时点有效。工具运行状态和结果事件继续实时更新，回答通过 `assistant_delta` 事件返回。`model_kwargs` 只传调用方明确给出的 JSON 参数，不按模型名猜测 thinking 或其他能力。

默认工具 schema 来自 `chefmate-retrieval` 的 Pydantic 请求模型，本地强制校验未知字段、类型、枚举、范围和嵌套参数。工具包括 `find_ingredients`、`search_recipes`、`get_recipe`、`get_search_options`，没有任意 SQL、文件或外部写入能力。检索服务通过同步协议注入，异步调用用 `asyncio.to_thread` 包装。

发送给模型的 schema 使用 LangChain 官方 `convert_to_openai_tool(PydanticModel)` 生成，递归内联嵌套 `$ref` 并移除 `$defs`，避免把预格式化 function dict 原样发送而跳过适配。内部请求仍由原始 Pydantic 模型验证，嵌套字段和参数约束保持完整。四个真实工具及完整提示词的供应商行为需通过应用端到端验证，单个请求成功不代表所有模型行为都已经验证。

当前实现将 Pydantic 模型交给 `StructuredTool`，标准 runner 绑定模型时走同一官方 schema 转换。middleware 使用 `abefore_model/aafter_model/awrap_model_call/awrap_tool_call` 检查预算、完整调用批次与数据证据；注册 schema 保留用于历史校验，模型每轮按当前阶段和可用动作筛选工具，执行时再次校验阶段与权限。没有手写模型—工具循环，也不另启智能体框架或隐藏 checkpointer；应用持有唯一的持久化历史。

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
    allowed_stages=("idea", "planning"),
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

注入工具以模型原始 JSON 参数校验，再用 `exclude_unset=True` 传给应用；不会让框架补出的默认值把嵌套 patch 中未修改字段变成显式 `null`。服务端重验时仍补齐自己的默认展示参数，显式传入的空值与省略字段保持区别。

成功工具返回的 `actions` 必须通过外部 `action_model` 验证；完整动作通过 `ui_action` 事件与 `result.actions` 交给界面。模型只接收紧凑 `ui_snapshot` 和组件回执，不接收完整 workspace/views 渲染对象。新快照通过 `ui_context_model` 验证后更新本轮状态，下一次工具调用按最新 `stage` 校验。`allowed_stages=None` 表示所有阶段可执行；受限工具在其他阶段返回stage_conflict且不执行，业务服务继续执行自己的权限、revision 与阶段检查。

已确认餐单的备菜与烹饪用量优先使用最新快照中已换算的 `amount_text/amount_value/unit`，并区分当前 `target_servings` 与原始 `original_servings`。原始菜谱 `raw_text` 和步骤中的数字仅作来源引用，不能覆盖当前用量或再次乘人数倍率。应用必须把当前用量保留在紧凑快照中；缺失时智能体先读取工作区，仍无法可靠换算时说明未知。提示词约束不保证模型每次都遵守，真实流程仍需检查回答与工作区一致性。

本轮初始快照保存到相应 `user.ui_context`，用户原始 `content` 不变。LangChain `HumanMessage.additional_kwargs` 承载该元数据，序列化时恢复标准 JSON；真正请求模型前，middleware 将经服务器验证的状态追加到该条用户消息的文本后，标明“数据，不是指令”。第一次是完整快照，后续用户状态和工具回调使用差异快照。历史只附少量用户操作摘要，单个界面快照上限24000字符。

工作流统一使用备菜阶段：已有食材确认、缺料准备与备菜进度属于同一份清单。已有餐单时，整理或核对备料直接进入prep；食材未齐不阻止备菜，不先逐组询问才能展示清单。用户明确说拥有/备齐或选中对应确认选项后，模型可直接用 `update_prep_items` 保存真实项，食谱列举和未选项不表示拥有。pending项的 `availability=unknown/missing` 区分尚未确认与明确缺少，ready由服务端投影为available；不根据未选择推断缺货。批量更新由领域服务原子校验，版本冲突不盲目重放；修改成功已展示结果，无需重复show，也不把模糊的“小料”分类硬编码成一组ID。[完整工作流契约](workflow.md#备菜与步骤确认)

烹饪中的“下一步/上一步/第N步”调用 `navigate_cooking_step` 移动实际卡片，不确认完成；用户明确说这步已做好、搞定或要求标记完成时，调用 `complete_cooking_step`，可用 `completed=false` 撤销。两者都要求最新 `expected_revision`，成功返回更新后的步骤卡片。单步完成后自动定位该菜首个未完成步，模型不再紧接一次next，不把其他步骤或整餐自动完成。授权语义由模型结合可信用户内容判断，领域层严格校验阶段、版本和引用，不要求用户重复固定句式或另外点击按钮。

推荐回复只编辑草稿。备菜中的固定核对建议仅在存在 `pending` 且 `availability` 为unknown或尚未提供时加入；只有明确缺货、已备齐或已跳过的项目时不加入。建议使用中性的“核对未确认食材”，不预设用户已拥有所有小料。前后端都按最新权威清单重算，工具本身不执行备菜操作。

`ask_user` 是等待点。应用保存封闭结构的 `pending_question`，核心通过标准 `StructuredTool(return_direct=True)` 在该条 `ToolMessage` 后结束本轮，不再请求模型。工具必须单独调用；与任何其他工具混在同批时整批拒绝，防止提问后仍有并行写操作。成功结果保留完整闭合历史，并设置 `awaiting_user=true`；工具历史只写 `status/question_id/revision` 的最小成功收据，宿主需要跨轮结清业务确认时还可附带不超过1000字符的 `receipt` 对象。完整问题来自已经验证的本轮权威状态，避免大工作区触发输出裁切后把已保存的问题误报为失败。没有保存问题的失败结果不会假装进入等待。用户选项或自由输入先由应用校验、持久化，再以新用户消息和最新状态继续正常轮次，不需要常驻等待任务或隐藏 checkpointer。

等待点本身只证明问题已保存，不代表某项写入已获批准。需要用户确认的应用工具必须在宿主侧绑定业务对象及版本、会话和真实问题ID，下一轮核对已保存的对应答案；模型参数中的“已批准”、别的问题答案或普通引用文字不能替代确认。个人菜谱的读改、审核与发布回执属于工作台注入能力，见[菜谱中心协议](workbench.md#菜谱中心-api)，不是本包新增的持久化或 HTTP API。

## 渐进 Skills

`SkillRegistry([explicit_skill_directory, ...])` 仅发现显式启用目录中的 `SKILL.md`。初始化读取并校验名称/简介，system prompt 只列元数据；`list_skills` 列元数据，`load_skill(name)` 才把正文放入工具结果。默认没有启用目录。

名称限制为小写字母、数字、下划线或连字符；路径解析必须保持在已启用目录内，不接受文件路径，不读取辅助文件，不运行脚本。YAML frontmatter 使用 `safe_load`，文件、元数据和整轮正文分别有预算；文件变更后返回 `skill_changed`，要求刷新配置。Skills 仅提供指导，不添加 shell、任意 SQL 或任意文件工具，也不能授予额外权限。

应用通过 `tool_skill_bindings={tool_name: skill_name}` 绑定现有工具，或在 `InjectedToolSpec(required_skill="personal-memory")` 上声明所属技能。`KITCHEN_TOOL_SKILLS` 是可选的厨房工具映射；工作台实际启用的组件、技能目录和工具归属由应用组件清单组装，不由该常量自动启用。关闭技能后，所属工具从模型本轮绑定中移除，执行边界也返回 `skill_disabled`，历史中的旧工具定义不构成新权限。已注册 schema 保留用于读取闭合的旧对话。通用的工作区读取和提问不属于某个专属技能；自定义菜谱的详情读取还需要 `recipe-authoring`。

工作台的 `app-preferences` 注入 `get_app_preferences/update_app_preferences`，只读写主题、配色、字体、密度、内联数量、估算标识和推荐回复等明确白名单；另绑定工作流工具 `set_kitchen_display`，只请求当前会话切换专注/普通对话模式，不写入持久外观或推进烹饪阶段。它不授予读取或修改模型、供应商、API Key、开发者、系统、运行预算、上下文或组件权限的能力，也不把通用设置接口暴露给模型。具体字段、独立版本和 HTTP 合同见[工作台配置边界](workbench.md#配置来源与默认值)。这是应用工具的权限边界，不能通过技能正文、嵌套字段或其他工具绕过。

跨阶段操作可用 `tool_skill_bindings={tool_name: {target_stage: skill_name}}` 声明目标阶段权限。`advance_cooking_stage` 进入备菜需要 `pantry-preparation`，进入烹饪或完成需要 `cooking-guide`，进入想法或规划需要 `meal-planning`。任一目标技能启用时模型仍可看见这个共用工具，执行时按请求的 `stage` 检查，不能借共用工具进入已关闭技能负责的阶段。

## 长期记忆注入

调用方拥有持久化记忆与历史检索服务，使用 `run_turn(..., memory_context={...})` 提供当前用户自定义记忆和助手总结。核心不读写数据库。动态记忆仅追加到最新用户消息的实际请求中，标明数据来源和优先级，不把旧副本累计保存到消息历史，也不改变稳定系统提示。用户当前明确要求优先于历史，用户自定义记忆优先于助手总结。

调用方应同时传入当前布尔授权 `memory_enabled`；工作台由全局组件与个人开关共同计算。显式 `false` 时忽略误传的非空 `memory_context`，禁止新个人记忆工具调用，并仅在请求投影中隐藏旧私有工具回执及参数（包括规范、原始与无效调用副本）；调用 ID 与结果配对仍完整，持久历史不改写。关闭对应技能也会屏蔽其旧私有内容。开启时，历史中转发的个人记忆同样标记 `context_usage.memory_attached`，由应用在发送前复核最新授权，不能只检查本轮新读取。当前昵称仍独立附加；普通旧对话中已经显示的文字不会因此被删除或清洗。

默认记忆读取上限为12,000字符，并占用总历史预算；旧历史按完整用户轮次清理。持久化记忆、关闭开关、用户编辑与助手总结的权限边界由应用实现，实际读写与检索工具继续受技能开关和工具输出预算约束。模型维护总结后，当前轮的动态记忆随成功回执刷新。个人菜谱搜索返回每道菜各自的真实快照，后续详情读取验证对应来源，不能拿另一道菜或官方库快照混用。

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
| `usage` | 实际模型调用数、输入/输出/总量/缓存/推理token及逐字段覆盖率；未报告项为null，不能视为0 |
| `context_usage` | 当前实际请求的分类字符数与明确标注的粗略token估算，不含图片像素或供应商封装 |
| `awaiting_user` / `pending_question` | 本轮已保存问题、等待用户回答；此时不生成后续推荐 |
| `debug_trace` | 真正模型请求边界捕获的每次系统提示、消息投影及实际工具schema，已脱敏且没有图片二进制或隐藏推理 |
| `skill_events` | 本轮每次 `load_skill` 的实际名称、状态、调用ID和正文字数 |

调用方传入当前用户消息，成功后保存 `messages`。失败、超时和取消返回原有输入历史，不加入未完成的 assistant/tool 片段。已经成功的业务工具写入由宿主独立持久化，不因生成历史回退而撤销。每轮新输入历史超出预算时仅移除完整的旧用户轮次，绝不把工具调用与结果拆开；当前轮增长超过预算会明确报错。

食材选择必须出现在先前真实 `find_ingredients` 返回中，菜谱详情 ID 必须来自先前真实搜索或详情，并且传入相同的 `expected_snapshot`。历史可以提供这些证据，因此“第一道怎么做”和“再限制到半小时”能延续上一轮。换数据快照后清空旧引用，工具返回 `snapshot_changed` 时必须重新查候选。
同一官方数据快照内，多次成功搜索和分页的菜谱 ID 合并作为引用证据；后续空结果或参数错误不撤销已验证的 ID，但当前展示仍使用最新搜索结果。官方快照只由官方检索四工具更新；个人菜谱详情及其他应用工具的独立快照不能替换它。个人菜谱继续按各自 ID 与版本核对来源。

自然语言执行策略：用户说“我想吃虾”或“找番茄做的菜”时，先获取候选，再默认覆盖符合常见意图的真实食材并执行 `search_recipes`，展示实际菜谱。用户无需先选数据库食材、虾种或补全全部可选条件；取得结果后再允许收窄口味或时间。只有候选不足、实质歧义、条件冲突等情况才先追问。问候和普通解释可以直接回复，不通过代码强制所有回答先查菜谱。该策略由真实模型端到端场景验收，离线脚本测试只能验证工具与历史协议。

初始推荐在用户和历史没有明确排序、数量要求时，智能体默认按 `total_time_min` 升序取8条；工具请求模型的默认值和最大范围保持完整。回答编号与最新搜索 `items` 顺序一致，让“第二道”对应同一张卡片。更多结果使用相同条件和排序分页，新筛选条件从第一页开始。

单项注册工具的schema保持稳定；每次模型请求根据最新current_actions与阶段绑定可执行工具，保留全部注册定义用于验证闭合历史。执行仍独立校验真实阶段和前置条件。服务器状态附在对应用户轮次，后续工具快照优先；仅传变化的快照使用 `snapshot_kind=delta`，组件按ID更新和删除，未列出的字段沿用此前状态。持久化用户文字和完整服务器快照不改写，旧完整快照也在模型请求投影时去重。相同成功只读查询在本轮内不重复执行/回传大结果，失败可重试。请求投影保留菜谱ID、来源快照、用量与未知/估算证据，不将这些事实摘要成猜测。
本轮观察到官方数据快照变化时，清除此前只读查询的去重记录，允许重新获取选项和候选；成功的新结果重新登记，相同快照和相同参数仍保持去重。个人菜谱的独立版本变化不会清掉官方查询记录。

`model_usage`事件与最终`usage`来自`AIMessage.usage_metadata`。逐次完整的输入和输出可以精确相加为总量，以 `total_tokens_source=input_output_sum` 区别供应商直接报告；缺失值不通过跨调用汇总补齐。`known_totals` 是已知小计，`coverage` 明确已知/直接报告/派生次数。模型身份使用真实返回元数据或当前请求的明确配置，历史中缺失的模型名称不推断。工具定义/系统前缀稳定是缓存前提，实际命中仍以供应商报告为准；未添加网关未声明支持的缓存参数。

同一请求边界发出不包含正文的 `context_usage`，按系统提示词、对话、工具回调、工具定义、界面状态、长期记忆、组件指南分别计算捕获文本与规范JSON的字符数。Token采用明确的ASCII四字符、其他字符一字符启发式；这是观测估算，不是计费用量或窗口容量。最终结果及失败/取消前的已捕获事件都可由应用保存。

## 开发者上下文检查

`tool_catalog(ui_context)` 返回全部已注册工具及schema、阶段、effect、自动展示、副作用与当前阻塞。开发者目录包含历史别名；实际请求trace.tools只记录本次绑定的可执行子集。应用端负责把目录和日志限制在开发者界面。

每次 `awrap_model_call` 在完成系统提示、历史差异投影和原生图片块装载后、调用模型前发出 `debug_context`，同时加入最终 `debug_trace`。每项包括 `round/system/messages/tools/stage/skills/native_images_count`；`capture=model_request` 表明来自实际请求边界。`messages.content` 保留发送文本；用户消息另有 `user_text` 和实际投影的 `ui_context`，工具消息另有解析后的 `result`，便于界面用正文与状态卡片展示。正文和结构中均剔除密钥、凭据字段、图片字节及隐藏推理，图片仅展示元数据；这不是供应商完整HTTP抓包。

模型失败或 `cancel_event` 中止时结果仍带已捕获诊断。宿主直接取消任务时继续抛出 `asyncio.CancelledError`，应用应保存此前实时收到的诊断事件。调试数据不加入后续模型历史，也不会改变系统提示或模型请求；旧轮次没有捕获记录时不能通过当前提示词拼接冒充当时实际上下文。

## 事件

`on_event` 可为同步或异步回调。每个事件都是 JSON 对象：

```json
{"type":"status","status":"thinking","message":"正在理解需求"}
{"type":"tool_start","call_id":"call_1","name":"find_ingredients","arguments":{"queries":["虾"]}}
{"type":"tool_end","call_id":"call_1","name":"find_ingredients","arguments":{"queries":["虾"]},"result":{"status":"ok"}}
{"type":"assistant_delta","text":"查到","delta":"查到"}
{"type":"status","status":"completed","message":"查询完成"}
{"type":"debug_context","context":{"round":1,"system":"…","messages":[],"tools":[],"capture":"model_request"}}
{"type":"skill_event","name":"cooking-guide","call_id":"call_2","status":"ok","chars":320}
```

`assistant_delta` 属于临时流式内容；失败或停止后界面应撤销临时回答，使用最终 `AgentTurnResult` 决定是否保存。`tool_end` 包含输出预算内的真实结果；巨大的原始导出对象和连接秘密字段不会送给模型或事件消费方。被截短的字段带标记，过大到无法合理裁剪时返回 `output_too_large`，要求缩小查询。

## 生命周期和边界

- 默认每轮最多 10 次模型请求、16 次工具调用，单个模型请求 120 秒、整轮 300 秒；SDK 默认不自动重试，避免慢请求连续重试耗尽整轮预算。调用方可用 `model_timeout_s/max_model_retries/turn_timeout_s` 在验证范围内明确配置。
- 工具结束后下一次模型请求开始时发出“正在分析查询结果”，让界面正确展示等待阶段。
- 流式工具参数必须是完整 JSON，整批名称/标识/参数结构验证通过后才执行；未知工具或半截参数不会执行任何调用。
- 模型以 `finish_reason="tool_calls"` 结束但没有提供完整标准调用时，返回 `incomplete_tool_batch`，不把引导语当作完成回答。
- DSML 等已知工具协议泄漏到普通文本时返回 `tool_protocol_leak`，不执行文本中的工具、不提交回答历史；流式缓冲防止拆分标记泄漏到界面。不会以宽松文本解析冒充标准工具协议。
- 食材和营养查询由数据库执行；候选选择与自然语言理解由模型承担。系统提示要求保留多轮硬条件、区分 USDA 参考与食材身份、说明估算、可选配料和原文依据。
- 本地schema或引用校验失败返回安全字段路径与修复提示。同工具/参数/错误第三次重复时禁用工具并请求一次据实收尾；推荐工具最多尝试一次，失败使用默认建议，不阻塞主回答。
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

`return_direct=True` 的等待工具语义对应官方工具文档：工具以匹配调用ID的 `ToolMessage` 结束，不再执行下一次模型推理。等待点、请求投影一致性和失败诊断均有注入测试模型覆盖；这些测试不代表真实供应商的提问质量。


## 参数完整性与工具契约

生产模型使用项目内IntegrityChatOpenAI，在原始SDK边界按请求隔离工具参数，仍由标准create_agent管理运行。Responses校验call_id/output_index、delta与各结束值的一致性，收到完整成功终态后才发布工具调用；结束事件有完整值时可恢复缺失delta。Chat Completions与非流式同样验证完整JSON，截断、重复键、非有限数字、身份冲突和缺终态不执行业务。未修改site-packages。

成功和失败的parameter_integrity只保存长度、哈希、字段名、结束状态与调用身份；不保存原始参数片段、密钥、图片字节或隐藏推理。流式正文保持正常输出，工具批次完整性通过后才交给执行器。连接测试复用同一适配器。

Chat Completions同时使用response_format与流式的特殊SDK分支尚未适配，发送请求前以provider_stream_format_unsupported明确拒绝；普通工具流式、Responses和非流式路径均有定向覆盖。

工作流工具描述、schema、阶段、前置条件、副作用由chefmate-workflow的TOOL_CONTRACTS统一提供。修改和展示单独调用，依赖前次结果的批次整批拒绝并返回闭合回调；只读查询可并行。模型更新偏好必须提供changed_fields和values，严格格式补齐的未列字段不会覆盖旧值。业务revision与presentation_revision分开，纯展示不让任务写入失效。
