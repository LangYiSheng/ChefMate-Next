# 随仓库提供的 358 道基础菜谱

这是 ChefMate 现有基础数据的精简在线导出，用于 Docker 首次启动自动初始化公共菜谱 PostgreSQL。数据版本为 `chefmate-bundled-20261005`，不是测试夹具，也不包含用户账号、对话、个人菜谱、图片或模型配置。

菜谱原文均来自 [HowToCook](https://github.com/Anduin2017/HowToCook)，逐条 `source` 保留原始相对路径；上游许可见 [HowToCook LICENSE](https://github.com/Anduin2017/HowToCook/blob/master/LICENSE)。保留了原有步骤、食材别名、来源引用、营养来源与估算标记。营养数据中有 USDA FDC 标识时继续保留该标识和每 100g 数据，不能将模型估算当作 USDA 实测。

## 内容与审核状态

原始清洗导出为 `20261005T053022_ab368ecd`，共 358 道菜谱。原导出 `recipes.json` 为 204,920,387 字节，包含大段重复的 USDA 候选、完整食物快照和转换上下文。本文件仅保留在线导入需要的字段及少量来源证据，大小为 4,300,185 字节，全部 11 张在线关系表的投影与原导出逐项完全相同。

原数据的状态保持不变：2 条 `approved`、356 条 `pending_review`，358 条的 `ready_for_recommendation` 均为 false。首次导入明确允许这份固定未全部审核的内置快照，方便浏览和查询现有菜谱；这不会把它们提升为“完整实测营养、已审核可推荐”。只检索可推荐数据的工具仍遵守原有过滤条件。进一步审核属于后续数据维护，不能通过改标志制造完整性。

| 关系 | 数量 |
| --- | ---: |
| 菜谱 | 358 |
| 步骤 | 3314 |
| 标签 | 2492 |
| 提示与阻塞说明 | 2207 |
| 来源引用 | 61 |
| 稳定食材 ID | 725 |
| 菜谱食材关系 | 3396 |
| 食材别名 | 690 |
| 过敏原关系 | 743 |
| 菜谱营养记录 | 358 |
| 食材营养记录 | 3396 |

## 校验与重新打包

`manifest.json` 保存文件 SHA256、原始导出哈希与数据版本，`projection-report.json` 保存实际投影对比结果。Docker 会校验清单后导入，已有数据库不会在重启时被盲目覆盖。

可在已安装 App 开发依赖的环境中验证：

```powershell
apps/chefmate-playground/.venv/Scripts/chefmate-storage.exe validate data/seed/catalog --data-version chefmate-bundled-20261005 --allow-unapproved
```

维护者从新的原始导出重新打包时，目标应是不存在的新目录：

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe scripts/package_catalog_seed.py path/to/original-export path/to/new-seed --data-version your-data-version --allow-unapproved
```

脚本先验证原导出，再验证精简导出，并比较所有在线表。审核与推荐状态、未知营养、估算标记或任何在线字段变化都会体现在投影差异中；不相同即拒绝发布。

发布另一份固定快照时，同时更新 `docker/seed_catalog.py` 中的固定数据版本和 SHA256，并执行种子初始化、重复启动与冲突保护测试。仅替换文件不会绕过初始化器的固定身份校验。
