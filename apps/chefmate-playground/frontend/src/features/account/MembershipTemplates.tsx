import {useCallback, useEffect, useRef, useState} from 'react';
import {Dialog} from '../../components/Dialog';
import {Icon} from '../../components/Icon';
import {Select} from '../../components/ui/Select';
import {api} from '../../runtime/http';
import {AccountLeavePrompt} from './leave-guard';
import {money} from './types';
import './membership-templates.css';

export type MembershipTemplate = {id: string; name: string; label: string; duration_days: number; weekly_limit_micros: number; five_hour_limit_micros: number; created_at: string; updated_at: string};
type Registry = {revision: number; items: MembershipTemplate[]};
type Draft = {name: string; label: string; days: string; weekly: string; hourly: string};
const empty: Draft = {name: '', label: 'ChefMate 会员', days: '30', weekly: '10', hourly: '2'};
const draftOf = (template?: MembershipTemplate): Draft => template ? {name: template.name, label: template.label, days: String(template.duration_days), weekly: String(template.weekly_limit_micros / 1_000_000), hourly: String(template.five_hour_limit_micros / 1_000_000)} : {...empty};
const amount = (text: string) => {
  if (!/^\d+(?:\.\d{1,6})?$/.test(text)) throw new Error('金额需为非负数字，最多保留 6 位小数。');
  const [whole, fraction = ''] = text.split('.'), value = Number(whole) * 1_000_000 + Number(fraction.padEnd(6, '0'));
  if (!Number.isSafeInteger(value) || value > 10 ** 15) throw new Error('金额超出可设置范围。');
  return value;
};
const valuesOf = (draft: Draft) => {
  const duration = Number(draft.days);
  if (!draft.name.trim() || !draft.label.trim() || !Number.isInteger(duration) || duration < 1 || duration > 3660) throw new Error('请填写模板名称、会员名称与有效期。');
  return {name: draft.name.trim(), label: draft.label.trim(), duration_days: duration, weekly_limit_micros: amount(draft.weekly), five_hour_limit_micros: amount(draft.hourly)};
};
const sameValues = (template: MembershipTemplate, draft: Draft) => {
  try {const values = valuesOf(draft); return (Object.keys(values) as (keyof typeof values)[]).every(key => template[key] === values[key]);} catch {return false;}
};
const summary = (template: MembershipTemplate) => `${template.duration_days} 天 · 每周 ${money(template.weekly_limit_micros)} · 每 5 小时 ${money(template.five_hour_limit_micros)}`;
const base = '/api/admin/membership-templates';

export function MembershipTemplates({disabled, onApply, onBusyChange, onDirtyChange}: {disabled: boolean; onApply: (template: MembershipTemplate) => void; onBusyChange: (busy: boolean) => void; onDirtyChange: (dirty: boolean) => void}) {
  const [data, setData] = useState<Registry | null>(null), [selected, setSelected] = useState(''), [managing, setManaging] = useState(false);
  const [editing, setEditing] = useState<MembershipTemplate | 'new' | null>(null), [draft, setDraft] = useState<Draft>({...empty});
  const [removing, setRemoving] = useState<MembershipTemplate | null>(null), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [error, setError] = useState(''), [message, setMessage] = useState(''), [conflict, setConflict] = useState(false), [uncertain, setUncertain] = useState(false), [missing, setMissing] = useState(false);
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const alive = useRef(true), working = useRef(false), readVersion = useRef(0), baseline = useRef<Draft>({...empty}), nameInput = useRef<HTMLInputElement>(null);
  const dirty = Boolean(editing && JSON.stringify(draft) !== JSON.stringify(baseline.current));
  useEffect(() => {onDirtyChange(dirty || uncertain); return () => onDirtyChange(false);}, [dirty, uncertain, onDirtyChange]);
  useEffect(() => {onBusyChange(busy); return () => onBusyChange(false);}, [busy, onBusyChange]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const version = ++readVersion.current, value = await api<Registry>(base, {signal});
    if (!value || !Number.isInteger(value.revision) || !Array.isArray(value.items)) throw new Error('会员模板格式不完整，请重新读取。');
    if (alive.current && !signal?.aborted && version === readVersion.current) {
      setData(value); setSelected(previous => value.items.some(item => item.id === previous) ? previous : value.items[0]?.id || '');
    }
    return value;
  }, []);
  useEffect(() => {
    alive.current = true; const controller = new AbortController();
    void load(controller.signal).catch(problem => {if (!controller.signal.aborted) setError(problem.message);}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => {alive.current = false; controller.abort(); readVersion.current++;};
  }, [load]);
  useEffect(() => {if (editing) nameInput.current?.focus();}, [editing]);
  const leave = (action: () => void) => {if (working.current) setError('正在保存会员模板，请稍候。'); else if (dirty || uncertain) setPendingLeave(() => action); else action();};
  const edit = (template: MembershipTemplate | 'new') => leave(() => {
    const initial = draftOf(template === 'new' ? undefined : template);
    baseline.current = initial; setDraft(initial); setEditing(template); setRemoving(null); setError(''); setMessage(''); setConflict(false); setMissing(false); setUncertain(false);
  });
  const closeEditor = () => leave(() => {setEditing(null); setError(''); setConflict(false); setMissing(false); setUncertain(false);});
  const closeManager = () => leave(() => {setManaging(false); setEditing(null); setRemoving(null); setConflict(false); setMissing(false); setUncertain(false);});
  const apply = (template: MembershipTemplate) => {if (disabled || working.current) return; onApply(template); setManaging(false); setMessage(`已套用“${template.name}”，可继续调整本次参数。`);};
  async function refresh() {
    if (working.current) return;
    working.current = true; setLoading(true); setError('');
    try {
      const latest = await load();
      if (alive.current) {
        const saved = editing && latest.items.find(item => editing === 'new' ? sameValues(item, draft) : item.id === editing.id && sameValues(item, draft));
        if (uncertain && saved) {setEditing(null); setUncertain(false); setMessage('已确认上次模板已保存。');}
        else if (uncertain && removing && !latest.items.some(item => item.id === removing.id)) {setRemoving(null); setUncertain(false); setMessage('已确认该模板已删除。');}
        else {
          setMissing(Boolean(editing && editing !== 'new' && !latest.items.some(item => item.id === editing.id)));
          setMessage(editing ? '已读取最新版本，草稿已保留。请对照当前模板复核后再保存。' : '模板列表已更新。');
          setUncertain(false);
        }
        setConflict(false);
      }
    } catch (problem) {if (alive.current) setError((problem as Error).message);}
    finally {working.current = false; if (alive.current) setLoading(false);}
  }
  async function save() {
    if (disabled || working.current || !editing || !data || conflict || uncertain || missing) return;
    let values;
    try {values = valuesOf(draft);} catch (problem) {setError((problem as Error).message); return;}
    working.current = true; setBusy(true); setError(''); setMessage(''); readVersion.current++;
    try {
      const latest = await api<Registry>(editing === 'new' ? base : `${base}/${encodeURIComponent(editing.id)}`, {method: editing === 'new' ? 'POST' : 'PATCH', body: JSON.stringify(editing === 'new' ? {revision: data.revision, ...values} : {revision: data.revision, values})});
      if (alive.current) {setData(latest); setEditing(null); setConflict(false); setUncertain(false); setSelected(latest.items.find(item => item.name === values.name)?.id || ''); setMessage('会员模板已保存，可选择套用。');}
    } catch (problem) {
      if (alive.current) {const status = (problem as {status?: number}).status; setError((problem as Error).message); setConflict(status === 409); setUncertain(!status || status >= 500);}
    } finally {working.current = false; if (alive.current) setBusy(false);}
  }
  async function remove() {
    if (working.current || !removing || !data || conflict || uncertain) return;
    working.current = true; setBusy(true); setError(''); setMessage(''); readVersion.current++;
    try {
      const latest = await api<Registry>(`${base}/${encodeURIComponent(removing.id)}?revision=${data.revision}`, {method: 'DELETE'});
      if (alive.current) {setData(latest); setRemoving(null); setSelected(previous => latest.items.some(item => item.id === previous) ? previous : latest.items[0]?.id || ''); setMessage('模板已删除，已开通的会员不受影响。');}
    } catch (problem) {
      if (alive.current) {const status = (problem as {status?: number}).status; setError((problem as Error).message); setConflict(status === 409); setUncertain(!status || status >= 500);}
    } finally {working.current = false; if (alive.current) setBusy(false);}
  }
  const selectedTemplate = data?.items.find(item => item.id === selected), locked = disabled || busy || loading;
  const current = editing && editing !== 'new' ? data?.items.find(item => item.id === editing.id) : null;
  const feedback = <>{error && <p className="error-box" role="alert">{error}{(conflict || uncertain) && <span>草稿已保留，请读取最新模板后复核。</span>}</p>}{message && <p className="account-success" role="status">{message}</p>}</>;
  return <>
    <section className="membership-template-picker" aria-label="选择会员模板"><div className="membership-template-heading"><div><h4>会员模板</h4><p>选择常用方案，再调整这次开通的参数。</p></div><button type="button" className="text-button" disabled={locked || !data} onClick={() => {setManaging(true); setError(''); setMessage('');}}>管理模板</button></div>
      {data?.items.length ? <><div className="membership-template-select"><Select aria-label="会员模板" value={selected} onValueChange={setSelected} disabled={locked} options={data.items.map(item => ({value: item.id, label: item.name}))}/><button type="button" className="secondary-button" disabled={locked || !selectedTemplate} onClick={() => selectedTemplate && apply(selectedTemplate)}>套用模板</button></div>{selectedTemplate && <p className="membership-template-summary">{summary(selectedTemplate)}</p>}</>
        : <p className="membership-template-empty">{loading ? '正在读取会员模板…' : !data ? '暂时无法读取已保存的模板，可手动填写本次会员参数。' : '还没有保存的模板，可在管理模板中添加常用方案。'}</p>}
      {!managing && feedback}{!managing && error && <button type="button" className="text-button" disabled={loading} onClick={() => void refresh()}>重新读取模板</button>}
    </section>
    {managing && <Dialog title={editing ? editing === 'new' ? '新增会员模板' : '编辑会员模板' : '管理会员模板'} className="membership-template-dialog" onClose={closeManager}>
      <div className="membership-template-dialog-heading"><p>模板只保存方案；套用或修改模板都不会直接开通会员，也不会追改已开通的会员。</p><button type="button" className="text-button" disabled={busy || loading} onClick={closeManager}>关闭模板</button></div>
      {feedback}
      {(conflict || uncertain) && <button type="button" className="secondary-button membership-template-reload" disabled={busy || loading} onClick={() => void refresh()}>读取最新版本并保留草稿</button>}
      {editing ? <form className="membership-template-editor" aria-label="保存会员模板" onSubmit={event => {event.preventDefault(); void save();}}>
        {current && conflict === false && JSON.stringify(draftOf(current)) !== JSON.stringify(baseline.current) && <div className="membership-template-current"><strong>当前保存的模板：{current.name}</strong><p>{current.label} · {summary(current)}</p></div>}
        {missing && <p className="account-notice">原模板已被删除。草稿仍保留，可以另存为新模板。</p>}
        <div className="membership-template-form-grid"><label className="account-field"><span>模板名称</span><input ref={nameInput} required maxLength={80} value={draft.name} disabled={busy || loading || uncertain} onChange={event => setDraft(previous => ({...previous, name: event.target.value}))} placeholder="例如：标准月卡"/></label><label className="account-field"><span>开通后的会员名称</span><input required maxLength={80} value={draft.label} disabled={busy || loading || uncertain} onChange={event => setDraft(previous => ({...previous, label: event.target.value}))}/></label><label className="account-field"><span>模板有效期（天）</span><input required type="number" min="1" max="3660" step="1" value={draft.days} disabled={busy || loading || uncertain} onChange={event => setDraft(previous => ({...previous, days: event.target.value}))}/></label><label className="account-field"><span>模板每周额度（元）</span><input required type="number" min="0" max="1000000000" step="0.000001" value={draft.weekly} disabled={busy || loading || uncertain} onChange={event => setDraft(previous => ({...previous, weekly: event.target.value}))}/></label><label className="account-field"><span>模板每 5 小时额度（元）</span><input required type="number" min="0" max="1000000000" step="0.000001" value={draft.hourly} disabled={busy || loading || uncertain} onChange={event => setDraft(previous => ({...previous, hourly: event.target.value}))}/></label></div>
        <div className="account-form-actions"><button type="button" className="text-button" disabled={busy || loading} onClick={closeEditor}>返回模板列表</button>{missing && <button type="button" className="secondary-button" disabled={busy || loading} onClick={() => {setEditing('new'); setMissing(false); setConflict(false);}}>另存为新模板</button>}<button type="submit" className="primary-button" disabled={busy || loading || conflict || uncertain || missing || !draft.name.trim() || !draft.label.trim() || editing !== 'new' && !dirty}>{busy ? '正在保存…' : '保存模板'}</button></div>
      </form> : <>
        <div className="membership-template-list-toolbar"><h3>常用方案</h3><button type="button" className="primary-button" disabled={busy || loading} onClick={() => edit('new')}><Icon name="plus"/>新增模板</button></div>
        <div className="membership-template-list">{data?.items.map(item => <article key={item.id}><div><h4>{item.name}</h4><p>{item.label}</p><small>{summary(item)}</small></div><div><button type="button" className="secondary-button" disabled={locked} onClick={() => apply(item)}>套用</button><button type="button" className="text-button" disabled={locked} onClick={() => edit(item)}>编辑</button><button type="button" className="text-button" disabled={locked} onClick={() => {setRemoving(item); setError(''); setMessage(''); setConflict(false); setUncertain(false);}}>删除</button></div></article>)}{!data?.items.length && <p className="membership-template-empty">还没有模板，先保存一份常用的会员方案吧。</p>}</div>
        {removing && <section className="membership-template-delete" aria-label="确认删除会员模板"><h3>删除“{removing.name}”？</h3><p>以后将不能再套用这个模板，已开通的会员与本次开通草稿都会保留。</p><div className="account-form-actions"><button type="button" className="text-button" disabled={busy || loading} onClick={() => {setRemoving(null); setConflict(false); setUncertain(false);}}>保留模板</button><button type="button" className="primary-button" disabled={busy || loading || conflict || uncertain} onClick={() => void remove()}>{busy ? '正在删除…' : '确认删除模板'}</button></div></section>}
      </>}
    </Dialog>}
    <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
  </>;
}
