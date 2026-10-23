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
    <p className="ui-hint">{data.servings == null ? '人数未知' : `${data.servings} 人份`} · {data.total_time_min == null ? '耗时未知' : `${data.total_time_min} 分钟`} · {({beginner: '新手', normal: '普通', advanced: '进阶'} as Record<string, string>)[data.difficulty] || '难度未知'}{data.estimated_servings && <span className="estimate-marker"> · 份量估算</span>}</p>
    <h3>食材</h3><ul className="ingredient-list">{data.ingredients?.map((item: Data, i: number) => <li key={item.ingredient_id || i}><span>{item.canonical_name || item.raw_name || item.name || item.ingredient_id}</span><span>{item.amount_text || [item.raw_amount, item.raw_unit].filter(Boolean).join(' ') || (item.grams != null ? `${item.grams} g` : '用量未知')}{(item.amount_evidence === 'estimated' || item.estimated_grams) && <small className="estimate-marker"> · 估算</small>}</span></li>)}</ul>
    <h3>做法</h3><ol>{data.steps?.map((step: string | Data, i: number) => <li key={i}><Markdown text={typeof step === 'string' ? step : step.instruction || step.description || step.text || ''} /></li>)}</ol>
    <h3>营养</h3><p className="ui-hint">{(data.nutrition?.estimated || data.nutrition_status === 'estimated') && <span className="estimate-marker">营养含估算，</span>}请结合实际份量核对；未知营养保持未知。</p>
    <dl className="chat-food-metrics">{[['calories_kcal', '热量', 'kcal'], ['protein_g', '蛋白质', 'g'], ['fat_g', '脂肪', 'g'], ['carbohydrate_g', '碳水', 'g']].map(([key, label, unit]) => <div key={key}><dt>{label}</dt><dd>{nutrition[key] ?? '—'} <small>{nutrition[key] == null ? '' : unit}</small></dd></div>)}</dl>
    {data.source && <details><summary>来源</summary><pre>{typeof data.source === 'string' ? data.source : JSON.stringify(data.source, null, 2)}</pre></details>}
    <button type="button" className="primary-button" onClick={() => onAsk(`我想做「${data.name}」，请读取菜谱 ${selection.id} 并带我选菜、核对份量后开始准备。`)}>聊聊这道菜</button>
  </article>;
}

export { Diagnostics } from './developer/Diagnostics';
