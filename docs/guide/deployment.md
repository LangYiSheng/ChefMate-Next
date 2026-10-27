# 用 Docker 运行 ChefMate Next

Docker 是日常启动方式：Node 与 Python 依赖在镜像里构建，浏览器和 API 由同一个服务提供。主机只需 Docker Engine / Docker Desktop 与 Docker Compose v2，不需要安装 Node、Python 或 uv。

先阅读[账号与多用户设计](../design/accounts-admin.md)，再按这里部署。镜像不会携带开发机的 `.env`、数据库、用户图片、API Key 或会话记录；已有本地数据不会因为启动容器自动归属某个新账号。

## 第一次启动

在仓库根目录运行：

```powershell
Copy-Item .env.docker.example .env
# 编辑根目录 .env，填写 CHEFMATE_ADMIN_PASSWORD 与 CHEFMATE_APP_DB_PASSWORD。
docker compose up --build -d --wait --wait-timeout 180
```

已有根目录 `.env` 时保留原文件，按模板补充字段。管理员用户名默认 `admin`，密码必须自行设置，至少 10 个字符；建议使用随机生成的长密码。`CHEFMATE_APP_DB_PASSWORD` 为应用 PostgreSQL 的独立密码，请使用至少 20 位、与管理员密码不同的密码。密码不会自动写进镜像，也没有通用初始密码。管理员只在显式指定的账号首次创建时初始化；重新启动不会覆盖已存在账号的密码或把普通账号提升为管理员。

密码含 `$`、`#`、空格或其他标点时，在 `.env` 用单引号包住原始值，避免 Compose 插值。容器入口会把默认应用库和配套菜谱库的密码编码到连接 URI 中；不需要手工把原始密码改成 `%40` 等形式。自行填写完整 PostgreSQL 连接 URI 时则需遵循 URI 编码规则，应用保留该显式地址。不要把完整展开后的 `docker compose config` 输出贴进日志；只检查格式时用 `config --quiet`。

打开 [ChefMate 工作台](http://127.0.0.1:18765)。用上述管理员账号登录并配置模型、计价及用户额度；普通用户在登录页注册账号，完成欢迎引导后进入自己的厨房。默认启动会自动准备独立基础菜谱库并导入随仓库发布的 358 条菜谱。模型可以稍后配置；账号、引导和管理入口不依赖模型供应商在线。

新环境的普通用户初始金额额度由 `CHEFMATE_NEW_USER_COST_LIMIT_MICROS` 提供种子，默认 `5000000`，即 **5 元人民币**（1 元 = 1,000,000 微元）。初始化后，管理员平台的注册策略负责开放/暂停注册及新用户默认金额；数据库中已经确认的金额策略优先于环境种子。调整默认值只影响之后注册的用户，不修改已有用户的金额额度。已有账号仍由管理员逐一调整，Token 数用于用量统计，不再作为用户余额或限制。

金额预算和本轮模型报价统一使用 CNY，不自动混合币种或换算汇率。首次配置真实模型前，管理员需保存确认计价；未确认报价时，有限金额预算的模型请求会被拒绝，而不是把未知成本计为零。确实免费的本地模型也可由管理员明确保存全零价格。

Windows 也可使用：

```powershell
./scripts/start-docker.ps1
```

脚本第一次会创建空白 `.env` 并提示填写必需项；以后会构建、启动并等待健康检查。`-NoBuild` 复用已构建镜像。修改源码或升级后应重新构建。

运维脚本已验证 Windows 自带的 PowerShell 5.1 与 PowerShell 7。含中文的 `.ps1` 有意保存为 UTF-8 BOM，编辑时保留该格式；Windows PowerShell 5.1 会把无 BOM 的非 ASCII 源码按旧代码页解读，见 [PowerShell 官方编码说明](https://github.com/MicrosoftDocs/PowerShell-Docs/blob/main/reference/7.7/Microsoft.PowerShell.Core/About/about_Character_Encoding.md)。需要单次运行当前仓库脚本时，也可使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-docker.ps1
```

三个运维脚本都支持 `-EnvFile`、`-ProjectName` 与 `-ComposeOverride`，分别用于其他环境文件、隔离项目和额外 Compose 文件。路径可包含空格或中文，调用时加引号；启动、停止和备份使用相同项目及文件组合。`CHEFMATE_IMAGE` 可覆盖默认本地镜像名，便于隔离验收。

启动脚本构建时，仅在 Git 工作区干净且没有显式构建标记时，将当前完整提交 SHA 写入镜像的 `CHEFMATE_BUILD_COMMIT` 与 OCI revision；结束后恢复调用进程环境。源码有未提交修改、没有 Git 或直接使用源码压缩包时，保持提交身份未知。CI 或手动构建可显式提供合法 SHA；`docker compose up --build` 不会自行执行 Git 命令，需先设置该构建环境变量。版本号和发布日志不会因此改变。

## 数据保存在哪里

默认 Compose 项目名是 `chefmate-next`。日常启动包含 App、PostgreSQL 18 应用库 `app-db`、独立菜谱库 `db` 与一次性初始化服务 `catalog-migrate`。两个数据库健康、基础菜谱初始化或校验成功后才启动 App；应用账号与个人业务结构由服务启动时检查。数据库没有主机端口，默认只在 Compose 私有网络内供 App 连接。

| 命名卷 | 内容 |
| --- | --- |
| `chefmate-next_app-db-data` | PostgreSQL 应用库：账号、登录会话、额度、用量、对话及个人业务记录 |
| `chefmate-next_chefmate-data` | `/var/lib/chefmate`：用户图片、管理与个人偏好配置、归档等文件 |
| `chefmate-next_chefmate-catalog` | 独立 PostgreSQL 基础菜谱库，默认含 358 条随仓库发布的数据 |
| `chefmate-next_catalog-status` | 初始化服务写入、App 只读检查的菜谱初始化完成标记 |

重建镜像和替换容器不会清空这些卷。应用库通过 `CHEFMATE_APP_DATABASE_URL` 连接，基础菜谱库通过另一个 `CHEFMATE_DATABASE_URL` 连接；两者用途不同。默认应用库为 `chefmate_app`，不要把个人数据 DSN 指到现有基础菜谱库。旧单用户 SQLite 数据保留在原目录，不会被启动过程自动导入 PostgreSQL 或分配给新账号；需要保留旧账号与个人记录时，先按[显式迁移指南](migrate-sqlite.md)处理，再启动目标 App。

App 运行进程使用 UID/GID `10001`，代码目录只读，临时文件写入独立 `/tmp`。默认单个 App 实例，运行中任务锁与工作区缓存按这个部署边界设计；需要横向扩容时，应先验证跨实例任务调度、模型配置更新和文件存储协议。PostgreSQL 存储并不自动带来多实例运行保证。

管理员与 PostgreSQL 的初始化密码都只在首次创建对应数据时生效。已有数据库卷时，仅修改 `.env` 的 PostgreSQL 密码不会自动修改数据库角色密码；应先备份，再按数据库凭据轮换流程同步修改角色与连接配置，不能删除卷重新初始化。

```powershell
docker compose ps
docker compose logs --tail 100 app
docker compose stop
docker compose start
```

停止并移除容器可运行 `docker compose down`，或 `./scripts/stop-docker.ps1`。这两种方式保留命名卷。不要在日常停止命令后附加 `-v`，它会删除保存数据的卷。

## 配置模型和基础菜谱

模型可以由管理员在界面中填写。也可以首次启动前在根目录 `.env` 填写 `OPENAI_MODEL`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`；已经持久化的管理员设置按应用的覆盖规则生效，后续修改以界面为准。普通用户无法读取密钥或修改全局模型连接。

默认 Compose 管理独立的基础菜谱库；应用库支持用 `CHEFMATE_APP_DATABASE_URL` 接入外部 PostgreSQL。自定义外部菜谱库时，需同时覆盖 App 与初始化服务的连接字段及依赖，明确沿用该固定种子或维护自己的初始化流程，不能只把 App 指向另一库。Docker 容器中的 `127.0.0.1` 指向容器自身；Docker Desktop 访问主机时使用 `host.docker.internal`。不要把 DSN 写进命令行参数、日志或 Git 提交。

USDA 营养库是可选的独立数据文件。需要时写一个本地 Compose 覆盖文件，将主机已有数据库只读挂载到 `/data/usda.db`，并设置 `CHEFMATE_USDA_DATABASE=/data/usda.db`。镜像不会下载、生成或伪造这个库。具体数据准备见[数据导览](data.md)与[食物数据参考](../reference/food-data.md)。

### 默认的 358 条基础菜谱

仓库的 [固定数据说明](../../data/seed/catalog/README.md)记录数据来源、原始文件摘要与精简方式。版本为 `chefmate-bundled-20261005`；JSON 保留全部在线存储投影，使用固定 SHA256 校验，随 Git 与镜像一起交付。初始化顺序是两个 PostgreSQL 健康 → `catalog-migrate` 建表并安全导入/校验 → App。

初始化只向空数据集导入；已存在同版本数据时逐表核对全部内容，不重新覆盖或改变导入时间。不同版本、无身份的已有数据或被修改的同版本内容会使初始化明确失败，原数据保留。完成标记会先撤销，只有全部成功才原子写回，App 的 Docker 健康检查也要求标记与随镜像发布的数据一致。遇到冲突应先备份并人工确认数据迁移，不能靠删除卷解决。

这批来源的 `approved_only=false`，包含 2 条已审核、356 条待审核，`ready_for_recommendation=true` 为 0 条。仅对这一固定摘要的内置包显式允许导入未审核记录，审核状态、营养估算与未知值原样保留；可检索不等于完成审核。严格要求 ready 的查询仍会过滤。USDA 原始营养库不随此包伪造或生成。

默认菜谱库密码复用 `CHEFMATE_APP_DB_PASSWORD`；可在首次创建卷前单设 `CHEFMATE_POSTGRES_PASSWORD`。旧命令的 `-f compose.catalog.yaml` 与脚本 `-WithCatalog` 保留为兼容别名，现在无需额外启用菜谱服务。显式整库替换仍须按[存储参考](../reference/storage.md)验证并人工执行，正常启动不会执行 `import --replace`。

## 端口、反向代理与 HTTPS

默认只发布主机回环地址 `127.0.0.1:18765`。修改 `.env` 的 `CHEFMATE_HTTP_PORT` 可换端口；只有确实需要远程访问时才把 `CHEFMATE_BIND_ADDRESS` 改为实际监听地址，并把访问域名加入 `CHEFMATE_ALLOWED_HOSTS`，然后重新创建容器。

公开服务应放在 HTTPS 反向代理后，设置 `CHEFMATE_COOKIE_SECURE=true`。代理保留公开访问的 `Host`，并发送 `X-Forwarded-Proto: https`。在 `.env` 的 `FORWARDED_ALLOW_IPS` 填入 App 实际看到的代理固定 IP，多个地址以逗号分隔；默认只信任 `127.0.0.1`。代理在另一个 Docker 容器或主机上时，应使用其实际对端地址，不能照搬主机浏览器地址，也不要配置成 `*`。Uvicorn 只接受可信代理的协议头，应用再将 `Origin` 的协议与请求协议、主机一起核对；漏配可信地址会让合法的 HTTPS 写请求被拒绝。

`CHEFMATE_ALLOWED_HOSTS` 填主机名，不含协议、端口或路径，保留 `127.0.0.1` 供容器健康检查使用。Cookie 开启 Secure 后浏览器通过纯 HTTP 访问不会发送登录凭据，因此本机 HTTP 测试默认保持 `false`。代理可信来源的处理见 [Uvicorn 部署文档](https://www.uvicorn.org/deployment/#running-behind-nginx)。

登录使用 HttpOnly 会话 Cookie；跨站写请求会在服务端拒绝。不要给管理接口额外放开跨域访问。账号密码注册当前不包含邮箱验证、邮件找回或外部身份服务；忘记密码由管理员执行重置。

## 升级与备份

升级前备份持久数据，保留当前源码提交。默认备份包含应用库 `app.dump`、基础菜谱库 `catalog.dump` 与运行文件 `runtime/`，三者成组保存；只备份数据库会遗漏图片与文件配置。不要直接复制运行中的 PostgreSQL 数据目录。

```powershell
./scripts/backup-docker.ps1
```

脚本拒绝覆盖已有备份、拒绝同一项目同时执行两次备份，先检查两个 PostgreSQL 就绪，再停止 App。它使用 `pg_dump -Fc -f` 后通过 Compose 复制文件，避免旧版 PowerShell 重定向损坏二进制。只有全部复制并计算摘要成功才原子写入 `backup.json` 的完成标记；清单包含双库导出及所有运行文件的长度和 SHA256，也记录 App 实际镜像 ID/引用、源码提交与是否有本地修改。没有可用 Git 信息时相关字段为 `null`，源码提交不冒充运行镜像的完整身份。`-WithCatalog` 仅保留兼容，默认已备份菜谱库。

备份含私人对话、图片及模型连接配置，应按私有数据保存；`backups/` 已被 Git 忽略。

此脚本只处理默认受管双库。它核对实际 App 容器的连接类型；显式完整 DSN、外部主机或自定义数据库名/用户/端口会在停止 App、创建备份目录前拒绝，避免把未使用的内置数据库误当成完整备份。使用外部数据库时，按该数据库的备份流程导出实际数据，并与运行文件成组保存；不在命令行或日志公开连接信息。

临时导出文件使用时间加随机 ID，成功后只清理本次精确生成的容器 `/tmp` 文件；失败目录保留供检查，不应当成完整备份。原先运行的 App 会在导出结束后恢复，并等待 App 与 PostgreSQL 健康；原先停止的 App 不会被自动启动。清理临时文件失败也会继续尝试恢复 App。

复制备份、恢复或升级前运行完整性检查：

```powershell
./scripts/verify-backup.ps1 -BackupDirectory './backups/自己的备份目录'
```

校验器会拒绝内容变化、缺失或额外文件、重复或越界路径、符号链接和目录联接。仅有完成标记不能代替摘要核对；旧版没有 SHA256 清单的备份不会自动视为通过。这个检查验证文件与清单一致，不替代离线保存或备份访问控制。

恢复应使用一个新的 Compose 项目和新卷：先只启动目标 `app-db` 与 `db`，不要启动 App 或 `catalog-migrate`。把 `app.dump` 复制到数据库容器的 `/tmp`，在空目标库运行 `pg_restore -U chefmate -d chefmate_app --no-owner --exit-on-error /tmp/app.dump`。运行文件恢复到新的 `chefmate-data` 卷，确认 UID/GID 为 `10001:10001`，再启动 App；目标库已经有初始化表时应重新准备一个空目标库，避免把两套数据混在一起。先在隔离端口检查账号、对话、图片、个人记录和用量，不直接覆盖现用卷。

将 `catalog.dump` 用 `pg_restore -U chefmate -d chefmate --no-owner --exit-on-error` 恢复到空菜谱库，然后再运行迁移与 App。不要先启动 `catalog-migrate` 建出空表，再把备份混入已有结构；外部现用菜谱库也不应被测试恢复覆盖。

更新源码后运行 `docker compose up --build -d --wait --wait-timeout 180`。保留卷后，账号、设置和个人记录应继续存在；不要用删除卷来“修复”迁移失败。数据库结构升级未验证回滚前，不把旧镜像覆盖新格式数据作为默认回滚方式。

## 判断是否真的启动好了

容器健康检查先核对菜谱初始化完成标记，再访问 `GET /healthz`，确认 ChefMate 服务与应用账号数据库可响应；账号库检查有 2 秒边界，不可用时返回不含连接详情的 503。`/healthz` 本身不检查模型供应商或基础菜谱实时查询；Docker 的初始化标记只证明启动时固定数据导入或校验成功，不代替持续监控菜谱数据库。数据库恢复后，健康检查应自行恢复，无需通过删除卷修复。

登录后检查 `/api/status` 和管理员模型连接测试，分别确认模型配置、基础库连接与实际工具调用。再做一次注册 → 欢迎配置 → 新建对话 → 刷新恢复；用第二个账号确认无法读取第一个账号的对话、图片和个人菜谱。重启容器后重新确认账号和记录仍在。

本轮构建、容器健康、账号隔离、持久化及成组恢复的实际结果见[Docker 验收记录](../engineering/records/2026-10-27-docker-acceptance.md)。尚未实际调用真实模型的环境，不应把“容器健康”报告为完整 AI 做饭流程通过。

构建与 Compose 语义参考 [Docker 多阶段构建](https://docs.docker.com/build/building/multi-stage/)、[Compose 启动顺序](https://docs.docker.com/compose/how-tos/startup-order/)和[命名卷](https://docs.docker.com/engine/storage/volumes/)。
