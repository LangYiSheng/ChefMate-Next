# ChefMate 数据清洗工具

把完整 Markdown 菜谱转成可以核对、计算和发布的数据：AI 提取结构，程序校验与计算，人来审阅。

[项目首页](../README.md) · [数据的一生](../docs/guide/data.md) · [完整操作参考](../docs/reference/data-cleaning.md) · [字段说明](../docs/reference/recipe-fields.md)

## 主要能力

- 单篇与后台批次转换，原文和修改历史保留。
- 本地 USDA 候选、共享已审阅映射、确定性营养计算。
- 可选的缺失估算与清晰标记，修改来源后失效。
- 整篇人工审阅、统一 JSON 导出、版本和 SHA256 清单。

## 快速开始

在本目录执行，已有环境和配置时保留原文件：

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
# 编辑本目录 .env，填写模型连接和本地 USDA 路径。
.venv/Scripts/python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

打开[清洗页面](http://127.0.0.1:8501)。导入 Markdown 后即可转换与审阅；USDA 库未安装时，营养查询明确不可用，结构化流程仍可使用。

## 代码入口

| 目录 | 职责 |
| --- | --- |
| [app/](app) | 单篇、批量、数据库、标准食材与设置页面 |
| [models/](models) | 清洗数据库、版本、批次和审阅 |
| [services/](services) | 转换、营养与后台任务编排 |
| [scripts/](scripts) | 初始化、导入、转换、批次与导出 CLI |
| [../packages/](../packages/README.md) | 共享契约、处理和食物查询能力 |

模型与清洗配置由本应用持有，不流入在线检索参数。导出经 Storage 校验后显式发布，不由工作台启动时自动导入。

## 验证

从仓库根目录运行：

```powershell
data_cleaning/.venv/Scripts/python.exe -m pytest data_cleaning/tests -q -p no:cacheprovider --basetemp=data_cleaning/data/intermediate/pytest
```

操作参数、估算与导出限制见[完整参考](../docs/reference/data-cleaning.md)；历史检查已集中到[工程档案](../docs/engineering/README.md)。
