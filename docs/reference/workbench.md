# 核心 App：配置、HTTP 与状态协议

本页是 `chefmate-playground` 的运行与接口参考。首次了解产品请先读[整体导览](../guide/overview.md)，修改实现时配合[开发指南](../guide/development.md)。
接口以[分域路由](../../apps/chefmate-playground/src/chefmate_playground/routes/)和[请求模型](../../apps/chefmate-playground/src/chefmate_playground/http_models.py)为准；历史实测结果位于[工程档案](../engineering/README.md)。

## 运行与数据位置

从仓库根目录、使用 Python 3.12+、uv 和 Node.js 22.12+：

```powershell
uv sync --project apps/chefmate-playground --extra dev
npm --prefix apps/chefmate-playground/frontend ci
npm --prefix apps/chefmate-playground/frontend run build
uv run --project apps/chefmate-playground chefmate-playground
```

默认访问 [本地工作台](http://127.0.0.1:18765/)，设置地址为 `/settings`；也可运行 `apps/chefmate-playground/run.ps1`，脚本自动执行 `npm ci`、前端构建和 uv 启动。
使用 `chefmate-playground --port 18766` 可选择其他端口。启动器只监听 `127.0.0.1`。
应用连接调用方准备的 PostgreSQL，启动时不下载 USDA、不启动数据库、不迁移或替换菜谱数据。

前端为 React 19.3.1、TypeScript 7 和 Vite 8。源码与样式在 [frontend/src](../../apps/chefmate-playground/frontend/src/)，全局样式入口是 [styles/index.css](../../apps/chefmate-playground/frontend/src/styles/index.css)，功能域保留自己的作用域样式。[frontend/public](../../apps/chefmate-playground/frontend/public/) 只保存字体、品牌与 Lucide sprite 等静态资源。构建生成 `apps/chefmate-playground/web/`，后端从此提供 SPA 页面并挂载 `/static`。直接使用 uv 启动前先构建，修改源码后也需重新构建；`npm --prefix apps/chefmate-playground/frontend run dev` 仅用于前端开发，其 `/api` 代理到默认后端端口。

| 数据 | 位置与所有者 |
| --- | --- |
| 基础菜谱 | PostgreSQL 指定 schema，由 `chefmate-storage` 管理当前发布集 |
| 会话、厨房任务、个人菜谱、收藏、制作与饮食记录、长期记忆、用量 | 默认应用目录 `.runtime/sessions.db` 的独立表 |
| 图片 | 会话数据库同目录的 `images/`，按会话归属管理 |
| 应用设置覆盖 | 应用目录 `.runtime/settings.json` |
| USDA 参考 | 配置指定的本地 SQLite，默认指向清洗子项目的食品库 |
| 界面显示偏好 | 当前浏览器的本地存储 |
| 未发送文字、确认后续轮 | 当前浏览器 localStorage，由 React 会话状态服务管理 |
| 未发送图片 Blob | 当前浏览器 IndexedDB；正式聊天图片仍归服务端会话 |

删除聊天不会连带删除独立的个人菜谱、制作记录、饮食记录、长期记忆或用量账本。
活跃对话先停止才能删除；图片按会话清理。[应用资源组装](../../apps/chefmate-playground/src/chefmate_playground/app.py) · [会话路由](../../apps/chefmate-playground/src/chefmate_playground/routes/conversations.py)

## 配置来源与默认值

首次配置从[应用模板](../../apps/chefmate-playground/.env.example)建立自己的 `.env`，填写数据库和模型信息；已有配置继续使用。
应用读取自身 `.env`，进程环境变量覆盖同名值，再应用设置页持久化的覆盖项。
共享包不读取应用或清洗平台的配置文件，数据库连接由宿主提供。

| 配置 | 默认与含义 |
| --- | --- |
| `CHEFMATE_DATABASE_URL` | 空；已有 PostgreSQL 连接，由调用方配置 |
| `CHEFMATE_DATABASE_SCHEMA` | `chefmate` |
| `CHEFMATE_USDA_DATABASE` | `../../data_cleaning/database/usda.db`；相对应用 `.env` 目录解析，空值关闭本地参考 |
| `OPENAI_API_KEY`、`OPENAI_MODEL` | 空；运行真实模型需要明确配置 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `CHEFMATE_AGENT_TIMEOUT` | 300 秒，整轮预算，读取时限制在 10–300 秒 |
| `CHEFMATE_AGENT_MODEL_TIMEOUT` | 120 秒，单次模型请求，10–300 秒 |
| `CHEFMATE_AGENT_MODEL_RETRIES` | 0 次，范围 0–2 |
| `CHEFMATE_AGENT_MAX_ROUNDS` / `CHEFMATE_AGENT_MAX_TOOL_CALLS` | 10 / 20，范围分别为 2–30 / 2–50 |
| `CHEFMATE_AGENT_MAX_HISTORY_CHARS` | 400000 字符；历史与本轮工具信息预算 |
| `CHEFMATE_AGENT_MAX_TOOL_RESULT_CHARS` | 60000 字符；单个工具结果预算 |
| `CHEFMATE_AGENT_STREAM_TOOL_CALLS` | `false`；验证配置的供应商协议后可显式启用 |
| `CHEFMATE_MODEL_SUPPORTS_IMAGES` / `CHEFMATE_MODEL_MAX_IMAGES` | `false` / 3；图片上限 1–12，包含保留历史 |
| `CHEFMATE_TIMER_LIMIT` / `CHEFMATE_CLEAR_TIMER_ON_STEP_CHANGE` | 3 / `false`；计时上限只接受 1 或 3 |
| `CHEFMATE_PLAYGROUND_PORT` | 18765 |
| `CHEFMATE_ENABLED_COMPONENTS` | 默认启用全部六个注册组件；逗号分隔 ID |

字符预算是应用的 JSON 读取预算，不是供应商的 token 窗口声明。
设置页模型协议支持 `chat_completions` 和 `responses`，默认前者；推荐回复默认开启、数量 3，可配置 1–6。
设置页保存与环境变量的完整映射见[配置类](../../apps/chefmate-playground/src/chefmate_playground/config.py)和[设置模型](../../apps/chefmate-playground/src/chefmate_playground/settings.py)。

`PATCH /api/settings` 使用 `{revision,section,values}`，section 为 `model/runtime/context/interaction/cooking/components`。
保存先核对设置版本，校验成功再原子写文件并应用；冲突保留用户草稿供复核。
活跃模型轮次存在时，除 `cooking` 外的分类会返回 409；计时策略使用最新设置读取，不替换当前 Agent。
查询不返回密钥；同一端点可沿用已有密钥，更换端点需要提供专用密钥。
模型连接测试验证原生工具请求、结果回传与最终回答，HTTP 200 本身不足以说明协议可用。

## 系统组件与交互契约

组件清单统一绑定指引、工具、界面、依赖和工厂，ID 为：

- `meal-planning`：选菜、条件与餐单。
- `pantry-preparation`：逐项备料确认。
- `cooking-guide`：步骤指引、任务完成与计时。
- `recipe-authoring`：个人菜谱草稿、保存、删除与恢复。
- `personal-memory`：用户偏好、助手摘要与历史检索。
- `food-journal`：照片/文字记餐、营养汇总与已完成餐食的本人份额。

关闭组件同时撤去对应模型工具和执行权限，已有记录保留。
共用的阶段工具按目标阶段核对所属组件；通用提问、工作区读取和技能加载分别保留自己的边界。[组件注册表](../../apps/chefmate-playground/src/chefmate_playground/components.py)

工作流展示支持 `display=inline|workspace|none`。服务端生成受控组件；前端使用当前可信投影交互，历史卡片中的原始 JSON 不作为写入依据。
厨房组件包括偏好、候选、详情、餐单、备料、步骤、计时和进度，饮食组件另含 `food_log`、`nutrition_summary`，等待点使用 `pending_question`。
浏览器显示偏好 `inline_limit` 取 1 或 2，控制每条回复默认展开的界面数量；当前可编辑内容优先，其余保留摘要及展开入口。相同组件在多条消息出现时，只允许最新所属消息提供当前任务操作。
完整模型字段见[工作流参考](workflow.md)与[智能界面设计](../design/agent-ui.md)。

## 会话、图片与厨房 API

表中 `{id}` 为真实会话 ID，`{image_id}` 为该会话的图片 ID。

| 接口 | 用途与参数 |
| --- | --- |
| `GET /api/sessions` | `limit` 默认 20、1–100；`cursor` 游标分页，返回 `next_cursor` |
| `POST /api/sessions` | 创建会话 |
| `GET /api/sessions/{id}` | 恢复已保存消息、任务与轮次状态 |
| `PATCH /api/sessions/{id}` | `title`（去空白后 1–80 字）或 `pinned`，至少修改一项 |
| `DELETE /api/sessions/{id}` | 删除未运行会话；制作记录同步失败时明确返回错误 |
| `POST /api/sessions/{id}/turn` | `{message,images?,request_id?,retry?,ui_snapshot?}`，返回 SSE；文字或图片至少提供一种 |
| `POST /api/sessions/{id}/cancel` | 显式取消当前轮 |
| `GET /api/sessions/{id}/workspace` | 当前完整组件投影与模型短快照 |
| `POST /api/sessions/{id}/actions` | `{action_id,base_revision,view_id?,type,payload}` |
| `POST /api/sessions/{id}/images` | 请求体为图片字节，`name` 查询参数为文件名；返回引用元数据 |
| `GET/DELETE /api/sessions/{id}/images/{image_id}` | 按归属读取、删除尚可移除的附件 |
| `GET /api/timer-sessions` | 发现有活动或近期结束计时器的会话，不激活聊天 |

`message` 最长 8000 字符；`images` 为已上传的唯一图片 ID，最多 12 个，实际仍受模型配置上限限制。
浏览器新聊天在首次发送前保留为本地草稿，点击推荐或填写模板只编辑输入框；发送时再创建会话并上传图片。
服务端验证会话图片归属，重新解码 JPG/PNG/WebP，限制上传 4000000 字节、像素总数 1600 万，缩至最长边 1600，并保存不含 EXIF 的 JPEG，最大 1500000 字节。
历史与调试记录只保存图片元数据，推理时按需加载原生图片块。[图片处理](../../apps/chefmate-playground/src/chefmate_playground/images.py)

工作区 `revision` 对应真实业务状态变化，`presentation_revision` 对应展示变化。
`action_id` 与内容指纹检查重复操作；旧版本、陌生 ID 或被替换组件返回冲突结果。
**HTTP 200 下也要读取业务 `status`**，不能把返回的当前工作区误当作旧操作已经执行。
直接 HTTP `change_stage` 动作返回 403，由对话意图调用阶段工具；用户真实备料、步骤和完成确认仍用受控动作保存。

`ask_user` 单独执行，保存至多六个不同选项与可选自填字段后结束本轮。
`answer_question` 动作按问题 ID、当前版本及选项规则保存答案，应用再用稳定 `answer:<question_id>` 请求标识自动续轮。
待回答时拒绝新的普通轮次；刷新、切换与断线不会使问题丢失。

## SSE 与轮次生命周期

事件以 `data: <JSON>` 发送，活跃轮次包含会话 ID、轮次 ID 和递增序号。
主要 type 为 `status/tool_start/tool_end/assistant_delta/ui_action/model_usage/context_usage/result/error/done`；开发者诊断单独保存，普通页面不会收到原始请求正文。
流式连接有保活注释；`done` 表示这轮终态。
最终 `result` 携带助手文字、工具记录、菜谱信息、界面动作、状态快照、用量，以及成功轮次的 `suggested_replies`。
推荐回复只替换输入草稿，不会直接发送；缺少有效模型建议时使用与任务阶段相符的默认草稿。

`request_id` 用于轮次请求去重，`retry=true` 重试末条失败或中断请求，避免重复追加同一条用户输入。
切换会话或离开页面只分离 SSE，当前服务进程内的轮次继续执行并保存；停止按钮显式取消。
服务重启将未完成轮次标为中断，完整历史和任务仍可恢复。
没有持久后台作业调度器，因此页面脱离与进程故障恢复需分别理解。[轮次管理](../../apps/chefmate-playground/src/chefmate_playground/runtime.py)

完整助手/工具批次校验后才提交成功历史；失败或停止保留用户输入，不保存半截生成批次。
已经成功的厨房或个人数据写入有独立原子边界，回答失败不会撤销那些真实修改。
下一轮从服务端重建可信状态，并把此前失败轮次的人类输入保留为必要上下文。

## 菜谱中心 API

| 接口 | 用途 |
| --- | --- |
| `GET /api/cookbook` | `query/source/offset/limit`，source 为 `all/user/favorites/drafts/deleted`，每页默认 12、最多 30 |
| `GET/PUT/DELETE /api/cookbook/{recipe_id}` | 读取、按版本更新草稿、移入回收站 |
| `POST /api/cookbook/{recipe_id}/publish`、`restore` | 按记录版本发布草稿、恢复个人菜谱 |
| `PUT /api/favorites/{recipe_id}` | 更新收藏 |
| `GET /api/recipes/{recipe_id}` | 读取基础或已保存个人菜谱的当前详情 |
| `POST /api/tools/{tool_name}` | 直接测试四个只读检索工具 |

个人菜谱标记 `origin=user`，使用自己的真实快照，基础菜谱仍来自 PostgreSQL。
草稿复用处理包的结构校验和确定性营养计算；时间、人数、克重缺乏原文依据时留空。
助手删除个人菜谱先发起真实待回答问题，再按对应答案执行；撤销删除走恢复接口。[菜谱服务](../../apps/chefmate-playground/src/chefmate_playground/cookbook.py) · [路由细节](../../apps/chefmate-playground/src/chefmate_playground/routes/library.py)

## 制作记录、个人饮食与记忆 API

| 接口 | 用途 |
| --- | --- |
| `GET /api/journal` | `days` 默认 7、1–366，`date` 可选；返回汇总、制作记录、饮食草稿、已保存及已删除记录 |
| `PATCH /api/journal/{entry_id}` | 更新制作反馈 `rating/notes` |
| `GET /api/food-logs` | 按状态、日期范围、查询词和分页读取个人饮食 |
| `GET/PUT/DELETE /api/food-logs/{identity}` | 读取、按 `{revision,record}` 修改草稿、按 revision 移入回收站 |
| `POST /api/food-logs/{identity}/publish`、`restore` | 请求体 `{revision}`，确认保存或恢复 |
| `GET/PATCH/DELETE /api/memory` | 当前偏好、摘要与版本；修改或清空需核对 revision |

照片或文字记餐先生成草稿，用户核对后保存才计入个人摄入。
真实完成烹饪会保存本次冻结配方制作历史；启用饮食记录组件时，单人餐按明确产品规则整份自动登记并标为估算，可继续更正。
多人或人数未知时先进入 `awaiting_portions`，等本人逐菜比例，不按人数擅自均分或把全桌记作个人摄入。
`get_cooked_meal_intake` 读取本餐版本，`record_cooked_meal_intake` 核对本轮原话与逐菜比例，营养由服务端按冻结配方计算。
同餐更正更新稳定 ID，未知指标为空；读取历史不会补登记，关闭组件不执行自动登记。[完成餐食规则](../../apps/chefmate-playground/src/chefmate_playground/cooked_intake.py)

长期记忆分开保存用户原文与助手摘要，当前明确要求优先，助手不能覆盖用户原文。
关闭功能后停用对应上下文与工具；个人历史按需检索。[个人数据路由](../../apps/chefmate-playground/src/chefmate_playground/routes/personal.py)

## 系统状态、用量与开发者 API

| 接口 | 用途 |
| --- | --- |
| `GET /api/status` | 配置、数据库就绪、菜谱数、模型能力、活动会话和组件 |
| `GET/PATCH /api/settings` | 脱敏配置与版本保存 |
| `POST /api/settings/test-model` | 所选协议的完整原生工具往返验证 |
| `GET /api/components` | 指引、工具、UI 和依赖清单 |
| `GET /api/usage?days=30` | `days=1..366` 或 `all`，实际报告用量与覆盖情况 |
| `GET /api/about` | 包版本、当前 Git 提交、菜谱及组件数量 |
| `GET /api/changelog` | 当前版本与按版本分组的更新日志，无需模型或菜谱库 |
| `GET /api/debug/catalog` | 真实注册工具 schema 与组件目录 |
| `GET /api/sessions/{id}/debug?turn_id=...` | 已捕获模型请求、工具过程、用量与参数完整性诊断 |

`/api/status.ready` 要求数据库读取就绪且存在 Agent；只看首页 HTTP 200 无法判断检索与模型是否准备好。
调试接口需 `X-ChefMate-Developer: 1`，这是本地明确显示开关，不能当身份认证。
独立调试表压缩保存最近 20 轮，没有捕获的旧轮次明确缺失，不能用当前配置重建后冒充历史请求。
API Key、数据库密码、图片字节和供应商隐藏推理不进入调试输出。[诊断路由](../../apps/chefmate-playground/src/chefmate_playground/routes/diagnostics.py)

用量以供应商报告为准；单次调用同时具备输入和输出时可派生总量，并标明来源。
未知字段保留空值，跨调用小计与覆盖率分别提供；不能据此猜测供应商缓存能力。[用量实现](../../apps/chefmate-playground/src/chefmate_playground/usage.py)

## 界面与验证入口

设置可内嵌打开，保留会话与未发送文字、图片草稿；菜谱中心和手记中的聊天入口创建新对话，旧草稿仍可恢复。
备料使用完整可滚动清单，待备项靠前，已备齐或明确跳过的项目划线沉底。步骤与当前提示沿用冻结菜谱；逐步阅读支持长说明分页，完整步骤列表也保留，浏览和翻页不会自动完成步骤。
计时保存 UTC 截止时间和暂停剩余量，页面刷新可恢复；新计时与到期提醒可自动展开，保留正在编辑的草稿和焦点，Escape 优先关闭浮层。发声遵守声音设置、浏览器交互解锁与页面运行条件，计时到期不会自动确认步骤。
完成烹饪与个人营养登记分别反馈；若厨房动作已完成而后续记录同步失败，使用原 `action_id` 重试完成请求，补齐记录同步后再续聊。
图片原位预览、桌面侧栏宽度、窄屏抽屉、主题配色和字体约定见[视觉规范](../design/ui.md)。

```powershell
uv run --project apps/chefmate-playground --extra dev python -m pytest apps/chefmate-playground/tests -p no:cacheprovider
npm --prefix apps/chefmate-playground/frontend test
npm --prefix apps/chefmate-playground/frontend run build
```

HTTP、协议与生命周期测试注入明确测试依赖，不能据此声称当前外部模型和数据库已实测。
真实模型、浏览器和来源数据的验证按日期保存在[工程记录](../engineering/README.md)；当前完整请求模型也可在运行应用的 `/docs` 查看。
