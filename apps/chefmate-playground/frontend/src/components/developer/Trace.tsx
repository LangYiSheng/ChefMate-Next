import { useState } from 'react';
import { Icon } from '../Icon';
import { Select } from '../ui/Select';
import { Badge, Content, Disclosure, Empty, ToolTimeline, UsageSummary } from './Primitives';
import { ToolsCatalog } from './Catalog';
import { type Data, formatDate, stageLabels, statusLabels, toolInfo } from './model';

const roles: Record<string, { label: string; icon: string }> = { system: { label: '系统提示词', icon: 'sliders-horizontal' }, user: { label: '用户输入', icon: 'message-circle' }, assistant: { label: '助手消息', icon: 'chef-hat' }, tool: { label: '工具结果', icon: 'plug' } };
function MessageNode({ message, index }: { message: Data; index: number }) {
  const role = roles[message.role] || { label: '其他消息', icon: 'info' };
  return <li className={`dev-message-node dev-role-${message.role}`}><span className="dev-event-dot"><Icon name={role.icon} /></span><article><header className="dev-event-heading"><strong>{role.label}</strong><span className="dev-muted">{index + 1}</span>{message.name && <Badge>{toolInfo(message.name).label}</Badge>}</header>
    <Content value={message.role === 'user' ? message.user_text ?? message.content : message.result ?? message.content} label="展开完整消息" />
    {message.tool_calls?.length > 0 && <div className="dev-request-calls"><h5>请求调用</h5>{message.tool_calls.map((call: Data, callIndex: number) => <article key={call.id || callIndex}><header><Icon name={toolInfo(call.name).icon} /><strong>{toolInfo(call.name).label}</strong><code>{call.name}</code></header><Content value={call.arguments ?? call.args} label="展开调用参数" />{call.id && <small className="dev-muted">调用 ID · {call.id}</small>}</article>)}</div>}
    {message.tool_call_id && <small className="dev-muted">对应调用 · <code>{message.tool_call_id}</code></small>}
    {message.ui_context && <Disclosure title="附带的服务器界面状态"><Content value={message.ui_context} label="展开界面状态" /></Disclosure>}
    {Array.isArray(message.images) && message.images.length > 0 && <p className="dev-muted"><Icon name="images" />附带 {message.images.length} 张图片，仅保留元数据</p>}
  </article></li>;
}
function RequestMessages({ request, tree }: { request: Data; tree: boolean }) {
  const messages: Data[] = Array.isArray(request.messages) ? request.messages : [];
  return <div className={tree ? 'dev-request-tree' : 'dev-request-timeline'}>
    <section className="dev-system-node"><header><Icon name="sliders-horizontal" /><h4>系统提示词</h4><Badge>实际请求</Badge></header><Content value={request.system} label="展开完整系统提示词" /></section>
    <section className="dev-request-messages"><h4><Icon name="message-circle" />请求消息 <span>{messages.length}</span></h4>{messages.length ? <ol className="dev-timeline" aria-label="模型输入消息">{messages.map((message, index) => <MessageNode message={message} index={index} key={`${message.tool_call_id || message.role}:${index}`} />)}</ol> : <Empty>这次请求没有留存消息内容。</Empty>}</section>
    <section className="dev-request-definitions"><Disclosure title={<span><Icon name="plug" />提供给模型的工具定义 · {request.tools?.length || 0} 项</span>}><ToolsCatalog tools={Array.isArray(request.tools) ? request.tools : []} /></Disclosure></section>
    {Array.isArray(request.skills) && request.skills.length > 0 && <Disclosure title={`可按需读取的组件指南 · ${request.skills.length} 项`}><ul className="dev-skill-list">{request.skills.map((skill: Data, index: number) => <li key={skill.id || skill.name || index}><Icon name="book-open" /><div><strong>{skill.name || skill.id || '组件指南'}</strong>{skill.description && <p>{skill.description}</p>}</div></li>)}</ul></Disclosure>}
  </div>;
}

export function TraceExplorer({ capture }: { capture: Data | null }) {
  const [selected, setSelected] = useState(''), [view, setView] = useState<'timeline' | 'tree'>('timeline');
  const requests: Data[] = Array.isArray(capture?.debug_trace) ? capture.debug_trace : [], options = requests.map((request, index) => ({ value: String(index), label: `第 ${request.round || index + 1} 次请求 · ${stageLabels[request.stage] || request.stage || '阶段未报告'} · ${request.messages?.length || 0} 条消息` }));
  const selectedIndex = Math.max(0, Math.min(Number(selected) || 0, requests.length - 1)), request = requests[selectedIndex];
  if (!capture || !requests.length) return <Empty>这轮没有留存完整模型请求。可在执行记录查看公开工具日志；系统提示词和输入内容不会补造。</Empty>;
  const usageCall = capture.usage?.calls?.find((call: Data) => call.round === request.round);
  const estimate = capture.context_usage?.round === request.round ? capture.context_usage : null;
  return <section className="dev-trace-explorer"><div className="dev-request-toolbar"><label className="dev-field"><span>模型请求</span><Select aria-label="选择模型请求" value={String(selectedIndex)} onValueChange={setSelected} options={options} /></label><div className="dev-view-toggle" aria-label="轨迹显示方式"><button type="button" aria-pressed={view === 'timeline'} onClick={() => setView('timeline')}><Icon name="list" />时间线</button><button type="button" aria-pressed={view === 'tree'} onClick={() => setView('tree')}><Icon name="layers" />树视图</button></div></div>
    <div className="dev-request-root"><Icon name="code-xml" /><div><h3>模型请求 #{request.round || selectedIndex + 1}</h3><p>{usageCall?.model || '模型名称未报告'} · {request.messages?.length || 0} 条消息 · {request.tools?.length || 0} 个工具{typeof request.native_images_count === 'number' ? ` · ${request.native_images_count} 张原生图片` : ''}</p></div><Badge tone="success">真实留存</Badge></div>
    <RequestMessages request={request} tree={view === 'tree'} />
    {request.parameter_integrity && <Disclosure title={`工具参数核验 · ${statusLabels[request.parameter_integrity.status] || request.parameter_integrity.status || '状态未报告'}`}><Content value={request.parameter_integrity} label="展开核验记录" /></Disclosure>}
    <UsageSummary usage={usageCall} contextUsage={estimate} heading="这次请求用量" />
    {capture.context_usage && !estimate && <p className="dev-muted">只留存了第 {capture.context_usage.round || '末'} 次请求的大小估算，当前请求不补算。</p>}
  </section>;
}

export function ExecutionLog({ capture, logs = [] }: { capture: Data | null; logs?: Data[] }) {
  const [legacyId, setLegacyId] = useState('');
  const legacyIndex = Math.max(0, Math.min(Number(legacyId) || 0, logs.length - 1)), legacy = logs[legacyIndex];
  const currentLog = capture ? logs.find(log => log.id === capture.turn_id) : legacy;
  const calls = capture?.tool_calls || currentLog?.tool_calls || [];
  const state = capture?.status;
  return <section className="dev-execution-log">
    {capture ? <div className="dev-run-heading"><h3>这轮做了什么</h3><Badge tone={state === 'completed' ? 'success' : state === 'running' ? 'running' : 'warning'}>{statusLabels[state] || state || '状态未报告'}</Badge><p>{formatDate(capture.created_at)}</p></div> : <><p className="dev-notice">仅有历史公开记录；完整模型输入未留存。</p>{logs.length > 0 && <label className="dev-field"><span>历史回复</span><Select aria-label="选择历史执行记录" value={String(legacyIndex)} onValueChange={setLegacyId} options={logs.map((log, index) => ({ value: String(index), label: `${formatDate(log.created_at) || `记录 ${index + 1}`} · ${(log.text || '工具执行').slice(0, 42)}` }))} /></label>}</>}
    {capture?.message && <section className="dev-user-request"><h4><Icon name="message-circle" />这轮用户输入</h4><Content value={capture.message} label="展开完整输入" /></section>}
    <ToolTimeline calls={calls} />
    {currentLog?.text && <section className="dev-final-response"><h4><Icon name="chef-hat" />助手回复</h4><Content value={currentLog.text} label="展开完整回复" /></section>}
    {capture && capture.skill_events?.length > 0 && <Disclosure title={`组件指南加载 · ${capture.skill_events.length} 次`}><ol className="dev-skill-list">{capture.skill_events.map((event: Data, index: number) => <li key={event.call_id || index}><Icon name="book-open" /><span>{event.name || event.id || '组件指南'}<small>{statusLabels[event.status] || event.status || '状态未提供'}{typeof event.chars === 'number' ? ` · ${event.chars} 字符` : ''}</small></span></li>)}</ol></Disclosure>}
    <UsageSummary usage={capture?.usage} contextUsage={capture?.context_usage} />
  </section>;
}
