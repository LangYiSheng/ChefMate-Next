# ChefMate Storage

把审核后的统一导出发布到 PostgreSQL，并提供一致的菜谱查询。正式 schema 只保存当前数据集，发布前校验、事务内替换。

[项目首页](../../README.md) · [数据导览](../../docs/guide/data.md) · [完整 CLI / Python API / 表结构](../../docs/reference/storage.md)

## 主要能力

- 导出结构、SHA256、身份、关系与营养一致性校验。
- 显式整库替换，失败保留旧版本；数据库结构与数据版本分开。
- 规范化关系表、参数化条件、稳定分页与营养来源字段。
- Repeatable Read 只读会话与数据集锁约定。

## 快速开始

在仓库根目录安装，连接信息由调用方提供：

```powershell
python -m pip install -e packages/chefmate-storage
chefmate-storage --help
```

```python
from chefmate_storage import RecipeStore, RecipeFilter

store = RecipeStore(database_url, schema="chefmate")
page = store.search(RecipeFilter(name="鸡", max_total_time_min=30), limit=20)
```

新库准备与发布是显式维护操作，分别使用 `migrate`、`validate`、`import --replace`。完整命令和破坏性替换语义见[操作参考](../../docs/reference/storage.md)。工作台正常启动不执行这些操作。

## 代码入口

[database.py](src/chefmate_storage/database.py)管理连接与读会话，[importer.py](src/chefmate_storage/importer.py)校验导入，[queries.py](src/chefmate_storage/queries.py)构造参数化查询，[migrations/](src/chefmate_storage/migrations)保存结构版本。

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages/chefmate-storage/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/storage-tests
```

真实数据库测试显式使用 `CHEFMATE_TEST_DSN`。本包不管理数据库进程、不运行模型，也不替代营养重算；测量范围见[历史存储验收](../../docs/engineering/records/storage-baseline.md)。
