# ChefMate Recipe Processing

文本/Markdown → 菜谱草稿 → 本地食材匹配 → 程序营养计算。可选 AI 估算独立标记。包不读取 .env、不保存菜谱，不管理用户、审阅或批次，不依赖智能体框架。

```python
from chefmate_contracts import default_vocabulary
from chefmate_food_data import LocalFoodData
from chefmate_recipe_processing import AIClient, process_recipe

# 配置由调用方提供，密钥不写入菜谱或处理结果。
with AIClient(config) as ai:
    result = process_recipe(
        {"kind": "markdown", "content": "# 水煮土豆\n2人份，土豆200g，煮熟。", "source_id": "custom-1"},
        ai=ai, vocabulary=default_vocabulary(), food_data=LocalFoodData(database_path),
        options={"with_nutrition": True, "estimate_missing": False},
    )
    document = result.recipe
```

接口：

| 函数 | 输入 | 输出 |
|---|---|---|
| extract_recipe(source, *, ai, vocabulary, ...) | 字符串、RecipeSource 或其字典；可选 prompt/feedback/current_json/entity_id/validator | RecipeDocument |
| validate_recipe(document, *, vocabulary=None) | 菜谱模型、字典或 JSON | RecipeDocument |
| match_ingredients(document, *, ai, food_data, ...) | 可选 snapshots/confirmed_lookup/entity_id/failed_only | MatchingResult（snapshots/changed/issues） |
| calculate_nutrition(document, snapshots, *, estimates=None, thresholds=None) | 菜谱、快照、可选估算和 TagThresholds | 兼容营养字典 |
| estimate_missing(document, snapshots, ai, entity_id="", ...) | 菜谱和已知信息 | 单独估算字典或 None |
| process_recipe(source, *, ai, vocabulary, ...) | 来源、依赖、ProcessingOptions、可选旧快照/反馈 | ProcessingResult |

`RecipeSource` 包含 kind=text/markdown、content、可选 source_id；不支持图片。`ProcessingOptions` 包含 with_nutrition/estimate_missing，默认均关闭。估算可独立启用；先查 USDA 时显式开启 with_nutrition。

`ProcessingResult` 包含 recipe、ingredient_matches、estimates、nutrition、issues、processing_metadata。元信息记录来源、包版本、词表哈希和模型；来源 ID 同时用作默认日志实体 ID。原有菜谱 JSON 不含该外层结构。草稿始终需要应用按产品要求确认，结构校验不代表内容已经真实准确。

提取失败抛异常；单项匹配/估算失败保留已成功的数据并返回问题。已有有效快照、已确认映射优先复用；模型只能选择候选中真实 ID。未知值不按零计算，不模拟烹饪损耗。估算独立放入 __ai_estimate__，菜谱修改后失效，不进入已确认共享映射。

## 异步调用和生命周期

```python
from chefmate_recipe_processing import AsyncAIClient, aprocess_recipe, RequestContext

async def run(source, config, foods):
    async with AsyncAIClient(config) as ai:
        return await aprocess_recipe(
            source, ai=ai, food_data=foods, vocabulary=default_vocabulary(),
            options={"with_nutrition": True},
            context=RequestContext(settings={"timeout": 60}, progress=print),
        )
```

对应异步入口为 aextract_recipe/amatch_ingredients/aestimate_missing/aprocess_recipe。默认异步适配器使用原生 AsyncOpenAI，支持 responses 和 deepseek_json，与同步规则一致。同步查询和日志回调在每个事件循环最多8个后台操作内执行；单份菜谱按食材顺序处理，多份菜谱可以独立并发。取消不转成业务失败、不重试，正在运行的只读线程操作允许完成。

模型通过 ModelClient.request / AsyncModelClient.arequest 协议注入。使用自定义异步适配器时必须实现 arequest，不将同步模型调用放进线程伪装成原生异步。RequestContext 提供调用级 settings/progress/on_call/on_error，适配器在调用开始时复制配置，不修改共享对象。

包创建的客户端由 AIClient.close / AsyncAIClient.aclose 或上下文管理器关闭；注入的 SDK 由调用方关闭。同一异步客户端应在同一事件循环中使用；应用负责服务级模型限流、队列和连接生命周期。回调为同步函数，在后台线程执行，应可用于独立数据库短连接或线程安全记录。

兼容 `RecipeProcessor.extract/enrich`，新增 aextract/aenrich；兼容 `calculate`、旧快照和旧模型类型。未来后端可以在服务层转换 RecipeDocument，不需要依赖清洗工具。
