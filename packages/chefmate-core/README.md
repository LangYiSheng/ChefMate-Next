# ChefMate Core 兼容层

此包保留旧导入和 CLI，业务实现已经迁到：

- [chefmate-contracts](../chefmate-contracts/README.md)：共享菜谱与词表。
- [chefmate-food-data](../chefmate-food-data/README.md)：本地 USDA 查询、维护与营养解析。
- [chefmate-recipe-processing](../chefmate-recipe-processing/README.md)：同步/异步菜谱处理。

旧 `from chefmate_core import ...`、`chefmate_core.schemas/nutrition/estimation` 和 `python -m chefmate_core.cli` 继续转发到新实现。`chefmate-usda` 命令由 food-data 包提供，参数不变。旧数据库、JSON 和快照无需迁移。

新代码请从三个职责明确的包导入；本轮不删除兼容层。安装、依赖与验证参见 [包总览](../README.md)。
