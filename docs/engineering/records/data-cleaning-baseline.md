> 工程档案：保留当时的计划、范围与验收结论。当前能力请从[文档入口](../../README.md)开始阅读，历史测试数量不代表本轮结果。

# Phase 1 验证记录

这是 Phase 1 的历史记录。完整实现的最新验证见 [VERIFICATION_FULL.md](data-cleaning-full.md)。

验证日期：2026-09-23。所有测试数据与默认数据库隔离。

## 环境

- Windows / PowerShell
- Python 3.12.14，独立 `.venv`
- Streamlit 1.64.0 / Pydantic 2.13.5 / python-dotenv 1.2.3
- 测试 pytest 9.1.1；完整安装版本见 `requirements-lock.txt`

系统登记的 Python 3.13 启动失败，因此实际使用可用的 Python 3.12 创建虚拟环境。沙箱内进程不能创建数据库目录，验证时使用沙箱外执行，未修改任何系统权限。

## 自动化

```text
python -m pytest --basetemp=data/intermediate/final-tests
12 passed in 3.52s

ruff check --config pyproject.toml app models schemas scripts services tests config.py
All checks passed!
```

覆盖：

1. 食材原名、原始用量、人数原句、操作补充、原始章节保留。
2. 重复导入/提取不增加统计、不撤销人工审核。
3. 源文件内容变化时新增版本并撤销旧结果，审核历史保留。
4. 错误格式/错误编码单条失败，不影响其他菜谱；原始字节保留，可重试。
5. `CLEAR` 校验、清空后重新导入及枚举初始化。
6. 过期写入冲突拒绝。
7. 仅已审核导出、来源信息与文件 SHA-256 校验。
8. Pydantic 拒绝额外营养字段和负时间；不确定用量保持 null。
9. 拒绝使用高于程序支持版本的 SQLite。
10. 显式强制重提取撤销已审核状态。
11. 解析失败记录可直接人工拒绝，失败重试保留拒绝决定。
12. Streamlit AppTest：各页面、审核通过、清空确认保护。

## 真实 HowToCook 单条冒烟验证

来源：[HowToCook 可乐鸡翅](https://github.com/Anduin2017/HowToCook/blob/master/dishes/meat_dish/%E5%8F%AF%E4%B9%90%E9%B8%A1%E7%BF%85.md)。

下载至 `data/intermediate/real-smoke/`，数据库 `data/intermediate/real-smoke.db`：

```text
import: imported=1, skipped=0, failed=0
extract: extracted=1, skipped=0, failed=0
菜谱总数=1，已提取=1，提取失败=0，原始食材数=9，待审核菜谱=1
```

这是一条真实样本验证，不代表已验证整个上游数据集。完整数据集未导入默认数据库。

## 真实浏览器验收

使用 Codex 内置浏览器访问本机 Streamlit，验证数据库为 `data/intermediate/browser-smoke.db`，含两个自编测试菜谱：

- Dashboard 显示 2 道菜、5 个原始食材、0 提取失败；视觉布局正常。
- 搜索“马铃薯”后仅显示此食材，出现次数 2、关联菜谱数 2，原名未被统一。
- 菜谱页显示原文、提取 JSON、可编辑表单及历史入口；保存“通过”和审核备注成功。
- 数据管理默认勾选“仅导出审核通过的数据”；导出成功。
- 实际导出目录：`data/exported/20260923T082025_d75e5825/`，manifest 显示 `recipe_count=1`、`approved_only=true`、`ready_for_recommendation=false`。

默认 `database/cleaning.db` 已初始化且保持空库，供正式导入使用。默认空库统计命令正常输出中文，无测试数据混入。

## 尚未验证/实现

Phase 2 的 AI 调用、标准食材管理、固定映射/合并以及 Phase 3 的 USDA 和营养计算尚未实现，因此没有发出相关 API 请求或声称这些功能已验证。详见 README TODO。
