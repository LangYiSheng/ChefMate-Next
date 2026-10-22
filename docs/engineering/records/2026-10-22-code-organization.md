# 代码结构与文档体系整理验收

日期：2026-10-22（Asia/Shanghai）。代码阶段提交：`baca9af`。

## 本轮范围

本轮整理核心 App、智能体与厨房工作流，按职责拆开入口文件。公共 HTTP 路径、包入口、工作区结构与业务门槛保持原有合同。附带修复浏览器恢复失效会话的缺口，并使独立包测试命令能找到公共 fixture。

| 原入口 | 整理前行数 | 整理后行数 | 新职责边界 |
| --- | ---: | ---: | --- |
| App `app.py` | 730 | 141 | 装配、生命周期、同源边界；路由分六组 |
| Agent `core.py` | 988 | 334 | 一轮生命周期；历史、工具、证据与错误边界分别维护 |
| Workflow `service.py` | 1,055 | 85 | 原子更新、版本与幂等；投影和厨房命令分开 |
| 前端 `app.js` | 1,477 | 284 | 控制器装配；输入、会话、轮次、渲染、详情分开 |
| 设置 `settings.js` | 673 | 360 | 配置读写与导航；字段与分类渲染分开 |
| 饮食 `meal_logging.py` | 620 | 304 | 仓储；模型、来源证据、工具分别维护 |
| 菜谱 `cookbook.py` | 560 | 410 | 仓储和检索组合；草稿契约、验证、工具分开 |

行数用于描述本轮规模，不作为模块质量阈值。具体文件与职责见[模块地图](../../guide/development.md)。Agent/Workflow 的内部协作基类共享同一服务实例，构造和可变状态仍有唯一归属。

## 自动验证

| 检查 | 结果 |
| --- | --- |
| App 与七个包完整 Python 回归，启用隔离 PostgreSQL 集成 | **638 passed / 1 skipped** |
| 清洗工具完整 Python 回归 | **87 passed** |
| 前端 Node 行为测试 | **189 passed** |
| App、packages、scripts Ruff；清洗工具 Ruff | 通过 |
| 全部前端 JavaScript 语法、Git diff 空白检查 | 通过 |
| 51 个浏览器模块 HTTP/MIME 与本地导入目标 | 通过 |
| Agent、Workflow、Storage wheel 构建及新模块/迁移资源检查 | 通过 |
| Markdown 文件、相对链接与标题锚点 | `scripts/check_docs.py` 通过，结果随文档提交重新检查 |

唯一跳过项是 `test_symlink_files_rejected`：当前 Windows 环境不能创建符号链接。真实数据库测试从当前配置取得连接，在随机 `cm_test_*` schema 内运行并清理，没有替换工作台基础菜谱集。

最终主回归命令为：

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest apps/chefmate-playground/tests packages -o addopts= -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/refactor-final-tests
data_cleaning/.venv/Scripts/python.exe -m pytest data_cleaning/tests -o addopts= -q -p no:cacheprovider --basetemp=data_cleaning/data/intermediate/refactor-final-tests
node --test --test-reporter=spec apps/chefmate-playground/static/tests/*.test.mjs
apps/chefmate-playground/.venv/Scripts/python.exe scripts/check_docs.py
```

主回归通过子进程环境显式注入 `CHEFMATE_TEST_DSN`，没有把凭据写入命令或文档。JUnit 结果保留在被忽略的应用/清洗运行目录。

## 真实 App 与浏览器

验收服务使用 `127.0.0.1:18766`，复用模型连接和只读基础菜谱库，个人数据使用独立的 `.runtime/refactor-acceptance/sessions.db`。启动状态为 `ready=true`，基础菜谱 358 道。

已验证：

1. 首页首条消息先创建会话，再发送请求。真实模型执行食材查询与菜谱筛选，返回六道半小时内候选及可操作组件。
2. 浏览器勾选白灼虾、确认后保存餐单，并自动发起续轮；真实阶段工具成功进入 `prep`，备菜清单可恢复。
3. 设置在原会话内打开，烹饪计时开关保存成功，返回后原餐单仍在。
4. 窄屏打开当前备菜，确认虾已备齐后变为 `1 / 11` 并沉底；刷新、重新打开任务仍为 `1 / 11`。
5. 执行中切到新对话，服务端活动轮次仍为 1；回到原会话可以看到运行状态，显式停止后终态为 cancelled，活动轮次归零。
6. 菜谱中心读取真实 358 道菜谱，搜索“虾”返回 9 道；页面与 API 数据一致。
7. 桌面、390×844 窄屏与 844×390 横屏检查通过，窄屏/横屏根滚动宽度分别为 390/844，没有横向溢出。浏览器控制台没有 warning/error。

真实续轮也遇到供应商超时，不能写成“所有真实模型场景均成功”。临时、仅验收进程内的脱敏异常包装定位到 `httpcore.ReadTimeout → httpx.ReadTimeout → OpenAI timeout`，等待响应头时超时。没有把诊断包装写进正式源码。此轮未声称重新走完真实烹饪至摄入登记的全部步骤，相关领域行为由本轮回归与此前[完整登记验收](2026-10-22-automatic-intake-and-typography.md)分别提供证据。

对照检查发现，重构前后 37 个工具定义、请求 schema 与系统提示相同；真实历史的校验、结果收集、模型投影和序列化均可在只读条件下完成。已保存的餐单和备菜不因生成失败丢失，半截模型历史不记成成功。

## 验收发现与处理

浏览器可能保留旧 `chefmate:active-session`，隔离服务或已清理数据目录不再拥有该会话。原实现 404 后仍继续使用失效 ID，发送提示“对话不存在”。现在打开/轮询遇到 404 会退回本地草稿，保留文字与本地图片，清理失效选择；迟到的 404 不覆盖后来选中的会话。新增回归覆盖双草稿、本地图片、失效响应及首条消息创建顺序。

单独执行某些包的测试时，最近的项目配置会让 pytest 停止寻找上级 `packages/conftest.py`。新增公共 `packages/pytest.ini` 固定这些包的测试根，fixture 保持单一来源；独立 Processing/Food Data/Contracts 命令分别验证通过。

全库 Ruff 发现处理包底部遗留导入，移动到顶部后通过原处理回归，没有更改计算规则。

## 文档交付

- [项目 README](../../../README.md)与各级模块 README 统一为用途、能力、快速开始、代码入口和验证。
- [导览](../../README.md)从核心 App 讲到智能体、数据与开发；案例注明合成或真实验收，技术点链接实现、测试与官方来源。
- [设计规范](../../design/README.md)集中维护现行架构、决策、交互视觉与工程约定。
- [参考资料](../../reference/README.md)承接完整接口与字段，移除本机配置断言与日期重复追加。
- [工程档案](../README.md)分别保存历史计划与验收，不把旧结论自动当作当前能力。

原有 `.gitignore` 修改与未跟踪 `test/` 保留。验收数据、配置、构建缓存与模型诊断未进入提交。

验收结束时活动轮次为 0，18766 隔离服务已关闭，并用连接检查确认不再响应；常用服务没有被重启或改写。临时浏览器视口已恢复。
