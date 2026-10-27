# 旧日常数据迁入 PostgreSQL

日期：2026-10-27。时间使用 UTC。

用户要求把现有单用户记录实际迁入新 Docker 部署。旧运行目录没有账号库，因此明确将这些记录归属新部署的初始化管理员，使用迁移工具的 `legacy-owner` 参数。不会合并其他验收服务的测试账号，也不会把个人数据、配置密钥或图片提交到 Git。

## 原数据盘点

12:03–12:05 只读盘点旧日常运行库 `sessions.db`，文件 23,617,536 字节、journal mode 为 `delete`，没有 WAL、SHM 或回滚日志。共有 47 个会话、329 条消息，运行中的会话为 0。

| 表 | 记录数 |
| --- | ---: |
| sessions | 47 |
| session_debug | 111 |
| model_usage_ledger | 143 |
| personal_recipes | 2 |
| personal_memory | 1 |
| food_journal | 6 |
| personal_food_logs | 5 |
| cooked_meal_intake | 4 |
| recipe_favorites | 0 |
| recipe_publish_confirmations | 0 |
| recipe_delete_confirmations | 0 |

另有 4 个图片文件，共 792,292 字节；`settings.json` 包含全局模型配置及个人偏好。旧目录同时存在大量历史测试目录和一个发布前数据库备份，因此迁移输入只选当前 `sessions.db`、`settings.json` 与 `images`，不复制整棵运行目录。

## 一致性快照及真实数据库演练

12:05:58 使用 SQLite 只读连接的 backup API 建立一致性数据库快照，再复制当前设置与图片。源文件在捕获前后的 SHA-256 一致；源数据库 SHA-256 为 `eec64f67baa810a981dd09067edc9cf804055de8527f1794716d1e8ddcbb4505`。没有停止或修改旧服务。

12:06:04 完成随机命名空间的 PostgreSQL 18 演练，使用新建的合成归属管理员。迁移工具核对全部来源表的记录数和内容指纹；上表数量全部一致，329 条消息和 0 个运行中会话保持一致。新版本的 `kitchen_profile` 表为空，没有为旧记录推定个性化授权。

另外逐项验证：

- 4 张图片的 SHA-256 完全一致；
- 全局 `settings.json` 内容保持一致，密钥未输出到报告；
- 用户目录仅保留 `PERSONAL_PERSISTED` 白名单中的个人偏好；
- 迁移前后旧日常源文件 SHA-256 保持一致。

演练结束后仅清理本次随机创建的 PostgreSQL schema。私有快照、设置、图片与报告保存在被 Git 忽略的 `.runtime/daily-migration`，未纳入提交。

## 实际部署迁入

新的目标项目为 `chefmate-next-main-iteration`，应用端口 18875、应用 PostgreSQL 端口 15475，使用独立的新卷。导入前已检查目标 `chefmate*` schema 不存在，App 尚未启动。

12:10:17 完成实际导入，旧记录明确归属新部署的初始化管理员 `admin`。上表记录数、329 条消息、全部图片和设置再次核对一致，原日常源文件 SHA-256 保持不变。管理员凭据读取本地私有部署配置，没有写入报告。

12:10:55 用禁用网络的一次性容器，把生成的 6 个私有文件装入 `chefmate-next-main-iteration_chefmate-data`。目标卷起初为空，复制后每个文件 SHA-256 一致；文件归属 UID/GID 10001，目录权限为 `0700`、文件权限为 `0600`。没有在容器里运行 App、检索或模型调用。

应用启动与重启后的 HTTP 核对结果将在服务就绪后补充。

迁入后不向这些真实旧会话发送模型请求。模型功能验收继续使用独立合成账号和合成资料。

旧 143 条 `model_usage_ledger` 保留在个人历史统计中。旧系统没有中央金额账本、当轮价格快照或账号归属，因此不为这些历史调用伪造价格或追补扣款；新的中央金额账本从迁入后的调用开始记录。

本记录涉及个人业务库；公共菜谱基础库和营养基础库具有各自的导入与读取边界，不能由此推断所有基础数据库都已迁移。
