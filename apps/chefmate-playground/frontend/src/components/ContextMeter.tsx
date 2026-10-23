import type { CSSProperties } from 'react';
import { Icon } from './Icon';

type Category = { key: string; label?: string; estimated_tokens?: number };
type Measurement = { estimated_tokens?: number; categories?: Category[] };
const colors: Record<string, string> = { system: '#7189c4', conversation: '#56a6a0', tools: '#c59450', tool_schemas: '#a389c4', ui_state: '#5998b9', memory: '#8baf62', guidance: '#ca858d' };
const count = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const number = (value: unknown) => count(value)?.toLocaleString('zh-CN') ?? '未估算';

export function ContextMeter({ context, onInspect }: { context: Measurement; onInspect: () => void }) {
  const categories = (context.categories || []).filter(row => row && typeof row.key === 'string');
  const sum = categories.reduce((total, row) => total + (count(row.estimated_tokens) || 0), 0);
  return <details className="context-meter"><summary><span className="context-meter-heading"><span><Icon name="layers" />本次上下文</span><strong>≈ {number(context.estimated_tokens)} <small>tokens</small><Icon name="chevron-down" /></strong></span>
    <span className="context-meter-bar" role="img" aria-label={sum ? `上下文分类占比，合计约 ${sum.toLocaleString('zh-CN')} Token` : '暂无可展示的分类估算'}>{categories.filter(row => (count(row.estimated_tokens) || 0) > 0).map(row => <span key={row.key} title={`${row.label || row.key}：约 ${number(row.estimated_tokens)} Token`} style={{ flexGrow: row.estimated_tokens, '--context-color': colors[row.key] || '#939b9d' } as CSSProperties} />)}</span></summary>
    <div className="context-meter-body"><dl>{categories.map(row => <div key={row.key}><dt><i style={{ '--context-color': colors[row.key] || '#939b9d' } as CSSProperties} />{row.label || row.key}</dt><dd>{number(row.estimated_tokens)}</dd></div>)}</dl>
      <p className="context-meter-caption">文字分类估算 · 不含图片及服务端封装</p><button type="button" className="context-meter-inspect" onClick={onInspect}>查看请求轨迹<Icon name="arrow-up-right" /></button></div></details>;
}
