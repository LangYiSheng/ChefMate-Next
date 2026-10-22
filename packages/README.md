# ChefMate 可复用包

七个 Python 3.12+ 包为核心 App 和清洗工具提供共同能力。每个包拥有自己的契约与测试，不反向导入应用，也不读取应用配置。

[项目首页](../README.md) · [模块地图](../docs/guide/development.md) · [数据的一生](../docs/guide/data.md) · [接口参考](../docs/reference/README.md)

## 包与边界

| 包 | 职责 | 主要依赖 |
| --- | --- | --- |
| [Contracts](chefmate-contracts/README.md) | 菜谱、营养、词表与快照契约 | Pydantic |
| [Food Data](chefmate-food-data/README.md) | 本地 USDA 读取与显式安装维护 | contracts、requests |
| [Recipe Processing](chefmate-recipe-processing/README.md) | AI 提取、候选匹配、确定性营养计算 | contracts、food-data、模型适配器 |
| [Storage](chefmate-storage/README.md) | 当前数据集发布、PostgreSQL 一致读取 | Psycopg、ijson |
| [Retrieval](chefmate-retrieval/README.md) | 食材候选、组合筛选与查询证据 | storage、contracts、food-data |
| [Workflow](chefmate-workflow/README.md) | 冻结餐单、份量、备菜、步骤、计时与视图 | retrieval、Pydantic |
| [Agent](chefmate-agent/README.md) | 标准执行器、边界校验、上下文与工具扩展 | retrieval、LangChain |

## 快速开始

开发整个工作台时，在仓库根目录同步应用即可安装本地 editable 包：

```powershell
uv sync --project apps/chefmate-playground --extra dev --python 3.12
```

在其他 Python 环境复用某个包时，按该包 README 安装所需本地依赖；例如：

```powershell
python -m pip install ./packages/chefmate-contracts ./packages/chefmate-food-data ./packages/chefmate-storage ./packages/chefmate-retrieval
```

数据库连接、模型与持久化由调用方注入；清洗端持有审阅和发布，App 持有在线个人数据。

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/package-tests
```

真实数据库测试需要显式测试 DSN。详见[开发指南](../docs/guide/development.md)，每个包的细节见[接口参考](../docs/reference/README.md)。
