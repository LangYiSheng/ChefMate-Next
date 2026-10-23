import type { Message } from '../../runtime/types';
import { Icon } from '../Icon';
import { Badge, ToolTimeline, UsageSummary } from './Primitives';
import './developer.css';

/** Public tool events only. Exact model input lives behind the developer debug API. */
export function ExecutionTrace({ message, developer }: { message: Message; developer: boolean }) {
  if (!developer) return null;
  const calls = message.tool_calls || [], running = Boolean(message.streaming), failed = Boolean(message.error), cancelled = Boolean(message.cancelled);
  return <details className="dev-inline-trace" open={running ? true : undefined}><summary><Icon name={running ? 'loader-circle' : failed ? 'circle-alert' : cancelled ? 'circle-stop' : 'layers'} /><strong>执行过程</strong><span>{calls.length} 次工具调用</span><Badge tone={running ? 'running' : failed ? 'warning' : cancelled ? 'muted' : 'success'}>{running ? '执行中' : failed ? '失败' : cancelled ? '已停止' : '已完成'}</Badge><Icon name="chevron-down" /></summary>
    <div className="dev-inline-trace-body"><ToolTimeline calls={calls} /><UsageSummary usage={message.usage} contextUsage={message.context_usage} /><p className="dev-muted">这里显示公开工具记录；系统提示词与实际请求输入可在右侧「调试 → 请求轨迹」查看。</p></div>
  </details>;
}
