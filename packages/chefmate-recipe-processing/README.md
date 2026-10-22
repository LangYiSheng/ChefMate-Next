# ChefMate Recipe Processing

把文本/Markdown 转成菜谱草稿，连接本地食材候选并计算营养。模型依赖可注入，估算明确标记，保存与审阅由宿主负责。

[项目首页](../../README.md) · [数据导览](../../docs/guide/data.md) · [完整同步/异步 API](../../docs/reference/recipe-processing.md)

## 主要能力

- 结构化提取与词表校验，模型只从真实营养候选中选择。
- 基于来源快照和克重的确定性计算。
- 缺失信息的可选估算，独立来源与失效规则。
- 同步/异步接口、调用级配置、取消与客户端生命周期。

## 快速开始

```python
from chefmate_contracts import default_vocabulary
from chefmate_recipe_processing import process_recipe

result = process_recipe(
    {"kind": "markdown", "content": "# 水煮土豆\n2人份，土豆200g，煮熟。"},
    ai=model_client,
    vocabulary=default_vocabulary(),
    food_data=local_foods,
    options={"with_nutrition": True, "estimate_missing": False},
)
document = result.recipe
```

示例中的模型客户端与食物服务由宿主提供，草稿仍需核对。核心来源支持文本与 Markdown，App 的图片整理在自己的接入层完成。

## 代码入口

[src/chefmate_recipe_processing/](src/chefmate_recipe_processing)按提取、匹配、计算、估算和模型适配分工。公共函数从包入口导出，`aprocess_recipe` 提供异步流程。

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages/chefmate-recipe-processing/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/processing-tests
```

未知值不补零，不模拟吸油与烹饪损耗；接口、回调和生命周期细节见[参考](../../docs/reference/recipe-processing.md)。
