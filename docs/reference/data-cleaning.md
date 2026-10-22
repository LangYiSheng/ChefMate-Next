# 清洗平台：转换、审核与导出参考

本页说明管理端操作与数据契约。项目流程先读[数据导览](../guide/data.md)，字段含义见[菜谱字段](recipe-fields.md)，应用入口见[清洗平台 README](../../data_cleaning/README.md)。

**完整 Markdown → AI 一次生成完整 JSON → 修改与审阅 → 营养计算 / 导出**。

AI 根据完整原文一次提取整份文档，再对整份结果修改与审核。首次输出包含原始与标准食材名、类别、过敏原、用量、人数、步骤、口味/方法/厨具/难度标签和 USDA 英文检索词；是否有依据仍由契约和审核环节核对。

## 启动

使用 Python 3.12+，以下命令从仓库根目录进入清洗子项目执行：

```powershell
cd data_cleaning
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

打开[清洗后台](http://127.0.0.1:8501/)。主界面包含 **菜谱转换 / 批量转换 / 数据库菜谱 / 标准食材 / 数据与设置**。

新机器安装（已有配置不要覆盖）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

配置仅从本目录 `.env` 读取，进程环境变量优先：

```dotenv
OPENAI_API_KEY=YOUR_API_KEY
OPENAI_MODEL=YOUR_MODEL
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_MODE=responses
USDA_DB_PATH=database/usda.db
```

模型和协议按自己的服务选择；上方值仅为占位配置，模板可使用其他受支持协议。改 `.env` 后重启后台。本地 USDA 库未安装时仍能完成 JSON 转换和审阅，营养查询可选。本地食品查询不需要 USDA API Key。

## USDA 本地数据与复用包

代码固定使用 Foundation Foods 与 SR Legacy 的选定发布版本，见[数据集配置](../../packages/chefmate-food-data/src/chefmate_food_data/datasets.py)。实际是否安装、数量和哈希以本地 `status` 输出为准。
默认原始 ZIP 位于 `data/raw/usda`，独立查询库位于 `database/usda.db`；导入验证会核对预期条目与空项，不由文档推断本机状态。
首次安装或恢复数据（当前目录 `data_cleaning`）：

```powershell
.\.venv\Scripts\python.exe -m chefmate_food_data.cli install --directory data/raw/usda --database database/usda.db
.\.venv\Scripts\python.exe -m chefmate_food_data.cli status --database database/usda.db
```

`download` 仅下载，`import` 仅导入；下载支持续传，导入校验成功才替换旧库。同哈希重复导入不写入。
“数据与设置”显示数据版本、数量与 SHA256。未安装时提示本地库不可用，不会回退到在线 API。
旧业务数据库、已审阅菜谱和历史营养快照保持兼容，不自动重算。失败项可以从本地重试，发生修改后重新审阅。

共享模型/词表在 `chefmate-contracts`，本地食物数据在 `chefmate-food-data`，菜谱处理在 `chefmate-recipe-processing`。
应用直接使用这三个包。其他 Python 项目可以独立安装 wheel，接口与同步/异步示例参见 [包总览](../../packages/README.md)。
转换入口支持文本/Markdown，不包含图片/PDF解析。整篇转换导出附带 `vocabulary.json`（实际词表、启用状态和内容哈希），并纳入 manifest 文件哈希；使用快照校验历史词表，不依赖当前数据库的启用状态。

## 单篇转换

1. **菜谱转换** 选择菜谱，上一篇/下一篇逐页浏览。左侧完整原文，右侧可编辑 JSON。
2. 点击 **AI 转换 / 按意见重做**，一次请求完成全部结构化字段，不输入旧规则解析结果。
3. 直接修改 JSON，或在 **让 AI 修改的意见** 写要求后重做。当前编辑框也会传给 AI，支持修复格式错误的 JSON。失败保留已有结果。
4. **保存修改** 留作待审阅；**通过审阅** 对整份 JSON 及当前营养来源一次确认；也可拒绝。重做成功/改变来源后重新待审阅。
5. 可选转换后自动查询 USDA，或点击 **补齐 / 重试 USDA 并重新计算**。无需到标准食材页操作。

原文、转换/修改历史、提示词、审阅记录和 USDA 快照保存在 SQLite。原文变更使旧结果失效；并发保存有版本检查。

## 批量转换

检查本批提示词、重试次数、是否附带营养查询及菜谱数量（0为全部），点击开始。

- 独立后台进程逐篇生成完整 JSON，不再运行旧版食材整理/标签阶段。
- 关闭浏览器不停止进程；自动刷新进度，支持停止、继续、失败重试。
- 成功项有检查点，已有当前版本 JSON 复用。已通过/拒绝记录不会被批量覆盖。
- 改提示词后可单篇重做，或在批量页勾选“按本批提示词重做已有待审阅 JSON”；默认复用已有结果，已通过/拒绝仍受保护。
- 启动时固定范围与配置快照，修改默认设置不影响运行中批次。
- 同库只允许一个批量进程。进程意外退出后先恢复中断状态，再继续。

## 提示词、重试与超时

在 **数据与设置** 保存默认提示词和参数；单篇/批量可临时覆盖提示词及重试次数。

| 参数 | 默认 | 范围 |
|---|---:|---:|
| 额外重试次数 | 2 | 0–5 |
| 每次请求超时 | 90秒 | 5–300秒 |
| 最大输出 tokens | 12000 | 1000–64000 |

上表是 `ConversionSettings` 的完整转换任务默认值，保存在清洗数据库中的设置或单次覆盖优先；`configured_ai` 将它们应用到该次模型调用，基础环境配置的请求默认值不代表转换任务最终预算。[转换设置与覆盖](../../data_cleaning/services/conversion.py)

额外重试2次表示最多请求3次。JSON/Schema错误、截断、可重试网络异常有限重试；400/401/403/404等不重复请求。USDA 只查询本地数据库，不使用网络重试或 API 密钥。转换以上述设置为准。

默认提示词：[convert.txt](../../data_cleaning/prompts/convert.txt)；应用 Schema 入口：[conversion.py](../../data_cleaning/schemas/conversion.py)，共享字段定义在[处理包 Schema](../../packages/chefmate-recipe-processing/src/chefmate_recipe_processing/schemas.py)。固定提示词、启用词表及 Schema 置于前缀，Markdown、反馈、当前 JSON 放在后面，便于缓存复用。实际命中由服务端决定，不能保证每次命中；服务记录保留实际返回的 usage / 缓存 token 数据，缺失指标不能当作缓存命中。

## AI 缺失估算模式

单篇页面勾选 **AI 估算缺失数据（标记估算值）**，再点击 **补齐 / 重试 USDA 并重新计算**，无需重做菜谱。批量页也有相同开关；数据与设置可保存默认开关。开启后即使未勾选自动营养查询，也会进行营养补齐和估算。

先保留已有原文克重与 USDA 指标，再让 AI 根据整份菜谱估算缺失的可食克重、人数及每100g营养。估算单独保存，不改写原始 JSON，不伪造 USDA 来源。页面显示估算表与依据，导出保留 `estimated`、字段列表、估算模型和时间。所有指标补齐且使用了估算时，`nutrition.status=estimated`；仍有缺项则为 `incomplete`。估算结果仍需整篇审阅，`ready_for_recommendation` 保持严格口径，估算结果不会自动设为 true。

编辑菜谱使旧估算失效；关闭估算开关并点击补齐按钮可移除估算，恢复严格计算。估算失败保留已获得的 USDA 结果，可重试。无法合理估计的值仍可为空，未知不按0处理。估算不会加入 USDA 共享映射。

## 营养计算

已通过整篇审阅、且原文版本仍有效的 USDA 快照自动成为共享映射。后续食材标准名称命中该名称或已审阅别名、英文检索词一致且 FDC ID 无冲突时，直接复用快照，无需再次请求 AI/本地 USDA。不同部位或加工状态请保留在标准名和检索词中。查询词变化时保守地重新匹配。

在“数据与设置 → 已确认的 USDA 共享映射”查看来源。编辑来源菜谱 aliases 并重新通过审阅即可确认别名；撤回为待审阅、拒绝或原文变化后停止作为共享来源。已经复用的历史营养快照不自动覆盖，需要重新转换或修改食材身份来清除旧快照后再匹配。共享映射从当前批准记录读取，无额外审核阶段或数据库迁移。导出增加 usda_mappings.json，菜谱搜索支持原始食材名、标准名、别名，以及已确认映射关联的相同 FDC ID。

程序按 JSON 的英文检索词查询本地 USDA（Foundation Foods 与 SR Legacy），AI 只在真实候选中选 ID，程序获取每100g数据，结合 grams 计算总营养、每人份与统一阈值标签。

- AI Schema 不允许营养值/FDC ID；候选外 ID 被校验拒绝。
- 明确 g/kg 可转换；“一个、少许、约两勺”不猜克重，证据保留。
- 未匹配或缺少指标时显示缺失，绝不按0处理。人数未知则每人份为 null。
- 营养详情包含候选、匹配理由、服务实际提供的匹配信息、来源与完整快照，随菜谱整篇审阅。
- 改标准名/检索词使对应快照失效；改克重则复用来源重新计算。
- 不模拟烹饪损耗、吸油量、成品失水。

## 导入、导出与命令行

**数据与设置 → 导入 Markdown** 仅保存原文，hash 相同跳过，变更新增原文版本。支持 HowToCook 根目录、dishes 或自定义菜谱目录，不执行上游脚本。

**导出菜谱 JSON** 默认只包含当前原文版本且审阅通过的记录。输出到 `data/exported/<批次>/recipes.json`，附带 manifest 与 SHA256。允许导出已审阅但营养不完整的 JSON，其 `ready_for_recommendation=false`；主项目使用前按该字段筛选。取消审核过滤只用于诊断。

```powershell
.\.venv\Scripts\python.exe scripts/cli.py init
.\.venv\Scripts\python.exe scripts/cli.py import --source data/raw/HowToCook
.\.venv\Scripts\python.exe scripts/cli.py convert --recipe-id 'RECIPE_ID'
.\.venv\Scripts\python.exe scripts/cli.py convert --recipe-id 'RECIPE_ID' --feedback "核对工具和食材" --with-nutrition
.\.venv\Scripts\python.exe scripts/cli.py batch --no-usda --limit 5
.\.venv\Scripts\python.exe scripts/cli.py batch --resume 'BATCH_ID'
.\.venv\Scripts\python.exe scripts/cli.py export
```

## 数据、清空与兼容

数据库默认 `database/cleaning.db`，启动时按仓库中的[迁移脚本](../../data_cleaning/models/migrations/)升级，遇到高于代码的版本会拒绝读取。当前仅保留完整 JSON 转换工具，旧版页面和独立阶段命令已移除；底层共享数据库及迁移代码保留。字段说明见 [菜谱字段](recipe-fields.md)。

清空位于设置折叠区，必须输入 CLEAR；有活动任务时阻止清空。不删除 SQLite 文件、磁盘原文、历史导出。配置只用于本机内部工具，没有多用户权限体系。

目录：`app` 页面；`services` 转换/营养/外部接口/任务；`models` 集中数据库访问与 migrations；`schemas` 校验；`prompts` 指令；初始词表由 contracts 包提供；`tests` 测试；`logs` 日志；`data` 原始/中间/导出数据。应用数据仍在清洗子项目，复用能力位于[独立包](../../packages/README.md)。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=data/intermediate/pytest
.\.venv\Scripts\ruff.exe check --no-cache app models schemas services scripts tests config.py
```

测试使用隔离数据库，合成营养数据不写入日常库。历史实测见[清洗工程记录](../engineering/README.md#2026-09-24)，自动化覆盖见[测试目录](../../data_cleaning/tests/)。

## 标准食材汇总展示

列表按标准名相同或 USDA ID 相同合并，关联分组一并合并。查询命中组内任一名称、别名或来源 ID 时显示整个分组；详情保留每篇标准名、原名、检索词、来源及匹配依据。这仅改变展示分组，不改菜谱、营养快照或共享映射。


## 实现与来源

- [转换服务](../../data_cleaning/services/conversion.py)：一次完整 JSON 提取、参数快照、失败保留、可选营养补齐。
- [转换仓库](../../data_cleaning/models/conversion.py)：整篇审核、当前原文版本、共享 USDA 映射与统一导出。
- [处理包](recipe-processing.md)：可注入 AI、来源校验、确定性营养计算和单独估算。
- [USDA 官方数据说明](https://fdc.nal.usda.gov/data-documentation/)：Foundation Foods 与 SR Legacy 的原始数据含义。
