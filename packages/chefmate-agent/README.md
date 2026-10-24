# ChefMate Agent

让自然语言需求接上真实查询和可验证操作的智能体核心。LangChain 标准 `create_agent` 承担模型与工具循环，ChefMate 负责预算、证据、权限、历史与事件边界。

同一官方数据快照内保留多次搜索、分页的真实引用证据；官方数据更新后清除旧引用与本轮查询去重记录。个人菜谱按自身版本独立验证，不替换官方数据快照。

[项目首页](../../README.md) · [技术导览](../../docs/guide/agent.md) · [完整 API](../../docs/reference/agent.md) · [设计规范](../../docs/design/agent-ui.md)

## 主要能力

- 四个只读检索工具，以及应用显式注入的业务工具。
- 按最新状态与组件权限筛选工具，并在执行时再次验证。
- 完整历史、整轮裁剪、差异快照、按需指南和有界记忆。
- 原生调用完整性、来源校验、超时、取消、用量与脱敏观测。

## 快速开始

应用同步会安装本包。独立使用时由调用方注入检索和配置：

```python
from chefmate_agent import AgentConfig, ChefMateAgent

agent = ChefMateAgent(
    retrieval_service,
    AgentConfig(model=model_name, api_key=api_key, base_url=base_url),
)
result = await agent.run_turn([{"role": "user", "content": "我想吃虾，半小时以内"}])
```

示例变量由宿主提供。扩展通过 `InjectedToolSpec`、`ToolExecutionContext` 和外部 UI 模型注入；数据库、HTTP 与厨房业务属于 App/工作流。见[完整接口和生命周期](../../docs/reference/agent.md)。

## 代码入口

| 模块 | 职责 |
| --- | --- |
| [core.py](src/chefmate_agent/core.py) | 智能体装配与一轮完整结果 |
| [runner.py](src/chefmate_agent/runner.py) | 标准框架与 middleware |
| [history.py](src/chefmate_agent/history.py) | 历史验证、裁剪、状态快照 |
| [tool_execution.py](src/chefmate_agent/tool_execution.py) | 工具参数、权限、阶段与结果 |
| [evidence.py](src/chefmate_agent/evidence.py) | 真实引用与来源 |
| [provider.py](src/chefmate_agent/provider.py)、[boundary.py](src/chefmate_agent/boundary.py) | 模型传输、JSON 与错误边界 |

## 验证

```powershell
apps/chefmate-playground/.venv/Scripts/python.exe -m pytest packages/chefmate-agent/tests -q -p no:cacheprovider --basetemp=apps/chefmate-playground/.runtime/agent-tests
```

离线测试使用明确的模型替身，真实模型验收另记于[工程档案](../../docs/engineering/README.md)。机制与案例见[智能体技术导览](../../docs/guide/agent.md)。
