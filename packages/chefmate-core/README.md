# ChefMate Core

独立 Python 3.12+ 包：文本/Markdown → AI 结构化 → 本地 USDA 匹配 → 营养计算。
核心包不导入 `data_cleaning` 或 Streamlit，不读取 `.env`，不保存菜谱或执行审阅。

## 安装

从项目根目录安装：

```powershell
uv pip install --python data_cleaning/.venv/Scripts/python.exe -e packages/chefmate-core
```

其他项目可用自己的 Python 环境安装该目录或构建后的 wheel。

## 下载与维护 USDA

在 `data_cleaning` 目录中运行（路径参数均相对于当前命令行目录）：

```powershell
.\.venv\Scripts\python.exe -m chefmate_core.cli install --directory data/raw/usda --database database/usda.db
.\.venv\Scripts\python.exe -m chefmate_core.cli status --database database/usda.db
.\.venv\Scripts\python.exe -m chefmate_core.cli search --database database/usda.db "potatoes raw"
```

`download --directory ...` 仅下载；`import --directory ... --database ...` 仅导入，后者无需网络。
`install` 依次完成两步。已下载文件复用，相同版本/哈希不重复导入。
中断会保留 `.part`，再次运行自动续传；服务器忽略 Range 时完整重下。
导入使用临时数据库，校验全部通过才替换旧库；Windows 下旧文件被占用时保留原库，关闭查询后重试。
不要同时运行多个维护命令。业务查询以只读短连接运行。

当前固定版本：Foundation 2026-04（363 条、跳过 32 个空项），SR Legacy 2018-04（7,793 条）。
所有下载来自 USDA 官方，导入记录 URL、SHA256、时间及记录数。
更新版本时先核实官方下载页，再更新 `datasets.py` 中版本、文件名、预期条数及空项数，重新下载导入。
原始数据与数据库不打包进 wheel，也不提交 Git。建议保留 ZIP 与数据库的外部备份。

## Python 接口

```python
from chefmate_core import AIClient, LocalFoodData, RecipeProcessor, TagThresholds, calculate

foods = LocalFoodData(r"D:\Dev\ChefMate\data_cleaning\database\usda.db")
foods.require_ready()
candidates = foods.search("chicken breast raw", limit=15)
food = foods.food(str(candidates[0]["fdcId"]))
print(foods.provenance(food["fdcId"]))

# 由调用方安全加载配置；核心包不读取环境变量。
ai = AIClient({
    "openai_key": "从调用方配置传入",
    "model": "从调用方配置传入",
    "base_url": "https://api.deepseek.com",
    "api_mode": "deepseek_json",  # 另支持 responses
})
processor = RecipeProcessor(ai, foods, progress=print)
vocabulary = [
    {"kind": "category", "code": "vegetable", "label": "蔬菜"},
    {"kind": "cooking_method", "code": "boil", "label": "煮"},
    {"kind": "tool", "code": "pot", "label": "锅"},
    {"kind": "difficulty", "code": "beginner", "label": "入门"},
]
document = processor.extract("# 水煮土豆\n2人份；土豆200g，放入锅内煮熟。", vocabulary=vocabulary)
enriched = processor.enrich(document)
nutrition = calculate(document, enriched["snapshots"], TagThresholds())
```

- `extract(text, *, vocabulary, prompt=None, feedback="", current_json=None, entity_id="", validator=None)`：返回 `ConvertedRecipe`，仅生成结构化字段；词表决定允许的分类/标签。默认提示词随包发布。
- `enrich(document, *, snapshots=None, confirmed_lookup=None, entity_id="", failed_only=False)`：返回 `{"snapshots": ..., "changed": bool}`。每项包含候选、理由、置信度、原始食物、每100g营养与 `dataset` 来源，或 `unmatched / request_failed / processing_failed` 诊断。
- `confirmed_lookup(ingredient)` 由调用方实现，仅返回已确认映射。已有有效快照优先复用；新的共享映射限 Foundation/SR Legacy。历史 Survey 快照仍可计算。
- `calculate(document, snapshots, thresholds)`：返回 total、per_serving、coverage、tags 和 ingredients。默认未知值保持 null，不按0计。可显式调用 `chefmate_core.estimation.estimate_missing` 补估缺失克重、人数及营养，并将结果以 `ESTIMATE_KEY` 存入 snapshots；`calculate` 保留估算标记与依据，已有事实优先。
- `LocalFoodData.status()` 无异常返回 ready/count/datasets/error；`require_ready()` 在不可用时抛 `FoodDataError`。`food(..., refresh=True)` 兼容旧参数，仍只读本地库。
- `AIClient` 支持注入 SDK client、on_call、on_error、progress 和 prompt_dir。on_call 接收 `(service, purpose, entity_id, status, request, response, error?)`，日志敏感字段由核心脱敏；审阅/持久化/批量控制由应用实现。

检索依次使用完整词、peeled/without skin 表述兼容、保留食材名称的放宽匹配，避免稀有加工词挤走目标食材。每层按相关度排序，相同分数优先 Foundation，默认15个候选。

词形归一化只用于候选检索，不代表食材身份相同。AI 仅在真实候选中选择 ID；不同部位、生熟、浓缩状态仍按现有匹配规则判断。近似替代不能自动变成同义词。

## 兼容与测试

旧 `data_cleaning.schemas` 和 `conversion_nutrition` 导入保留转发。营养快照字段仅新增 `dataset`，不改原食材 ID/快照 key。更换数据集不会自动覆盖历史审阅结果。

从项目根目录执行：

```powershell
data_cleaning\.venv\Scripts\python.exe -m pytest data_cleaning/tests --basetemp=data_cleaning/data/intermediate/core-tests
data_cleaning\.venv\Scripts\ruff.exe check --config data_cleaning/pyproject.toml data_cleaning packages/chefmate-core/src
```
