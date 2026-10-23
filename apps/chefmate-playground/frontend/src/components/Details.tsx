import { useEffect, useState } from 'react';
import { api, safeText } from '../runtime/http';
import { Markdown } from './Markdown';
import { Icon } from './Icon';
type Data = Record<string, any>;

export function RecipeDetails({ selection, onAsk }: { selection: { id: string; snapshot?: Data } | null; onAsk: (text: string) => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!selection) return;
    const abort = new AbortController();
    setData(null); setError('');
    const request = selection.snapshot ? api('/api/tools/get_recipe', { method: 'POST', signal: abort.signal, body: JSON.stringify({ recipe_id: selection.id, expected_snapshot: selection.snapshot }) }) :
      api(`/api/recipes/${encodeURIComponent(selection.id)}`, { signal: abort.signal });
    request.then(payload => {
      if (abort.signal.aborted) return;
      if (payload.status && payload.status !== 'ok') throw new Error(payload.status === 'snapshot_changed' ? '菜谱数据已更新，请重新查询。' : '这道菜谱暂时不可用。');
      setData(payload.recipe || payload);
    }).catch(e => { if (!abort.signal.aborted) setError(safeText(e.message)); });
    return () => abort.abort();
  }, [selection, reload]);
  if (!selection) return <div className="panel-empty"><Icon name="book-open" /><p>点击回复中的菜谱，查看食材用量、完整步骤和营养信息。</p></div>;
  if (error) return <div className="error-box" role="alert">{error}<button type="button" className="text-button" onClick={() => setReload(reload + 1)}>重试读取</button></div>;
  if (!data) return <p className="panel-loading">正在读取菜谱…</p>;
  const nutrition = data.nutrition?.per_serving || data.nutrition?.total || data.nutrition || {};
  return <article className="ui-component recipe-detail"><h2>{data.name}</h2><Markdown text={data.description || ''} />
    <p className="ui-hint">{data.servings == null ? '人数未知' : `${data.servings} 人份`} · {data.total_time_min == null ? '耗时未知' : `${data.total_time_min} 分钟`}{data.estimated_servings && <span className="estimate-marker"> · 份量估算</span>}</p>
    <h3>食材</h3><ul className="ingredient-list">{data.ingredients?.map((item: Data, i: number) => <li key={item.ingredient_id || i}><span>{item.canonical_name || item.raw_name || item.name || item.ingredient_id}</span><span>{item.amount_text || [item.raw_amount, item.raw_unit].filter(Boolean).join(' ') || (item.grams != null ? `${item.grams} g` : '用量未知')}{(item.amount_evidence === 'estimated' || item.estimated_grams) && <small className="estimate-marker"> · 估算</small>}</span></li>)}</ul>
    <h3>做法</h3><ol>{data.steps?.map((step: string | Data, i: number) => <li key={i}><Markdown text={typeof step === 'string' ? step : step.instruction || step.description || step.text || ''} /></li>)}</ol>
    <h3>营养</h3><p className="ui-hint">{(data.nutrition?.estimated || data.nutrition_status === 'estimated') && <span className="estimate-marker">营养含估算，</span>}请结合实际份量核对；未知营养保持未知。</p>
    <dl className="chat-food-metrics">{[['calories_kcal', '热量', 'kcal'], ['protein_g', '蛋白质', 'g'], ['fat_g', '脂肪', 'g'], ['carbohydrate_g', '碳水', 'g']].map(([key, label, unit]) => <div key={key}><dt>{label}</dt><dd>{nutrition[key] ?? '—'} <small>{nutrition[key] == null ? '' : unit}</small></dd></div>)}</dl>
    {data.source && <details><summary>来源</summary><pre>{typeof data.source === 'string' ? data.source : JSON.stringify(data.source, null, 2)}</pre></details>}
    <button type="button" className="primary-button" onClick={() => onAsk(`我想做「${data.name}」，请读取菜谱 ${selection.id} 并带我选菜、核对份量后开始准备。`)}>聊聊这道菜</button>
  </article>;
}

function Value({ value }: { value: any }) {
  if (value == null) return <span className="dev-null">未提供</span>;
  if (typeof value !== 'object') return <span>{typeof value === 'boolean' ? value ? '是' : '否' : String(value)}</span>;
  return <dl className="dev-fields">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{item && typeof item === 'object' ? <details><summary>{Array.isArray(item) ? `${item.length} 项` : '查看内容'}</summary><Value value={item} /></details> : <Value value={item} />}</dd></div>)}</dl>;
}

export function Diagnostics({ sessionId, status, developer }: { sessionId: string | null; status: Data | null; developer: boolean }) {
  const [mode, setMode] = useState('connection');
  const [data, setData] = useState<Data | null>(null);
  const [turn, setTurn] = useState('');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!developer || mode === 'connection') return;
    const controller = new AbortController();
    setError(''); setData(null);
    const path = sessionId ? `/api/sessions/${encodeURIComponent(sessionId)}/debug${turn ? `?turn_id=${encodeURIComponent(turn)}` : ''}` : '/api/debug/catalog';
    api(path, { signal: controller.signal, headers: { 'X-ChefMate-Developer': '1' } }).then(payload => { if (!controller.signal.aborted) setData(payload); }).catch(e => { if (!controller.signal.aborted) setError(safeText(e.message)); });
    return () => controller.abort();
  }, [sessionId, developer, mode, turn, reload]);
  useEffect(() => setTurn(''), [sessionId]);
  const capture = data?.capture;
  return <div className="developer-panel"><div className="dev-tabs" role="tablist" aria-label="调试内容">{[['connection', '连接'], ...(developer ? [['tools', '工具'], ['components', '组件'], ['logs', '执行记录'], ['context', '请求轨迹']] : [])].map(([id, label]) =>
    <button type="button" role="tab" aria-selected={mode === id} key={id} onClick={() => setMode(id)}>{label}</button>)}</div>
    {mode === 'connection' ? <section className="connection-card"><h3>连接状态</h3><dl>{[['服务', status?.ready ? '已连接' : '待连接'], ['模型', status?.model || '尚未配置'], ['菜谱库', status?.database?.ready ? '已连接' : '待连接'], ['菜谱数量', status?.database?.recipe_count ?? '未知'], ['数据版本', status?.database?.data_version || '未知']].map(([label, value]) => <div className="connection-line" key={String(label)}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section> : <>
      <button type="button" className="secondary-button" onClick={() => setReload(reload + 1)}>刷新记录</button>{error && <p role="alert" className="error-box">{error}</p>}
      {!data && !error && <p>正在读取…</p>}
      {data?.runs?.length > 0 && ['logs', 'context'].includes(mode) && <label className="dev-turn-label">查看轮次<select aria-label="查看调试轮次" value={turn || capture?.turn_id || ''} onChange={event => setTurn(event.target.value)}>{data!.runs.map((run: Data) => <option key={run.turn_id} value={run.turn_id}>{run.message?.slice(0, 28) || run.turn_id} · {run.status}</option>)}</select></label>}
      {mode === 'tools' && data?.tools?.map((tool: Data) => <details key={tool.name}><summary>{tool.name} · {tool.available ? '可执行' : '当前不可用'}</summary><p>{tool.description}</p><Value value={{ effect: tool.effect, allowed_stages: tool.allowed_stages, preconditions: tool.preconditions, blockers: tool.blockers, parameters: tool.parameters }} /></details>)}
      {mode === 'components' && data?.components?.map((component: Data) => <details key={component.id}><summary>{component.name || component.id}</summary><Value value={component} /></details>)}
      {mode === 'logs' && <Value value={capture?.tool_calls || data?.logs} />}
      {mode === 'context' && <><p className="dev-note">实际留存的请求轨迹与服务端报告用量。未留存的轮次不补造上下文。</p>{capture?.debug_trace?.map((request: Data, i: number) => <details key={i}><summary>第 {request.round || i + 1} 次请求</summary><Value value={request} /></details>)}<Value value={capture?.usage} /></>}
    </>}
  </div>;
}
