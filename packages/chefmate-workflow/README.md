# ChefMate Workflow

把一顿饭保存为可继续执行的厨房任务：选择真实菜谱、冻结配方、安排份量、确认备料、推进步骤并管理计时。

[项目首页](../../README.md) · [从一顿饭开始](../../docs/guide/overview.md) · [完整 API](../../docs/reference/workflow.md) · [设计规范](../../docs/design/agent-ui.md)

## 主要能力

- 有类型的工具与用户动作，统一的阶段/副作用合同。
- 原子修改、修订号、幂等操作和业务/展示版本分离。
- 可信菜谱引用、冻结来源、可缩放用量与未知值保留。
- 最多四道共享餐单、备菜门槛、逐步确认与服务端截止时间。
- 完整界面投影与提供给模型的短快照。

## 快速开始

由宿主提供读取与原子修改回调：

```python
from chefmate_workflow import WorkflowService

service = WorkflowService(
    retrieval_service,
    get_state=lambda: repository.read_workspace(session_id),
    mutate=lambda callback: repository.mutate_workspace(session_id, callback),
)
snapshot = service.model_snapshot()
workspace = service.get_workspace()
```

示例 repository 需要由应用实现；`mutate` 必须原子执行并保存 callback 对工作区字典的修改。模型工具使用 `execute_tool`，界面使用 `apply_action`，均检查引用、版本与阶段。详细合同见[接口参考](../../docs/reference/workflow.md)。

## 代码入口

| 模块 | 职责 |
| --- | --- |
| [service.py](src/chefmate_workflow/service.py) | 事务、修订与幂等入口 |
| [models.py](src/chefmate_workflow/models.py)、[contracts.py](src/chefmate_workflow/contracts.py) | 类型与工具合同 |
| [projection.py](src/chefmate_workflow/projection.py) | 份量、营养和界面/模型投影 |
| [kitchen.py](src/chefmate_workflow/kitchen.py) | 餐单、阶段、步骤与计时 |
| [tool_commands.py](src/chefmate_workflow/tool_commands.py)、[user_actions.py](src/chefmate_workflow/user_actions.py) | 模型与用户命令分发 |

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages/chefmate-workflow/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/workflow-tests
```

本包不启动模型、数据库或浏览器提醒；这些生命周期由宿主负责。[决策与取舍](../../docs/design/decisions.md)解释为什么这样分工。
