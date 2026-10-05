# ChefMate Contracts

共享菜谱数据和词表定义。无模型 SDK、数据库或应用依赖。

`RecipeDocument` 是现有 `ConvertedRecipe` 的别名，保留字段、默认值和序列化结构。也提供 `ConvertedIngredient`、`RecipeReference`、`Nutrients` 和 `StrictModel`。克重/人数必须有依据，未知营养保持 `None`，菜谱提取结构不容纳 AI 编造的营养值。

```python
from chefmate_contracts import RecipeDocument, default_vocabulary, validate_recipe_vocabulary

document = RecipeDocument.model_validate_json(saved_json)
# 历史结构读取不检查当前是否启用；生成或编辑时再检查实际词表。
validate_recipe_vocabulary(document, default_vocabulary())
```

`default_vocabulary()` 每次读取包内默认词表并返回独立条目。`VocabularyEntry` 包含 `kind/code/label/enabled`，`VocabularyKind` 定义类别，`Difficulty` 固定为 beginner/normal/advanced。其余条目代码可维护，显示名与代码分离。

`validate_vocabulary(entries)` 接受条目列表或 `VocabularySnapshot`；兼容旧 `status` 字段，只有 approved 表示启用。旧的 kind/code 二字段输入使用 code 作为显示名。检查重复代码、非法类别、难度和空显示名。

`vocabulary_snapshot(entries)` 按类别/代码排序，返回 schema_version=1、完整 entries 和 sha256；哈希包含标签和启用状态，与输入顺序无关。`VocabularySnapshot.model_validate_json()` 验证导入内容的哈希。使用导出快照读取历史词表，避免实时词表变动影响历史含义。
