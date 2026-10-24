# 工程档案

这里保存项目逐步形成时的工程证据：`records/` 记录实现与验收，`plans/` 保留分析、计划和当时的待办。
档案用于回答“当时解决了什么、怎样验证、还有什么限制”。当前功能与设计请从[文档入口](../README.md)、[设计规范](../design/README.md)和[接口参考](../reference/README.md)阅读。

历史验收只覆盖记录中的版本、环境与场景，不能当作今天重新测试的结果。
历史计划里的未勾选任务不自动成为当前待办，已勾选项目也不替代现行代码和最新验收。本文按记录日期整理；跨日期补充会单独说明。

最近一轮：[三档字号、步骤与专注回复验收](records/2026-10-24-readable-cooking-ui.md)。前轮见[厨房卡片与专注对话交互验收](records/2026-10-24-kitchen-interaction-feedback.md)，当前版本见[0.2.1 工作台与对话内操作验收](records/2026-10-24-release-0.2.1.md)。早期结构整理见[代码与文档整理验收](records/2026-10-22-code-organization.md)，首次版本日志页面见[0.2.0 发布记录](records/2026-10-22-release-0.2.0.md)。

## 验收与实现记录

### 2026-10-24

| 记录 | 关注点 |
| --- | --- |
| [三档字号、步骤与专注回复](records/2026-10-24-readable-cooking-ui.md) | 字号滑条、紧凑步骤、真实对话导航、折叠回复、共享聊天和独立滚动 |
| [厨房卡片与专注对话交互](records/2026-10-24-kitchen-interaction-feedback.md) | 16 条浏览器批注、卡片折叠与重点、阶段切换、工具状态、真实模型和响应式验收 |
| [0.2.1 工作台与对话内操作](records/2026-10-24-release-0.2.1.md) | 统一控件、设置索引、开发者轨迹、普通偏好白名单及菜谱审核确认 |

### 2026-10-23

| 记录 | 关注点 |
| --- | --- |
| [React 前端完整迁移](records/2026-10-23-react-migration.md) | 单一 React 应用、运行状态与草稿、工作区与设置、旧实现移除、真实模型和响应式验收 |

### 2026-10-22

| 记录 | 关注点 |
| --- | --- |
| [代码结构与文档整理](records/2026-10-22-code-organization.md) | 前后端模块拆分、文档重组、接口与实际链路验证 |
| [完成餐食营养登记与清单排版](records/2026-10-22-automatic-intake-and-typography.md) | 单人完成自动登记、多人本人份额、冻结来源、同餐修正、历史不回填、清单与字体 |
| [工作台图标、导航与烹饪交互](records/2026-10-22-workbench-navigation-polish.md) | 导航和操作入口、步骤交互、用量显示、真实浏览器验证 |
| [系统配色、字体与颜色卡](records/2026-10-22-appearance-presets.md) | 外观预设、持久偏好修复、资源和跨页面一致性 |

### 2026-10-21

| 记录 | 关注点 |
| --- | --- |
| [个人菜谱故障与备菜/烹饪界面](records/2026-10-21-recipe-preparation-focus.md) | 故障复现、个人菜谱契约、聚焦交互与验收 |
| [个人菜谱删除与恢复](records/2026-10-21-recipe-recovery.md) | 确认问题、回收站、恢复、隔离数据范围 |
| [对话内饮食组件与观测界面](records/2026-10-21-conversation-refinement.md) | 对话内营养组件、公开过程与开发者观察入口 |
| [平铺工作区与控件](records/2026-10-21-flat-workspace.md) | 页面层级、标签、原生控件、操作区与窄屏 |
| [营养手记、系统组件与模型观测](records/2026-10-21-nutrition-components.md) | 个人饮食草稿、组件边界、用量与观测验证范围 |
| [个性化厨房](records/2026-10-21-personal-kitchen.md) | 共享餐单、个人菜谱、长期记忆和真实模型闭环 |

### 2026-10-13 与跨期工作台基线

| 记录 | 关注点 |
| --- | --- |
| [工作台早期与后续验证汇编](records/playground-baseline.md) | 初始记录为 10 月 13 日，后续追加 10 月 21 日验证；按内部日期分段阅读 |
| [旧版功能迁移矩阵](records/legacy-feature-matrix.md) | 10 月 13 日只读考察旧版代码，列出复用与不迁移边界；代码存在不代表旧外部服务已运行验证 |

### 2026-10-05

| 记录 | 关注点 |
| --- | --- |
| [PostgreSQL 存储基线](records/storage-baseline.md) | 导出验证、事务替换、真实数据库、独立 wheel、查询与演示评分 |

### 2026-09-26

| 记录 | 关注点 |
| --- | --- |
| [本地 USDA 与复用包](records/data-cleaning-local-usda.md) | 本地数据导入、校验与包验证；包含 10 月 5 日产物补充，路径和数量按记录时点理解 |

### 2026-09-24

| 记录 | 关注点 |
| --- | --- |
| [完整 JSON 转换流程](records/data-cleaning-conversion.md) | 单篇完整转换、修改审核与营养补齐 |
| [一键批量 AI](records/data-cleaning-batch.md) | 后台任务、停止继续、检查点和批量边界 |

### 2026-09-23

| 记录 | 关注点 |
| --- | --- |
| [清洗完整流水线](records/data-cleaning-full.md) | 当时分阶段流程的自动化、USDA 与浏览器验证，后续转换流程已有调整 |
| [清洗 Phase 1 基线](records/data-cleaning-baseline.md) | 初始范围、单条冒烟、浏览器测试与当时未实现项 |

## 历史计划与分析

### 2026-10-22

- [浏览器批注与反馈](plans/2026-10-22-browser-feedback.md)：当时操作、阅读、设置与版本反馈的处理计划。

### 2026-10-21

- [工具、提示词与契约审计](plans/2026-10-21-agent-tool-contract-audit.md)：真实故障链、参数与流式问题、预算收敛和分阶段验收方案。
- [控件与平铺工作区完善](plans/2026-10-21-flat-workspace-refinement.md)：控件、层级和页面整理边界。
- [营养手记、组件与可观测性](plans/2026-10-21-nutrition-components-observability.md)：饮食记录、组件配置和诊断入口设计。
- [个性化厨房升级](plans/2026-10-21-personal-kitchen.md)：共享份量、个人菜谱、长期记忆与手记的原始实施拆分。

## 新记录怎样放

新增验收用 `records/YYYY-MM-DD-topic.md`，写明日期、实现范围、检查结果和未覆盖部分；新增计划用 `plans/YYYY-MM-DD-topic.md`，说明问题依据、边界和验证方法。
历史事实保留在档案，新的产品协议进入指南或参考，新形成的通用约定进入设计规范。运行数据和凭证配置不提交；截图引用需说明所属验收场景与可访问性。
