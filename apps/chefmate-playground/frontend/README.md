# ChefMate React 前端

React 19.3.1、TypeScript 7 与 Vite 8。页面、表单、聊天和厨房视图全部由 React 渲染；Python 保持 HTTP/SSE 与领域规则的所有权。

## 运行

仓库根目录运行 `apps/chefmate-playground/run.ps1`，会安装锁定依赖、构建前端，再启动本机 Python 服务。默认地址为 `http://127.0.0.1:18765/`。

前端独立命令在本目录执行：

```powershell
npm ci
npm run build
npm test
npm run dev
```

开发地址为 `http://127.0.0.1:5173/static/`，API 代理到 `127.0.0.1:18765`，需要同时运行 Python 服务。生产构建输出到 `../web`，不提交构建产物。Python 将同一应用用于 `/`、`/settings`、`/recipes`、`/journal`，将构建资源挂在 `/static`。没有构建时页面返回明确的 503 提示。

## 代码边界

| 目录 | 职责 |
| --- | --- |
| `src/runtime` | HTTP/SSE、运行任务、会话恢复、文本/图片草稿与待定请求对账 |
| `src/components` | 会话导航、聊天、输入区、菜谱详情、诊断和安全文本 |
| `src/components/ui` | 主题化 Select、键盘与焦点行为、统一输入框样式 |
| `src/components/developer` | 工具和组件目录、公开执行过程、真实请求时间线与树视图 |
| `src/features/preferences` | 普通偏好 API、串行保存、权威读取与浏览器缓存 |
| `src/features/workspace` | 结构化任务、用户动作、问答确认、专注模式、后台计时 |
| `src/features/settings` | 设置、配色字体、长期记忆、用量与版本日志 |
| `src/features/centers` | 菜谱搜索编辑、饮食核对、做饭手记与回收恢复 |
| `src/styles` | 全局颜色、字体、控件及页面布局；各功能域另有作用域样式 |
| `public` | 本地字体、品牌、图标及其许可证 |

运行任务与计时器由应用级 store 管理，React 通过 `useSyncExternalStore` 订阅；切换组件不取消服务端任务。厨房动作携带 `base_revision` 与持久化 `action_id`，网络结果不确定时复用同一标识。模型输出通过安全 Markdown 与白名单 React 组件渲染，不注入模型生成的 HTML。

普通偏好从服务端读取，首次使用只迁移浏览器外观，之后本地存储作为缓存。写入保留读取时的版本，冲突不自动覆盖；运行完成与断线恢复后重新读取当前权威状态，不把旧会话工具结果回放成设置。菜谱审核卡也读取当前记录，待回答问题、续轮或同步期间不能另行发布。

完整启动、隔离验收与发布约定见[开发指南](../../../docs/guide/development.md)。
