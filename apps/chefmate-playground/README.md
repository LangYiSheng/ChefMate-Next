# ChefMate 本地测试工作台

一个本地厨房工作台，连接真实菜谱检索、标准 LangChain Agent 和独立厨房任务服务。前端使用原生 HTML/CSS/JavaScript；FastAPI 负责应用配置、HTTP、SSE 与会话保存。

## 启动

在仓库根目录运行：

```powershell
uv sync --project apps/chefmate-playground --extra dev --python data_cleaning/.venv/Scripts/python.exe
uv run --project apps/chefmate-playground chefmate-playground
```

访问 <http://127.0.0.1:18765>。也可以执行本目录的 `run.ps1`，或用 `--port 端口号` 换端口。服务只监听本机地址。

本次开发已准备应用自己的 `.env`，复用了现有模型和测试数据库配置。该文件被 Git 忽略。其他环境从 `.env.example` 创建自己的 `.env`：

- `CHEFMATE_DATABASE_URL`：已有 PostgreSQL 数据库连接。
- `CHEFMATE_DATABASE_SCHEMA`：已有菜谱 schema，默认 `chefmate`。
- `CHEFMATE_USDA_DATABASE`：可选本地 USDA 库，相对路径以应用目录为基准。
- `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL`：模型配置。
- `CHEFMATE_AGENT_TIMEOUT`、`CHEFMATE_AGENT_MAX_ROUNDS`、`CHEFMATE_AGENT_MAX_TOOL_CALLS`：单轮执行预算。
- `CHEFMATE_AGENT_MODEL_TIMEOUT`、`CHEFMATE_AGENT_MODEL_RETRIES`：单次模型请求超时与 SDK 重试次数；默认 120 秒、0 次，整轮预算默认 300 秒，失败由用户明确重试。
- `CHEFMATE_AGENT_STREAM_TOOL_CALLS`：模板默认 `false`，只有验证过所配置服务的标准流式工具协议后才打开。本机配置已启用：修正嵌套 schema 后，真实候选结果下的续轮请求已返回标准流式工具调用。
- `CHEFMATE_ENABLED_SKILLS`：逗号分隔的应用内技能名，默认 `meal-planning,pantry-preparation,cooking-guide`。只允许应用已声明的技能目录。

应用读取自己的 `.env`，以及通过设置页保存的本地覆盖配置。库包不会自动读取清洗端配置。启动不会下载 USDA、启动数据库、迁移菜谱表或重新导入数据。

## 使用

先说“给我一个食材、时间和人数的表单”，填写“虾、30分钟、2人”，再说“按刚填的找几个”。也可以直接说“我想吃虾”。模型调用展示工具打开表单、菜品选择和详情；用户确认后保存餐单，继续采购、备菜和烹饪。

当前支持 9 类组件：偏好表单、菜品选择、菜谱详情、餐单与份量、购物清单、备菜清单、烹饪步骤、计时器、任务进度。模型主动展示的核心组件可在聊天内直接操作，补充内容采用工作区入口，当前工作区一次展示一种内容；候选和清单分页，详情按食材/做法/营养/说明切换，烹饪逐步浏览。没有卡片内的嵌套滚动条；极长内容仍可通过页面区域滚动读取。表单修改在下一条自然语言发送前自动保存；菜品选择需要点击确认。勾选、份量、步骤和计时操作直接保存，下一轮附带服务端核对过的短快照。

阶段是会话状态，展示在标题和会话侧栏中。告诉助手“接下来准备食材”“备好了，开始做吧”“做完了”，由模型调用阶段工具，业务服务仍检查准备和完成门槛；网页不能直接跳阶段。`change_stage` HTTP 动作被拒绝，用户对实际食材/步骤的确认保留。

独立设置页提供单行摘要、展开轨迹和隐藏三种显示，偏好在当前浏览器保存。内容来自公开过程文本与实际工具调用，显示使用中、成功、拒绝/失败和中断；不提取模型隐藏推理字段。展开轨迹可继续展开公开说明或技术工具记录，最终回答独立显示。

时间和配方来自原始菜谱。可换算的用量按可靠份数依据缩放；原份数未知时保留原量并显示提示。购物清单仅合并身份、名称和单位一致的已知用量。计时器保存 UTC 截止时间和暂停剩余量，刷新可恢复；全部备料经用户确认才能开始烹饪，全部步骤完成才能结束。

模型使用标准 `langchain.agents.create_agent` 和 middleware 执行模型/工具循环，按最新阶段筛选业务工具。三个内置 Skills 先只提供简介，调用 `load_skill` 才加载正文；“技能”页显示可用内容和实际加载记录。具体职责和状态协议见[架构说明](../../docs/agent-ui-architecture.md)。

页面提供会话切换、停止、重试、真实菜谱卡片、详情和折叠的工具输入输出。未配置模型时显示明确状态，不使用假回答替代。停止本轮不会写入半截工具历史。切换对话或浏览器断开只分离实时视图，后台本轮继续运行；重新打开可恢复状态。停止按钮会取消模型调用。

本机当前使用经验证的原生流式工具传输，状态、工具事件和回答通过 SSE 更新。模板保守地关闭流式；其他模型服务需要单独验证协议能力。当前服务首个片段可能等待较久，因此模型请求与整轮预算分别配置，停止按钮可随时取消。

对话保存在 `.runtime/sessions.db`，与 PostgreSQL 菜谱库独立，不提交 Git。成功模型历史、真实工具结果和页面消息分别保存。失败或取消后可重试末条消息，不重复追加。服务重启会把未完成的本轮标为中断。

失败、停止或重启中断后的用户文字仍保留为下一轮的上下文。例如“想吃虾”失败后再补“半小时以内”，智能体仍知道要查虾；失败的工具结果和半截回答不会当作已完成证据。

## HTTP 接口

| 接口 | 用途 |
|---|---|
| `GET /api/status` | 模型配置、数据库状态和菜谱数量 |
| `GET/POST /api/sessions` | 会话列表 / 创建 |
| `GET/DELETE /api/sessions/{id}` | 恢复 / 删除未运行的会话 |
| `GET /api/sessions/{id}/workspace` | 当前完整组件与模型短快照 |
| `POST /api/sessions/{id}/actions` | 受控动作：`{action_id,base_revision,view_id?,type,payload}` |
| `POST /api/sessions/{id}/turn` | `{message, request_id?, retry?, ui_snapshot?}`；返回 SSE |
| `POST /api/sessions/{id}/cancel` | 停止当前轮 |
| `GET /api/skills` | 已启用技能的名称和简介 |
| `GET/PATCH /api/settings` | 脱敏设置读取与版本校验保存 |
| `POST /api/settings/test-model` | 检查所选协议的原生工具调用与结果回传 |
| `GET /api/recipes/{recipe_id}` | 当前真实菜谱详情 |
| `POST /api/tools/{name}` | 直接测试四个只读查询工具 |

SSE 的 `data` 是 JSON，事件 `type` 为 `status`、`tool_start`、`tool_end`、`assistant_delta`、`ui_action`、`result`、`error`、`done`，并携带会话、本轮和递增序号。最终结果包含文字、菜谱列表、选定详情、工具调用、界面动作和快照。中间过程与最终回答分开显示。密码、API Key 与原始供应商异常不会返回页面。

工作区以 `revision` 校验动作，`action_id` 防止重复提交。发生冲突会要求复核并保留草稿。后端重新构造模型上下文，客户端伪造的菜谱、勾选和条件不能作为业务事实；模型执行期间发生的用户修改不会被旧结果覆盖。

工具参数见[检索包](../../packages/chefmate-retrieval/README.md)、[工作流包](../../packages/chefmate-workflow/README.md)，执行约束见[智能体核心](../../packages/chefmate-agent/README.md)。

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest apps/chefmate-playground/tests -p no:cacheprovider
node --test apps/chefmate-playground/static/tests/ui-protocol.test.mjs
```

HTTP 与生命周期测试注入明确的测试模型，不请求生产模型。真实模型、数据库及浏览器验证另行记录在 [VERIFICATION.md](VERIFICATION.md)。

工具保留版本与导入时间组成的快照，未知营养不补零，估算保留标记。当前资料没有可靠的“主料／必选”字段，界面展示原文和 warnings 供核对。

本轮完成厨房主流程。旧版账号、图片识别、语音和长期记忆的接入边界见[迁移矩阵](../../packages/chefmate-workflow/docs/legacy-feature-matrix.md)，这些能力尚未迁入当前工作台。


## 设置与聊天内交互（2026-10-14）

打开 `/settings`，按「模型连接／界面显示／厨房技能／执行设置／数据与状态」分类配置，聊天页只保留设置入口。模型设置支持 OpenAI Chat Completions 和 Responses；连接测试验证原生工具调用、工具结果回传与最终回答，不能把 HTTP 200 或文本工具标记当作成功。

模型、技能和预算的设置保存在被 Git 忽略的 `.runtime/settings.json`，覆盖应用 `.env` 默认值；数据库连接仍由 `.env` 管理。设置查询不返回密钥，空密钥仅在同一端点沿用，换供应商必须提供专用密钥。保存采用版本校验和原子文件替换；有对话执行时拒绝应用，等待结束后再保存。界面显示偏好保存在当前浏览器。

工作流工具新增 `display=inline|workspace|none`：模型主动选择本轮内容和展示位置；通常只展示一个核心组件。聊天默认最多展开两项，可在设置改成一项。读取工作区不会展示所有已有视图。聊天内组件使用服务端当前版本，与工作区共用草稿、动作标识和版本检查；旧组件替换后仅保留入口。详情在原位切换，返回时保留选择与分页。

过程仅显示公开操作、工具状态及结果，不提取模型内部推理；平铺轨迹、完成后收起的单行摘要和完全隐藏均可选。

模型连接页只显示实际配置项，不内置供应商测试预设。2026-10-14 使用专用密钥验证 `https://apiclaude.cc/v1` 的 `gpt-5.6-terra`：Responses 协议与流式原生工具调用、结果回传通过。密钥只留在本地配置中。
