# 菜谱 AI 转换工具

**完整 Markdown → AI 一次生成完整 JSON → 修改与审阅 → 营养计算 / 导出**。

无需规则预提取，无需先建立标准食材库，无需逐阶段审核。第一次输出已包含原始与标准食材名、类别、过敏原、用量、人数、步骤、口味/方法/厨具/难度标签和 USDA 英文检索词。

## 启动

本机已有 Python 3.12 虚拟环境：

```powershell
cd D:\Dev\ChefMate\data_cleaning
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

打开 <http://127.0.0.1:8501>。主界面只有 **菜谱转换 / 批量转换 / 数据与设置**。

新机器安装（已有配置不要覆盖）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

配置仅从本目录 `.env` 读取，进程环境变量优先：

```dotenv
OPENAI_API_KEY=你的DeepSeek密钥
OPENAI_MODEL=deepseek-flash
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_MODE=deepseek_json
USDA_API_KEY=你的USDA密钥
```

改 `.env` 后重启后台。USDA 密钥未填也能完成 JSON 转换和审阅，营养查询可选。

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

额外重试2次表示最多请求3次。JSON/Schema错误、截断、可重试网络异常有限重试；400/401/403/404等不重复请求。USDA 共用重试和超时。转换以上述设置为准。

默认提示词：`prompts/convert.txt`；Schema：`schemas/conversion.py`。固定提示词、启用词表及 Schema 置于前缀，Markdown、反馈、当前 JSON 放在后面，便于缓存复用。实际命中由服务端决定，不能保证每次命中；服务记录保留返回的 usage / 缓存 token 数据。见 [DeepSeek 缓存文档](https://api-docs.deepseek.com/guides/kv_cache/)。

## 营养计算

已通过整篇审阅、且原文版本仍有效的 USDA 快照自动成为共享映射。后续食材标准名称命中该名称或已审阅别名、英文检索词一致且 FDC ID 无冲突时，直接复用快照，无需再次请求 AI/USDA。不同部位或加工状态请保留在标准名和检索词中。查询词变化时保守地重新匹配。

在“数据与设置 → 已确认的 USDA 共享映射”查看来源。编辑来源菜谱 aliases 并重新通过审阅即可确认别名；撤回为待审阅、拒绝或原文变化后停止作为共享来源。已经复用的历史营养快照不自动覆盖，需要重新转换或修改食材身份来清除旧快照后再匹配。共享映射从当前批准记录读取，无额外审核阶段或数据库迁移。导出增加 usda_mappings.json，菜谱搜索支持原始食材名、标准名、别名，以及已确认映射关联的相同 FDC ID。

程序按 JSON 的英文检索词查询 USDA，AI 只在真实候选中选 ID，程序获取每100g数据，结合 grams 计算总营养、每人份与统一阈值标签。

- AI Schema 不允许营养值/FDC ID；候选外 ID 被校验拒绝。
- 明确 g/kg 可转换；“一个、少许、约两勺”不猜克重，证据保留。
- 未匹配或缺少指标时显示缺失，绝不按0处理。人数未知则每人份为 null。
- 营养详情包含候选、理由、置信度、来源与完整快照，随菜谱整篇审阅。
- 改标准名/检索词使对应快照失效；改克重则复用来源重新计算。
- 不模拟烹饪损耗、吸油量、成品失水。

## 导入、导出与命令行

**数据与设置 → 导入 Markdown** 仅保存原文，hash 相同跳过，变更新增原文版本。支持 HowToCook 根目录、dishes 或自定义菜谱目录，不执行上游脚本。

**导出菜谱 JSON** 默认只包含当前原文版本且审阅通过的记录。输出到 `data/exported/<批次>/recipes.json`，附带 manifest 与 SHA256。允许导出已审阅但营养不完整的 JSON，其 `ready_for_recommendation=false`；主项目使用前按该字段筛选。取消审核过滤只用于诊断。

```powershell
.\.venv\Scripts\python.exe scripts/cli.py init
.\.venv\Scripts\python.exe scripts/cli.py import --source data/raw/HowToCook
.\.venv\Scripts\python.exe scripts/cli.py convert --recipe-id <ID>
.\.venv\Scripts\python.exe scripts/cli.py convert --recipe-id <ID> --feedback "核对工具和食材" --with-nutrition
.\.venv\Scripts\python.exe scripts/cli.py batch --no-usda --limit 5
.\.venv\Scripts\python.exe scripts/cli.py batch --resume <批次ID>
.\.venv\Scripts\python.exe scripts/cli.py export
```

## 数据、清空与兼容

数据库 `database/cleaning.db` 安全自动迁移至版本4。当前仅保留完整 JSON 转换工具，旧版页面和独立阶段命令已移除；底层共享数据库及迁移代码保留。字段说明见 [FIELDS.md](FIELDS.md)。

清空位于设置折叠区，必须输入 CLEAR；有活动任务时阻止清空。不删除 SQLite 文件、磁盘原文、历史导出。配置只用于本机内部工具，没有多用户权限体系。

目录：`app` 页面；`services` 转换/营养/外部接口/任务；`models` 集中数据库访问与 migrations；`schemas` 校验；`prompts` 指令；`vocab` 初始词表；`tests` 测试；`logs` 日志；`data` 原始/中间/导出数据。所有内容均在本子项目。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=data/intermediate/pytest
.\.venv\Scripts\ruff.exe check --no-cache app models schemas services scripts tests config.py
```

测试使用隔离数据库；合成营养数据不会进入正式库。可选扩展为多用户权限、并行任务、更多营养指标，不影响当前简化流程。
