# 当前 JSON 字段说明

流程：完整 Markdown → AI 完整 JSON → 人工修改/审阅 → USDA 数据与程序计算。原文保留在数据库中，AI 不提供营养数值。

本页的菜谱与食材字段对应共享 [`RecipeDocument`](../../packages/chefmate-contracts/src/chefmate_contracts/schemas.py)，也是基础菜谱和个人菜谱采用的领域文档。后面的导出、营养与转换配置是处理流程的外层数据，不应全部塞进 `RecipeDocument`；工作台草稿记录的保存与发布合同见[菜谱中心 API](workbench.md#菜谱中心-api)。

## 菜谱字段

| 字段 | 含义与设计原因 |
|---|---|
| name | 菜名，用于展示、搜索。 |
| description | 原文简介；没有则 null，不补写未经证实的功效。 |
| original_servings | 原文人数/份量描述，保留出处，不用数值覆盖原句。 |
| servings | 这份 JSON 全部用量对应的人数，正数或 null；用于每人份计算。按份数公式记录时须说明基准。 |
| servings_evidence | 人数依据；防止把一盘、一锅误当一人份。 |
| prep_time_min | 原文明示的准备分钟数，未知为 null。 |
| cook_time_min | 原文明示的烹饪分钟数，未知为 null。 |
| total_time_min | 原文明示的总分钟数，未知为 null；不擅自拆分或补齐时间。 |
| difficulty | beginner / normal / advanced 或 null。转换提示词约定一二星→新手，三星→普通，四五星→进阶；不是上游官方分级声明，也不是工作台个人草稿的星级解析接口。只读取明确难度字段，冲突、尺度不明或原文未提供时保留 null，不按菜名、步骤数或时间猜测。 |
| ingredients | 食材对象列表，结构见下表；同时阅读清单与步骤，补齐步骤实际使用的食材。 |
| steps | 有序操作文字列表，保留用量、火候、时间、操作顺序。 |
| taste_tags | 口味代码列表，如 rich、savory；来自启用词表，支持多选。 |
| cooking_methods | 烹饪方式代码列表，如 pan_fry、stir_fry；用于筛选。 |
| required_tools | 厨具代码列表，如 wok、stove；用于可制作条件筛选。 |
| warnings | 不确定、冲突、遗漏等审阅提示。它不是可执行校验规则，不会自动阻止计算。 |

## 每项食材字段

| 字段 | 含义与设计原因 |
|---|---|
| raw_name | 原始名称，如肉蟹、鸡蛋，保证可追溯。 |
| raw_amount | 原始用量文字，可含约、适量；与 raw_unit 共同表达原始数量，不要用克重或合计推断覆盖引用。 |
| raw_unit | 原始单位，如只、g、ml；未知为 null。 |
| raw_text | 原文引用，多处可换行引用，不夹入 AI 解释。 |
| ingredient_id | 英文 snake_case 标识，表示标准食材身份；模型生成的相同含义不保证跨菜谱 ID 完全一致，审阅时仍需核对。 |
| canonical_name | 标准名称，应对应实际使用部位；清单写鸡蛋、步骤只用蛋清时，这里写蛋清。 |
| category | 食材类别代码，用于分类；例如 seafood、vegetable、seasoning。 |
| aliases | 原文明示或人工确认的等价名称；不把上位词当别名，也不会在当前流程自动创建全局固定映射。 |
| allergens | 已确认的过敏原代码；当前 Schema 为列表，空列表仅代表未记录到已确认信息，不能解释为无过敏原。未知复合配料需 warnings。 |
| grams | 与匹配部位对应的全部可食克重，供营养计算。未知、整体含壳重量、只明确部分用量时为 null；零只表示确定为零。 |
| amount_evidence | 克重原文或人工依据，以及为何不能换算的说明。有 grams 必须有依据，但程序不能仅凭文字自动证明事实正确。 |
| scalable | 是否允许按份数比例缩放的标记，不代表允许猜克重，也不自动实现整只食材取整；当前计算不靠此字段判断数据可靠性。 |
| usda_query | 有依据的英文营养检索词；不加未经证实的品种、加工状态，不是 FDC ID，也不是已匹配证明。 |

例：整蟹约300g保留在原始字段，grams=null；生粉总量为5g加未知蘸粉量时 grams=null；蛋清不能查询全蛋营养。质量单位 g/kg 可以确定性换算，体积与数量不能由 AI 猜成质量。

例如原文“鸡蛋2个”可表示为 `raw_amount="2"`、`raw_unit="个"`、`grams=null`；“2个”不等于已知100克。工作台把简单的数量和单位拆开；范围或约量仍保留原始用量文字，不把复杂描述强拆成确定数字。基础菜谱和个人菜谱的检索详情沿用这些字段。厨房任务中的 `amount_text/amount_value/unit/grams/scaled/estimated` 则是按本次份量生成的[界面投影](workflow.md#业务边界)，不能当作原始来源写回，或再乘一次人数倍率。

## 工作台草稿的来源依据

工作台以 `RecipeDraftRequest` 接收用户来源和待核对内容，再转换成共享 `RecipeDocument`。`source_text/source_kind/source_image_ids/time_evidence/difficulty_evidence` 属于工作台草稿记录外层；`difficulty_evidence` 不是共享菜谱 JSON 的新字段。草稿中的 `difficulty` 只接受上述三个代码或 null；填写难度时，依据必须逐字引用来源中的明确难度说明。用户手动补充的难度也要作为用户补充保存，而不是由程序推测。

个人草稿的份数、克重和时间须有对应原文依据；单一步骤“煮15分钟”不能直接成为整道菜 `total_time_min=15`，也不能用总时间倒推出准备或烹饪时间。这些是工作台额外的来源校验，不代表共享结构校验能独立证明原文事实正确。旧记录缺少难度、克重或证据时继续展示未知/缺少依据；读取和升级不会追补猜测值。

## 程序添加的导出字段

| 字段 | 含义与原因 |
|---|---|
| recipe_id | 稳定菜谱 ID，用于关联、追溯。 |
| source | 原始文件来源。 |
| raw_source_text | 完整 Markdown 快照，保留原始信息。 |
| provenance.source_hash | 原文内容指纹，检测原文变化。 |
| provenance.status | pending_review / approved / rejected 等审核状态。 |
| provenance.reviewed_at / review_note | 审阅时间与备注。 |
| provenance.updated_at | 最近更新时间。 |
| provenance.prompt | 该次转换使用的提示词记录，便于追溯。 |
| ready_for_recommendation | 已审阅且营养指标齐全时为 true；不是自动证实语义和匹配准确。 |

## nutrition 对象（程序计算）

| 字段 | 含义与原因 |
|---|---|
| status | complete / incomplete，表示当前指标和用量是否齐全。 |
| total | 全配方营养；某指标任一食材缺失，则该指标为 null，不以0代替未知。 |
| per_serving | total ÷ servings；人数不明则 null。 |
| tags | 按配置阈值生成的营养标签；数据不完整不生成。 |
| coverage | 每个营养指标有可计算数据的食材项数，不是重量覆盖率。 |
| ingredients | 每项的 name、grams、nutrition（该项贡献）、source（来源快照）、issue（缺失原因）。 |
| thresholds | 标签生成所用阈值与计算基准快照。 |
| note | 计算假设：按投入可食克重，不模拟烹饪损耗。 |

营养指标：calories_kcal（千卡）、protein_g（蛋白质克）、fat_g（脂肪克）、carbohydrate_g（碳水克）、sugar_g（糖克）、sodium_mg（钠毫克）。公式为每100g数值 × grams / 100。

source 快照包括 query、candidates、confidence、reason、source（USDA_FDC）、fdc_id、name、per_100g、food（原始详情）、fetched_at；失败时有 error。confidence 是 AI 选择信心，不是科学准确率；候选 ID 校验只能证明候选真实存在，不能保证选对食物。

导出 manifest 保存批次时间、条数、审核过滤状态、文件 SHA256 等，用于验证批次完整性。

## 转换配置

| 字段 | 含义 |
|---|---|
| prompt | 默认转换提示词；空设置时读取 prompts/convert.txt。 |
| retries | 额外重试0–5次；2表示最多3次请求，认证等不可重试错误会提前停止。 |
| timeout | 单次请求超时5–300秒，不是整批总时限。 |
| max_output_tokens | 单次输出上限1000–64000，实际也受服务端模型限制。 |

当前没有 nutrition_status 等额外食材审核状态。提示词约束不等于确定性语义校验；人工审阅仍需核对食材部位、可食克重、总用量与 USDA 候选是否一致。

## references 参考资料

菜谱顶层 references 是对象列表，每项 title 保存原文链接标题，url 保存完整 HTTP/HTTPS 地址。用于追溯做法参考、视频教程等，不抓取视频内容。没有参考为 []；旧 JSON 缺少此字段仍可读取，默认空列表，重新转换后才能补齐。随整篇 JSON 保存、审核和导出，不需要数据库迁移。


## 本地 USDA 来源（新增）

新营养快照的 `dataset` 记录 backend=local、data_type、version、source_url、sha256、imported_at、record_count、skipped_nulls。旧快照缺少该字段仍然有效，不据当前数据版本补写历史来源。USDA 原始数据库独立于 cleaning.db，不参与清空业务数据。

## AI 估算扩展

`ConversionSettings.estimate_missing` 默认 false。估算存储在 nutrition_json 的 `__ai_estimate__` 中，与 USDA 快照分开；包含 source=AI_ESTIMATE、document_hash、created_at、model、ingredients、servings 和 servings_reason。每项包括 ingredient_id、grams、per_100g、reason。

营养输出新增 estimated、estimated_total_fields、estimated_per_serving_fields、estimated_servings、estimation、tags_estimated；食材新增 original_grams、estimated_grams、estimated_nutrients、estimate 和 source_issue。实际用于计算的 grams 可来自估算，原始 JSON grams 保持不变。已有克重、人数、USDA 指标优先；旧数据无需迁移。status 新增 estimated 表示指标齐全但含估算，incomplete 仍可通过 estimated=true 表明部分使用了估算。
