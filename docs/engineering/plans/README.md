# 历史计划与分析

[工程档案](../README.md) · [实现与验收记录](../records/README.md) · [当前文档](../../README.md)

这里保留任务开始时的问题、拆分方案和验证设想，不是当前开发待办。未勾选不表示今天仍未实现，已勾选也不替代代码与实际验收；执行中改变的范围按对应记录核对。

## 2026-10-27

| 计划 | 当时关注点 | 后续记录 |
| --- | --- | --- |
| [账号、管理平台与 Docker](2026-10-27-accounts-admin-docker.md) | 登录、用户隔离、管理员、部署及初始实施顺序 | [账号与 PostgreSQL](../records/2026-10-27-accounts-admin-postgresql.md)、[会员与多模型](../records/2026-10-27-membership-models-preferences.md)、[0.3.0 发布](../records/2026-10-28-release-0.3.0.md) |

## 2026-10-23

| 计划 | 当时关注点 | 后续记录 |
| --- | --- | --- |
| [React 迁移完整性与界面复核](2026-10-23-react-parity-review.md) | 迁移遗漏、会话切换、计时、设置与统一视觉 | [复核与修复](../records/2026-10-23-react-parity-review.md) |
| [React 前端完整迁移](2026-10-23-react-migration.md) | 替换旧前端、覆盖完整交互、保存用户数据 | [迁移验收](../records/2026-10-23-react-migration.md) |

## 2026-10-22

| 计划 | 当时关注点 | 后续记录 |
| --- | --- | --- |
| [浏览器批注与反馈](2026-10-22-browser-feedback.md) | 操作、阅读、设置和版本展示 | [工作台与导航](../records/2026-10-22-workbench-navigation-polish.md)、[代码与文档整理](../records/2026-10-22-code-organization.md) |

## 2026-10-21

| 计划 | 当时关注点 | 后续记录 |
| --- | --- | --- |
| [工具、提示词与契约审计](2026-10-21-agent-tool-contract-audit.md) | 参数、流式、预算与真实故障链 | [对话与组件](../records/2026-10-21-conversation-refinement.md)、[后续真实模型迭代](../records/2026-10-24-conversation-quality.md) |
| [控件与平铺工作区完善](2026-10-21-flat-workspace-refinement.md) | 控件、层级和页面边界 | [平铺工作区与控件](../records/2026-10-21-flat-workspace.md) |
| [营养手记、组件与可观测性](2026-10-21-nutrition-components-observability.md) | 个人饮食、组件配置与诊断入口 | [营养手记与模型观测](../records/2026-10-21-nutrition-components.md) |
| [个性化厨房升级](2026-10-21-personal-kitchen.md) | 共享份量、个人菜谱、记忆与手记 | [个性化厨房](../records/2026-10-21-personal-kitchen.md) |

新增计划使用 `YYYY-MM-DD-topic.md`，写清问题依据、边界和验证方法；完成后在对应实现记录中说明实际结果和变化，不重写历史计划冒充事后结论。
