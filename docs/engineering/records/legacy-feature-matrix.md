> 工程档案：保留当时的计划、范围与验收结论。当前能力请从[文档入口](../../README.md)开始阅读，历史测试数量不代表本轮结果。

# 旧版 ChefMate 功能迁移矩阵

2026-10-13 对 `D:\ChefMate` 只读考察。以下“实装”指存在真实代码链路，本轮没有运行旧数据库、模型、ASR 或视觉服务。旧目录未发现 AGENTS.md；未读取 .env。新版原有检索/core/playground 修改保持不变。

| 功能 | 旧版证据（绝对路径及行号） | 本轮领域包迁移与边界 |
| --- | --- | --- |
| 登录/注册/登出 | `D:/ChefMate/backend/app/services/auth_service.py:31`，`D:/ChefMate/backend/app/api/routes/auth.py:17` | 不迁账号，应用负责单用户工作台 |
| 首次引导/偏好表单 | `D:/ChefMate/frontend/src/components/WorkspaceOnboardingModal.vue:25`，`D:/ChefMate/frontend/src/components/ProfileSettingsPanel.vue:114` | 闭合 preference_form 和受控 Partial 更新；不复制六步引导 |
| 对话/SSE/持久化 | `D:/ChefMate/backend/app/api/routes/conversations.py:26`，`D:/ChefMate/backend/app/repositories/conversation_repository.py:338` | 复用新版应用会话生命周期；领域包不接管模型或 SSE |
| 菜谱浏览与搜索范围 | `D:/ChefMate/frontend/src/components/RecipeLibraryPanel.vue:22` | 复用新版真实检索；投影候选和详情 |
| 长期偏好推荐 | `D:/ChefMate/backend/app/services/recipe_service.py:136` | 偏好进入短快照供模型调用真实检索；不宣称已实现推荐算法 |
| Agent 候选随机抽样 | `D:/ChefMate/backend/app/agent/tools.py:332` | 不迁随机抽3规则，保留可解释硬条件 |
| 四阶段任务 | `D:/ChefMate/backend/app/domain/enums.py:4`，`D:/ChefMate/backend/app/services/task_service.py:118` | 显式 idea/planning/shopping/prep/cooking/completed；分离采购勾选和备料 |
| 动态任务菜谱原件 | `D:/ChefMate/backend/app/domain/models.py:44`，`D:/ChefMate/backend/app/services/task_service.py:73` | 冻结真实检索原件、保留源快照，独立执行状态 |
| 生成新菜谱 | `D:/ChefMate/backend/app/agent/tools.py:342` | 本轮不开放任意菜谱生成，全部菜谱来自真实检索或已冻结任务 |
| 推荐/详情卡片 | `D:/ChefMate/backend/app/domain/cards.py:26`，`D:/ChefMate/frontend/src/components/cards/MessageCardRenderer.vue:49` | 闭合 recipe_choices/recipe_detail，历史展示只读 |
| 备料清单/闪卡 | `D:/ChefMate/frontend/src/components/cards/PantryStatusCard.vue:49` | prep_checklist 与直接持久化动作；闪卡是应用呈现方式 |
| 独立购物清单 | 旧版路由仅 `D:/ChefMate/backend/app/api/router.py:6` 六组；缺料来自 `D:/ChefMate/backend/app/domain/cards.py:91` | 旧版没有购物车/采购 API；本轮从餐单新增安全合并购物投影 |
| 烹饪步骤与进度 | `D:/ChefMate/frontend/src/components/cards/CookingGuideCard.vue:35`，`D:/ChefMate/backend/app/agent/tools.py:614` | cooking_guide，浏览/完成分离，全餐单 done 门槛 |
| 前端计时器 | `D:/ChefMate/frontend/src/App.vue:114`，`:571`，`:1272` | 不复制内存倒计时；UTC 截止时间与暂停剩余量可恢复 |
| 图片识别 | `D:/ChefMate/backend/app/services/file_service.py:18`，`D:/ChefMate/backend/app/services/vision_service.py:26` | 本轮不迁外部多模态调用；未来先用户确认再解析本库食材 |
| 本地 YOLO | `D:/ChefMate/backend/app/services/vision_service.py:51` 明确 NotImplementedError | 未实装接入，不能作为已完成视觉模型功能迁移 |
| 语音输入/唤醒 | `D:/ChefMate/frontend/src/composables/useVoiceInput.ts:404`、`:696`，`D:/ChefMate/backend/app/services/voice_service.py:34` | 本轮不迁外部 ASR；旧版为输入，未有 TTS 输出链路 |
| 历史任务/用户记忆 | `D:/ChefMate/backend/app/services/task_service.py:200`，`D:/ChefMate/backend/app/repositories/conversation_repository.py:281` | 当前会话任务与最近20操作；独立用户长期历史后续做 |

## 复用规则

1. 源菜谱与任务原件分离：`D:/ChefMate/backend/app/utils/recipe_snapshot.py:38`。
2. 没有食材/步骤不能进入执行、备料未齐不能烹饪、步骤未完成不能结束：同文件 `:259`；必需食材规则见 `D:/ChefMate/backend/app/domain/cards.py:150`。新版源库缺可靠可选字段，改成逐项用户确认并显示边界。
3. 按阶段限制修改：`D:/ChefMate/backend/app/utils/recipe_snapshot.py:223`。
4. 备料/烹饪阶段显示对应卡片：`D:/ChefMate/backend/app/services/conversation_service.py:455`。
5. 推荐阶段取消不计历史，备料/烹饪取消保留历史：`D:/ChefMate/backend/app/agent/tools.py:500`、`:599`。本轮没有独立多任务历史系统，不伪造这一完成程度。

## 不能照搬的实现

- 客户端勾选和浏览只是深拷贝显示 overlay，仍依赖模型再写任务：`D:/ChefMate/backend/app/utils/recipe_snapshot.py:180`、`D:/ChefMate/backend/app/services/conversation_service.py:311`。新版用户动作直接持久化，模型解释依据可信短快照。
- UI 说关闭记忆开关就不能更新，但工具未检查开关：`D:/ChefMate/frontend/src/components/ProfileSettingsPanel.vue:233`、`D:/ChefMate/backend/app/agent/tools.py:145`。不把提示词当服务权限。
- 同轮切阶段后继续新阶段工具的提示与一次绑定工具清单不一致：`D:/ChefMate/backend/app/agent/prompts.py:25`、`D:/ChefMate/backend/app/agent/graph.py:17`、`D:/ChefMate/backend/app/services/conversation_service.py:201`。领域包做实际阶段校验，智能体框架由新版核心负责。
- `D:/ChefMate/backend/README.md:12` 仍称骨架，与已实现持久化和 LangGraph 代码不符。README 的 ml/YOLO 声明也不能替代实际接入证据。
- 旧版默认份量/耗时、文本别名、可选字段不能覆盖新版 null、估算、真实身份和源数据版本规则。
