# 工程档案

这里回答“当时解决了什么、怎样验证、还有什么限制”。当前操作、规则和接口请从[文档中心](../README.md)阅读；历史结论只覆盖记录中的版本、环境与场景。

## 发布与关键验收

| 要追溯的内容 | 记录 |
| --- | --- |
| 当前发布范围与来源 | [0.3.0：账号、管理平台与部署](records/2026-10-28-release-0.3.0.md) |
| 智能体和多端交互修复 | [真实模型流程、业务补丁与界面打磨](records/2026-10-28-agent-and-ui-polish.md) |
| 账号、个人数据与授权 | [账号与 PostgreSQL 改造](records/2026-10-27-accounts-admin-postgresql.md)、[日常旧数据迁移](records/2026-10-27-daily-postgresql-migration.md) |
| 会员、计价与额度同步 | [会员与多模型](records/2026-10-27-membership-models-preferences.md)、[实时同步与尾数使用](records/2026-10-28-quota-sync-and-consistency.md) |
| Docker、备份与运行隔离 | [备份恢复验收](records/2026-10-27-docker-acceptance.md)、[72 账号持续运行检查](records/2026-10-27-multiuser-soak.md) |
| 设置和管理平台布局 | [管理与个人设置](records/2026-10-28-admin-settings-ui.md)、[称呼编辑与侧目录](records/2026-10-28-profile-editor-and-outline.md) |
| 较早发布 | [0.2.2](records/2026-10-27-release-0.2.2.md)、[0.2.1](records/2026-10-24-release-0.2.1.md)、[0.2.0](records/2026-10-22-release-0.2.0.md) |

## 完整档案

- [实现与验收记录](records/README.md)：按日期浏览全部记录，保留真实结果、失败、限制和提交来源。
- [历史计划与分析](plans/README.md)：查看任务开始时的拆分及其后续记录，不作为当前待办清单。

历史计划的勾选状态不自动代表现行实现；旧测试数量、模型表现与服务器状态也不代表今天重新验证的结果。

## 新文档放在哪里

| 内容 | 归属 |
| --- | --- |
| 如何使用、部署或开发 | [指南](../guide/README.md) |
| 当前产品与工程约定、选择理由 | [设计规范](../design/README.md) |
| 配置、字段、端点和模块 API | [接口与数据参考](../reference/README.md) |
| 一次实现和实际验收 | `records/YYYY-MM-DD-topic.md`，同时加入记录索引 |
| 开始实施前的分析和计划 | `plans/YYYY-MM-DD-topic.md`，同时加入计划索引 |

历史事实保留在原记录；新的产品协议写入指南或参考，新形成的通用约定写入设计规范。运行数据和凭证配置不提交，截图说明所属验收场景与可访问性。
