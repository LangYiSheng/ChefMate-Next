# ChefMate Storage

独立 Python 3.12+ 包，将 `data_cleaning` 的统一导出写入 PostgreSQL 18+，提供菜谱详情和筛选查询。正式库只保存当前数据集；数据库版本、结构版本和导出格式版本分别管理。整体来源链先读[数据导览](../guide/data.md)，新项目接入先看[包 README](../../packages/chefmate-storage/README.md)。

本包依赖共享契约，不依赖清洗管理界面，不运行 AI、不重新计算营养，也不启动或管理 PostgreSQL、Docker、HTTP 服务。数据库由调用方准备，连接信息由调用方提供。

## 安装与连接

从仓库根目录安装到自己的 Python 3.12+ 环境：

```powershell
python -m pip install -e packages/chefmate-storage
chefmate-storage --help
```

其他项目可以安装该目录或构建后的 wheel。连接已有数据库时设置 `CHEFMATE_DATABASE_URL`；以下密码只是占位符，请在本地替换，不要提交真实配置：

```powershell
$env:CHEFMATE_DATABASE_URL = 'postgresql://chefmate:YOUR_PASSWORD@127.0.0.1:5432/chefmate_test'
chefmate-storage --schema chefmate migrate
```

密码中的保留字符需要 URL 编码；也可以使用 psycopg 支持的 `host=... dbname=... user=... password=...` 连接字符串。Python API 直接接收连接字符串，CLI 从环境变量读取。包不会自动读取 `.env`；`.env.example` 只是配置模板，应用负责加载自己的配置。

默认 PostgreSQL schema 是 `chefmate`，可通过每个 CLI 命令前的 `--schema` 改成独立 schema。schema 名称只接受小写字母开头的字母、数字和下划线，最长 63 字符；拒绝 `public`、`information_schema` 和 `pg_` 前缀。迁移需要数据库中的建 schema / 表权限，导入需要该 schema 的读写权限。无需超级用户。

也可以使用初始化辅助脚本：

```powershell
.\packages\chefmate-storage\scripts\Initialize-Database.ps1 -Schema chefmate
```

该脚本只调用已安装的 `chefmate-storage migrate`，不创建数据库用户、不管理数据库进程、不读取或输出密码。重复迁移不会清空数据；表结构版本保存在 `schema_migrations`。

## 整库替换流程

导出目录需要有 `recipes.json` 和 `manifest.json`。manifest 的 `sha256` 中声明的其他文件也必须存在并通过哈希校验，但不会入库。当前仅接收统一转换导出的 `manifest.schema_version = 2`、`phase = "unified"`，不接收旧提取阶段导出。

```powershell
# 仅校验文件，不连接数据库。
chefmate-storage validate 'data_cleaning/data/exported/EXAMPLE_BATCH' --data-version 'example-v1'

# 导入必须显式指定 --replace。
chefmate-storage --schema chefmate import 'data_cleaning/data/exported/EXAMPLE_BATCH' --replace --data-version 'example-v1'

# 查看当前数据版本。
chefmate-storage --schema chefmate metadata
```

`data_version` 建议由发布方指定，例如 `example-v1`。没有指定时使用导出目录名称，不增加菜谱历史版本。

导入在写数据库前完成格式、SHA256、菜谱数量、稳定 ID 和营养关联校验，还会核对食材贡献合计、总营养 / 人数与每份营养的一致性。哈希基于原文件字节；验证后只保留所选业务字段，不保存哈希、完整 manifest、原始 JSON、原文 Markdown、审核记录、匹配过程或历史批次。

整库替换会移除当前 schema 中旧菜谱的业务数据，并写入完整新数据集：新导出缺席的菜谱会删除，同 ID 菜谱会采用新导出的内容。空导出默认拒绝，避免误清空库；只有显式 `--allow-empty` 才允许空快照。`--replace` 和 `--allow-empty` 是两个独立开关。

默认要求 `manifest.approved_only = true`，同时检查每道菜的 `provenance.status = "approved"`。诊断导出须显式 `--allow-unapproved`；未审核菜谱却标为可推荐的导出仍会拒绝，不会自动提高推荐资格。非推荐菜谱仍会入库，默认搜索只返回 `ready_for_recommendation = true` 的菜谱。

全部业务表替换与 `dataset_metadata` 更新在同一事务中完成。校验失败或数据库写入失败时，原有数据和版本保持不变。包内导入之间使用锁串行执行；包内详情与筛选查询使用 `REPEATABLE READ` 只读事务，读取一致的数据集。自行写 SQL 的调用方需要在多条查询间使用同一 `REPEATABLE READ` 事务，或遵守应用发布约定；仅使用默认 `READ COMMITTED` 事务不能保证多条查询读取同一版本。锁等待上限为 30 秒。

这些表属于菜谱数据快照。其他业务系统可保存稳定 `recipe_id`，但不应通过级联删除把收藏、任务等业务数据绑到快照表；菜谱缺席时由应用明确决定如何展示或处理。替换只影响指定 schema 的存储表，不清空数据库的其他 schema。

## 关系表

| 表 | 主键 / 关联 | 保存内容 |
| --- | --- | --- |
| `dataset_metadata` | 单行 `id = 1` | 当前 `data_version`、`export_schema_version`、`exported_at`、`imported_at` |
| `schema_migrations` | 单行 `id = 1` | 表结构 `version` 和迁移时间 |
| `recipes` | `recipe_id` | 名称、简介、人数、原人数描述、耗时、难度、来源、推荐状态、人数估算标记 |
| `recipe_steps` | 菜谱 ID + 顺序 | 步骤文本 |
| `recipe_labels` | 菜谱 ID + 类型 + 值 | 口味、烹饪方法、厨具、营养标签；营养标签估算标记 |
| `recipe_notes` | 菜谱 ID + 类型 + 顺序 | 烹饪提示、不可推荐原因 |
| `recipe_references` | 菜谱 ID + 顺序 | 参考标题、HTTP/HTTPS 链接 |
| `ingredients` | `ingredient_id` | 跨菜谱查询的标准食材身份 |
| `ingredient_aliases` | 菜谱 ID + 食材顺序 + 别名 | 当前菜谱食材的已确认别名 |
| `ingredient_allergens` | 菜谱 ID + 食材顺序 + 过敏原 | 当前菜谱食材已记录的过敏原 |
| `recipe_ingredients` | 菜谱 ID + 食材顺序；关联标准食材 | 标准名称、分类、原名称、原用量、原单位、原描述、有效克重、可缩放状态、克重估算标记 |
| `recipe_nutrition` | 菜谱 ID | 状态、六项总营养、六项每份营养及各指标估算标记 |
| `recipe_ingredient_nutrition` | 菜谱 ID + 食材顺序 | 六项营养贡献、估算标记、USDA FDC ID、六项每 100g USDA 原始营养 |

```mermaid
erDiagram
    recipes ||--o{ recipe_steps : steps
    recipes ||--o{ recipe_labels : labels
    recipes ||--o{ recipe_notes : notes
    recipes ||--o{ recipe_references : references
    recipes ||--o{ recipe_ingredients : contains
    recipes ||--|| recipe_nutrition : nutrition
    ingredients ||--o{ recipe_ingredients : identity
    recipe_ingredients ||--o{ ingredient_aliases : aliases
    recipe_ingredients ||--o{ ingredient_allergens : allergens
    recipe_ingredients ||--o| recipe_ingredient_nutrition : contribution
```

`dataset_metadata` 和 `schema_migrations` 独立于菜谱关系，两者各保存一条当前记录。manifest 的导出 `schema_version` 不能当成数据库结构版本。

完整列定义、类型、外键、检查约束和索引见 [001_initial.sql](../../packages/chefmate-storage/src/chefmate_storage/migrations/001_initial.sql)。列表顺序字段 `ordinal` 从 0 开始；标签和别名属于集合，不保留输入排列。

菜谱食材按顺序识别，同一个标准食材可在一道菜里出现多次。`ingredient_id` 用于跨菜谱筛选；标准名称、分类、别名和过敏原保留在当前菜谱食材层。当前导出中相同 ID 可能有不同部位描述、分类或复合产品信息，导入不静默覆盖，也不把某道菜的产品过敏原传播成该 ID 的全局事实。

菜谱、食材和营养通过关系字段查询，没有原始 JSON 存储列。外键保证食材明细与菜谱关联，唯一约束避免标签等关系重复。当前索引覆盖标签、标准食材、别名、过敏原、推荐状态与耗时，以及总量 / 每份的热量、蛋白质、钠范围。

名称搜索使用 `ILIKE '%文本%'`；小型数据集可以顺序扫描，普通 B-tree 不能保证加速这类包含搜索。数据量增长后应通过 `EXPLAIN ANALYZE` 测量，再按需要增加 `pg_trgm` 索引或额外营养索引。本包不自动安装数据库扩展。

## 字段映射与空值规则

| 导出字段 | 入库规则 |
| --- | --- |
| 菜谱 `recipe_id` | 原样保留作为稳定身份，重复 ID 拒绝 |
| `name`、`description`、`original_servings`、耗时、`difficulty`、`source` | 保存当前值，未知值保持 `NULL` |
| `servings` | 优先真实人数；缺失时使用程序营养结果中的有效人数，保留 `estimated_servings` |
| 食材 `grams` | 优先真实克重；缺失时使用同序号营养食材中的有效克重，保留 `estimated_grams` |
| `raw_name`、`raw_amount`、`raw_unit`、`raw_text`、`scalable` | 保留原描述；`raw_text` 是食材引用，不是整篇原文 |
| `nutrition.total`、`nutrition.per_serving` | 保存程序导出的当前数值与逐指标估算标记，不自行补值 |
| `nutrition.ingredients[].nutrition` | 保存每项食材当前营养贡献与估算标记 |
| 每项营养来源的 `fdc_id`、`per_100g` | 只保存 USDA 对应值，未知保持 `NULL`；AI 估算不能替代 USDA 每 100g 来源值 |
| `warnings`、`blocking_reasons`、`references`、各类标签 | 保存列表内容和需要的顺序；标签在集合内去重 |

六项营养指标的名称与单位固定：`calories_kcal`（千卡）、`protein_g`、`fat_g`、`carbohydrate_g`、`sugar_g`（克）、`sodium_mg`（毫克）。未知值与确定为零不同，数据库不会将未知值补成 0。

人数、克重、营养值、营养标签分别保留估算标记。`nutrition.status` 可为 `complete`、`estimated`、`incomplete`；`estimated = true` 与 `incomplete` 可以同时成立。导入不根据这些字段重新放宽推荐资格，使用原导出的 `ready_for_recommendation`。

每份营养和营养标签在本包中是发布时的派生结果。重新计算由 `chefmate-recipe-processing` / 清洗流程完成后重新发布完整数据集；请勿只更新人数或总营养而保留旧派生值。

USDA 匹配保存在具体菜谱食材上，不建立全局的标准食材 → USDA 单一映射。同一种食材的生熟、部位与加工状态可对应不同 FDC ID。

过敏原空列表仅表示未记录到已确认过敏原，不证明食材不含过敏原。排除查询按已记录关系执行；未知复合配料需要由应用结合 warnings 提示。

## CLI 查询

```powershell
chefmate-storage --schema chefmate get 'recipe-id'

chefmate-storage --schema chefmate search --name '鸡' --ingredient chicken --exclude-ingredient peanut --max-time 30 --limit 20 --offset 0

chefmate-storage --schema chefmate search --taste savory --method stir_fry --tool wok --nutrition-tag high_protein --exclude-allergen peanut

# 指标:基准:最小值:最大值，基准为 total 或 per_serving，边界可留空。
chefmate-storage --schema chefmate search --nutrient 'protein_g:per_serving:20:' --nutrient 'sodium_mg:per_serving::600'

# 显式包含未达到推荐条件的菜谱。
chefmate-storage --schema chefmate search --include-unready
```

`--ingredient`、`--exclude-ingredient`、`--taste`、`--method`、`--tool`、`--nutrition-tag`、`--exclude-allergen`、`--nutrient` 可以重复。筛选使用标准食材 ID 和标签代码；名称搜索按菜名包含文本匹配。多个要求同时满足，排除项命中任意一个即排除。营养与耗时字段未知时，不满足对应范围条件。

搜索先在数据库中筛选和分页，不把全库菜谱加载到 Python 再过滤。结果按 `recipe_id` 排序，`limit` 为 1–200（默认 20），`offset` 为非负整数。详情通过稳定菜谱 ID 读取当前关系数据，不受搜索默认推荐条件限制。

## Python 接口

```python
import os

from chefmate_storage import NutrientRange, RecipeFilter, RecipeStore

store = RecipeStore(os.environ["CHEFMATE_DATABASE_URL"], schema="chefmate", connect_timeout=5)
store.migrate()
store.replace_from_export(
    "data_cleaning/data/exported/EXAMPLE_BATCH",
    data_version="example-v1",
)
metadata = store.metadata()
recipe = store.get_recipe("recipe-id")

page = store.search(
    RecipeFilter(
        name="鸡",
        ingredient_ids=("chicken",),
        exclude_ingredient_ids=("peanut",),
        cooking_methods=("stir_fry",),
        exclude_allergens=("peanut",),
        max_total_time_min=30,
        nutrients=(
            NutrientRange("protein_g", minimum=20, basis="per_serving"),
            NutrientRange("sodium_mg", maximum=600, basis="per_serving"),
        ),
    ),
    limit=20,
    offset=0,
)
print(page["total"], page["items"])
```

`RecipeFilter` 还支持 `taste_tags`、`required_tools`、`nutrition_tags`、`min_servings`。集合字段使用 tuple，默认 `ready_for_recommendation=True`；设为 `None` 包含全部菜谱，设为 `False` 只查询不可推荐菜谱。`NutrientRange` 的基准默认为 `per_serving`，也可为 `total`。

`metadata()` 返回当前版本字典，其中 `db_schema_version` 来自迁移表；尚未导入数据时返回 `None`。`get_recipe()` 返回选定业务字段组成的详情，不存在时返回 `None`；每项食材带自己的 `nutrition`（贡献、USDA 每 100g 数值、FDC ID、估算标记），不返回原始来源快照。`search()` 返回 `{"total": 总命中数, "items": 当前页菜谱摘要}`，摘要含基本字段、`nutrition_status` 和 `nutrition_estimated`。

调用方无需拼接 SQL。连接按操作建立和关闭，不依赖全局连接，也不要求应用使用特定 Web 框架。

## 查询与简单打分示例

[`examples/query_and_score.py`](../../packages/chefmate-storage/examples/query_and_score.py) 是独立的只读实验脚本，不改变存储包的推荐资格或数据库内容。从仓库根目录运行：

```powershell
python packages/chefmate-storage/examples/query_and_score.py --ingredients egg tomato potato --max-time 30 --top 8 --include-unready
```

`--include-unready` 显式包含测试库中的不可推荐菜谱；不传该参数时只使用正式可推荐数据。示例假设用户已有这些标准食材 ID，以及脚本输出的常备调料清单（盐、水、油、葱姜蒜、酱油等），不代表实际库存。

候选必须有已知耗时且不超过时间上限，并至少包含一种已备的非常备食材。100 分规则如下：

| 分项 | 规则 | 满分 |
| --- | --- | --- |
| 食材覆盖 | 已备的不同非常备食材 ID 数 / 菜谱全部不同非常备食材 ID 数 | 40 |
| 耗时 | `30 × (1 - 菜谱耗时 / 时间上限)` | 30 |
| 难度 | `beginner` 得 20，`normal` 得 10，其他或未知得 0 | 20 |
| 营养信息覆盖 | 六项总营养有值且非估算计 1，有值且估算计 0.5，缺失计 0；合计乘 `10 / 6` | 10 |

常备食材不计入覆盖分母，重复使用同一标准食材只计一次，未知/不在库存中的 ID 仍计入缺货。SQL 在 PostgreSQL 内完成候选筛选、分项计分和排序，只读取返回的前 N 条。输出各项分数、缺少食材、营养估算状态和推荐资格，可检查某道菜为什么排在前面。

上述权重和估算折半都是人为选定的演示规则，营养信息覆盖分不表示健康程度或统计置信度。这个例子偏向快捷、简单、用料齐全的菜，尚未考虑厨具、菜品类型、食材可替代性和用户口味；调料清单也需要按实际库存调整。

## 多操作只读会话与检索条件

`RecipeStore.read_session()` 将元数据、食材目录、查询选项、分组筛选和详情读取绑定到同一只读快照。退出上下文后会话不可再使用。既有 `search()` / `get_recipe()` 接口仍可单独调用。

读取语句默认设置 15 秒的 PostgreSQL `statement_timeout`，可在 `RecipeStore(..., read_statement_timeout_ms=15000)` 调整为 100–120000 毫秒。该限制只作用于读取事务，不截断整库导入；连接超时仍由 `connect_timeout` 管理。异步应用取消等待无法强制终止已运行的同步线程，数据库语句超时为后台读取提供有界收束。

```python
from chefmate_storage import IngredientGroup, IngredientSelector, RecipeFilter, RecipeSort

with store.read_session() as session:
    metadata = session.metadata()
    result = session.search(RecipeFilter(
        ingredient_groups=(IngredientGroup((IngredientSelector("shrimp"), IngredientSelector("peeled_shrimp"))),),
        max_total_time_min=30,
        available_tools=("stove", "wok", "pot"),
    ), sort=RecipeSort("total_time_min", "asc"))
```

食材组内 OR、组间 AND，选择器可附加 `canonical_name` 精确限定同 ID 的不同形态。新增条件含时间下限、三种难度、口味任一/排除、烹饪方法任一、可用厨具子集和逐营养指标的估算政策。`available_tools=None` 不限制、空元组表示无厨具。营养未知值不补零。会话搜索在 SQL 内筛选、计数、排序和分页，并批量读取当前页的标签、营养、原文和 warnings。

`lookup_ingredients()` 用名称、别名、英文 ID 或 FDC 营养参考查找 `(ingredient_id, canonical_name)` 候选，不从原文推断身份；FDC 关联不等于同义。`search_options()` 返回当前数据记录的代码及菜谱数。智能体参数、快照校验和候选解释见独立的 [`chefmate-retrieval`](../../packages/chefmate-retrieval/README.md) 包。

## 测试与构建

安装开发依赖后，无数据库环境可运行文件校验测试；真实 PostgreSQL 集成测试需要单独设置 `CHEFMATE_TEST_DSN`，不会自动使用日常的 `CHEFMATE_DATABASE_URL`：

```powershell
python -m pip install -e 'packages/chefmate-storage[dev]'
$env:CHEFMATE_TEST_DSN = 'postgresql://test_user:YOUR_TEST_PASSWORD@127.0.0.1:5432/chefmate_test'
python -m pytest packages/chefmate-storage/tests
```

每个集成测试创建随机专用 schema，结束后只清理该 schema，不清空数据库或其他 schema。测试覆盖迁移、完整字段导入、筛选、重复替换、关系约束、失败事务回滚和版本更新。

构建 wheel：

```powershell
python -m pip install build
python -m build packages/chefmate-storage
```

SQL 迁移随 wheel 打包。交付验证应同时包含源码环境和独立环境安装 wheel 后的真实数据库读写，确认包没有意外依赖仓库路径。

历史实际测试数据、通过项目和性能测量范围见[存储基线记录](../engineering/records/storage-baseline.md)，其中服务器状态和测试数量只适用于记录时点。
