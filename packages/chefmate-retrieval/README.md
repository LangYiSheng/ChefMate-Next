# ChefMate Retrieval

只读的食材候选与多约束菜谱查询。智能体解释需求、选择真实候选，工具执行确定条件并返回证据，不静默放宽限制。

[项目首页](../../README.md) · [数据导览](../../docs/guide/data.md) · [完整 API 与查询案例](../../docs/reference/retrieval.md)

## 主要能力

- 按真实名称、别名与引用查找食材，保留形态和匹配原因。
- 食材组内 OR、组间 AND，同时筛选耗时、厨具、难度、标签与营养。
- 稳定排序、分页、逐指标估算标记和未知值规则。
- 一次查询内一致读取，跨请求通过数据版本和导入时间检查引用。
- 使用真实代码或精确且唯一的显示名称，显式返回规范化结果；无效选项按字段提供可用代码与修正提示。

## 快速开始

```python
from chefmate_storage import RecipeStore
from chefmate_retrieval import RetrievalService

service = RetrievalService(RecipeStore(database_url, schema="chefmate"))
candidates = service.find_ingredients({"queries": ["虾", "shrimp", "prawn"]})
options = service.get_search_options()
```

DSN 由调用方提供。真实选择器取自 candidates，选项取自 options；不要从示例猜 ID。四个公开方法为 `find_ingredients/search_recipes/get_recipe/get_search_options`，参数模型可导出 JSON Schema。
优先使用候选返回的 `selector` 和选项的 `code`。口味、方法、厨具、营养标签和过敏原的精确唯一 `label` 可规范为代码，记录在 `normalized_options`；未知或歧义值仍拒绝。`invalid_selection` 的 `unknown_options/valid_options/recovery` 指向具体字段，修正时保留其他有效条件。

`ingredient_groups` 是组内 OR、组间 AND，库存列表不等于所有菜必须同时含全部库存。`taste_tags_any` 也是硬过滤。`available_tools` 需要完整厨具集合，`["wok"]` 不包含 `stove`；不完整清单应省略过滤，再核对候选的真实厨具要求。`get_search_options.input_contracts` 返回这些使用边界。

## 代码入口

[service.py](src/chefmate_retrieval/service.py)组织查询，[models.py](src/chefmate_retrieval/models.py)定义闭合参数。数据库操作委托 Storage，不调用模型或执行任意 SQL。

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages/chefmate-retrieval/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/retrieval-tests
```

真实 PostgreSQL 测试需要 `CHEFMATE_TEST_DSN`，使用随机测试 schema。完整参数、零结果、快照失效和复合食材边界见[接口参考](../../docs/reference/retrieval.md)。
