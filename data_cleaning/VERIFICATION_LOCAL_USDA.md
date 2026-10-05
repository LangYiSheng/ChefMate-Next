# USDA 本地部署与核心包验证

日期：2026-09-26。代码回滚基线：`5e1da7376a97b624ceb057b98736655de478bb82`。
正式菜谱库未执行迁移、重算或批量更新；所有功能验证使用隔离库。

## 数据交付

| 数据 | 版本 | 有效记录 | 跳过空项 | ZIP字节数 |
| --- | --- | ---: | ---: | ---: |
| Foundation Foods | 2026-04 | 363 | 32 | 469303 |
| SR Legacy | 2018-04 | 7793 | 0 | 13456312 |

下载目录：`data/raw/usda`。数据库：`database/usda.db`，共8156条，244604928字节。
数据库保存原始食物 JSON、FTS5索引和来源清单；查询为只读，不使用 USDA API。

SHA256：
- Foundation：`186e988ec542e913f51ef62b86a47758e8cdd0d1dc3889e7b055581f3c09c77a`
- SR Legacy：`0fe8ae486a2c8eb42cb96413f058deb51863a46c8fb8eeb4b1fb45006dd338ef`

## 验证结果

- 完整测试：81 passed，20.12秒。覆盖原工作流、下载续传、服务端忽略Range、部分下载重试、无效ZIP、重复ID、异常记录、条数错误、失败保留原库、幂等导入和来源追踪。
- 新增检索测试覆盖 potato/potatoes、peeled/without skin、同分Foundation优先、生熟状态和无候选。
- 网络阻断测试确认本地 search/food/provenance 不访问 HTTP。
- 最终状态表调整后，2项页面回归再次通过。
- Ruff（项目配置）及 `git diff --check` 通过。
- wheel 独立安装目录验证通过：不导入 data_cleaning/Streamlit；默认提示词随包提供；能读取8156条记录并检索到本地食物。

## 真实模型与浏览器

使用现有模型配置，在内存中处理测试文本“去皮生土豆200g，2人份”。AI生成结构化菜谱，随后选择本地Foundation ID `2346401`，标注为常见品种近似替代，营养结果完整。
模型联调不写正式菜谱库，测试原文和结果位于 `data/intermediate/core-live-result.json`。

Edge 无界面浏览器验证了单篇、批量、数据与设置页面；本地库已就绪时自动营养选项默认启用。真实页面点击审核后，隔离数据库记录为 approved。
截图：`data/intermediate/usda-single.png`、`usda-batch.png`、`usda-settings-final.png`。
测试服务仅监听127.0.0.1:8502，验证结束后关闭。

## 当前可复用产物（2026-10-05 更新）

源码与调用说明：[包总览](../packages/README.md)。当前提供 contracts、food-data、recipe-processing 三个独立包，清洗环境安装对应 editable 包。

安装包位于 `data/intermediate/package-dist/`：

- `chefmate_contracts-0.1.0-py3-none-any.whl`
- `chefmate_food_data-0.1.0-py3-none-any.whl`
- `chefmate_recipe_processing-0.1.0-py3-none-any.whl`

旧聚合包及导入兼容层已移除；业务数据库、菜谱 JSON 和历史营养快照不需要迁移。
USDA_API_KEY 已停用；AI 处理仍调用调用方配置的外部模型。
