# ChefMate 文档

当前 App 版本为 **0.3.0**。账号、会员额度、管理平台、Docker/PostgreSQL 和交互改进见 [0.3.0 发布记录](engineering/records/2026-10-28-release-0.3.0.md)；设置中的“版本更新日志”保留各版本与提交来源。

## 按要做的事情开始

| 现在要做什么 | 从这里读 |
| --- | --- |
| 第一次了解 ChefMate | [从一顿饭开始](guide/overview.md) → [智能体的核心技术](guide/agent.md) → [数据的一生](guide/data.md) |
| 注册、管理偏好、查看额度，或管理站点 | [账号与管理员操作](guide/accounts.md) |
| 启动、升级或备份服务 | [Docker 部署与备份](guide/deployment.md) |
| 迁移已有 SQLite 个人数据 | [显式迁入 PostgreSQL](guide/migrate-sqlite.md) |
| 修改代码和运行检查 | [模块地图与开发指南](guide/development.md) |
| 查规则、字段或接口 | [设计规范](design/README.md) · [接口与数据参考](reference/README.md) |
| 追溯某次改动和实测范围 | [工程档案](engineering/README.md) |

## 目录怎样分工

| 目录 | 内容 |
| --- | --- |
| [指南](guide/README.md) | 从产品流程到使用、部署、迁移与开发，说明如何完成一件事 |
| [设计规范](design/README.md) | 现行领域边界、交互与视觉、工程约定及选择理由 |
| [接口与数据参考](reference/README.md) | App 配置和端点、独立模块 API、数据字段与协议 |
| [工程档案](engineering/README.md) | [实现与验收记录](engineering/records/README.md)及[历史计划](engineering/plans/README.md)，按记录时点阅读 |

当前行为以源码、现行指南和参考为核对入口。历史档案保留当时的事实与限制，旧测试数量、模型表现、部署状态和计划勾选不会自动延伸到今天。

文中的源码和测试链接用于追到实现；外部引用解释框架、协议或数据的原始来源。产品描述与设计收益不等同于未开展的量化效果承诺。
