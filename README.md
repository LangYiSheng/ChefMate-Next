# ChefMate

面向食材检索、多约束菜谱查询和多轮烹饪交互的独立 Python 包与本地测试工作台。

| 目录 | 职责 |
|---|---|
| `data_cleaning` | AI 结构化转换、人工审核、本地营养补充与导出 |
| `packages/chefmate-contracts` | 菜谱、营养和词表契约 |
| `packages/chefmate-food-data` | 本地 USDA 查询与维护 |
| `packages/chefmate-recipe-processing` | 可注入 AI 的菜谱处理算法 |
| `packages/chefmate-storage` | PostgreSQL 存储与一致的只读查询 |
| `packages/chefmate-retrieval` | 食材候选、分组条件、查询证据与工具参数 |
| `packages/chefmate-workflow` | 厨房任务、冻结菜谱、界面协议、份量、备菜、步骤和计时 |
| `packages/chefmate-agent` | LangChain 标准 Agent、业务 middleware、界面工具与按需 Skills |
| `apps/chefmate-playground` | 聊天与厨房工作区、个人菜谱、食光手记、长期记忆、SSE 和本地持久化 |

## 运行测试页面

```powershell
uv run --project apps/chefmate-playground chefmate-playground
```

访问 <http://127.0.0.1:18765>。配置与完整启动步骤见 [工作台说明](apps/chefmate-playground/README.md)。

三个核心查询工具和可用选项工具均只读现有菜谱库，智能体从真实食材候选和菜谱引用中选择。营养未知值、估算标记、可选配料和复合配方证据随结果保留。

查询、工作流与智能体可以直接供其他后端使用。模型调用闭合的展示工具，前端通过9种白名单组件渲染；用户修改表单、选菜、勾选备菜或确认步骤时直接保存任务，下一轮对话接收服务端核对的短快照和操作记录。

支持最多4道菜的共享餐单，整餐人数与每盘配方份量分开安排。图片或文字可整理成个人菜谱草稿，核对后保存并再次烹饪；食光手记记录尝试、完成、反馈和营养资料；长期记忆提供用户偏好、助手总结与按需历史检索。未知数值和不能缩放的食材保留来源限制，营养均分参考不代表实际摄入。

烹饪步骤与聊天同屏，全部菜完成后可确认本次菜肴并自然收尾。计时到期有浏览器声音提醒，设置可试听和关闭，并提供浅色、深色和跟随系统主题。关闭厨房技能会同时停用其对应工具。

框架分工、组件工具、动作与版本边界见[智能界面架构](docs/agent-ui-architecture.md)。
