# ChefMate App

把聊天、选菜、备菜、烹饪和个人饮食记录连接起来的本地厨房工作台。原生浏览器前端负责交互，FastAPI 负责 HTTP/SSE、依赖装配与持久化。

[项目首页](../../README.md) · [完整使用与接口参考](../../docs/reference/workbench.md) · [模块地图](../../docs/guide/development.md) · [设计规范](../../docs/design/README.md)

## 主要能力

- 真实菜谱检索、共享餐单、可核对的备菜与步骤、持久化计时。
- 文字/图片输入、个人菜谱草稿、收藏、删除与恢复。
- 食光手记、本人食用份额、长期记忆、独立模型用量账本。
- 系统组件开关、模型协议与图片能力配置、公开工具轨迹和脱敏观测。
- 设置中的版本更新日志，按版本查看已发布的功能与改进。
- 单会话运行、显式停止、失败重试与断线后状态恢复。

## 快速开始

在仓库根目录运行，已有配置文件时保留原文件：

```powershell
uv sync --project apps/chefmate-playground --extra dev --python 3.12
Copy-Item apps/chefmate-playground/.env.example apps/chefmate-playground/.env
# 编辑 .env，填写已有 PostgreSQL 库和自己的模型连接。
uv run --project apps/chefmate-playground chefmate-playground
```

打开[本地工作台](http://127.0.0.1:18765)，核对[服务状态](http://127.0.0.1:18765/api/status)。更换端口可加 `--port 18766`。

应用读取自己的 `.env` 和本地设置覆盖。启动不会建库、迁移基础菜谱、导入数据或下载 USDA。配置字段见 [.env.example](.env.example)，新环境数据准备见[开发指南](../../docs/guide/development.md)。

## 代码入口

| 位置 | 职责 |
| --- | --- |
| [app.py](src/chefmate_playground/app.py)、[app_context.py](src/chefmate_playground/app_context.py) | 服务装配、当前依赖与生命周期 |
| [routes/](src/chefmate_playground/routes) | 系统、会话、工作区、菜谱、个人记录、调试路由 |
| [runtime.py](src/chefmate_playground/runtime.py)、[sessions.py](src/chefmate_playground/sessions.py) | 执行轮次、SSE 与会话聚合 |
| [interactions.py](src/chefmate_playground/interactions.py) | 可信轮次与厨房领域服务绑定 |
| [cookbook.py](src/chefmate_playground/cookbook.py)、[meal_logging.py](src/chefmate_playground/meal_logging.py) | 个人菜谱与饮食仓储 |
| [cooked_intake.py](src/chefmate_playground/cooked_intake.py)、[memory.py](src/chefmate_playground/memory.py) | 本人份额与长期记忆 |
| [static/app.js](static/app.js)、[static/settings.js](static/settings.js) | 前端装配入口与设置入口 |

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest apps/chefmate-playground/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/app-tests
node --test apps/chefmate-playground/static/tests/*.test.mjs
```

会话与个人数据位于被忽略的 `.runtime`，与只读基础菜谱库独立。完整 API、状态协议与可用性边界见[接口参考](../../docs/reference/workbench.md)，实际模型与浏览器证据见[工程档案](../../docs/engineering/README.md)。
