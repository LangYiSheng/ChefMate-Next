# ChefMate 交互与视觉规范

页面用平铺内容、清楚层级、细边界和少量强调色组织任务。聊天、厨房、设置和个人中心共用语义颜色、字体角色、控件和导航保护。
当前默认是 **浅色 `light` + 暖白墨黑 `ink` + 宋黑搭配 `mixed`**，已有有效偏好继续保留；缺失或非法值按同一默认规则修复。
默认值与可选组合以 [appearance-presets.ts](../../apps/chefmate-playground/frontend/src/features/settings/appearance-presets.ts) 为准，历史默认颜色和字体不作为现行规则。

界面由 React 19.3.1 组件和 TypeScript 状态服务实现，Vite 8 负责构建。组件和样式位于 `frontend/src/`；`frontend/public/` 只保存字体、品牌与第三方图标资源，生产页面由后端从 `web/` 构建目录提供。

[main.tsx](../../apps/chefmate-playground/frontend/src/main.tsx) 统一导入 [styles/index.css](../../apps/chefmate-playground/frontend/src/styles/index.css)，其下按基础角色、公共控件、页面框架、聊天和详情分层。设置、个人中心与厨房各自管理作用域 CSS，使用同一套颜色与字体角色；控件样式不跨功能域覆盖。

## 主题和语义颜色

支持浅色、深色、跟随系统；浅色与深色分别保存配色选择，跟随系统时切换到相应配色。
暖白墨黑、鼠尾草绿、燕麦奶咖等是可选方案，绿色不再是全局固定默认色。
配色选择、预览和应用使用同一份预设，禁止在页面另维护一套近似色值。

基础颜色在 [foundation.css](../../apps/chefmate-playground/frontend/src/styles/foundation.css)，预设通过 `appearanceTokens()` 覆盖以下角色：

| 变量 | 用途 |
| --- | --- |
| `--color-background` | 页面外框与侧栏底色 |
| `--color-canvas` | 主工作区、设置和预览内容 |
| `--color-surface/--color-hover` | 输入、轻量内容与悬停 |
| `--color-border/--color-border-strong` | 细边框、聚焦与拖动边界 |
| `--color-text/--color-muted/--color-subtle` | 正文、辅助说明和低优先级提示 |
| `--color-accent/--color-accent-hover/--color-accent-soft` | 主操作、悬停和选中背景 |
| `--color-on-accent` | 实色强调按钮上的文字与图标 |
| `--color-success/--color-warning/--color-danger` | 成功、待处理与错误，同时使用文字表达状态 |
| `--color-overlay/--color-shadow` | 模态遮罩与轻阴影 |

颜色变化同步到主页面、内嵌/独立设置、预览和浏览器主题色；跨窗口存储变化和系统主题变化使用同一应用入口。
普通外观与对话偏好通过 `/api/ui-preferences` 保存到当前应用配置，浏览器 `chefmate:ui-settings` 是外观投影与离线回退。
服务端尚未初始化外观时可迁入已有浏览器偏好；后续以服务端版本核对保存，历史工具结果不能重放旧偏好。
这些字段与模型、组件、计时及本机开发者设置分别管理。[主题应用](../../apps/chefmate-playground/frontend/src/features/settings/appearance.ts) · [偏好同步](../../apps/chefmate-playground/frontend/src/features/preferences/client.ts)

## 字体按角色选择

字体声明、基础角色与默认排版在 [foundation.css](../../apps/chefmate-playground/frontend/src/styles/foundation.css)，字体文件随应用提供；功能域样式按具体用途使用角色变量。
新样式优先使用角色变量，避免某个页面写死宋体后覆盖用户的阅读选择。

| 角色 | 变量 | 默认 `mixed` |
| --- | --- | --- |
| 大标题与主要菜名 | `--font-title/--font-title-weight` | 加粗宋体，字重 700 |
| 聊天正文、说明和烹饪步骤 | `--font-body/--font-reading` | 黑体；阅读角色沿用正文选择 |
| 营养指标、计时和步骤数值 | `--font-numeric` | 黑体，适用处使用等宽数字 |
| 导航、表单、按钮、单位、注释和小标题 | `--font-ui` | 黑体 |
| 代码与技术片段 | 独立等宽字体 | Consolas 等系统等宽回退 |

可选 `sans` 统一黑体、`serif` 使用宋体标题与正文、`wenkai` 使用文楷标题与正文；控件与数值仍保持黑体。
宋黑搭配表示标题宋体、正文与数值黑体，不能沿用过去“正文和大数值默认宋体”的写法。
紧凑标题、注释和单位在阅读容器里仍显式使用 UI 角色，防止继承正文后混乱。

正文按阅读场景保持适当行距，步骤指令沿用阅读角色和正常字重；标题、说明与操作不能只靠字体大小区别。
本地字体、回退与许可证见[字体资源](../../apps/chefmate-playground/frontend/public/fonts/README.md)。

## 页面框架与导航

布局常用 8、12、16、24、32px 间距。桌面侧栏与底色相接，主工作区使用有限圆角和细边界；长内容在所属主区域滚动。
移动端取消外框留白，正文与输入区保留边距；控件输入字号保持 16px，避免手机聚焦放大。
主框架、侧栏与抽屉断点由 [shell.css](../../apps/chefmate-playground/frontend/src/styles/shell.css) 管理；聊天和详情分别由 [chat.css](../../apps/chefmate-playground/frontend/src/styles/chat.css)、[details.css](../../apps/chefmate-playground/frontend/src/styles/details.css) 管理，功能域布局由其作用域 CSS 管理。

侧栏默认 248px，可在 208–360px 之间拖动或用键盘调整。
桌面折叠后保留紧凑导航与展开入口，窄屏使用抽屉，桌面折叠偏好不剥夺手机导航。
会话先取 20 条，通过“加载更多对话”按游标追加；加载失败保留当前列表。

置顶优先，其余按最近对话活动排序。会话菜单提供重命名、置顶与删除，重命名和删除使用页内弹窗。
名称和置顶独立保存，不修改厨房任务和对话活动时间；运行中的对话删除受后端保护。
导航先处理未保存修改，再执行原目标动作，延迟确认不能恢复过时的点击意图。[应用导航](../../apps/chefmate-playground/frontend/src/App.tsx) · [设置状态](../../apps/chefmate-playground/frontend/src/features/settings/settings-state.ts)

设置通过 `/settings` 路由在主工作区显示，直接打开该路径使用相同页面、字段层级与外观。
菜谱中心、食光手记和设置切换时，旧会话、未发送文字和图片草稿继续保留。
中心里的“发起聊天”建立新会话；查看、收藏、编辑和恢复仍留在对应中心语义内。

设置分类内使用顶部页签组织内容，切换时保留草稿，并支持方向键、Home 与 End。
短视口中页签不吸顶，随内容滚动，避免遮挡表单和操作。
内容区足够宽、当前可见内容至少有两个目录节点时，长期记忆、组件、更新记录、用量、关于或数据页面可显示右侧“本页内容”索引；索引只收录可见页签的节点，点击定位并跟随滚动高亮。[设置页签](../../apps/chefmate-playground/frontend/src/features/settings/SettingsFields.tsx) · [设置索引](../../apps/chefmate-playground/frontend/src/features/settings/SettingsIndex.tsx)

## 控件和保存反馈

设置、个人中心和开发者轮次选择共用自制 [Select.tsx](../../apps/chefmate-playground/frontend/src/components/ui/Select.tsx)，使用按钮与 listbox 浮层，保持主题、键盘导航、禁用项、必填和焦点反馈一致，不再由各页面使用原生下拉菜单。
React 受控输入的基础形状、字体、聚焦和错误状态由 [fields.css](../../apps/chefmate-playground/frontend/src/components/ui/fields.css) 统一；[设置字段](../../apps/chefmate-playground/frontend/src/features/settings/SettingsFields.tsx)和[中心控件](../../apps/chefmate-playground/frontend/src/features/centers/CenterControls.tsx)负责标签与行为。功能布局分别放在 [settings-react.css](../../apps/chefmate-playground/frontend/src/features/settings/settings-react.css)、[centers-react.css](../../apps/chefmate-playground/frontend/src/features/centers/centers-react.css)；厨房协议卡片、问题和计时使用 [workspace.css](../../apps/chefmate-playground/frontend/src/features/workspace/workspace.css)。
字段标签与说明明确关联控件；宽屏可采用左右布局，窄屏标签移到输入上方。输入和选择使用同一套边界与表面；数字保留 `input[type=number]`，开关支持键盘空格，密码可切换可见性。
未知数字留空，必填、禁用和错误有明确说明，不能用零代替未知。

表单主要操作保持足够触控空间，主按钮以实色强调，放弃用轻边界；页签使用下划线，与大块卡片容器区分。
输入区接近底色，聚焦微调边界，避免厚光圈与额外投影。
保存反馈放在稳定操作区，以图标和文字区分读取/保存中、未保存、已同步与失败；错误靠近问题字段，成功不挤占正文。

详情页签共用同一份输入，保存中锁定字段，所有离开入口保护未保存修改。
响应只更新发起它的页面和编辑器；切换详情后迟到响应不能覆盖新选择。
窄屏整页详情隔离背景、约束焦点，关闭时恢复列表位置与原焦点；桌面根据实际可用工作区宽度显示侧详情。

## 图标、图片与动效

图标使用本地 Lucide SVG sprite，由 [Icon.tsx](../../apps/chefmate-playground/frontend/src/components/Icon.tsx) 管理。
新增图标同步白名单、sprite 与许可证来源；品牌和浏览器图标使用同一矢量来源，通过主题角色着色。
图标按钮有可访问名称，装饰图标对辅助技术隐藏；不能只靠图标或颜色表示失败和等待。

消息图片用正方形裁切缩略图，完整内容在页内模态预览；支持 Escape 和关闭后焦点恢复。
公开执行活动位于助手回复处，使用实际状态、加载图标和逐字显示；辅助技术读取一次完整状态，逐字动画不重复播报。工具参数、结果、用量和执行详情仅在开发者模式渲染。[活动文字](../../apps/chefmate-playground/frontend/src/components/ActivityText.tsx)
所有动画尊重减少动态效果：直接显示全文，停止旋转或切换到即时导航，快速开关取消过期动画。

## 对话、建议和提问

新会话在首次发送前保留浏览器草稿，选图片只生成本地压缩预览，发送时才创建会话和上传附件。
开场从能力模板中展示有限选择，填空与模板按钮只编辑输入文字；图片模板按模型实际图片能力显示。
推荐回复默认三条，可按设置调整数量；它们以轻量标题放在输入区上方，点击只替换草稿。
确认条件、选菜和回答问题的按钮有明确提交语义，保存成功后才自动续轮；关闭推荐隐藏当前与历史建议，不删除服务端记录。

每条回复按 `inline_limit` 默认展开一项或最多两项内容，优先当前可编辑视图；其余保留摘要、展开与适用的全屏入口。手动展开保留用户的查看意图。相同组件在多条消息出现时，只有最新所属消息能操作当前任务，历史展示使用当时快照并保持只读。[消息组件](../../apps/chefmate-playground/frontend/src/features/workspace/index.tsx)

待回答问题暂时替换输入区，选项、自填和确认按可用高度组织，跨页选择保留。
确认操作位于选项滚动区外，长问题和选项保留全文；短屏分页显示选项，自填与多选草稿跨页保留，缩减辅助留白，自填输入保持 16px。
会话提示点配“等你回答”文字，刷新或切换后恢复同一问题；全屏场景共用这套状态。[问答协议](agent-ui.md#待回答问题)

个人菜谱在对话内使用只读审核卡，食材、做法、来源依据按图标页签查看；原始数量和单位照实显示，缺失的份数、耗时与难度显示未知。
卡片按菜谱 ID 和版本去重，仅由最新所属消息展示，并先读取服务端当前记录。“告诉助手怎么改”只填写带草稿 ID 的输入文字；明确点击保存才发布，成功后移除草稿操作。
待答问题优先于直接保存，工作区或会话仍在同步时禁用保存；冲突和读取失败保留当前内容，提供重新读取入口，迟到响应不能更新其他会话。[审核卡](../../apps/chefmate-playground/frontend/src/components/RecipeReview.tsx)

## 备料与烹饪工作区

备料使用完整清单，按具体菜品保留食材身份、用量和确认状态。
待准备项目靠前，已备齐或明确跳过的项目划线沉底；取消确认回到待备区，不能把另一道菜同名食材推断为已确认。
备料不提供逐项大字模式和重复侧边总览；完整列表保持可滚动和可操作，全部备齐可保存后退出。

烹饪提供逐步阅读与完整步骤列表；“当前执行步骤”“正在浏览步骤”“完成状态”用文字区分。
逐步内容和完整列表在所属区域内滚动，不能把“所有全屏内容禁止滚动”作为全局规则。
全屏长说明按可用空间分页，保留全部字符与滚动兜底，调整窗口时保留阅读位置。说明翻页与步骤浏览分别提供导航，主要执行动作位置稳定；浏览步骤不完成步骤，明确确认后才跟随下一步。[阅读分页](../../apps/chefmate-playground/frontend/src/features/workspace/ReadingPages.tsx)
大纲用于浏览，步骤完成仍有独立动作；进入烹饪后收起过时条件、选菜与备料入口。

专注模式使用常驻的普通 `section` 场景。进入时主聊天和已打开的右栏一同平移离场，专注场景从另一侧进入；关闭时反向切回，右栏收起也保留节点完成过渡。
非活动场景通过 `inert` 与 `aria-hidden` 隔离交互；厨房内容和助手交流可切换，底部输入共享文字、图片草稿和停止状态。
关闭时恢复原焦点；打开或关闭专注模式不等于启动或停止模型轮次。减少动态效果时直接切换。[场景与过渡](../../apps/chefmate-playground/frontend/src/App.tsx) · [框架样式](../../apps/chefmate-playground/frontend/src/styles/shell.css)
烹饪文本使用当前阅读字体，控件、步骤编号与数值使用各自角色。手机输入保持完整宽度，横屏缩减辅助留白并保留可滚动内容与执行动作。[专注组件](../../apps/chefmate-playground/frontend/src/features/workspace/index.tsx) · [厨房作用域样式](../../apps/chefmate-playground/frontend/src/features/workspace/workspace.css)

完成本次菜肴先检查并保存全餐状态，再退出执行场景和续聊。
饮食反馈必须区分制作完成、单人自动登记、多人待补本人份额与同步待重试，不能在失败或等待时统一显示“已经记入摄入”。[个人登记规则](agent-ui.md#制作量与个人摄入)
若完成动作已经保存而记录同步失败，保留原动作标识并提供重试；厨房快照里的成功动作记录不能单独证明后续个人登记已完成。

## 计时与提醒

聊天和全屏右上角提供计时入口，浮层管理暂停、继续、结束与分钟/秒输入；步骤保留启动或查看入口。
新计时保存成功后自动展示，多计时保留可滚动列表。每步优先展示一个适用计时器并提供打开浮层的入口，当前策略和剩余名额来自服务端。
默认上限三个运行/暂停计时，可选单计时及真实步骤改变时清理；降低上限保留已有计时，默认不自动清理。

倒数由单一状态服务按服务端 UTC 截止时间与单调经过时间校准，暂停保留剩余量，刷新后恢复。聊天与专注模式共用时钟和提醒状态，组件重挂载不新增独立倒数或重复响铃。
到期可打开提醒，但保留输入焦点、草稿和对话状态；Escape 优先关浮层，不能意外退出烹饪。
后台会话可提醒，查看提醒才切换会话，不自动替换正在做的菜。响铃不代替步骤完成；声音遵守设置和浏览器交互解锁要求，同次检查中多个计时到期共用一次提示声，页面关闭期间不承诺闹钟通知。[计时服务](../../apps/chefmate-playground/frontend/src/features/workspace/store.ts) · [计时浮层](../../apps/chefmate-playground/frontend/src/features/workspace/Timers.tsx)

## 开发者观察与可访问性验证

普通模式不显示上下文彩条、调试页签、执行详情和开发水印，错误与停止、重试入口仍保留。
开发者模式的上下文彩条按最近请求的文本分类估算展示，不能解读成模型窗口占比；顶部小水印显示开发模式、版本和当前时间。
诊断区分连接、工具、组件、执行记录与请求轨迹：工具按业务分组，可按中文名称和功能搜索、按可用状态筛选；组件分别展示启用状态与依赖可用性。
会话轮次与模型请求分别选择，时间线或树视图区分系统提示词、用户、助手、工具调用与结果；参数和结果先显示概要，再按需展开完整内容。
只展示实际捕获的脱敏请求，图片保留元数据，隐藏推理正文不显示；报告用量、部分已知值和请求大小粗估分别标明。没有记录的旧轮次明确缺失，不能用当前配置拼出一份“历史请求”。[诊断组件](../../apps/chefmate-playground/frontend/src/components/developer/Diagnostics.tsx) · [上下文彩条](../../apps/chefmate-playground/frontend/src/components/ContextMeter.tsx) · [开发水印](../../apps/chefmate-playground/frontend/src/components/DeveloperWatermark.tsx)

验证覆盖键盘、焦点恢复、窄屏与横屏、草稿和导航、减少动态效果，以及当前预设的文字/主操作对比。
确定性检查见[会话生命周期测试](../../apps/chefmate-playground/frontend/src/runtime/store.test.ts)、[输入组件测试](../../apps/chefmate-playground/frontend/src/components/Composer.test.tsx)、[工作区状态测试](../../apps/chefmate-playground/frontend/src/features/workspace/store.test.ts)和[厨房组件测试](../../apps/chefmate-playground/frontend/src/features/workspace/views.test.tsx)。在 `frontend/` 运行 `npm test` 和 `npm run build`；真实浏览器范围记录在[工程档案](../engineering/README.md)。
