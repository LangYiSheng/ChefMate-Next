# ChefMate App

把聊天、选菜、备菜、烹饪和个人饮食记录连接起来的本地厨房工作台。React 19.3.1、TypeScript 7 和 Vite 8 前端负责交互，FastAPI 负责 HTTP/SSE、依赖装配与持久化。

[项目首页](../../README.md) · [完整使用与接口参考](../../docs/reference/workbench.md) · [模块地图](../../docs/guide/development.md) · [设计规范](../../docs/design/README.md)

## 主要能力

- 真实菜谱检索、共享餐单、可核对的备菜与步骤、持久化计时。
- 标题栏统一折叠的任务卡片、整餐/单菜备料与宽屏双列清单；大字步骤配大纲导航，完整步骤采用紧凑列表。
- 专注做菜时底部显示可收起的助手回复与工具活动，随阶段切换内容；可通过对话切换步骤、进入或退出专注模式。
- 文字/图片输入、对话内菜谱审核与修改、按版本确认保存、收藏、删除与恢复。
- 食光手记、本人食用份额、长期记忆、独立模型用量账本。
- 七个系统组件开关、模型协议与图片能力配置；开发者模式提供上下文分类条、工具目录、请求时间线与树视图。
- 统一主题控件、设置标签页与宽屏目录；普通外观和对话偏好可由助手按白名单读取及修改。
- 外观设置提供小、中、大三档字号滑块，预览后保存到整个工作台，默认中档。
- 设置中的版本更新日志，按版本查看已发布的功能与改进。
- 单会话运行、显式停止、失败重试与断线后状态恢复。

## 快速开始

日常启动使用仓库根目录的 [Docker Compose](../../docs/guide/deployment.md)，包含独立应用 PostgreSQL。注册登录后进入各自的工作区，管理员维护模型、额度、价格和开发者许可。以下本机方式用于源码开发；需要在应用 `.env` 配置管理员初始化信息。

需要 Python 3.12+、uv 和 Node.js 22.12+。在仓库根目录运行，已有配置文件时保留原文件：

```powershell
uv sync --project apps/chefmate-playground --extra dev --python 3.12
Copy-Item apps/chefmate-playground/.env.example apps/chefmate-playground/.env
# 编辑 .env，填写已有 PostgreSQL 库和自己的模型连接。
npm --prefix apps/chefmate-playground/frontend ci
npm --prefix apps/chefmate-playground/frontend run build
uv run --project apps/chefmate-playground chefmate-playground
```

打开[本地工作台](http://127.0.0.1:18765)，核对[服务状态](http://127.0.0.1:18765/api/status)。更换端口可加 `--port 18766`。

也可执行 `apps/chefmate-playground/run.ps1 -Port 18765`：脚本依次运行 `npm ci`、`npm run build` 和 `uv run`。Vite 将页面与本地资源输出到应用的 `web/`，Python 从这里提供 SPA 页面和 `/static` 资源。直接运行 `uv run` 前必须完成构建；修改前端后重新构建才能更新后端提供的页面。

应用读取自己的 `.env` 和本地设置覆盖。启动不会建库、迁移基础菜谱、导入数据或下载 USDA。配置字段见 [.env.example](.env.example)，新环境数据准备见[开发指南](../../docs/guide/development.md)。

## 日报与周报

食光手记的概览提供每日、每周回顾入口，也可直接切换“日报”“周报”。日报按北京时间的实际用餐日期汇总，周报采用周一至周日的自然周，周选择仅列出有已确认饮食记录的周。只统计已确认的个人饮食，草稿、删除记录与整餐制作量不参与统计。

四项核心营养可展开来源；周趋势支持点击日期进入日报。日均值逐指标使用“有已知值的记录日”作分母，缺失日与未知值不补零。用户可确认“这一天已记完”，该状态与营养字段完整性独立，记录修改后失效。两周均已结束、逐日确认记完且对应营养字段完整时才提供比较。

“整理 AI 回顾”按需调用当前模型，结合后台计算的统计、餐食与每日趋势生成简短文字回顾；统计不依赖模型。结果按报告数据指纹缓存，修改后提示更新，模型失败仍保留统计。账号环境下调用接入现有用量预约和结算。

启动时在各用户数据空间增加 `nutrition_day_checks` 和 `nutrition_report_reviews` 表，不改变原饮食记录。当前时区沿用食光手记的 `Asia/Shanghai`。

## 代码入口

### 语音输入

输入框发送按钮左侧的麦克风支持实时转写。点击停止会保留文字；录音中点击发送会先停止采音、等待最终转写，再发送一次。连接失败或收尾超时保留草稿，切换会话或隐藏页面自动释放麦克风。单次录音最多约三分钟。

服务端从 `CHEFMATE_RUNTIME_DIR/speech.env`（本机默认 `.runtime/speech.env`）或进程环境读取 `CHEFMATE_SPEECH_API_KEY`，可用 `CHEFMATE_SPEECH_RESOURCE_ID` 覆盖默认 `volc.seedasr.sauc.duration`。Docker 中配置文件路径为 `/var/lib/chefmate/speech.env`；密钥不进入前端、镜像或版本库。修改此文件后下一次录音生效。

浏览器需要麦克风权限，远程访问需要 HTTPS。本机 localhost/127.0.0.1 可直接测试。音频以 16 kHz 单声道 PCM 通过已登录、同源 WebSocket 转发至火山引擎，不保存原始录音。语音服务用量由火山引擎计费，当前不纳入 ChefMate 的文本模型额度账本。

| 位置 | 职责 |
| --- | --- |
| [app.py](src/chefmate_playground/app.py)、[app_context.py](src/chefmate_playground/app_context.py) | 服务装配、当前依赖与生命周期 |
| [routes/](src/chefmate_playground/routes) | 系统、会话、工作区、菜谱、个人记录、调试路由 |
| [runtime.py](src/chefmate_playground/runtime.py)、[sessions.py](src/chefmate_playground/sessions.py) | 执行轮次、SSE 与会话聚合 |
| [interactions.py](src/chefmate_playground/interactions.py) | 可信轮次与厨房领域服务绑定 |
| [cookbook.py](src/chefmate_playground/cookbook.py)、[meal_logging.py](src/chefmate_playground/meal_logging.py) | 个人菜谱与饮食仓储 |
| [recipe_publication.py](src/chefmate_playground/recipe_publication.py)、[recipe_tools.py](src/chefmate_playground/recipe_tools.py) | 菜谱审核、修改及绑定用户答案的版本确认 |
| [ui_preferences.py](src/chefmate_playground/ui_preferences.py) | HTTP 与智能体共用的普通偏好白名单、版本与保存边界 |
| [cooked_intake.py](src/chefmate_playground/cooked_intake.py)、[memory.py](src/chefmate_playground/memory.py) | 本人份额与长期记忆 |
| [frontend/src/App.tsx](frontend/src/App.tsx)、[main.tsx](frontend/src/main.tsx) | React 应用装配、页面导航与启动 |
| [frontend/src/runtime/](frontend/src/runtime/) | JSON/SSE 传输、会话运行、恢复与浏览器草稿 |
| [frontend/src/components/](frontend/src/components/)、[features/](frontend/src/features/) | 对话组件、设置、个人中心和烹饪工作区 |
| [frontend/src/components/ui/](frontend/src/components/ui/)、[components/developer/](frontend/src/components/developer/) | 共享 Select 与输入样式、开发者目录和执行轨迹 |
| [frontend/src/features/preferences/](frontend/src/features/preferences/) | 服务端偏好同步、本地缓存与写入竞态处理 |
| [frontend/src/styles/](frontend/src/styles/)、[styles/index.css](frontend/src/styles/index.css) | 全局颜色与字体角色、控件、页面框架、聊天和详情布局 |
| [settings-react.css](frontend/src/features/settings/settings-react.css)、[centers-react.css](frontend/src/features/centers/centers-react.css)、[workspace.css](frontend/src/features/workspace/workspace.css) | 设置、个人中心、厨房与计时的作用域样式 |
| [frontend/public/](frontend/public/) | 随构建复制的字体、品牌资源和 Lucide sprite |

## 验证

### 长任务与中断恢复

AI 回顾的生成接口立即返回 `202` 和 `review_job`，模型在工作区后台执行；页面轮询状态，切页、刷新或断网不会取消任务。相同周期的生成请求合并，结果按饮食数据版本缓存。每个工作区最多同时生成两份回顾，排队与执行合计最多八项。任务状态持久化，服务重启后标记中断，由用户点击继续生成。

对话在模型完成一轮输出、每个工具返回时保存执行检查点。显式停止保留已输出文字和工具结果；重启后不自动执行，用户发送“继续”时带上中途上下文。已开始但未保存结果的调用标记为结果未知，同参数的写入会被拦截，需核对业务记录；这不等同于跨服务事务或任意工具的恰好一次执行。恢复从保存的消息与工具边界继续，不承诺接续供应商中断的单次 token 流。

当前后台执行归单个应用进程所有，数据库负责保存状态和结果；多进程或多副本部署前需增加任务租约与共享调度。模型等待期间不持有数据库事务，普通页面请求保持独立。

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest apps/chefmate-playground/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/app-tests
npm --prefix apps/chefmate-playground/frontend test
npm --prefix apps/chefmate-playground/frontend run build
```

Docker 中账号与个人记录位于应用 PostgreSQL，图片和设置位于持久卷；本机 SQLite 适配供旧数据与离线测试使用。完整 API、状态协议与可用性边界见[接口参考](../../docs/reference/workbench.md)，实际模型与浏览器证据见[工程档案](../../docs/engineering/README.md)。
