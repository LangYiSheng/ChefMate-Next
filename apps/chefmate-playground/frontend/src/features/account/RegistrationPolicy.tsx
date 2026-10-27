import {useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Dialog} from '../../components/Dialog';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';

export type RegistrationPolicyValue = {revision: number; registration_enabled: boolean; default_cost_limit_micros: number | null};
type Draft = {registration_enabled: boolean; cost: string};
const draftOf = (policy: RegistrationPolicyValue): Draft => ({registration_enabled: policy.registration_enabled,
  cost: policy.default_cost_limit_micros == null ? '' : String(policy.default_cost_limit_micros / 1_000_000)});

export function RegistrationPolicy({policy, onSaved}: {policy: RegistrationPolicyValue; onSaved: (value: RegistrationPolicyValue) => void}) {
  const [baseline, setBaseline] = useState(policy), [draft, setDraft] = useState(() => draftOf(policy));
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [conflict, setConflict] = useState(false), [confirmReload, setConfirmReload] = useState(false);
  const alive = useRef(true), working = useRef(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(baseline));
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(dirty, busy, setError);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {
    if (policy.revision <= baseline.revision) return;
    if (dirty) {setConflict(true); setError('注册与默认额度已在其他页面更新，当前草稿已保留。');}
    else {setBaseline(policy); setDraft(draftOf(policy)); setConflict(false);}
  }, [policy, baseline.revision, dirty]);
  const adopt = (value: RegistrationPolicyValue) => {setBaseline(value); setDraft(draftOf(value)); setConflict(false); setError('');};
  async function save() {
    if (working.current || conflict) return;
    working.current = true; setBusy(true); setError(''); setMessage('');
    try {
      const value = await api<RegistrationPolicyValue>('/api/admin/policy', {method: 'PATCH', body: JSON.stringify({
        revision: baseline.revision, registration_enabled: draft.registration_enabled,
        default_cost_limit_micros: draft.cost === '' ? null : Math.round(Number(draft.cost) * 1_000_000),
      })});
      if (alive.current) {adopt(value); onSaved(value); setMessage('注册与新用户额度已保存，现有用户额度不变。');}
    } catch (reason) {if (alive.current) {setError((reason as Error).message); if ((reason as {status?: number}).status === 409) setConflict(true);}}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  async function reload() {
    if (working.current) return;
    working.current = true; setBusy(true); setError(''); setMessage('');
    try {const value = await api<RegistrationPolicyValue>('/api/admin/policy'); if (alive.current) {adopt(value); onSaved(value); setConfirmReload(false); setMessage('已读取最新注册与默认额度。');}}
    catch (reason) {if (alive.current) setError((reason as Error).message);}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  return <section className="admin-section admin-registration-policy" aria-labelledby="registration-policy-heading"><h2 id="registration-policy-heading">注册与新用户额度</h2>
    <p className="account-fineprint">默认额度只用于之后注册的新用户，现有用户的额度保持不变。关闭注册后，已有账号仍可登录。</p>
    <form onSubmit={event => {event.preventDefault(); void save();}}>
      <label className="admin-check"><span><strong>开放新用户注册</strong><small>允许通过账号和密码创建新的厨房账号。</small></span><input type="checkbox" role="switch" aria-label="开放新用户注册" checked={draft.registration_enabled === true} disabled={busy} onChange={event => {setDraft(previous => ({...previous, registration_enabled: event.target.checked})); setMessage('');}}/></label>
      <label className="account-field"><span>新用户默认金额额度（元）</span><input aria-label="新用户默认金额额度（元）" type="number" min="0" max="1000000000" step="0.000001" value={draft.cost} disabled={busy} onChange={event => {setDraft(previous => ({...previous, cost: event.target.value})); setMessage('');}} placeholder="留空为不限"/><small>按管理员设置的模型单价扣减，留空为不限。</small></label>
      {error && <p className="error-box" role="alert">{error}{conflict && <span>请复核最新政策后再保存。</span>}</p>}{message && <p className="account-success" role="status">{message}</p>}
      <div className="account-form-actions"><button className="text-button" type="button" disabled={busy} onClick={() => dirty ? setConfirmReload(true) : void reload()}>重新读取注册设置</button><button className="primary-button" type="submit" disabled={busy || !dirty || conflict}>{busy ? '正在处理…' : '保存注册与默认额度'}</button></div>
    </form>
    <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
    {confirmReload && <Dialog title="读取最新注册设置？" onClose={() => {if (!busy) setConfirmReload(false);}}><div className="account-leave-prompt"><p>这会放弃当前未保存的注册与默认额度，读取服务器中的最新版本。</p>{error && <p className="error-box" role="alert">{error}</p>}<div className="account-form-actions"><button className="secondary-button" type="button" disabled={busy} onClick={() => setConfirmReload(false)}>保留草稿</button><button className="primary-button" type="button" disabled={busy} onClick={() => void reload()}>{busy ? '正在读取…' : '放弃并读取'}</button></div></div></Dialog>}
  </section>;
}
