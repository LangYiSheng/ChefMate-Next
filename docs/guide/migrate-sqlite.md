# 将旧 SQLite 个人数据迁入 PostgreSQL

Docker 默认把账号和个人记录保存在应用 PostgreSQL，图片与设置仍在文件卷。本工具用于显式迁移旧 SQLite 运行目录，不会在普通启动时自动认领或覆盖旧数据。

先停止旧服务并备份完整运行目录。源库不能有尚未收拢的 `*.db-wal`、`*.db-shm` 或 `*.db-journal`；如有这些文件，需先使用旧环境完成 SQLite checkpoint 和正常关闭，再重新备份。不要直接删除日志文件，它们可能仍包含尚未合入主库的数据。

## 迁移条件

- 源目录包含 `accounts.db` 与 `users/<id>/sessions.db`，或旧单用户 `sessions.db`。
- 目标使用全新的应用数据库/schema 前缀和空的运行目录。已初始化过的账号 schema 会被拒绝，不做合并。
- 源目录与目标目录互不包含，不能通过符号链接或 Windows 目录联接跳转。
- 对旧单用户内容必须明确指定归属账号。不会给第一个注册用户自动分配旧对话。

迁移在随机临时 schema 中建表，复制身份、会话、个人菜谱、手记、记忆与用量，逐表核对记录数和内容指纹，再切换到正式命名空间。未知表、未知列、非法用户 ID 和命名空间冲突都会停止迁移。源数据库只读打开，迁移失败不修改源数据。

只复制已知的设置与图片目录，不复制运行目录中的临时脚本、测试凭据或其他杂项。用户设置只保留个人偏好字段，全局模型配置仍留在全局设置文件。旧归档目录保留在原备份中，不自动转换其含义。

## Docker 执行

按[部署指南](deployment.md)设置 `.env` 中的管理员密码和应用数据库密码，准备全新的应用数据卷。先只启动应用数据库，不启动 App：

```powershell
docker compose up -d app-db --wait --wait-timeout 180
```

将下面的源目录替换为自己的已停止并备份的运行目录。只读挂载源，密码通过 Compose 环境变量传入，不写进命令参数：

```powershell
$legacyRuntime = 'D:/ChefMate-Backup/runtime'
docker compose run --rm --no-deps -v "${legacyRuntime}:/legacy:ro" app python -m chefmate_playground.migrate_data --source-dir /legacy --target-dir /var/lib/chefmate --apply
```

如果是只有 `sessions.db` 的旧单用户库，需要显式添加 `--legacy-owner admin`，且 `.env` 的初始化管理员用户名必须与它一致。迁移工具会创建该明确指定的管理员并导入旧内容。已有账号库迁移时，`--legacy-owner` 则需指向源账号中已经存在的用户；同一用户同时有根目录旧库和个人独立库时会拒绝自动合并。

工具返回不含密码的表计数和目标 schema。确认成功后再启动：

```powershell
docker compose up -d --wait --wait-timeout 180
```

首次登录后检查旧对话、个人菜谱、记忆、手记、图片和额度，再用第二个账号检查隔离。源备份在核对完成之前一直保留。

源码环境也可运行相同 Python 模块，通过 `CHEFMATE_APP_DATABASE_URL` 指定目标连接；使用 `--schema-prefix` 选择另一组全新命名空间。迁移需要数据库的建 schema、建表和写入权限。

## 失败与回退

失败会回收本次随机创建的 SQL 临时 schema，保留源和可供排查的文件暂存目录。目标已有内容时不要删数据强行重跑；先查看原因，再选择另一个全新目标。文件系统与 PostgreSQL 无法组成跨系统原子事务，极端的提交或文件错误应结合数据库与目标目录共同核对，不能只看一次命令的退出码。

真实迁移测试覆盖两用户身份、价格账本、会话、记忆、主题与图片；另外验证旧单用户显式归属、源哈希不变、未知表拒绝、路径逃逸拒绝与审计自增序列恢复。实测结果按工程记录中的环境与日期阅读。
