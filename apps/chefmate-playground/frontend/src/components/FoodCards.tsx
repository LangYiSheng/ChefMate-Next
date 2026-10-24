import { useEffect, useId, useState } from 'react';
import { api, safeText } from '../runtime/http';
import { CardToggle } from './CardToggle';

type Data = Record<string, any>;
const tools = new Set(['create_food_log_draft', 'revise_food_log_draft', 'get_food_log', 'confirm_food_log', 'get_cooked_meal_intake', 'record_cooked_meal_intake']);
const macros = [['calories_kcal', '热量', 'kcal'], ['protein_g', '蛋白质', 'g'], ['fat_g', '脂肪', 'g'], ['carbohydrate_g', '碳水', 'g']];
function Metrics({ summary }: { summary?: Data }) {
  return <dl className="chat-food-metrics">{macros.map(([key, title, unit]) => {
    const value = summary?.total?.[key] ?? summary?.known_total?.[key];
    return <div key={key}><dt>{title}</dt><dd>{Number.isFinite(value) ? Math.round(value * 10) / 10 : '—'}<small>{Number.isFinite(value) ? unit : ''}</small></dd>
      {summary?.total?.[key] == null && summary?.known_total?.[key] != null && <small>已知部分</small>}</div>;
  })}</dl>;
}

function FoodCard({ receipt, locked, onAsk }: { receipt: Data; locked: boolean; onAsk: (text: string) => void }) {
  const [record, setRecord] = useState(receipt);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [expanded, setExpanded] = useState(true), bodyId = useId();
  useEffect(() => {
    const abort = new AbortController();
    setRecord(receipt);
    api(`/api/food-logs/${encodeURIComponent(receipt.id)}`, { signal: abort.signal })
      .then(data => { if (!abort.signal.aborted) setRecord(data); })
      .catch(error => { if (!abort.signal.aborted) setNotice(safeText(error.message)); });
    return () => abort.abort();
  }, [receipt]);
  async function publish() {
    if (saving || locked) return;
    setSaving(true); setNotice('');
    try {
      const saved = await api(`/api/food-logs/${encodeURIComponent(record.id)}/publish`, { method: 'POST', body: JSON.stringify({ revision: record.revision }) });
      setRecord(saved); setNotice('这顿饭记好了。');
    } catch (error) {
      setNotice(safeText((error as Error).message));
      // A response can be lost after the transaction committed. Read authority before retrying.
      try { setRecord(await api(`/api/food-logs/${encodeURIComponent(record.id)}`)); } catch { /* Keep the draft available. */ }
    } finally { setSaving(false); }
  }
  const doc = record.document, title = doc?.title || record.title || '这顿饭';
  return <section className="chat-food-block" aria-label="饮食记录"><header className="chat-food-heading"><div className="card-disclosure-title">
    <p className="chat-food-kicker">{record.date}</p><h3>{title}</h3></div>
    <div className="card-disclosure-actions"><span className={`chat-food-state ${record.status === 'saved' ? 'saved' : ''}`}>{record.status === 'saved' ? '已记录' : '待确认'}</span><CardToggle title={`饮食记录：${title}`} expanded={expanded} controls={bodyId} onToggle={() => setExpanded(value => !value)} /></div></header>
    <div id={bodyId} className="card-disclosure-body" hidden={!expanded}>
    <ul className="chat-food-items">{doc?.items?.map((item: Data, index: number) => <li key={item.id || index}><span><strong>{item.name}</strong><small>{item.portion || '份量待补充'}</small></span>
      <span className="chat-food-item-energy">{Number.isFinite(item.nutrition?.calories_kcal) ? `${item.nutrition.calories_kcal} kcal` : '营养待补充'}</span></li>)}</ul>
    <Metrics summary={record.nutrition} /><p className="chat-food-note">{record.nutrition?.estimated && <span className="estimate-marker">营养含估算；</span>}{record.status === 'saved' ? '已计入个人饮食。' : '尚未计入营养统计。可以直接告诉我哪里要改。'}</p>
    {doc && <details className="chat-food-source"><summary>来源与估算依据</summary>{doc.items?.map((item: Data, index: number) => <div key={index}><h4>{item.name}</h4><p>{item.evidence}</p>
      {item.assumptions?.map((text: string, i: number) => <p key={i}>{text}</p>)}</div>)}<p>{doc.notes}</p></details>}
    {record.status === 'draft' && <div className="chat-food-actions"><button type="button" className="primary-button" disabled={locked || saving || !doc} onClick={publish}>{saving ? '正在确认…' : '确认记录'}</button>
      <button type="button" className="secondary-button" disabled={locked || saving} onClick={() => onAsk(`我想调整${record.date}的“${doc?.title || '这顿饭'}”：`)}>修改这顿饭</button></div>}
    </div>
    {notice && <p role="status" className="chat-food-note">{notice}</p>}
  </section>;
}

function FoodSummary({ search }: { search: Data }) {
  const [expanded, setExpanded] = useState(true), bodyId = useId();
  return <section className="chat-food-block chat-food-summary"><header className="chat-food-heading"><h3>最近的饮食记录</h3><CardToggle title="最近的饮食记录" expanded={expanded} controls={bodyId} onToggle={() => setExpanded(value => !value)} /></header>
    <div id={bodyId} className="card-disclosure-body" hidden={!expanded}><Metrics summary={search.summary} /><p>{search.total ?? search.items?.length ?? 0} 条已保存记录 · 未记录的日子不代表没有吃</p></div>
  </section>;
}

export function foodRecords(message: Data): Data[] {
  const records = new Map<string, Data>();
  for (const call of message.tool_calls || []) if (tools.has(call.name) && call.result?.status === 'ok') {
    const record = call.result.record || call.result.draft;
    if (record?.id) records.set(record.id, record);
  }
  return [...records.values()];
}

export function FoodCards({ message, locked, onAsk, ownedIds }: { message: Data; locked: boolean; onAsk: (text: string) => void; ownedIds: Set<string> }) {
  if (message.streaming) return null;
  const search = message.tool_calls?.findLast((call: Data) => call.name === 'search_food_logs' && call.result?.status === 'ok')?.result;
  return <div className="chat-food-content">{foodRecords(message).filter(record => ownedIds.has(record.id)).map(record => <FoodCard key={record.id} receipt={record} locked={locked} onAsk={onAsk} />)}
    {search && <FoodSummary search={search} />}
  </div>;
}
