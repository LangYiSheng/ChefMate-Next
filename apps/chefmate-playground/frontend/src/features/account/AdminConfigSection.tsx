import {useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Dialog} from '../../components/Dialog';
import {Icon} from '../../components/Icon';
import {ComponentSettings} from '../settings/ComponentSettings';
import {Field, SettingsGroup} from '../settings/SettingsFields';
import {isDirty, type Values} from '../settings/settings-state';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';
import '../settings/settings-react.css';
import './admin-config.css';

type ConfigSection = 'runtime' | 'context' | 'components';
type ConfigSnapshot = Values & {revision: string};
const draftOf = (snapshot: ConfigSnapshot, section: ConfigSection): Values => section === 'components' ? Object.fromEntries((snapshot.available_components || []).map((id: string) => [id, (snapshot.enabled_components || []).includes(id)])) : {...snapshot[section]};
export function AdminConfigSection(props: {section: ConfigSection; notify: (message: string) => void}) {
  return <ConfigEditor key={props.section} {...props}/>;
}
function ConfigEditor({section, notify}: {section: ConfigSection; notify: (message: string) => void}) {
  const [record, setRecord] = useState<ConfigSnapshot | null>(null), [draft, setDraft] = useState<Values>({}), [baseline, setBaseline] = useState<Values>({});
  const [busy, setBusy] = useState(true), [error, setError] = useState(''), [message, setMessage] = useState(''), [conflict, setConflict] = useState(false), [reload, setReload] = useState(false);
  const alive = useRef(true), working = useRef(false), form = useRef<HTMLFormElement>(null);
  const dirty = isDirty(draft, baseline), {pendingLeave, setPendingLeave} = useAccountLeaveGuard(dirty, busy, setError);
  const adopt = (snapshot: ConfigSnapshot) => {const accepted = draftOf(snapshot, section); setRecord(snapshot); setDraft(accepted); setBaseline(structuredClone(accepted)); setConflict(false);};
  async function read(signal?: AbortSignal) {
    if (working.current) return;
    working.current = true; setBusy(true); setError(''); setMessage('');
    try {const snapshot = await api<ConfigSnapshot>('/api/admin/settings', {signal}); if (alive.current && !signal?.aborted) adopt(snapshot);}
    catch (reason) {if (alive.current && !signal?.aborted) setError((reason as Error).message);}
    finally {working.current = false; if (alive.current && !signal?.aborted) setBusy(false);}
  }
  useEffect(() => {alive.current = true; const controller = new AbortController(); void read(controller.signal); return () => {alive.current = false; controller.abort(); working.current = false;};}, []);
  async function save() {
    if (!record || working.current || busy || conflict || !form.current?.reportValidity()) return;
    working.current = true; setBusy(true); setError(''); setMessage('');
    const values = section === 'components' ? {enabled_components: Object.keys(draft).filter(key => draft[key])} : {...draft};
    try {
      const accepted = await api<ConfigSnapshot>('/api/admin/settings', {method: 'PATCH', body: JSON.stringify({revision: record.revision, section, values})});
      if (!alive.current) return;
      adopt(accepted); setMessage('设置已保存，接下来的请求使用新配置。'); notify('运行配置已保存。'); window.dispatchEvent(new CustomEvent('chefmate:settings-saved'));
    } catch (reason) {if (alive.current) {const problem = reason as Error & {status?: number}; setConflict(problem.status === 409); setError(problem.message + (problem.status === 409 ? ' 当前草稿已保留，请重新读取并核对后再保存。' : ''));}}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  const field = (label: string, key: string, options: {min: number; max: number; hint?: string}) => <Field key={key} label={label} type="number" value={draft[key]} onChange={value => {setDraft(current => ({...current, [key]: value})); setMessage('');}} disabled={busy} {...options}/>;
  return <section className="admin-config-section react-settings" data-section={section} aria-label={{runtime: '执行设置', context: '上下文预算', components: '系统组件'}[section]}>
    {error && <p className="settings-error" role="alert">{error}</p>}{message && <p className="settings-success" role="status">{message}</p>}
    {!record ? <div className="settings-load-state"><p role="status">{busy ? '正在读取运行配置…' : '暂时无法读取运行配置。'}</p>{!busy && <button type="button" className="secondary-button" onClick={() => void read()}>重新读取</button>}</div> : <form ref={form} onSubmit={event => {event.preventDefault(); void save();}}>
      {section === 'runtime' && <><SettingsGroup title="等待与重试">{field('单轮任务时限', 'turn_timeout_s', {min: 10, max: 300, hint: '秒，10–300'})}{field('单次模型等待', 'model_timeout_s', {min: 10, max: 300, hint: '秒，10–300'})}{field('模型请求重试', 'model_max_retries', {min: 0, max: 2})}</SettingsGroup><SettingsGroup title="操作预算">{field('模型调用上限', 'max_model_rounds', {min: 2, max: 30})}{field('工具调用上限', 'max_tool_calls', {min: 2, max: 50})}</SettingsGroup></>}
      {section === 'context' && <SettingsGroup title="信息容量">{field('会话上下文上限', 'max_history_chars', {min: 4000, max: 2000000, hint: '字符，包含对话历史与本轮工具信息。默认 400,000。'})}{field('单次工具结果上限', 'max_tool_result_chars', {min: 2000, max: 200000, hint: '字符，控制每次查询送给模型的信息量。默认 60,000。'})}<p className="settings-note">这里按字符计量。增加容量会让每轮请求携带更多内容，也会增加模型使用金额。</p></SettingsGroup>}
      {section === 'components' && <ComponentSettings components={record.components || []} values={draft} change={(key, value) => setDraft(current => ({...current, [key]: value}))} disabled={busy}/>}
      <footer className="settings-footer"><span role="status"><Icon name={busy ? 'loader-circle' : dirty ? 'circle-alert' : 'circle-check'}/>{busy ? '正在处理…' : conflict ? '请先重新读取并核对' : dirty ? '有未保存的修改' : '已与服务器同步'}</span><div><button type="button" className="text-button" disabled={busy} onClick={() => dirty ? setReload(true) : void read()}>重新读取</button><button type="submit" className="primary-button" disabled={busy || !dirty || conflict}>保存修改</button></div></footer>
    </form>}
    <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
    {reload && <Dialog title="重新读取运行配置？" onClose={() => setReload(false)}><p>当前分类的草稿会在成功读取后替换为服务器配置。</p><div className="account-form-actions"><button type="button" className="secondary-button" onClick={() => setReload(false)}>保留草稿</button><button type="button" className="primary-button" onClick={() => {setReload(false); void read();}}>放弃并读取</button></div></Dialog>}
  </section>;
}
