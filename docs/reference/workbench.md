# 核心 App：配置、HTTP 与状态协议

本页是 `chefmate-playground` 的运行与接口参考。首次了解产品请先读[整体导览](../guide/overview.md)，修改实现时配合[开发指南](../guide/development.md)。
接口以[分域路由](../../apps/chefmate-playground/src/chefmate_playground/routes/)和[请求模型](../../apps/chefmate-playground/src/chefmate_playground/http_models.py)为准；历史实测结果位于[工程档案](../engineering/README.md)。

## 运行与数据位置

日常运行按 [Docker 部署指南](../guide/deployment.md)启动，默认包含应用 PostgreSQL。下列命令用于源码开发，需要 Python 3.12+、uv 和 Node.js 22.12+：

```powershell
uv sync --project apps/chefmate-playground --extra dev
npm --prefix apps/chefmate-playground/frontend ci
npm --prefix apps/chefmate-playground/frontend run build
uv run --project apps/chefmate-playground chefmate-playground
```

默认访问 [本地工作台](http://127.0.0.1:18765/)，设置地址为 `/settings`；也可运行 `apps/chefmate-playground/run.ps1`，脚本自动执行 `npm ci`、前端构建和 uv 启动。
使用 `chefmate-playground --port 18766` 可选择其他端口。启动器默认监听 `127.0.0.1`，Docker 通过环境变量显式监听 `0.0.0.0`。
应用连接调用方准备的 PostgreSQL，启动时不下载 USDA、不启动数据库、不迁移或替换菜谱数据。

前端为 React 19.3.1、TypeScript 7 和 Vite 8。源码与样式在 [frontend/src](../../apps/chefmate-playground/frontend/src/)，全局样式入口是 [styles/index.css](../../apps/chefmate-playground/frontend/src/styles/index.css)，功能域保留自己的作用域样式。[frontend/public](../../apps/chefmate-playground/frontend/public/) 只保存字体、品牌与 Lucide sprite 等静态资源。构建生成 `apps/chefmate-playground/web/`，后端从此提供 SPA 页面并挂载 `/static`。直接使用 uv 启动前先构建，修改源码后也需重新构建；`npm --prefix apps/chefmate-playground/frontend run dev` 仅用于前端开发，其 `/api` 代理到默认后端端口。

| 数据 | 位置与所有者 |
| --- | --- |
| 基础菜谱 | PostgreSQL 指定 schema，由 `chefmate-storage` 管理当前发布集 |
| 身份、会话哈希、额度、成本、审计 | 应用 PostgreSQL 的 `<prefix>_accounts` schema |
| 会话、厨房任务、个人菜谱、收藏、制作与饮食记录、长期记忆、用量 | 应用 PostgreSQL 的 `<prefix>_user_<id>` 独立 schema；本机旧数据与离线测试可使用 SQLite 适配 |
| 图片 | 运行目录 `users/<id>/images/`，按用户与会话归属管理 |
| 应用设置覆盖 | 应用目录 `.runtime/settings.json` |
| USDA 参考 | 配置指定的本地 SQLite，默认指向清洗子项目的食品库 |
| 界面显示偏好 | 应用设置覆盖中的安全偏好域；浏览器本地存储作为缓存与首次迁移来源 |
| 未发送文字、确认后续轮 | 当前浏览器 localStorage，由 React 会话状态服务管理 |
| 未发送图片 Blob | 按账号分区的浏览器 IndexedDB；正式聊天图片仍归服务端会话 |

删除聊天不会连带删除独立的个人菜谱、制作记录、饮食记录、长期记忆或用量账本。
活跃对话先停止才能删除；图片按会话清理。[应用资源组装](../../apps/chefmate-playground/src/chefmate_playground/app.py) · [会话路由](../../apps/chefmate-playground/src/chefmate_playground/routes/conversations.py)

## 配置来源与默认值

首次配置从[应用模板](../../apps/chefmate-playground/.env.example)建立自己的 `.env`，填写数据库和模型信息；已有配置继续使用。
应用读取自身 `.env`，进程环境变量覆盖同名值，再应用设置页持久化的覆盖项。
共享包不读取应用或清洗平台的配置文件，数据库连接由宿主提供。

| 配置 | 默认与含义 |
| --- | --- |
| `CHEFMATE_APP_DATABASE_URL` | Docker 必需；应用个人数据 PostgreSQL，与公共菜谱连接分开；本机开发空值保留 SQLite 适配 |
| `CHEFMATE_APP_DATABASE_SCHEMA` | `chefmate`，命名空间前缀最多 25 位 |
| `CHEFMATE_ADMIN_USERNAME` / `CHEFMATE_ADMIN_PASSWORD` | 显式初始化管理员，不接管普通账号或重设已有密码 |
| `CHEFMATE_RUNTIME_DIR` | 应用 `.runtime`；Docker 为 `/var/lib/chefmate`，保存图片与设置 |
| `CHEFMATE_NEW_USER_COST_LIMIT_MICROS` | 5000000，即 ¥5；仅首次初始化金额政策，之后以管理员保存的默认额度为准 |
| `CHEFMATE_ALLOWED_HOSTS` / `CHEFMATE_COOKIE_SECURE` | 允许访问的主机名、HTTPS 会话 Cookie 开关 |
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
| `CHEFMATE_ENABLED_COMPONENTS` | 默认启用全部七个注册组件；逗号分隔 ID；已有显式清单继续按该清单启用 |

字符预算是应用的 JSON 读取预算，不是供应商的 token 窗口声明。
设置页模型协议支持 `chat_completions` 和 `responses`，默认前者；推荐回复默认开启、数量 3，可配置 1–6。
设置页保存与环境变量的完整映射见[配置类](../../apps/chefmate-playground/src/chefmate_playground/config.py)和[设置模型](../../apps/chefmate-playground/src/chefmate_playground/settings.py)。

`PATCH /api/settings` 使用 `{revision,section,values}`，仅允许个人 `interaction/cooking` 分类。全局模型、组件和预算使用管理员的 `GET/PATCH /api/admin/settings`，分类为 `model/runtime/context/components`。
保存先核对设置版本，校验成功再原子写文件并应用；冲突保留用户草稿供复核。
活跃模型轮次存在时，除 `cooking` 外的分类会返回 409；计时策略使用最新设置读取，不替换当前 Agent。
普通外观和对话偏好使用独立的 `GET/PATCH /api/ui-preferences`：读取返回 `{revision,initialized,values}`，保存发送 `{revision,changes}`。字段白名单为 `theme/light_palette/dark_palette/font_pairing/font_size/density/inline_limit/show_estimates/reply_suggestions/reply_suggestion_count`。`font_size=small|medium|large`，根字号为 14 / 15 / 16px，默认中；滑块预览草稿，保存后全局生效，大档保留调整前尺寸。该域独立校验版本，可在轮次运行中更新，不暴露模型、开发者、系统或密钥。首次使用将浏览器外观迁入服务端；冲突不自动重放修改。
这里的 `revision` 是不透明 CAS 标识，不能按大小判断新旧，也不能与 `/api/settings` 的版本互换。409 的 `detail.preferences` 提供当前快照；未保存草稿需保留，重新读取后复核。首次迁移只发送八个外观字段，保留服务端已有的推荐回复开关与数量。智能体 `get_app_preferences/update_app_preferences` 成功返回 `preferences`，更新另返回只含实际变化的 `changed`；两者都按 `app-preferences` 组件和技能约束。[偏好服务](../../apps/chefmate-playground/src/chefmate_playground/ui_preferences.py)
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
- `app-preferences`：普通外观和对话偏好的读取与修改，以及当前厨房的专注/普通对话显示切换；不访问敏感设置。通过同名技能按需提供工具。

关闭组件同时撤去对应模型工具和执行权限，已有记录保留。
共用的阶段工具按目标阶段核对所属组件；通用提问、工作区读取和技能加载分别保留自己的边界。[组件注册表](../../apps/chefmate-playground/src/chefmate_playground/components.py)

工作流展示支持 `display=inline|workspace|none`。服务端生成受控组件；前端使用当前可信投影交互，历史卡片中的原始 JSON 不作为写入依据。
厨房组件包括偏好、候选、详情、餐单、备料、步骤、计时和进度，饮食组件另含 `food_log`、`nutrition_summary`，等待点使用 `pending_question`。
普通应用偏好 `inline_limit` 取 1 或 2，控制每条回复默认展开的界面数量；当前可编辑内容优先，其余保留摘要及展开入口。相同组件在多条消息出现时，只允许最新所属消息提供当前任务操作。
卡片的展开、收起和全屏入口位于标题右侧；正常同步不显示“已同步”，失败与冲突仍就地提供反馈和重试。用餐条件一次展示全部字段，较长候选、餐单和详情通过“展开其余”追加内容，避免反复上一页、下一页。

`show_prep_checklist(recipe_ids=null)` 展示整餐合并清单，`recipe_ids=[真实菜谱ID]` 展示指定菜品，也可选择多个已入餐单的 ID；界面用 `set_checklist_scope` 动作切换同一范围。单菜准备完成不代表整餐已备齐。

已有餐单时，要求整理、打开或核对备料会进入prep，缺少食材也可先准备。清单 `status=pending/ready/skipped` 与 `availability=unknown/available/missing` 分开：尚未说明或未选项目保持unknown，明确缺少保存为pending+missing并显示“待购买”，明确拥有/备齐才记录ready并投影为available。`update_prep_items` 仅对pending项接受 `availability=missing|unknown`；所有pending仍阻止进入cooking，不因标记缺货而绕过门槛。[备菜确认协议](workflow.md#备菜与步骤确认)

`set_kitchen_display` 属于 `app-preferences`，参数为 `{mode:"focus"|"conversation",view_kind?}`。进入专注模式时，可指定当前已打开的厨房卡片；省略按阶段选择。返回普通对话须省略 `view_kind`。工具只生成带稳定 `action_id` 的会话展示动作，不推进阶段、不确认食材或步骤，也不修改持久外观偏好。浏览器仅执行当前可见聊天中新到达的动作，按会话和动作 ID 去重；首次读取历史只建立基线，恢复当前运行轮次时可接收此前未交付的动作，旧历史与后台会话不能强行切换当前页面。[展示动作接收](../../apps/chefmate-playground/frontend/src/runtime/store.ts)

`navigate_cooking_step` 属于 `cooking-guide`，仅在烹饪阶段使用：`direction=next|previous|current|step`、必填 `expected_revision`，只有 `step` 提供从 1 开始的 `step_number`；`recipe_id` 省略使用当前菜，指定时必须来自当前餐单。它切换真实活动步骤并刷新卡片，保留所有完成标记，遵守已有的换步计时策略；不以“下一步”推断用户完成了上一步。首尾返回 `first_step/last_step`，不循环、不自动完成或推进阶段。界面大纲和前后按钮仅浏览；助手工具的真实导航会清除过时的本地浏览位置。[导航实现](../../packages/chefmate-workflow/src/chefmate_workflow/kitchen.py)

`complete_cooking_step` 同属 `cooking-guide`，只在用户明确确认单步完成或撤销时使用。必填 `expected_revision`，`recipe_id/step_id` 省略表示当前步，`completed` 默认true、false撤销；成功更新卡片并定位该菜首个未完成步骤，不自动结束本餐。用户只说“下一步”仍使用导航工具，不能代替完成授权。无当前步且未指定真实步骤时返回 `step_required`，旧版本或非法引用不会修改任何进度。
完整模型字段见[工作流参考](workflow.md)与[智能界面设计](../design/agent-ui.md)。

## 身份与管理员 API

除 `/healthz`、静态页面与注册登录接口外，业务 API 都要求有效 Cookie，并以服务端会话确定用户。未登录返回 401，未完成引导或权限不足返回 403；不存在或不属于自己的业务 ID 返回 404。写接口核对同源；请求头、用户 ID 参数和浏览器设置不能授予管理权限。

| 接口 | 行为 |
| --- | --- |
| `GET /api/auth/me` | 当前用户或 `null`，含欢迎状态、角色、开发者授权、档案、额度 |
| `POST /api/auth/register` / `login` | `{username,password}`；注册为普通用户并签发 HttpOnly Cookie |
| `POST /api/auth/logout` | 撤销会话并清除 Cookie |
| `PATCH /api/auth/password` | `{current_password,new_password}`，撤销旧会话并轮换当前 Cookie |
| `PATCH /api/auth/onboarding` | `{display_name,cooking_level,household_size,preferences,theme,use_for_ai}`；经验值 `beginner/comfortable/confident`，AI 使用默认关闭 |
| `GET /api/auth/quota` | 自己的金额额度、已用、暂估占用、预留、可用余额及单价；Token 仅作统计 |
| `GET /api/admin/users` | 用户列表 |
| `GET/PATCH /api/admin/policy` | `{revision,registration_enabled,default_cost_limit_micros}`；控制开放注册和后续注册账号的默认金额，不追改已有金额配置 |
| `PATCH /api/admin/users/{id}` | 停用、角色、开发者许可、`cost_limit_micros` 金额额度；`null` 不限、0 停止新模型调用；拒绝 Token 上限字段 |
| `POST /api/admin/users/{id}/password` | `{new_password}`，重置并撤销该用户会话 |
| `GET/PATCH /api/admin/pricing` | 每百万输入、缓存输入、输出 Token 的 CNY 单价，revision 防覆盖；GET 含只读 `configured`，首次必须显式保存确认，允许明确确认全零免费 |
| `POST /api/admin/users/{id}/usage/{turn_id}/reconcile` | 管理员核对该轮最终总金额：`revision`、`final_cost_micros`、必填 `reason`；运行/预留未结束或版本冲突返回 409，原始记录不改写 |
| `GET /api/admin/users/{id}/usage/{turn_id}/reconciliations` | 按版本倒序读取该轮完整核对历史；仅管理员 |
| `GET /api/admin/usage` | `days` 为 1–366；全体、用户和每日汇总，缺失与估算单列 |
| `GET /api/admin/users/{id}/usage` | 用户用量明细与历史价格快照 |
| `POST /api/admin/settings/test-model` | 连接与原生工具往返测试，用量计入管理员账本 |
| `GET /api/admin/data` | 个人数据计数、附件文件占用、存储类型与运行状态 |
| `POST /api/admin/users/{id}/reset-data` | `{confirm_username}`；先归档 schema/文件再重建，保留身份和账本，撤销该用户会话 |
| `GET /api/admin/audit` | 管理动作与字段，不记录密码或密钥 |

厨房档案位于 `/api/memory` 的 `kitchen_profile`。只有用户明确开启 `use_for_ai` 且长期记忆启用时进入模型上下文，不覆盖手写偏好与助手总结。清空记忆也会清除当前档案投影，普通刷新不会恢复。规则见[账号设计](../design/accounts-admin.md)。

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
普通进度优先显示实际运行工具的中文任务名称，例如“整理备菜清单”，工具结束后继续显示当前轮次状态；取消与连接恢复状态优先。
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
草稿复用处理包的结构校验和确定性营养计算；原文缺失且用户未要求补充的时间、人数、难度保留未知，克重仍只接受原文明确值。食材建议用量写在 `amount` 并标明建议，不冒充实测 `grams`。
`get_recipe_draft/revise_recipe_draft` 支持对话内核对和修改；`request_recipe_publication` 发起真实问题，`confirm_recipe_publication` 绑定会话、草稿版本和用户答案执行。修改草稿使旧确认失效，助手自行传入“已批准”不能发布。回复审核卡也会重新读取当前记录，按版本确认保存；已有待回答问题时以问题面板为唯一确认入口。
个人菜谱采用基础菜谱相同的 `raw_amount/raw_unit/grams` 食材契约，难度取 `beginner/normal/advanced`。用户要求建议或明确给出参数时，创建请求顶层、修订请求的 `recipe` 中可填写 `metadata_provenance`，固定可选字段为 `servings/difficulty/prep_time_min/cook_time_min/total_time_min`。每项为 `{kind:"suggested"|"user_confirmed",evidence,question_id?}`：建议引用本轮真实请求；用户确认引用真实数值答案，通过选项回答时还须绑定宿主 `last_answer.question_id`。同组选项“普通、1人份、30分钟”可直接用于这三项，不需要重新措辞或再次确认。未标记字段继续严格核对原文，步骤计时不能变成整道菜总耗时。

仅改难度、份数、总耗时优先 `revise_recipe_draft(recipe_id,revision,metadata_patch)`，与完整 `recipe` 严格二选一。`metadata_patch` 只接受 `difficulty/servings/total_time_min` 及按字段提供的 `metadata_provenance`；未列字段和来源保留，`null` 明确清空对应值与来源，非空新值须有该项真实依据。服务器从已读的同一版本重建校验输入，只更新所列字段及派生标记，食材、步骤、原文和备菜/烹饪分项保持原样，不为凑总时间重写其他内容。

个人记录包含标准 `document`、记录外层的 `metadata_provenance`，以及 `source_metadata_evidence:{servings,difficulty,time}` 中保留的原文引用。外层 `time_evidence/difficulty_evidence` 可包含用户确认或助手建议的展示标签，不能直接当原文写回。建议参数自动加入说明，建议份数在营养与规范化菜谱中标为估算；用户只改总耗时不应自动补齐或凑改备菜、烹饪小计。原始 `source_text` 保留，实际补充另有依据，来源信息不挤入共享 RecipeDocument 的其他字段。

新增或改变对话参数来源会在写入事务内核对当前请求及宿主答案；手工 HTTP 写入不能伪造对话确认。未改的值可完整保留原有 `metadata_provenance`；编辑器手动改值时将该字段改为用户补充的原文依据并清除该项旧来源标记，其余字段沿用。参数确认只修改草稿，发布仍使用独立的版本绑定确认流程。[参数来源实现](../../apps/chefmate-playground/src/chefmate_playground/recipe_provenance.py)

| 智能体菜谱工具 | 成功结果与确认边界 |
| --- | --- |
| `create_recipe_draft/get_recipe_draft/revise_recipe_draft` | `draft` 中返回当前个人记录，含 `recipe_id/status/revision/document`；读取已保存记录时 `status=saved`，不因此变回草稿 |
| `request_recipe_publication` | 当前版本须已通过工具读取或整理；返回真实问题与 `receipt.confirmation_id/recipe_id/revision/next_tool`，此时尚未发布 |
| `confirm_recipe_publication` | 传 `confirmation_id`；回执绑定会话、草稿版本、问题和发起轮次，宿主保存同题 `option_ids=['publish']` 才发布，返回 `published=true` 与 `draft.status=saved` |

发布准备期间若草稿已变化，只撤回自己创建且仍匹配的问题；不能清掉后来出现的另一道问题。已保存菜谱的读取与重新安排烹饪继续使用正常检索合同。[工具绑定](../../apps/chefmate-playground/src/chefmate_playground/recipe_tools.py) · [确认仓储](../../apps/chefmate-playground/src/chefmate_playground/recipe_publication.py)
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
饮食项的 `portion` 是份量文字，`portion_basis=user_provided` 时须引用本轮用户连续原话，保留原有数字写法和标点；例如“一瓶净含量1升”不能改写成“1瓶（净含量1升）”。`amount_g` 仅为质量克数，不是升或毫升。只提供体积时可保留原份量、`amount_g=null`，按每100毫升等依据估算所需营养；若按密度近似换成克数，`portion_basis` 必须为 `estimated`，且 `assumptions` 说明密度假设，不能标成用户称量。未要求或未知的营养继续留空。

来源错误返回 `food_portion_not_quoted` 或 `food_grams_not_provided`，附对应字段位置及修复提示。只修正错误的份量、质量或来源标记，不重写已有正确 `source_text`、照片原文及图片引用；修订继续基于 `get_food_log` 读回的同一记录和版本。[饮食字段](../../apps/chefmate-playground/src/chefmate_playground/food_log_models.py) · [来源核对](../../apps/chefmate-playground/src/chefmate_playground/food_log_evidence.py)

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
| `GET/PATCH /api/ui-preferences` | 独立白名单普通偏好与 CAS 保存，允许活跃轮次更新 |
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
备料使用完整可滚动清单，待备项靠前，已备齐或明确跳过的项目划线沉底；实际卡片宽度达到 560px 时采用双列，窄卡片保持单列。步骤与当前提示沿用冻结菜谱；逐步正文突出，底部为“上一步 / 完成这一步 / 下一步”，跳转使用可折叠大纲。完整步骤为紧凑列表，仅选中行显示操作。专注宽屏左右分别滚动，长说明不再分页；浏览不会自动完成步骤。
专注模式底部回复可折叠；执行中显示本轮问题与活动，完成后替换为最终回复，新轮次自动展开，错误和停止反馈始终可见。“和助手聊聊”复用普通聊天组件，支持完整消息、图片和卡片。通过聊天推进备菜或烹饪阶段后，专注内容随真实阶段切换；退出专注模式不停止模型轮次。
计时保存 UTC 截止时间和暂停剩余量，页面刷新可恢复；浮层统一空列表、运行列表和表单的内边距，内容与视口变化时重新定位。新计时与到期提醒可自动展开，保留正在编辑的草稿和焦点，Escape 优先关闭浮层。发声遵守声音设置、浏览器交互解锁与页面运行条件，计时到期不会自动确认步骤。
完成烹饪与个人营养登记分别反馈；若厨房动作已完成而后续记录同步失败，使用原 `action_id` 重试完成请求，补齐记录同步后再续聊。
图片原位预览、桌面侧栏宽度、窄屏抽屉、主题配色和字体约定见[视觉规范](../design/ui.md)。

```powershell
uv run --project apps/chefmate-playground --extra dev python -m pytest apps/chefmate-playground/tests -p no:cacheprovider
npm --prefix apps/chefmate-playground/frontend test
npm --prefix apps/chefmate-playground/frontend run build
```

HTTP、协议与生命周期测试注入明确测试依赖，不能据此声称当前外部模型和数据库已实测。
真实模型、浏览器和来源数据的验证按日期保存在[工程记录](../engineering/README.md)；当前完整请求模型也可在运行应用的 `/docs` 查看。
