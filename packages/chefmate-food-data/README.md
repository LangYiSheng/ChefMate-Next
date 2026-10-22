# ChefMate Food Data

只读查询本地 USDA 数据，并提供显式下载、校验和安装维护。每条食物保留 FDC ID、数据集与营养来源。

[项目首页](../../README.md) · [数据导览](../../docs/guide/data.md) · [完整接口与维护命令](../../docs/reference/food-data.md)

## 主要能力

- Foundation Foods 与 SR Legacy 的独立记录和本地全文检索。
- 每 100g 营养解析、未知值与来源保留。
- 安装版本、URL、哈希与计数记录，先验证新库再替换。
- 日常查询不下载数据，数据文件不打包进 wheel。

## 快速开始

```python
from chefmate_food_data import LocalFoodData

foods = LocalFoodData("path/to/usda.db")
foods.require_ready()
candidates = foods.search("potatoes raw", limit=15)
record = foods.get_food(str(candidates[0]["fdcId"])) if candidates else None
```

数据库路径由宿主提供。安装/恢复使用 `chefmate-usda install`，实际状态使用 `chefmate-usda status`，完整参数见[维护参考](../../docs/reference/food-data.md)。

## 代码入口

[src/chefmate_food_data/](src/chefmate_food_data)包含查询、营养解析、数据集维护与 CLI。数据类型与覆盖区别以 [USDA 官方说明](https://fdc.nal.usda.gov/data-documentation/)为准。

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages/chefmate-food-data/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/food-data-tests
```

来源与本地安装历史见[USDA 验收](../../docs/engineering/records/data-cleaning-local-usda.md)，历史数量不等于当前安装状态。
