# ChefMate Contracts

共享菜谱、营养、食材与词表契约。为清洗、处理、存储和在线应用提供共同的数据语言，没有模型、数据库或 App 依赖。

[项目首页](../../README.md) · [字段说明](../../docs/reference/recipe-fields.md) · [数据导览](../../docs/guide/data.md)

## 主要能力

- `RecipeDocument/ConvertedRecipe`、食材、引用、营养与严格模型。
- 难度和词表类别、代码、显示名称与启用状态。
- 稳定排序和 SHA256 的词表快照，历史读取可使用当时版本。
- 人数/克重依据与未知营养语义，AI 提取结构不容纳任意营养值。

## 快速开始

```python
from chefmate_contracts import RecipeDocument, default_vocabulary, validate_recipe_vocabulary

document = RecipeDocument.model_validate_json(saved_json)
validate_recipe_vocabulary(document, default_vocabulary())
```

`RecipeDocument` 是现有 `ConvertedRecipe` 的别名，保留字段和序列化结构。历史文档可先读取，再在生成或编辑时检查当前词表。

## 代码入口

[src/chefmate_contracts/](src/chefmate_contracts)定义模型、默认词表和快照。每次 `default_vocabulary()` 返回独立条目；`VocabularySnapshot` 校验内容哈希。

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages/chefmate-contracts/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/contracts-tests
```

字段的业务理由、估算和审核区别见[数据的一生](../../docs/guide/data.md)。
