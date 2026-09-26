# 完整流水线验证记录

日期：2026-09-23。Phase 1–3 均已实现并接入后台及 CLI。旧 Phase 1 记录保留在 VERIFICATION.md，本文件为当前完整版本验证依据。

## 自动化检查

环境：Windows / Python 3.12.14 / Streamlit 1.64.0 / Pydantic 2.13.5 / OpenAI SDK 2.54.0。完整依赖版本见 requirements-lock.txt。

```text
ruff check --config pyproject.toml app models schemas scripts services tests config.py
All checks passed!

python -m pytest --basetemp=data/intermediate/full-final-tests
34 passed in 11.77s

python -m compileall -q app models schemas scripts services
通过，无编译错误
```

测试涵盖：

- Phase 1 原始字节/版本、幂等提取、失败隔离、人工拒绝、重试、清空、导出校验。
- 从 v1 到 v2 的非破坏性迁移，旧 raw_name 保留。
- canonical/alias/fixed 优先于 AI；固定映射增改/禁用/删除及复用。
- AI 候选越界、Schema 错误、拒答、枚举越界的有限重试和转人工。
- 使用真实 OpenAI SDK + httpx MockTransport 验证 Responses JSON Schema strict 协议、store=false 和 Pydantic 解析。
- 原始名称合并、标准 ID 合并、别名与历史映射同步。
- 词表禁用持久化、稳定 ID/别名冲突、过期审核写入拒绝。
- USDA 缓存、请求超时、单位校验、失败重试及密钥脱敏。
- 完整合成流程的总营养、每人份、阈值标签及最终审核门槛。
- 缺失糖等营养值保留 null，未知份量不猜，个数不会自动产生分数。
- 来源/USDA/规则变化后撤销下游审核，最终导出剔除未复核结果。
- 最终 JSON schema、来源快照、审核过滤、manifest 和 SHA-256 校验。
- 所有 Streamlit 页面渲染与固定规则保存，原始审核与清空确认。

全部合成营养仅位于隔离测试数据库，不是从 AI 生成的生产营养数据，也没有进入默认工作库。

## 真实 USDA 接口

使用 [USDA 官方文档公开的 DEMO_KEY](https://fdc.nal.usda.gov/api-guide/) 进行了两次只读请求，测试环境变量只在该进程内设置，没有写入 .env。

1. 搜索 chicken breast raw：成功返回 Foundation / SR Legacy / Survey 候选。
2. 获取搜索结果 FDC 2646170（Chicken, breast, boneless, skinless, raw）：成功返回完整详情，生产解析器成功提取每100g指标并写入缓存。

真实结果缺失糖含量，解析结果 sugar_g=null，未补零。现代 Foundation 热量字段可通过官方能量 ID 优先级解析。真实响应、请求记录和缓存保存在 `data/intermediate/usda-live.db`，未写入默认库，未自动替用户审核来源匹配。

## 真实浏览器

隔离数据库：`data/intermediate/full-browser.db`，菜谱和营养来源均明确标注为测试。

- Dashboard 显示全部阶段统计，导航覆盖标准食材、固定规则、USDA、营养及日志。
- 营养页显示总量、每人份、覆盖率、计算说明和标签；页面渲染检查通过。
- 在页面将 high_protein 阈值从 30 改为 31，保存后标签按预期消失。
- USDA 页面展示不可编辑的每100g数值、FDC 标识、完整原始快照和审核控件。
- 在页面重新通过 USDA 审核后，营养页提示“标准化未审核或已过期”，防止跳过依赖复核。
- 返回菜谱页面选择通过标准化审核并保存；数据库核对 status=approved、ready_for_recommendation=true，阈值调整仍然生效。

浏览器验收用的 8502 服务在交付时关闭。正式入口为 8501，使用已迁移的默认 `database/cleaning.db`。

## 密钥与当前验证范围

本次检查确认 OPENAI_API_KEY、OPENAI_MODEL 和用户 USDA_API_KEY 尚未配置。**没有发出真实 OpenAI 模型请求，也没有将模拟 SDK 测试描述为真实模型联调。** 用户在 .env 配置可用模型和密钥后即可发起对应任务。

USDA 的真实搜索/详情已验证；完整数据集、所有 USDA 数据类型和全部模型的质量未逐一验证。工具只接受 Foundation / SR Legacy / Survey 每100g数据，品牌标签基准需后续专门核验。

默认工作数据库保持空库，可自行导入真实 HowToCook；未修改 data_cleaning 目录外的项目代码。
