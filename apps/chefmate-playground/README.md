# ChefMate App

把聊天、选菜、备菜、烹饪和个人饮食记录连接起来的本地厨房工作台。React 19.3.1、TypeScript 7 和 Vite 8 前端负责交互，FastAPI 负责 HTTP/SSE、依赖装配与持久化。

[项目首页](../../README.md) · [完整使用与接口参考](../../docs/reference/workbench.md) · [模块地图](../../docs/guide/development.md) · [设计规范](../../docs/design/README.md)

## 主要能力

- 真实菜谱检索、共享餐单、可核对的备菜与步骤、持久化计时。
- 文字/图片输入、对话内菜谱审核与修改、按版本确认保存、收藏、删除与恢复。
- 食光手记、本人食用份额、长期记忆、独立模型用量账本。
- 七个系统组件开关、模型协议与图片能力配置；开发者模式提供上下文分类条、工具目录、请求时间线与树视图。
- 统一主题控件、设置标签页与宽屏目录；普通外观和对话偏好可由助手按白名单读取及修改。
- 设置中的版本更新日志，按版本查看已发布的功能与改进。
- 单会话运行、显式停止、失败重试与断线后状态恢复。

## 快速开始

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

## 代码入口

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

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest apps/chefmate-playground/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/app-tests
npm --prefix apps/chefmate-playground/frontend test
npm --prefix apps/chefmate-playground/frontend run build
```

会话与个人数据位于被忽略的 `.runtime`，与只读基础菜谱库独立。完整 API、状态协议与可用性边界见[接口参考](../../docs/reference/workbench.md)，实际模型与浏览器证据见[工程档案](../../docs/engineering/README.md)。
