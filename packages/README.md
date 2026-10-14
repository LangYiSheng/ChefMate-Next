# ChefMate 可复用包

| 包 | 职责 | 依赖 |
|---|---|---|
| [chefmate-contracts](chefmate-contracts/README.md) | 菜谱结构、营养数值、词表及校验 | Pydantic |
| [chefmate-food-data](chefmate-food-data/README.md) | 本地 USDA 查询、维护和每 100g 营养解析 | contracts、requests |
| [chefmate-recipe-processing](chefmate-recipe-processing/README.md) | 文本结构化、食材匹配、营养计算和标记估算 | contracts、food-data、OpenAI SDK |
| [chefmate-storage](chefmate-storage/README.md) | PostgreSQL 当前数据集存储与一致只读查询 | Psycopg、ijson |
| [chefmate-retrieval](chefmate-retrieval/README.md) | 食材候选、分组筛选、查询证据与工具参数 | storage、contracts、food-data |
| [chefmate-workflow](chefmate-workflow/README.md) | 厨房任务、冻结快照、闭合动作与界面投影 | retrieval、Pydantic |
| [chefmate-agent](chefmate-agent/README.md) | 标准 Agent、middleware、多轮历史、UI工具与按需 Skills | retrieval、LangChain、LangGraph、ChatOpenAI |

均为 Python 3.12+ 包，不导入 `data_cleaning`。清洗端持有配置、业务数据库、审阅、批次与导出；未来后端可独立调用相同能力，按需要转换成更简化的服务模型。

在项目根目录安装开发版本，不改变现有第三方依赖：

```powershell
uv pip install --python data_cleaning/.venv/Scripts/python.exe --no-deps -e packages/chefmate-contracts -e packages/chefmate-food-data -e packages/chefmate-recipe-processing
```

新环境先安装 `data_cleaning/requirements-lock.txt`（在 `data_cleaning` 目录执行，editable 路径相对于该目录）。发布到其他项目时安装构建出的 wheel，三个业务包版本为 `0.1.0`。

验证：

```powershell
data_cleaning/.venv/Scripts/python.exe -m pytest data_cleaning/tests packages -p no:cacheprovider --basetemp=data_cleaning/data/intermediate/package-tests
data_cleaning/.venv/Scripts/ruff.exe check --config data_cleaning/pyproject.toml data_cleaning packages
```

数据库格式、历史菜谱 JSON、食材身份和快照 key 保持兼容。菜谱处理包支持文本/Markdown；智能体与检索由独立包负责。本地聊天工作台见 [chefmate-playground](../apps/chefmate-playground/README.md)。
