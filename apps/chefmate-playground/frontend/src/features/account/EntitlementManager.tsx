import {useCallback, useEffect, useRef, useState} from 'react';
import {Dialog} from '../../components/Dialog';
import {Icon} from '../../components/Icon';
import {api} from '../../runtime/http';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';
import {QuotaOverview} from './QuotaOverview';
import {money, type AccountUser, type FundsOverview, type MembershipFund} from './types';
import './entitlement-manager.css';

type Report = {user: AccountUser; entitlements: FundsOverview};
type Mode = 'credit' | 'membership' | 'revoke' | null;
type Pending = {path: string; body: Record<string, string | number>; mode: Mode};
const defaults = {label: '额外充值额度', amount: '10', memberLabel: 'ChefMate 会员', days: '30', weekly: '10', hourly: '2', reason: ''};
type Draft = typeof defaults;
const micros = (value: string) => {
  if (!/^\d+(?:\.\d{1,6})?$/.test(value)) throw new Error('金额请填写非负数字，最多保留 6 位小数。');
  const [whole, fraction = ''] = value.split('.');
  const amount = Number(whole) * 1_000_000 + Number(fraction.padEnd(6, '0'));
  if (!Number.isSafeInteger(amount) || amount > 10 ** 15) throw new Error('金额超出可设置范围。');
  return amount;
};

export function EntitlementManager({user, onClose, onChanged}: {user: AccountUser; onClose: () => void; onChanged: (user: AccountUser) => Promise<void>}) {
  const [report, setReport] = useState<Report | null>(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>(null), [draft, setDraft] = useState<Draft>({...defaults}), [revoking, setRevoking] = useState<MembershipFund | null>(null);
  const [error, setError] = useState(''), [message, setMessage] = useState(''), [uncertain, setUncertain] = useState(false), [conflict, setConflict] = useState(false);
  const alive = useRef(true), working = useRef(false), pending = useRef<Pending | null>(null), requestId = useRef(crypto.randomUUID()), readVersion = useRef(0);
  const firstInput = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const base = `/api/admin/users/${encodeURIComponent(user.id)}`;
  const dirty = uncertain || mode === 'credit' && (draft.label !== defaults.label || draft.amount !== defaults.amount)
    || mode === 'membership' && (draft.memberLabel !== defaults.memberLabel || draft.days !== defaults.days || draft.weekly !== defaults.weekly || draft.hourly !== defaults.hourly)
    || mode === 'revoke' && Boolean(draft.reason);
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(Boolean(dirty), busy || loading, setError);
  const load = useCallback(async (signal?: AbortSignal) => {
    const version = ++readVersion.current;
    const value = await api<Report>(`${base}/entitlements`, {signal});
    if (!value?.user?.quota?.funds || !Array.isArray(value.entitlements?.memberships)) throw new Error('额度信息不完整，请重新读取。');
    if (alive.current && !signal?.aborted && version === readVersion.current) setReport(value);
    return value;
  }, [base]);
  useEffect(() => {
    alive.current = true; const controller = new AbortController();
    void load(controller.signal).catch(problem => {if (!controller.signal.aborted) setError(problem.message);})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => {alive.current = false; controller.abort();};
  }, [load]);
  useEffect(() => {if (mode) firstInput.current?.focus();}, [mode, revoking?.id]);
  const leave = (action: () => void) => {
    if (working.current || busy) setError('正在保存额度，请稍候。');
    else if (dirty) setPendingLeave(() => action);
    else action();
  };
  const choose = (next: Mode, member?: MembershipFund) => leave(() => {
    pending.current = null; requestId.current = crypto.randomUUID(); setMode(next); setDraft({...defaults});
    setRevoking(member || null); setError(''); setMessage(''); setUncertain(false); setConflict(false);
  });
  const patch = (key: keyof Draft, value: string) => {setDraft(previous => ({...previous, [key]: value})); setMessage('');};
  async function refresh() {
    if (working.current) return;
    working.current = true; setLoading(true); setError('');
    try {
      const value = await load(), operation = pending.current;
      if (alive.current) {
        const request = operation?.body.request_id;
        const savedGrant = request && [...value.entitlements.permanent.sources, ...value.entitlements.memberships]
          .some(item => (item as {request_id?: string}).request_id === request);
        const savedRevoke = operation?.mode === 'revoke' && value.entitlements.memberships.some(item => item.id === revoking?.id && item.revoked_at && item.revision > Number(operation.body.revision));
        if (uncertain && (savedGrant || savedRevoke)) {
          pending.current = null; requestId.current = crypto.randomUUID(); setMode(null); setDraft({...defaults}); setRevoking(null); setUncertain(false); setConflict(false);
          setMessage('已确认上次操作已保存，额度已更新。');
          try {await onChanged(value.user);} catch {if (alive.current) setError('额度已保存，管理列表同步失败。关闭后刷新列表即可。');}
        } else setMessage('已读取最新额度。');
      }
    }
    catch (problem) {if (alive.current) setError((problem as Error).message);}
    finally {working.current = false; if (alive.current) setLoading(false);}
  }
  async function save() {
    if (working.current || !mode || conflict) return;
    let operation: Pending;
    try {
      if (uncertain && pending.current) operation = pending.current;
      else if (mode === 'credit') {
        const amount = micros(draft.amount);
        if (!amount || !draft.label.trim()) throw new Error('请填写额度名称与大于零的充值金额。');
        operation = {mode, path: `${base}/credit-grants`, body: {amount_micros: amount, label: draft.label.trim(), request_id: requestId.current}};
      } else if (mode === 'membership') {
        const duration = Number(draft.days);
        if (!draft.memberLabel.trim() || !Number.isInteger(duration) || duration < 1 || duration > 3660) throw new Error('请填写会员名称与 1～3660 天的有效期。');
        operation = {mode, path: `${base}/memberships`, body: {label: draft.memberLabel.trim(), duration_days: duration,
          weekly_limit_micros: micros(draft.weekly), five_hour_limit_micros: micros(draft.hourly), request_id: requestId.current}};
      } else {
        if (!revoking || !draft.reason.trim()) throw new Error('请填写撤销原因。');
        operation = {mode, path: `${base}/memberships/${encodeURIComponent(revoking.id)}/revoke`, body: {revision: revoking.revision, reason: draft.reason.trim()}};
      }
    } catch (problem) {setError((problem as Error).message); return;}
    working.current = true; readVersion.current++; pending.current = operation; setBusy(true); setError(''); setMessage('');
    try {
      const value = await api<Report>(operation.path, {method: 'POST', body: JSON.stringify(operation.body)});
      if (alive.current) {
        readVersion.current++;
        setReport(value); setMode(null); setDraft({...defaults}); setRevoking(null); setUncertain(false); setConflict(false);
        pending.current = null; requestId.current = crypto.randomUUID();
        setMessage(operation.mode === 'credit' ? '永久充值额度已增加。' : operation.mode === 'membership' ? '会员已开通，有效期从现在开始，额度窗口在首次使用时启动。' : '会员已撤销，历史用量和已发起请求的预留会保留。');
        try {await onChanged(value.user);} catch {if (alive.current) setError('额度已保存，管理列表同步失败。关闭后刷新列表即可。');}
      }
    } catch (problem) {
      if (alive.current) {
        const status = (problem as {status?: number}).status;
        setError((problem as Error).message); setConflict(status === 409);
        setUncertain(!status || status >= 500);
      }
    } finally {working.current = false; if (alive.current) setBusy(false);}
  }
  const locked = busy || loading || uncertain || conflict;
  const validDraft = mode === 'credit' ? Boolean(draft.label.trim() && draft.amount) : mode === 'membership'
    ? Boolean(draft.memberLabel.trim() && draft.days && draft.weekly && draft.hourly) : Boolean(draft.reason.trim());
  return <Dialog title={`@${user.username} 的额度管理`} className="entitlement-manager" onClose={() => leave(onClose)}>
    <div className="entitlement-toolbar"><p>限时会员、基础额度与永久充值分别记账。</p><div><button type="button" className="secondary-button" disabled={busy || loading} onClick={() => void refresh()}><Icon name="refresh-cw"/>刷新额度</button><button type="button" className="text-button" disabled={busy} onClick={() => leave(onClose)}>关闭</button></div></div>
    {error && <p className="error-box" role="alert">{error}{conflict && <span> 草稿已保留。请刷新额度并核对最新状态；重新选择操作后再提交。</span>}</p>}
    {message && <p className="account-success" role="status">{message}</p>}
    {loading && <p role="status">正在读取额度…</p>}
    {report && <>
      <div className="entitlement-actions"><button type="button" disabled={busy || loading} onClick={() => choose('credit')} aria-pressed={mode === 'credit'}><Icon name="coins"/><span><strong>增加永久额度</strong><small>使用基础额度后继续扣减</small></span><Icon name="plus"/></button><button type="button" disabled={busy || loading} onClick={() => choose('membership')} aria-pressed={mode === 'membership'}><Icon name="sparkles"/><span><strong>开通限时会员</strong><small>设置有效期、周与 5 小时额度</small></span><Icon name="plus"/></button></div>
      {mode && <form className="entitlement-form" aria-label={mode === 'credit' ? '增加永久充值额度' : mode === 'membership' ? '开通会员' : '撤销会员'} onSubmit={event => {event.preventDefault(); void save();}}>
        <h3>{mode === 'credit' ? '增加永久充值额度' : mode === 'membership' ? '开通 ChefMate 会员' : `撤销 ${revoking?.label}`}</h3>
        {mode === 'credit' ? <div className="entitlement-form-grid"><label className="account-field"><span>额度名称</span><input ref={element => {firstInput.current = element;}} required maxLength={80} value={draft.label} disabled={locked} onChange={event => patch('label', event.target.value)}/></label><label className="account-field"><span>充值金额（元）</span><input type="number" required min="0.000001" max="1000000000" step="0.000001" value={draft.amount} disabled={locked} onChange={event => patch('amount', event.target.value)}/></label></div>
          : mode === 'membership' ? <div className="entitlement-form-grid"><label className="account-field"><span>会员名称</span><input ref={element => {firstInput.current = element;}} required maxLength={80} value={draft.memberLabel} disabled={locked} onChange={event => patch('memberLabel', event.target.value)}/></label><label className="account-field"><span>有效期（天）</span><input type="number" required min="1" max="3660" step="1" value={draft.days} disabled={locked} onChange={event => patch('days', event.target.value)}/></label><label className="account-field"><span>每周额度（元）</span><input type="number" required min="0" max="1000000000" step="0.000001" value={draft.weekly} disabled={locked} onChange={event => patch('weekly', event.target.value)}/></label><label className="account-field"><span>每 5 小时额度（元）</span><input type="number" required min="0" max="1000000000" step="0.000001" value={draft.hourly} disabled={locked} onChange={event => patch('hourly', event.target.value)}/></label></div>
          : <><p>该会员将停止承接新请求，原来的额度记录与正在执行的预留仍会保留。</p><label className="account-field"><span>撤销原因</span><textarea ref={element => {firstInput.current = element;}} required rows={3} maxLength={500} value={draft.reason} disabled={locked} onChange={event => patch('reason', event.target.value)} placeholder="填写本次撤销的原因"/></label></>}
        {mode === 'membership' && <p className="account-fineprint">周额度与 5 小时额度同时生效；5 小时窗口不会越过本周结束时间。多个会员优先使用最早到期的。任一限制设为零时，该会员没有可用额度。</p>}
        {uncertain && <p className="account-notice">上次请求的结果尚未确认。表单已保留；重试会核对同一项操作，避免重复增加额度。</p>}
        <div className="account-form-actions"><button type="button" className="text-button" disabled={busy} onClick={() => choose(null)}>取消</button><button type="submit" className="primary-button" disabled={busy || loading || conflict || !validDraft}>{busy ? '正在保存…' : uncertain ? '重试同一操作' : mode === 'credit' ? '确认增加额度' : mode === 'membership' ? '确认开通会员' : '确认撤销会员'}</button></div>
      </form>}
      <QuotaOverview quota={report.user.quota} onRefresh={load}/>
      {report.entitlements.memberships.some(item => item.status === 'active') && <section className="entitlement-member-actions" aria-label="会员管理"><h3>管理现有会员</h3>{report.entitlements.memberships.filter(item => item.status === 'active').map(item => <div key={item.id}><span><strong>{item.label}</strong><small>到期 {new Date(item.expires_at).toLocaleDateString('zh-CN')} · 当前可用 {money(item.available_micros)}</small></span><button type="button" className="text-button" disabled={busy || loading} onClick={() => choose('revoke', item)}>撤销会员</button></div>)}</section>}
    </>}
    <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
  </Dialog>;
}
