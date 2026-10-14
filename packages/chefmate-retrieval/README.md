# ChefMate Retrieval

独立、只读的食材与菜谱工具包。智能体理解用户需求、选择当前库中的真实候选；本包严格执行结构化条件并返回匹配依据。它不调用模型、不修改业务数据、不生成 SQL 给模型执行，也不自动删除条件或放宽查询。

## 安装与调用

从仓库根目录安装以下本地包到 Python 3.12+ 环境：

```powershell
python -m pip install packages/chefmate-contracts packages/chefmate-food-data packages/chefmate-storage packages/chefmate-retrieval
```

应用负责提供 PostgreSQL DSN、schema，以及可选的本地 USDA 数据库路径。本包不读取 `.env`、不执行迁移、不启动数据库。不要把凭据放进智能体工具参数或日志。

```python
from chefmate_storage import RecipeStore
from chefmate_retrieval import RetrievalService, IngredientLookupRequest

tools = RetrievalService(RecipeStore(database_url, schema="chefmate"))
candidates = tools.find_ingredients({"queries": ["虾", "shrimp", "prawn"]})
schema = IngredientLookupRequest.model_json_schema()
```

`RetrievalService(store, foods=None)` 的四个同步方法返回可序列化的字典：

| 方法 | 输入 | 主要输出 |
| --- | --- | --- |
| `find_ingredients` | `IngredientLookupRequest` 或字典 | 当前库中的食材候选、使用数量、匹配原因和原文例子 |
| `search_recipes` | `RecipeSearchRequest` 或字典 | 菜谱摘要、匹配食材、营养、warnings、分页 |
| `get_recipe` | `RecipeDetailRequest` 或字典 | `recipe` 下的完整食材、用量、步骤、营养与原文依据 |
| `get_search_options` | 无参数 | 当前快照、代码/中文名称/关联菜谱数、营养单位、排序字段 |

公开参数模型支持 `model_json_schema()`，可以直接用于框架无关的工具注册。参数拒绝额外字段、空字符串、错误类型、非法数值、未知难度和排序字段；数据库中的食材引用及标签代码还会在同一读取事务内检查。

## 食材查找

`queries` 最多 10 个词，按 `ingredient_id`、标准名称和已确认别名进行大小写无关的字面子串检索。`%`、`_` 和反斜线按普通字符处理；原文不参与搜索，因此“炒虾用油”不会让食用油变成虾候选。工具不会自动生成同义词或推断类别。

每个候选按 `(ingredient_id, canonical_name)` 区分，包含：

- `selector`：可直接用于菜谱查询的真实选择器。
- `aliases`、`categories`、`fdc_ids`、`recipe_count` 和 `recommendable_recipe_count`。
- `matched_by`、`match_evidence`：精确名称、别名、ID，或仅共用 USDA 参考。
- `reference_only`：仅经 USDA 参考匹配时为 `true`，需检查组成。
- `examples`：至多 3 条使用实例，包含菜谱、原文、克重与估算标记。

优先展示精确名称，其次精确别名、ID，然后子串匹配，最后参考匹配；同等分数按 ID 和标准名称稳定排序。`limit` 为 1–100，默认 30；`total` 和 `has_more` 表示是否还有候选。候选全集在 PostgreSQL 内按查询条件筛选后才在检索层排序，适合当前小型目录；本版本不提供大型目录的全文索引或候选游标。

可以传 `usda_query` 搜索已安装的 `LocalFoodData`，或传 `fdc_ids` 辅助查找。USDA 候选只补充当前菜谱食材引用；没有关联的 USDA 食材不会伪造成本库食材。共享 FDC ID 不代表同义、相同组成或必然含虾。没有配置或无法读取 USDA 库时会返回 `usda.error`，只有 USDA 查询的请求返回 `status="usda_unavailable"`。

## 菜谱条件

```python
result = tools.search_recipes({
    "ingredient_groups": [
        {"any_of": [{"ingredient_id": "shrimp"}, {"ingredient_id": "peeled_shrimp"}]},
        {"any_of": [{"ingredient_id": "tomato"}]},
    ],
    "exclude_ingredients": [{"ingredient_id": "shrimp", "canonical_name": "虾仁"}],
    "max_total_time_min": 30,
    "difficulties": ["beginner", "normal"],
    "available_tools": ["stove", "wok", "pot"],
    "nutrients": [{"name": "protein_g", "basis": "per_serving", "minimum": 20,
                   "allow_estimated": True}],
    "sort": {"field": "total_time_min", "direction": "asc"},
    "expected_snapshot": candidates["snapshot"],
    "limit": 20,
    "offset": 0,
})
```

此例要求包含一种指定虾、同时包含番茄，排除 ID 为 `shrimp` 且名称为“虾仁”的具体形态。示例中的 ID 应以自己的 `find_ingredients` 结果为准。不同食材组之间为 AND，组内为 OR；多个出现项不会重复返回菜谱。

支持名称、总耗时上下限、三种难度、`taste_tags_all` / `taste_tags_any` / `exclude_taste_tags`、`cooking_methods_any`、`nutrition_tags_all`、`exclude_allergens` 和推荐资格。所有不同条件同时满足。口味、方法、厨具等代码从 `get_search_options` 获取，未识别的选项返回明确错误，而不是删除条件。

`available_tools=null` 不限制厨具；`[]` 表示没有厨具，只匹配没有已记录必需厨具的菜谱。提供集合时，菜谱已记录的全部必需厨具必须为该集合的子集。

六种营养范围支持 `total` / `per_serving`。上下限包含边界；未知值为 `null`，范围筛选不匹配未知值。`allow_estimated=false` 只检查正在筛选的指标和基准，不因其他指标估算而排除整道菜。每份营养沿用菜谱人数基准，并返回人数是否估算；工具不换算人数、不自行设定“低脂”等阈值。

支持菜谱 ID、名称、总时间、难度及 12 种营养数值排序。SQL 先筛选、计数、排序，再分页；相同排序值以菜谱 ID 升序稳定裁决，未知值始终放最后。难度使用 `beginner → normal → advanced`。`limit` 为 1–100，默认 20，`offset` 为 0–100000。

搜索摘要包含具体命中食材、对应组编号、原文与克重标记、菜谱 warnings，以及营养数值/逐指标估算标记。`matched_groups` 从 0 开始。当前库没有可靠的主料、必选、可选字段，不支持将这些语义当成硬过滤；可选组成只能据原文和 warnings 解释。标签缺失也不能证明不辣或过敏安全。

## 一致性与明确结果

每次查找、搜索、详情和选项读取都绑定一个 `RecipeStore.read_session()`：`REPEATABLE READ, READ ONLY` 事务与共享数据集锁保证该次响应内的元数据、候选、计数、分页和证据一致。没有缓存目录，不会将不同数据版本混合。

`snapshot` 同时含 `data_version` 与 UTC `imported_at`。调用方应在“选择食材 → 查询 → 详情 → 下一页”的过程中传回 `expected_snapshot`。即使发布方复用同一 `data_version`，重新导入的时间变化也能使旧引用失效。不同但等价的 ISO 时间偏移会规范为 UTC。

| `status` | 含义 |
| --- | --- |
| `ok` | 查询成功；有效条件命中 0 条时仍为 `ok`，条件保留在 `normalized_query` |
| `snapshot_changed` | 当前数据集已变化；没有继续执行引用查询，应重新找候选 |
| `invalid_selection` | 某食材 ID/形态或选项不存在；列出 `unknown_ingredients` / `unknown_options` |
| `not_found` | 该快照不存在此菜谱，`recipe=null` |
| `usda_unavailable` | 仅 USDA 的查找请求无法执行，提供具体原因 |

请求结构错误抛出 Pydantic `ValidationError`；连接和数据库故障继续抛出底层异常，由应用转为合适的错误响应。工具没有隐藏重试或失败时的假数据。

## 验证

离线测试验证严格参数、快照失效、空厨具集合、原文证据、USDA 参考与身份边界。PostgreSQL 测试复用存储包的真实统一导出形状，使用随机 `cm_test_*` schema 并在结束后清理，覆盖分组逻辑、形态排除、分页/未知值排序、逐指标估算和同版本重新导入。设置 `CHEFMATE_TEST_DSN` 才运行隔离数据库测试；不会改当前 `chefmate` schema。

```powershell
python -m pytest packages/chefmate-retrieval/tests packages/chefmate-storage/tests -q -p no:cacheprovider
```
