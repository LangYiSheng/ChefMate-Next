import {useEffect, useRef, useState} from 'react';
import {Dialog} from '../../components/Dialog';
import {api} from '../../runtime/http';
import type {AccountUser} from './types';
import {money, tokens} from './types';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';

export type UsageEntry = {
  turn_id: string; session_id: string; model: string; status: string; active: boolean; created_at: string;
  total_tokens: number | null; input_tokens?: number | null; output_tokens?: number | null;
  cost_micros: number; estimated_cost_micros: number; incomplete: boolean;
  effective_cost_micros: number; effective_estimated_cost_micros: number;
  reconciliation: null | {revision: number; final_cost_micros: number; reason: string; actor_id: string; created_at: string};
};
type Report = {user: AccountUser; items: UsageEntry[]};
const statusLabel = (status: string) => ({running: '执行中', completed: '已完成', cancelled: '已取消', error: '请求失败', interrupted: '已中断'} as Record<string, string>)[status] || status || '状态未报告';
const validAmount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const entryMoney = (value: unknown) => validAmount(value) ? money(value) : '未报告';
const timeLabel = (value: string) => Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN') : '时间未记录';
const initialAmount = (entry: UsageEntry) => entry.reconciliation && validAmount(entry.reconciliation.final_cost_micros) ? String(entry.reconciliation.final_cost_micros / 1_000_000) : '';
const lockReason = (entry: UsageEntry) => entry.active || entry.status === 'running' ? '执行中不可核对'
  : !entry.turn_id || entry.active !== false && !['completed', 'cancelled', 'error', 'interrupted'].includes(entry.status) ? '记录状态待确认'
  : entry.reconciliation && (!Number.isInteger(entry.reconciliation.revision) || entry.reconciliation.revision < 1 || !validAmount(entry.reconciliation.final_cost_micros)) ? '核对版本待确认' : '';
const countedMoney = (entry: UsageEntry) => {
  const known = entry.effective_cost_micros ?? entry.cost_micros, estimated = entry.effective_estimated_cost_micros ?? entry.estimated_cost_micros;
  return validAmount(known) && validAmount(estimated) ? entryMoney(known + estimated) : '未报告';
};
function readReport(value: Report) {
  if (!value || !Array.isArray(value.items) || value.items.some(entry => !entry || typeof entry !== 'object')) throw new Error('用量明细格式不完整，请重新读取。');
  return value;
}

export function UsageDetails({user, onClose, onReconciled}: {user: AccountUser; onClose: () => void; onReconciled: (user: AccountUser) => void}) {
  const [report, setReport] = useState<Report | null>(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [message, setMessage] = useState(''), [editing, setEditing] = useState<UsageEntry | null>(null);
  const [amount, setAmount] = useState(''), [reason, setReason] = useState(''), [conflict, setConflict] = useState(false), [confirmReload, setConfirmReload] = useState(false);
  const alive = useRef(true), working = useRef(false);
  const amountInput = useRef<HTMLInputElement>(null), lastEntryButton = useRef<HTMLButtonElement | null>(null);
  const dirty = Boolean(editing && (amount !== initialAmount(editing) || reason !== ''));
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(dirty, busy, setError);
  const base = `/api/admin/users/${encodeURIComponent(user.id)}/usage`;
  useEffect(() => {if (editing) amountInput.current?.focus(); else if (lastEntryButton.current?.isConnected) lastEntryButton.current.focus();}, [editing?.turn_id]);
  useEffect(() => {
    alive.current = true; const controller = new AbortController();
    void api<Report>(base, {signal: controller.signal}).then(value => {const next = readReport(value); if (!controller.signal.aborted) setReport(next);})
      .catch(problem => {if (!controller.signal.aborted) setError(problem.message);}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => {alive.current = false; controller.abort();};
  }, [base]);
  const leave = (action: () => void) => {if (working.current) setError('正在处理用量明细，请稍候。'); else if (dirty) setPendingLeave(() => action); else action();};
  const choose = (entry: UsageEntry) => {if (editing?.turn_id !== entry.turn_id) leave(() => {setEditing(entry); setAmount(initialAmount(entry)); setReason(''); setError(''); setMessage(''); setConflict(false);});};
  async function reload() {
    if (working.current) return; working.current = true; setLoading(true); setError('');
    try {const value = readReport(await api<Report>(base)); if (alive.current) {setReport(value); setEditing(null); setAmount(''); setReason(''); setConflict(false); setConfirmReload(false); setMessage('已读取最新用量与核对记录。');}}
    catch (problem) {if (alive.current) setError((problem as Error).message);}
    finally {working.current = false; if (alive.current) setLoading(false);}
  }
  async function save() {
    if (working.current || !editing || conflict || amount === '' || !reason.trim()) return;
    working.current = true; setBusy(true); setError(''); setMessage('');
    try {
      const value = await api<{ok: true; user: AccountUser; entry: UsageEntry}>(`${base}/${encodeURIComponent(editing.turn_id)}/reconcile`, {method: 'POST', body: JSON.stringify({revision: editing.reconciliation?.revision ?? 0, final_cost_micros: Math.round(Number(amount) * 1_000_000), reason: reason.trim()})});
      if (alive.current) {
        setReport(previous => previous ? {user: value.user, items: previous.items.map(entry => entry.turn_id === value.entry.turn_id ? value.entry : entry)} : previous);
        setEditing(null); setAmount(''); setReason(''); setMessage('核对结果已保存，累计金额与余额已更新。'); onReconciled(value.user);
      }
    } catch (problem) {if (alive.current) {setError((problem as Error).message); if ((problem as {status?: number}).status === 409) setConflict(true);}}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  return <Dialog title={`@${user.username} 的用量明细`} className="admin-usage-dialog" onClose={() => leave(onClose)}>
    <p className="account-fineprint">显示最近 100 条模型请求。核对金额单独留存；原始用量、单价和金额记录会保留。</p>
    <div className="admin-usage-toolbar"><button type="button" className="secondary-button" disabled={loading || busy} onClick={() => dirty ? setConfirmReload(true) : void reload()}>读取最新明细</button><button type="button" className="text-button" disabled={busy} onClick={() => leave(onClose)}>关闭明细</button></div>
    {error && <p className="error-box" role="alert">{error}{conflict && <span>当前金额和原因草稿已保留，请读取最新记录后复核。</span>}</p>}{message && <p className="account-success" role="status">{message}</p>}
    {loading && <p role="status">正在读取用量明细…</p>}
    {report && <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>发生时间</th><th>模型与状态</th><th>原始记录金额</th><th>当前计入额度</th><th><span className="visually-hidden">核对操作</span></th></tr></thead><tbody>{report.items.map((entry, index) => <tr key={entry.turn_id || `unknown-${index}`} aria-selected={Boolean(editing && editing.turn_id === entry.turn_id)}>
      <th scope="row"><time dateTime={Number.isFinite(Date.parse(entry.created_at)) ? entry.created_at : undefined}>{timeLabel(entry.created_at)}</time><small title={entry.turn_id}>{entry.turn_id?.slice(0, 12) || '轮次未记录'}</small></th>
      <td><strong>{entry.model || '未报告模型'}</strong><small>{entry.active || entry.status === 'running' ? '执行中' : statusLabel(entry.status)} · {!validAmount(entry.total_tokens) ? '用量未报告' : `已报告 ${tokens(entry.total_tokens)} Token`}</small></td>
      <td>{entryMoney(entry.cost_micros)}{validAmount(entry.estimated_cost_micros) && entry.estimated_cost_micros > 0 && <small>另计未知用量预估 {entryMoney(entry.estimated_cost_micros)}</small>}</td>
      <td>{countedMoney(entry)}<small>{entry.reconciliation ? '已人工核对' : entry.incomplete ? '含未完整报告的预估' : '按请求单价计算'}</small></td>
      <td><button type="button" className="secondary-button" disabled={loading || busy || Boolean(lockReason(entry))} aria-label={`核对 ${entry.turn_id || `未知轮次 ${index + 1}`} 的金额`} onClick={event => {lastEntryButton.current = event.currentTarget; choose(entry);}}>{lockReason(entry) || (editing?.turn_id === entry.turn_id ? '正在核对' : entry.reconciliation ? '再次核对' : '核对金额')}</button></td>
    </tr>)}{!report.items.length && <tr><td colSpan={5} className="admin-table-empty">还没有模型请求记录。</td></tr>}</tbody></table></div>}
    {editing && <form className="admin-reconcile-form" aria-label="核对本轮金额" onSubmit={event => {event.preventDefault(); void save();}}>
      <h3>核对本轮总金额</h3><p>请根据供应商记录核实这轮请求的总金额。保存后会用核对金额替代这一轮的原金额与未知用量预估，重新计算余额。</p>
      {editing.reconciliation && <div className="account-notice"><strong>最近一次核对：{entryMoney(editing.reconciliation.final_cost_micros)}</strong><p>{editing.reconciliation.reason || '未记录原因'}</p><small>{timeLabel(editing.reconciliation.created_at)}</small></div>}
      <label className="account-field"><span>核对后总金额（元）</span><input ref={amountInput} aria-label="核对后总金额（元）" type="number" min="0" max="1000000000" step="0.000001" required value={amount} disabled={busy || loading} onChange={event => {setAmount(event.target.value); setMessage('');}} placeholder="填写这一轮最终总金额"/></label>
      <label className="account-field"><span>核对原因</span><textarea aria-label="核对原因" required maxLength={500} rows={3} value={reason} disabled={busy || loading} onChange={event => {setReason(event.target.value); setMessage('');}} placeholder="例如：已核对供应商账单，未产生费用"/></label>
      <div className="account-form-actions"><button type="button" className="text-button" disabled={busy || loading} onClick={() => leave(() => {setEditing(null); setAmount(''); setReason(''); setConflict(false); setError('');})}>取消核对</button><button type="submit" className="primary-button" disabled={busy || loading || conflict || amount === '' || !reason.trim()}>{busy ? '正在保存核对…' : '保存核对金额'}</button></div>
    </form>}
    <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
    {confirmReload && <Dialog title="读取最新用量？" onClose={() => {if (!loading) setConfirmReload(false);}}><div className="account-leave-prompt"><p>这会放弃未保存的核对金额和原因，读取服务器中的最新记录。</p>{error && <p className="error-box" role="alert">{error}</p>}<div className="account-form-actions"><button type="button" className="secondary-button" disabled={loading} onClick={() => setConfirmReload(false)}>保留草稿</button><button type="button" className="primary-button" disabled={loading} onClick={() => void reload()}>放弃并读取</button></div></div></Dialog>}
  </Dialog>;
}
