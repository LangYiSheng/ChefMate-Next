import { useState, type ReactNode } from 'react';
import { Icon } from '../Icon';
import { type Data, displayValue, numberText, serialized, summary, toolInfo, callStatus } from './model';

export function Badge({ children, tone = 'muted' }: { children: ReactNode; tone?: string }) { return <span className={`dev-badge dev-tone-${tone}`}>{children}</span>; }
export function Empty({ children }: { children: ReactNode }) { return <div className="dev-empty"><Icon name="info" /><p>{children}</p></div>; }
export function Disclosure({ title, children, className = '' }: { title: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  return <details className={`dev-disclosure ${className}`} onToggle={event => setOpen(event.currentTarget.open)}><summary>{title}<Icon name="chevron-down" /></summary>{open && children}</details>;
}
export function Content({ value, label = '完整内容' }: { value: any; label?: string }) {
  const clean = displayValue(value);
  if (clean == null) return <p className="dev-muted">未留存</p>;
  const text = serialized(clean), short = text.length <= 260 && typeof clean === 'string';
  return <div className="dev-content">{short ? <pre>{text}</pre> : <><p className="dev-content-summary">{summary(clean)}</p><Disclosure title={label}><pre>{text}</pre></Disclosure></>}</div>;
}
export function ToolCallCard({ call, index }: { call: Data; index: number }) {
  const info = toolInfo(call.name || call.tool_name), state = callStatus(call);
  return <li className={`dev-tool-event dev-tone-${state.tone}`}><span className="dev-event-dot"><Icon name={info.icon} /></span><article>
    <header className="dev-event-heading"><span className="dev-step-number">{String(index + 1).padStart(2, '0')}</span><strong>{info.label}</strong><Badge tone={state.tone}>{state.text}</Badge></header>
    <code className="dev-code-name">{call.name || call.tool_name || '未命名工具'}</code>
    <div className="dev-call-io"><section><h5><Icon name="arrow-up-right" />输入参数</h5><Content value={call.arguments ?? call.args} label="展开完整参数" /></section><section><h5><Icon name="corner-down-left" />执行结果</h5><Content value={call.result} label="展开完整结果" /></section></div>
  </article></li>;
}
export function ToolTimeline({ calls }: { calls: Data[] }) { return calls.length ? <ol className="dev-timeline" aria-label="工具执行时间线">{calls.map((call, index) => <ToolCallCard call={call} index={index} key={call.call_id || call.id || index} />)}</ol> : <Empty>这轮没有留存工具调用。</Empty>; }

export function UsageSummary({ usage, contextUsage, heading = '本轮用量' }: { usage?: Data | null; contextUsage?: Data | null; heading?: string }) {
  if (!usage && !contextUsage) return <p className="dev-muted">用量未报告。</p>;
  const metrics = [['input_tokens', '输入'], ['output_tokens', '输出'], ['cached_tokens', '缓存读取'], ['total_tokens', '合计']];
  return <section className="dev-usage" aria-label={heading}><header><h4>{heading}</h4>{usage && <Badge>报告用量</Badge>}</header>
    {usage && <><dl className="dev-usage-grid">{metrics.map(([key, label]) => { const coverage = usage.coverage?.[key], known = usage.known_totals?.[key]; return <div key={key}><dt>{label} tokens</dt><dd>{numberText(usage[key])}</dd>{usage[key] == null && typeof known === 'number' && <small>已知 {numberText(known)}，非完整统计</small>}{coverage && <small>{coverage.reported ?? 0}/{coverage.calls ?? usage.model_calls ?? '—'} 次报告{coverage.derived ? ` · ${coverage.derived} 次按输入与输出相加` : ''}</small>}</div>; })}</dl>
      <p className="dev-muted">{typeof usage.model_calls === 'number' ? `${usage.model_calls} 次模型请求` : '模型请求次数未报告'}{usage.total_tokens_source === 'input_output_sum' ? ' · 合计按输入与输出相加' : ''}{typeof usage.reasoning_tokens === 'number' ? ` · 推理输出用量 ${numberText(usage.reasoning_tokens)} tokens` : ''}</p></>}
    {contextUsage && <div className="dev-estimate"><header><h5>请求大小粗估</h5><Badge tone="warning">估算值</Badge></header><p>≈ {numberText(contextUsage.estimated_tokens)} tokens{typeof contextUsage.round === 'number' ? ` · 第 ${contextUsage.round} 次请求` : ''}</p><p className="dev-muted">不含图片和供应商封装，不能用作模型窗口上限或实际账单。</p>{Array.isArray(contextUsage.categories) && <dl className="dev-estimate-categories">{contextUsage.categories.map((category: Data) => <div key={category.key}><dt>{category.label || category.key}</dt><dd>≈ {numberText(category.estimated_tokens)}</dd></div>)}</dl>}</div>}
  </section>;
}
