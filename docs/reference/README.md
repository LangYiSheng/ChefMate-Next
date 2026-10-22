# 接口与数据参考

这里集中保存配置、字段、Python API 和交互协议的细节，供实现或排错时查阅。
第一次认识项目，建议从[一顿饭的完整流程](../guide/overview.md)开始，再读[智能体](../guide/agent.md)、[数据](../guide/data.md)和[开发指南](../guide/development.md)。

## 按要解决的问题查

| 参考文档 | 主要内容 | 对应入口 |
| --- | --- | --- |
| [核心 App](workbench.md) | 启动、配置优先级、HTTP、SSE、个人资料与状态协议 | [应用 README](../../apps/chefmate-playground/README.md) |
| [Agent](agent.md) | 模型与工具注入、middleware、上下文、Skills、事件、失败边界 | [智能体包](../../packages/chefmate-agent/README.md) |
| [Workflow](workflow.md) | 冻结菜谱、工作区、版本与动作、阶段门槛、份量、计时 | [工作流包](../../packages/chefmate-workflow/README.md) |
| [Retrieval](retrieval.md) | 食材候选、组内 OR/组间 AND、快照、选项、证据与营养过滤 | [检索包](../../packages/chefmate-retrieval/README.md) |
| [Storage](storage.md) | PostgreSQL 关系模型、导入验证、替换事务、CLI 与只读会话 | [存储包](../../packages/chefmate-storage/README.md) |
| [Food Data](food-data.md) | 本地 USDA 维护、只读查询、来源和公开 API | [食品数据包](../../packages/chefmate-food-data/README.md) |
| [Recipe Processing](recipe-processing.md) | AI 接口注入、同步/异步处理、营养计算与估算 | [处理包](../../packages/chefmate-recipe-processing/README.md) |
| [清洗平台](data-cleaning.md) | 单篇/批量转换、人工审核、共享映射、配置与导出 | [清洗平台 README](../../data_cleaning/README.md) |
| [菜谱字段](recipe-fields.md) | 原文依据、身份、用量、人数、步骤、营养、审核和导出字段 | [共享契约包](../../packages/chefmate-contracts/README.md) |

## 几处需要一起读的边界

- **菜谱引用**：检索的真实 ID 和数据集快照、工作流的任务冻结、Agent 的来源证据检查分别覆盖不同环节。
- **版本**：数据集快照、工作区业务版本、展示版本，以及个人记录修订号各有职责，不能互换。
- **营养**：做菜用量与本人摄入分别保存；单人真实完成的自动登记和多人逐菜份额由 App 实施。
- **持久化**：对话完整历史提交、厨房原子动作、个人资料保存分别有边界，生成失败不会统一回滚已成功写入。
- **能力配置**：组件清单管理产品能力；按需 Skill 正文提供指引，具体权限仍由工具和业务服务检查。

字段和端点以对应源码、运行应用的 `/docs` 与测试为最终核对入口。
设计原因见[规范目录](../design/README.md)，按日期保存的实测、旧方案和迁移考察见[工程档案](../engineering/README.md)；旧记录里的测试数量、模型表现或服务器状态不自动代表当前运行环境。
