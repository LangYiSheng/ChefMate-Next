# React 前端迁移验收

## 实现范围

- 前端完整改为 React 19.3.1、TypeScript 7.0.2、Vite 8.3.5；设置与个人中心进入同一应用，移除 iframe 和原生 DOM 控制器。
- 删除 86 个旧 HTML/JS/旧实现测试文件。38 个 CSS、字体、品牌图标及许可证文件迁入 `frontend/public`，清理前逐文件 SHA-256 核对一致；只对迁移后的 README 修正路径。
- Python 保留领域服务、HTTP/SSE 和用户数据合同；四个 UI 路由返回同一个构建，资源同源挂在 `/static`。构建缺失时明确返回 503，不回退旧页面。
- `run.ps1` 安装锁定前端依赖并构建，再启动服务。生成的 `web` 与 `node_modules` 不提交。版本保持 0.2.0。

运行层使用应用级 store 与 `useSyncExternalStore`，任务和计时器不依附页面组件。文字和图片草稿分别保存到 localStorage/IndexedDB；待定 POST 与确认动作保留请求标识，刷新先读取服务端权威状态，不自动重发用户任务。

## 自动检查

| 检查 | 结果 |
| --- | --- |
| TypeScript + Vite 生产构建 | 通过，无构建警告 |
| Vitest | 8 个文件，64 项通过 |
| Python App 与领域包回归 | 643 项：620 通过，23 按条件跳过，0 失败 |
| Ruff | 通过 |
| 本地 Markdown 链接 | 通过 |
| `git diff --check` | 通过 |

前端重点覆盖 SSE 分块 UTF-8/命名/多行事件、断流恢复、显式停止、切会话/卸载迟到响应、文本/图片草稿、POST 待定请求对账、确认续轮幂等、版本冲突、最终刷新补读、历史控件只读、计时器、设置跨分类修订、409 保草稿、导航保护、记忆与营养未知值。

Python 的跳过项依赖显式 PostgreSQL 集成测试配置或可选环境；本轮没有设置 `CHEFMATE_TEST_DSN`，不能将这些跳过项记为通过。

复现命令：

```powershell
npm --prefix apps/chefmate-playground/frontend run build
npm --prefix apps/chefmate-playground/frontend test
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest apps/chefmate-playground/tests packages -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/pytest-react-final
apps/chefmate-playground/.venv/Scripts/ruff.exe check --no-cache --config apps/chefmate-playground/pyproject.toml apps/chefmate-playground/src packages
apps/chefmate-playground/.venv/Scripts/python.exe scripts/check_docs.py
```

## 真实服务与浏览器

隔离服务为 `http://127.0.0.1:18766`，会话、个人数据、图片和设置位于 App 的 `.runtime/react-preview`；复用现有模型配置与只读基础菜谱库。检查发现已有 `ChefMate_Test` 容器停止，启动该现有容器后 `/api/status` 显示模型已配置、数据库就绪，基础库 358 道菜谱。

实际执行：

1. 设置在主工作台打开；修改显示模式后尝试侧栏离开，确认保护有效；保存后主题应用。
2. 菜谱中心实际返回 358 道菜谱，搜索“虾”返回 9 道。
3. 使用真实外部模型查询“一人、15 分钟、白灼虾”，得到真实菜谱与选菜组件；生成期间切到设置，任务继续。
4. 勾选白灼虾并确认，保存餐单后只发起一次自动续轮，展示备料清单。
5. 明确声明测试食材已备齐，Agent 通过阶段规则进入烹饪；界面逐步确认 5 个步骤，最后才允许完成整餐。
6. 创建 8 秒计时，观察到时状态；到期未自动完成烹饪步骤；刷新后计时和步骤恢复。
7. 完成整餐后，真实模型读取自动登记结果，食光手记显示 1 餐、1 道菜与 1 条个人饮食记录，估算值继续标记。
8. 上传本地生成的合成 PNG，压缩成图片草稿；文字和图片刷新后恢复，图片可页内预览。合成图片没有发送到模型。
9. 会话重命名正常；手机专注模式保留文字草稿，完整输入区宽度正确。
10. 1280×720、390×844、844×390 检查，无横向溢出；浏览器控制台未观察到 error/warn。

截图和 XML 测试结果保存在未提交的 `.runtime/react-preview`。本轮验证的是本机浏览器与现有模型配置；没有进行真实移动设备、系统休眠、后台声音听感或所有供应商兼容性验收。

## 交付

原有根目录 `.gitignore` 与未跟踪的 `test/` 保留；密钥、个人配置、运行数据库、测试截图与构建产物不入提交。迁移为工程变更，未虚构新增产品功能或升级版本号。
