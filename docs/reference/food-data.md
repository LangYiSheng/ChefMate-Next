# ChefMate Food Data

本地 USDA Foundation 2026-04（363 条，跳过32个空项）与 SR Legacy 2018-04（7,793条）统一检索，各记录保留独立 FDC ID、数据集和原始 JSON，不合并同名食物或平均营养。

SQLite 格式保持 schema version 2：`datasets` 保存版本、URL、SHA256、导入时间和计数；`foods` 保存 FDC ID、名称、数据集和 raw_json；`foods_fts` 保存名称全文索引。ZIP 和数据库不进入 wheel，由调用方指定路径。

```python
from chefmate_food_data import LocalFoodData

foods = LocalFoodData("path/to/usda.db")
foods.require_ready()
options = foods.search("potatoes raw", limit=15)
record = foods.get_food(str(options[0]["fdcId"]))
print(record.nutrients_per_100g, record.provenance)
```

接口：

- `status()`：ready/count/datasets/path 或错误；`require_ready()` 在不可用时抛 `FoodDataError`。
- `list_datasets()`：安装版本和来源。
- `search(query, limit=15)`：保持 fdcId/description/dataType 候选格式和旧检索排序。
- `get_food(fdc_id)`：`FoodRecord`（fdc_id、description、data_type、nutrients_per_100g、provenance）。
- `get_raw_food(fdc_id)`：原始 USDA JSON；兼容方法 `food(..., refresh=False)`、`provenance()` 继续可用。
- `parse_nutrients(raw)`：处理 USDA 编号/单位优先级，未知值保持 None；支持读取历史 Survey 快照，不增加 Survey 查询库。

维护模块提供 `download_datasets(folder)`、`import_datasets(folder, database)` 和 `install_datasets(folder, database)`；旧 `download()` 仍可用。下载续传，导入先建立临时库、校验后替换旧库，同版本/哈希不重复写入。

在 `data_cleaning` 目录执行：

```powershell
.venv/Scripts/python.exe -m chefmate_food_data.cli install --directory data/raw/usda --database database/usda.db
.venv/Scripts/python.exe -m chefmate_food_data.cli status --database database/usda.db
```

`chefmate-usda` 的 download/import/install/status/search 命令参数不变。查询只读本地库，维护操作才允许下载。不要同时执行多个维护命令；Windows 替换时文件被占用会保留旧库，关闭读取后重试。
