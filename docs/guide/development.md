# 模块地图与开发指南

从 App 的一个请求开始读代码：React 组件与状态服务 → HTTP 路由 → 应用服务 → 独立领域包 → 仓储，再沿着结果返回界面。下表用来回答“这个改动应该放在哪里”。

## App 后端

根目录：[`apps/chefmate-playground/src/chefmate_playground`](../../apps/chefmate-playground/src/chefmate_playground)。

| 模块 | 职责与改动入口 |
| --- | --- |
| `app.py` / `app_context.py` | 装配依赖、生命周期、同源边界，持有当前配置与服务 |
| `http_models.py` | HTTP 设置、会话和发送请求的结构校验 |
| `routes/system.py` | 状态、设置、模型连接检查、关于 |
| `routes/conversations.py` | 会话、流式轮次、取消、删除 |
| `routes/workspace.py` | 工作区、用户动作、图片 |
| `routes/library.py` | 基础/个人菜谱、收藏、发布与恢复 |
| `routes/personal.py` | 记忆、食光手记、饮食草稿和摄入 |
| `routes/diagnostics.py` | 调试目录、轮次轨迹和用量 |
| `runtime.py` / `sessions.py` | 单会话运行、SSE、完整历史与原子会话聚合 |
| `interactions.py` / `components.py` | 可信轮次、领域服务绑定、系统组件合同与工厂 |
| `cookbook.py` | 个人菜谱仓储和基础/个人检索组合 |
| `recipe_drafts.py` / `recipe_tools.py` | 菜谱草稿契约与校验、智能体菜谱工具 |
| `meal_logging.py` | 饮食记录仓储、修订与回收站 |
| `food_log_models.py` / `food_log_evidence.py` / `food_log_tools.py` | 记餐契约、来源核对、模型工具 |
| `cooked_intake.py` / `journal.py` | 从制作历史核对本人份额、汇总制作与摄入 |
| `memory.py` / `usage.py` / `images.py` | 长期记忆、独立用量、会话图片 |

配置更新通过共享 context 生效。不要在新路由里缓存一份旧配置；工作区和记忆的权限 getter 必须读取当前值。HTTP 层只转换协议，不再长出第二套厨房规则。

## App 前端

根目录：[`apps/chefmate-playground/frontend`](../../apps/chefmate-playground/frontend)。使用 React 19.3.1、TypeScript 7 和 Vite 8；组件、状态服务、样式和前端测试位于 `src/`，`public/` 保存字体、品牌与第三方图标资源。

| 模块 | 职责 |
| --- | --- |
| `src/main.tsx` / `src/App.tsx` | React 根节点、页面导航、主布局与服务生命周期 |
| `src/runtime/store.ts` | 会话分页与切换、发送、取消、恢复、草稿与确认后续轮 |
| `src/runtime/http.ts` / `src/runtime/attachments.ts` | JSON/SSE 协议、脱敏、图片压缩与 IndexedDB 草稿 |
| `src/components/Composer.tsx` / `Conversation.tsx` | 受控输入、附件、消息、公开过程与建议 |
| `src/components/Details.tsx` / `Markdown.tsx` | 菜谱与调试详情、安全文本渲染 |
| `src/features/workspace/` | 厨房状态服务、白名单视图、问答、专注模式与计时 |
| `src/features/settings/` | 设置读取与保存、外观、记忆、用量与导航保护 |
| `src/features/centers/` | 菜谱中心、编辑器、食光手记与个人饮食 |
| `src/styles/index.css` / `foundation.css` | 全局样式入口、本地字体声明、语义颜色与字体角色 |
| `src/styles/controls.css` / `shell.css` | 公共控件、主框架、侧栏、移动抽屉与布局断点 |
| `src/styles/chat.css` / `details.css` | 聊天、输入、消息卡片、菜谱与调试详情布局 |
| `src/features/settings/settings-react.css` / `centers/centers-react.css` / `workspace/workspace.css` | 各功能域的作用域样式，复用全局颜色与字体角色 |
| `public/fonts/` / `brand/` / `vendor/` | 本地字体、品牌矢量与 Lucide sprite，随构建复制 |

组件用 `useSyncExternalStore` 订阅稳定快照，运行中的会话由状态服务持有。组件卸载、切换页面和关闭专注模式都不请求取消后端任务；显式停止才调用取消端点。异步响应核对生命周期和来源会话，避免迟到结果覆盖新选择。新增行为测试随对应模块保存为 `*.test.ts` 或 `*.test.tsx`，由 Vitest 执行。

厨房状态与持久草稿由 `features/workspace/store.ts` 管理；`QuestionPanel.tsx`、`Timers.tsx` 与 `ReadingPages.tsx` 分别负责确认问题、计时浮层与长说明分页。模型轮次完成后才展示新表单，确认动作保存成功后再续轮。计时使用单一状态服务和服务端时钟，React 组件挂载不会新增独立计时循环；备料保留完整列表，步骤浏览与执行确认分开。具体交互见[视觉规范](../design/ui.md)。

`npm run build` 先运行 TypeScript 检查，再将 SPA 与 `public/` 资源输出到 `apps/chefmate-playground/web/`。后端从这个目录提供页面，并把构建资源挂载到 `/static`；`web/` 为生成目录，不手改构建后的文件。

## 智能体与厨房领域

| 包与模块 | 职责 |
| --- | --- |
| Agent `core.py` | 装配与单轮生命周期 |
| Agent `runner.py` | 标准框架执行与 middleware |
| Agent `history.py` | 历史验证、整轮裁剪与任务快照 |
| Agent `tool_execution.py` / `evidence.py` | 工具执行、权限、参数与真实引用 |
| Agent `boundary.py` / `provider.py` | JSON/错误边界与模型传输 |
| Workflow `service.py` | 原子更新、修订与幂等入口 |
| Workflow `projection.py` | 份量、营养、界面和模型快照投影 |
| Workflow `kitchen.py` | 餐单、备菜、阶段、步骤与计时 |
| Workflow `tool_commands.py` / `user_actions.py` | 模型命令与用户动作分发 |
| Workflow `models.py` / `contracts.py` / `support.py` | 类型、工具合同和共享领域规则 |

这两组内部协作使用基类拆分同一实例的职责，构造与状态仍由公开服务统一持有，没有各自的可变业务单例。修改时关注跨模块调用，公共入口仍是 `ChefMateAgent` 和 `WorkflowService`。数据侧四个包与检索侧的进一步说明见[包目录](../../packages/README.md)。

## 启动与配置

在仓库根目录执行：

```powershell
uv sync --project apps/chefmate-playground --extra dev --python 3.12
Copy-Item apps/chefmate-playground/.env.example apps/chefmate-playground/.env
npm --prefix apps/chefmate-playground/frontend ci
npm --prefix apps/chefmate-playground/frontend run build
uv run --project apps/chefmate-playground chefmate-playground
```

已有 `.env` 时保留原文件。模板中的模型密钥、模型名称和数据库连接需要填好，配置以[模板](../../apps/chefmate-playground/.env.example)及 [PlaygroundConfig](../../apps/chefmate-playground/src/chefmate_playground/config.py)为准。模型协议、图片能力和流式工具支持均显式设置；供应商兼容性需要单独测试。

前端需要 Node.js 22.12+。Windows 的 `apps/chefmate-playground/run.ps1` 会先安装锁定依赖并构建，再启动 uv 服务。前端开发可另开终端运行 `npm --prefix apps/chefmate-playground/frontend run dev`；Vite 开发服务将 `/api` 代理到 `127.0.0.1:18765`。对外验收以后端提供的构建页面为准。

`GET /api/status` 同时显示配置与菜谱库状态。HTTP 200 不代表模型可以完成工具调用，模型连接页另有完整工具往返检查。

### 新环境准备数据

1. 使用[清洗工具](../../data_cleaning/README.md)导入原文、转换、核对并导出当前统一格式。
2. 准备自己的 PostgreSQL 数据库，将 DSN 放入进程环境变量 `CHEFMATE_DATABASE_URL`；不要把密码写进工具参数或提交。
3. 在已安装 Storage 的环境里显式执行下列维护命令，导入目标须确认是准备发布的 schema：

```powershell
chefmate-storage --schema chefmate migrate
chefmate-storage validate 'path/to/approved-export' --data-version 'your-version'
chefmate-storage --schema chefmate import 'path/to/approved-export' --replace --data-version 'your-version'
```

`--replace` 会替换目标 schema 的当前菜谱数据集。格式、推荐资格、空快照与权限说明见[存储参考](../reference/storage.md)。工作台运行时只读基础菜谱库。

本地 USDA 安装是显式维护步骤，见[食物数据参考](../reference/food-data.md)。清洗工具使用自己的依赖锁文件，从 `data_cleaning` 目录安装；不要把两个应用的配置混在一起。

### 隔离浏览器验收

更换端口本身不会隔离数据。验收应同时替换会话数据库路径；可在仓库根目录创建临时脚本运行以下内容：

```python
from dataclasses import replace
from pathlib import Path
import uvicorn
from chefmate_playground import PlaygroundConfig, create_app

config = replace(
    PlaygroundConfig.load(),
    sessions_database=Path("apps/chefmate-playground/.runtime/preview/sessions.db").resolve(),
    port=18766,
)
uvicorn.run(create_app(config), host="127.0.0.1", port=18766)
```

用 App 虚拟环境执行脚本。此方式复用已有模型连接与只读基础库，把新会话、个人数据、图片和设置覆盖放到隔离目录。修改隔离配置不会写回常用目录。

## 检查命令

日常更新保持当前版本，有用户可见变化时才在 [releases.json](../../apps/chefmate-playground/releases.json) 中累计日志，同一版本允许多条记录；用户要求发版时再汇总更新说明，并保留原始条目供追溯。只有用户明确要求升级版本号时，才修改 App 的 `pyproject.toml` 版本并同步锁文件。各可复用包的版本独立管理。

每条记录包含 `source: {branch, commit}`，保存被记录的已存在代码快照的分支名和完整提交 SHA；页面显示分支名和短提交 ID。该来源随记录保存，不动态读取当前 `HEAD`，也不用填写日志提交自身的哈希。设置里的“版本更新日志”读取这一份内容；“关于”和 OpenAPI 从同一项目版本读取。

日志用直白的话介绍用户能看到的功能、使用体验和问题修复；后端整理等工程细节记入工程档案。较大更新完成相应检查后自动提交并推送，保留无关的本地改动，推送后核对本地与远端提交一致。详细约定见[版本与发布](../design/engineering.md#版本与发布)。

在仓库根目录运行，先确保临时目录父路径存在：

```powershell
New-Item -ItemType Directory -Force apps/chefmate-playground/.runtime | Out-Null
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest apps/chefmate-playground/tests packages -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/pytest
npm --prefix apps/chefmate-playground/frontend test
npm --prefix apps/chefmate-playground/frontend run build
apps/chefmate-playground/.venv/Scripts/ruff.exe check --no-cache --config apps/chefmate-playground/pyproject.toml apps/chefmate-playground/src packages
apps/chefmate-playground/.venv/Scripts/python.exe scripts/check_docs.py
```

清洗端独立验证：

```powershell
data_cleaning/.venv/Scripts/python.exe -m pytest data_cleaning/tests -q -p no:cacheprovider --basetemp=data_cleaning/data/intermediate/pytest
```

设置 `CHEFMATE_TEST_DSN` 才运行 PostgreSQL 集成测试；测试创建随机 schema 并清理，不向业务 schema 导入。该凭据应只连接允许测试的数据库。

浏览器验收至少覆盖：首页新消息、真实检索、保存餐单、备菜/步骤、刷新与会话恢复、停止、设置保存/返回、个人中心。检查控制台、真实响应以及桌面/窄屏/横屏溢出。验收服务、数据库和停机状态记入[工程档案](../engineering/README.md)，离线替身与真实模型结果分别说明。
