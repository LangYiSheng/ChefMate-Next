import {lazy, Suspense, useCallback, useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Icon} from '../../components/Icon';
import {Dialog} from '../../components/Dialog';
import {Select} from '../../components/ui/Select';
import type {AccountUser, Pricing} from './types';
import {money, tokens, quotaAmounts} from './types';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';
import {RegistrationPolicy, type RegistrationPolicyValue} from './RegistrationPolicy';
import {UsageDetails} from './UsageDetails';

const GlobalSettings = lazy(() => import('../settings/Settings'));
type Tab = 'overview' | 'users' | 'pricing' | 'models' | 'data' | 'audit';
type UsageRow = {user: AccountUser; turns: number; total_tokens: number; input_tokens: number; output_tokens: number; cost_micros: number; incomplete_turns: number; estimated_tokens: number; estimated_cost_micros: number; reported_cost_micros?: number; reconciled_turns?: number};
type Summary = Omit<UsageRow, 'user'>;
type UsageReport = {summary: Summary; users: UsageRow[]; daily: (Summary & {date: string})[]; pricing: Pricing};
type AuditRow = {actor_id: string; target_id: string; action: string; fields: string[]; created_at: string};
type UserData = {user_id: string; username: string; counts: {sessions: number; recipes: number; journal: number; memory: number}; bytes: number; active: boolean};
type DataReport = {items: UserData[]; legacy_data_present: boolean; notice: string};
const tabs: [Tab, string, string][] = [['overview', '用量与成本', 'chart-no-axes-combined'], ['users', '用户与额度', 'users-round'], ['pricing', '模型计价', 'coins'], ['models', '模型与运行', 'sliders-horizontal'], ['data', '数据管理', 'database'], ['audit', '操作记录', 'list-checks']];
const cleanSummary: Summary = {turns: 0, total_tokens: 0, input_tokens: 0, output_tokens: 0, cost_micros: 0, incomplete_turns: 0, estimated_tokens: 0, estimated_cost_micros: 0};
export function AdminPanel({user, refreshAccount, notify, onClose}: {user: AccountUser; refreshAccount: () => Promise<void>; notify: (message: string) => void; onClose: () => void}) {
  const [tab, setTab] = useState<Tab>('overview'), [users, setUsers] = useState<AccountUser[]>([]), [usage, setUsage] = useState<UsageReport | null>(null), [pricing, setPricing] = useState<Pricing | null>(null), [audit, setAudit] = useState<AuditRow[]>([]), [data, setData] = useState<DataReport | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [query, setQuery] = useState(''), [edit, setEdit] = useState<AccountUser | null>(null), [reset, setReset] = useState<AccountUser | null>(null);
  const [generation, setGeneration] = useState(0);
  const [policy, setPolicy] = useState<RegistrationPolicyValue | null>(null);
  const [usageUser, setUsageUser] = useState<AccountUser | null>(null);
  const usageButtons = useRef(new Map<string, HTMLButtonElement>()), closedUsageUser = useRef<string | null>(null);
  const alive = useRef(true), sequence = useRef(0);
  const reload = useCallback(() => setGeneration(value => value + 1), []);
  useEffect(() => {if (!usageUser && !loading && closedUsageUser.current) {usageButtons.current.get(closedUsageUser.current)?.focus(); closedUsageUser.current = null;}}, [usageUser, loading]);
  useEffect(() => {alive.current = true; return () => {alive.current = false; sequence.current++;};}, []);
  useEffect(() => {
    const controller = new AbortController(), version = ++sequence.current;
    setError(''); setLoading(true);
    const load = async () => {
      if (tab === 'models') return;
      if (tab === 'overview') {const value = await api<UsageReport>('/api/admin/usage', {signal: controller.signal}); if (!controller.signal.aborted) {setUsage(value); setPricing(value.pricing);}}
      else if (tab === 'pricing') {const value = await api<Pricing>('/api/admin/pricing', {signal: controller.signal}); if (!controller.signal.aborted) setPricing(value);}
      else if (tab === 'audit') {const [value, accounts] = await Promise.all([api<{items: AuditRow[]}>('/api/admin/audit', {signal: controller.signal}), api<{items: AccountUser[]}>('/api/admin/users', {signal: controller.signal})]); if (!controller.signal.aborted) {setAudit(value.items); setUsers(accounts.items);}}
      else {
        const [accounts, report, registration] = await Promise.all([api<{items: AccountUser[]}>('/api/admin/users', {signal: controller.signal}), tab === 'data' ? api('/api/admin/data', {signal: controller.signal}) : Promise.resolve(null), tab === 'users' ? api<RegistrationPolicyValue>('/api/admin/policy', {signal: controller.signal}) : Promise.resolve(null)]);
        if (!controller.signal.aborted) {setUsers(accounts.items); if (report) setData(report); if (registration) setPolicy(registration);}
      }
    };
    void load().catch(reason => {if (!controller.signal.aborted && version === sequence.current) setError(reason.message);}).finally(() => {if (!controller.signal.aborted && version === sequence.current) setLoading(false);});
    return () => controller.abort();
  }, [tab, generation]);
  const changed = async (next?: AccountUser) => {
    setEdit(null); reload();
    if (next?.id === user.id) await refreshAccount();
    notify('用户设置已保存。');
  };
  const summary = usage?.summary || cleanSummary;
  const shownUsers = users.filter(item => `${item.username} ${item.display_name}`.toLocaleLowerCase().includes(query.toLocaleLowerCase().trim()));
  const selected = tabs.find(item => item[0] === tab)!;
  const usageCurrency = usage?.pricing?.currency || 'CNY';
  const unpriced = usage?.pricing && [usage.pricing.input_per_million, usage.pricing.output_per_million, usage.pricing.cached_input_per_million].every(value => Number(value) === 0);
  const userNames = new Map(users.map(item => [item.id, `@${item.username}`]));
  const auditUser = (id: string, fallback: string) => !id ? fallback : userNames.get(id) || `${id.slice(0, 8)}…`;
  const chooseTab = (next: Tab) => {const resume = () => {setTab(next); setQuery('');}; const event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin', resume}}); if (window.dispatchEvent(event)) resume();};
  const requestReload = () => {const resume = () => {setPolicy(null); reload();}; const event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin-refresh', resume}}); if (window.dispatchEvent(event)) resume();};
  const openUsage = (item: AccountUser) => {const resume = () => {setPolicy(null); reload(); setUsageUser(item);}; const event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin-usage', resume}}); if (window.dispatchEvent(event)) setUsageUser(item);};
  return <section className="admin-page" data-tab={tab} aria-label="ChefMate 管理平台"><nav className="admin-nav" aria-label="管理分类"><div className="admin-nav-title"><Icon name="shield-check"/><span>管理平台</span></div>{tabs.map(([id, label, icon]) => <button type="button" key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => chooseTab(id)}><Icon name={icon}/><span>{label}</span></button>)}</nav>
    <div className="admin-workspace"><header className="account-page-heading"><div><p className="account-eyebrow">ChefMate · 管理员</p><h1>{selected[1]}</h1><p>{({overview: '从真实请求记录了解使用情况与估算成本。', users: '分配额度，管理账号状态与开发者权限。', pricing: '按模型服务的计价设置单价，后续请求使用新单价。', models: '统一设置这套服务使用的模型、工具和执行参数。', data: '查看数据状态，按用户备份并重置厨房资料。', audit: '查看账号、权限、额度与计价的管理记录。'} as Record<Tab, string>)[tab]}</p></div>{tab !== 'models' && <button type="button" className="secondary-button" disabled={loading} onClick={requestReload}><Icon name="refresh-cw"/>{loading ? '读取中…' : '刷新'}</button>}</header>
      {error && <div className="error-box" role="alert">{error}<button type="button" onClick={requestReload}>重新读取</button></div>}
      {loading && tab !== 'models' && <p className="panel-loading" role="status">正在读取管理信息…</p>}
      {!loading && !error && tab === 'overview' && usage && <>
        <dl className="account-metrics admin-summary"><div><dt>模型已报告 Token</dt><dd>{tokens(summary.total_tokens)}</dd><small>输入 {tokens(summary.input_tokens)} · 输出 {tokens(summary.output_tokens)}</small></div><div><dt>计入金额（含管理员核对）</dt><dd>{money(summary.cost_micros, usageCurrency)}</dd><small>{summary.reconciled_turns ? `${tokens(summary.reconciled_turns)} 轮已人工核对 · ` : ''}原报告金额 {money(summary.reported_cost_micros ?? summary.cost_micros, usageCurrency)}</small></div><div><dt>请求轮数</dt><dd>{tokens(summary.turns)}</dd><small>{summary.incomplete_turns ? `${tokens(summary.incomplete_turns)} 轮用量报告不完整` : '当前记录的用量报告完整'}</small></div></dl>
        {summary.incomplete_turns > 0 && <p className="account-notice">模型未完整报告的请求共 {tokens(summary.incomplete_turns)} 轮，原未知用量预估 {tokens(summary.estimated_tokens || 0)} Token。当前未核对金额预估 {money(summary.estimated_cost_micros || 0, usageCurrency)} 已占用金额额度；核对金额不会修改原 Token 报告。</p>}
        {usage.pricing.configured === false ? <p className="account-notice">管理员尚未确认模型计价，有金额额度的用户暂时不能发起模型请求。<button type="button" className="text-button" onClick={() => chooseTab('pricing')}>设置并确认计价</button></p> : unpriced && <p className="account-notice">当前模型单价已设为零，后续请求不会扣减用户金额额度。这里的金额仍需与模型服务商实际账单核对。<button type="button" className="text-button" onClick={() => chooseTab('pricing')}>核对模型单价</button></p>}
        <section className="admin-section"><h2>近期每日已报告用量</h2>{usage.daily.length ? <div className="admin-daily-chart" role="img" aria-label={usage.daily.slice(-14).map(item => `${item.date}，已报告 ${tokens(item.total_tokens)} Token${item.estimated_tokens ? `，另计预估 ${tokens(item.estimated_tokens)} Token` : ''}`).join('；')}>{usage.daily.slice(-14).map(item => <div key={item.date}><span className="admin-daily-number">{tokens(item.total_tokens)}</span><div className="admin-daily-track"><span style={{height: `${item.total_tokens > 0 ? Math.max(2, item.total_tokens / Math.max(1, ...usage.daily.slice(-14).map(day => day.total_tokens)) * 100) : 0}%`}}/></div><small>{item.date.slice(5)}</small></div>)}</div> : <div className="admin-empty"><Icon name="chart-no-axes-combined"/><h3>从第一段对话开始积累</h3><p>用户发起模型请求后，这里会显示每日用量。</p></div>}</section>
        <section className="admin-section"><h2>按用户统计</h2><div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>用户</th><th>轮数</th><th>已报告 Token</th><th>计入金额</th><th>用量报告</th></tr></thead><tbody>{usage.users.map(item => <tr key={item.user.id}><th scope="row"><strong>{item.user.display_name || item.user.username}</strong><small>@{item.user.username}</small></th><td>{tokens(item.turns)}</td><td>{tokens(item.total_tokens)}{Boolean(item.estimated_tokens) && <small>另计预估 {tokens(item.estimated_tokens)}</small>}</td><td>{money(item.cost_micros, usageCurrency)}{Boolean(item.estimated_cost_micros) && <small>另计预估 {money(item.estimated_cost_micros, usageCurrency)}</small>}</td><td>{item.incomplete_turns ? `${tokens(item.incomplete_turns)} 轮不完整` : '完整'}</td></tr>)}{!usage.users.length && <tr><td colSpan={5} className="admin-table-empty">还没有模型用量记录。</td></tr>}</tbody></table></div></section>
      </>}
      {tab === 'users' && policy && <RegistrationPolicy policy={policy} onSaved={setPolicy}/>}
      {!loading && !error && tab === 'users' && <>
        <label className="admin-search"><Icon name="search"/><input aria-label="搜索用户" value={query} maxLength={80} onChange={event => setQuery(event.target.value)} placeholder="搜索账号或称呼"/></label>
        <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>用户</th><th>状态与权限</th><th>已用 / 总金额额度</th><th>预留金额</th><th>剩余额度</th><th><span className="visually-hidden">操作</span></th></tr></thead><tbody>{shownUsers.map(item => {const amounts = quotaAmounts(item.quota); return <tr key={item.id}>
          <th scope="row"><strong>{item.display_name || item.username}{item.id === user.id && <span className="ui-badge">你</span>}</strong><small>@{item.username}</small></th>
          <td><span className="ui-badge" data-state={item.disabled ? 'disabled' : 'active'}>{item.disabled ? '已停用' : '正常'}</span><span className="ui-badge">{item.role === 'admin' ? '管理员' : '用户'}</span>{item.developer_allowed && <small>开发者模式可用</small>}</td>
          <td>{money(amounts.charged)}<small>共 {money(item.quota.cost_limit_micros)}</small>{Boolean(item.quota.estimated_cost_micros) && <small>含预估 {money(item.quota.estimated_cost_micros)}</small>}</td>
          <td>{money(amounts.reserved)}</td><td>{money(amounts.remaining)}</td><td><div className="admin-user-actions"><button type="button" className="secondary-button" aria-label={`管理 ${item.username}`} onClick={() => setEdit(item)}>管理</button><button ref={button => {if (button) usageButtons.current.set(item.id, button); else usageButtons.current.delete(item.id);}} type="button" className="text-button" aria-label={`查看 ${item.username} 的用量明细`} onClick={() => openUsage(item)}>用量明细</button></div></td>
        </tr>;})}{!shownUsers.length && <tr><td colSpan={6} className="admin-table-empty">没有找到符合条件的用户。</td></tr>}</tbody></table></div>
        <p className="account-fineprint">金额额度是账号累计上限，按模型计价扣减。调整上限不会抹去历史金额；正在执行的请求会预留金额。</p>
      </>}
      {!loading && !error && tab === 'pricing' && pricing && <PricingEditor key={JSON.stringify(pricing)} pricing={pricing} onSaved={next => {setPricing(next); notify('模型单价已保存，后续请求使用新单价。');}}/>}
      {tab === 'models' && <Suspense fallback={<p className="panel-loading" role="status">正在打开模型设置…</p>}><GlobalSettings scope="admin" initialSection="model" apiBase="/api/admin/settings" onClose={onClose} onSaved={() => {notify('全局设置已保存。'); window.dispatchEvent(new CustomEvent('chefmate:settings-saved'));}}/></Suspense>}
      {!loading && !error && tab === 'data' && <><div className="account-notice"><Icon name="database"/>个人资料按账号隔离。重置前会先保存备份，原账号、额度与用量记录会保留。</div>{data && <div className="admin-data-overview"><h2>存储概览</h2><p>{tokens(users.length)} 个账号 · {tokens(data.items.filter(item => item.active).length)} 个厨房正在执行请求</p><p>{data.notice}</p>{data.legacy_data_present && <p className="account-notice">检测到旧工作台资料，已保留在原处。迁移方式见部署文档。</p>}</div>}<div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>用户</th><th>对话 / 菜谱</th><th>手记 / 记忆</th><th>本地附件占用</th><th>操作</th></tr></thead><tbody>{users.map(item => {const record = data?.items.find(entry => entry.user_id === item.id); return <tr key={item.id}><th scope="row"><strong>{item.display_name || item.username}</strong><small>@{item.username}</small></th><td>{tokens(record?.counts.sessions ?? 0)} / {tokens(record?.counts.recipes ?? 0)}</td><td>{tokens(record?.counts.journal ?? 0)} / {tokens(record?.counts.memory ?? 0)}</td><td>{record ? `${(record.bytes / 1024 / 1024).toLocaleString('zh-CN', {maximumFractionDigits: 2})} MB` : '尚未创建'}</td><td><button type="button" className="secondary-button" disabled={record?.active} aria-label={`备份并重置 ${item.username} 的数据`} onClick={() => setReset(item)}>{record?.active ? '有请求运行中' : '备份并重置'}</button></td></tr>;})}</tbody></table></div><p className="account-fineprint">全量服务备份与恢复方法见部署文档。重置不会删除共享菜谱库。</p></>}
      {!loading && !error && tab === 'audit' && <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>时间</th><th>操作</th><th>操作者</th><th>对象</th><th>修改字段</th></tr></thead><tbody>{audit.map((item, index) => <tr key={`${item.created_at}-${index}`}><td>{new Date(item.created_at).toLocaleString('zh-CN')}</td><td>{auditLabel(item.action)}</td><td className="admin-id" title={item.actor_id}>{auditUser(item.actor_id, '系统')}</td><td className="admin-id" title={item.target_id}>{auditUser(item.target_id, '全局')}</td><td>{item.fields.map(auditFieldLabel).join('、') || '—'}</td></tr>)}{!audit.length && <tr><td colSpan={5} className="admin-table-empty">还没有管理操作记录。</td></tr>}</tbody></table></div>}
    </div>
    {edit && <UserEditor user={edit} onClose={() => setEdit(null)} onSaved={changed}/>}
    {usageUser && <UsageDetails key={usageUser.id} user={usageUser} onClose={() => {closedUsageUser.current = usageUser.id; setUsageUser(null);}} onReconciled={next => {reload(); notify('核对金额已保存。'); if (next.id === user.id) void refreshAccount().catch(problem => notify(problem.message));}}/>}
    {reset && <DataReset user={reset} onClose={() => setReset(null)} onComplete={async () => {setReset(null); reload(); notify('已备份并重置这位用户的厨房资料。'); if (reset.id === user.id) window.location.reload();}}/>}
  </section>;
}

function auditLabel(action: string) {return ({'user.update': '更新用户', 'user.password_reset': '重置密码', 'pricing.update': '更新单价', 'user.register': '注册账号', 'admin.bootstrap': '初始化管理员', 'user.data_reset': '备份并重置数据', update_user: '更新用户', reset_password: '重置密码', update_pricing: '更新单价', update_policy: '更新注册与默认额度', migrate_monetary_budgets: '迁移为金额预算', register: '注册账号', bootstrap_admin: '初始化管理员', change_password: '更新密码', reset_data: '备份并重置数据', reconcile_usage: '核对用量金额', import_legacy_data: '导入旧数据'} as Record<string, string>)[action] || action;}
function auditFieldLabel(field: string) {return ({disabled: '账号状态', role: '角色', developer_allowed: '开发者权限', token_limit: '旧 Token 额度', legacy_token_budget: '旧 Token 额度迁移', cost_limit_micros: '金额额度', registration_enabled: '开放注册', default_token_limit: '旧新用户 Token 额度', default_cost_limit_micros: '新用户默认金额额度', input_per_million: '输入单价', output_per_million: '输出单价', cached_input_per_million: '缓存单价', configured: '计价已确认', final_cost_micros: '核对后金额', reason: '核对原因', legacy_owner: '旧单用户归属', onboarding_completed: '欢迎引导状态'} as Record<string, string>)[field] || field;}

function PricingEditor({pricing, onSaved}: {pricing: Pricing; onSaved: (pricing: Pricing) => void}) {
  const [draft, setDraft] = useState(pricing), [baseline, setBaseline] = useState(pricing), [busy, setBusy] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(false);
  const [conflict, setConflict] = useState(false), [reloadConfirmation, setReloadConfirmation] = useState(false);
  const alive = useRef(true), working = useRef(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(dirty, busy, setError);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  async function save() {
    if (working.current || conflict) return;
    working.current = true; setBusy(true); setError(''); setSaved(false);
    try {
      const next = await api<Pricing>('/api/admin/pricing', {method: 'PATCH', body: JSON.stringify({revision: baseline.revision, currency: draft.currency, input_per_million: draft.input_per_million, output_per_million: draft.output_per_million, cached_input_per_million: draft.cached_input_per_million})});
      if (alive.current) {setBaseline(next); setDraft(next); onSaved(next); setSaved(true);}
    } catch (reason) {
      if (alive.current) {setError((reason as Error).message); if ((reason as {status?: number}).status === 409) setConflict(true);}
    } finally {working.current = false; if (alive.current) setBusy(false);}
  }
  async function readLatest() {
    if (working.current) return;
    working.current = true; setBusy(true); setError('');
    try {
      const next = await api<Pricing>('/api/admin/pricing');
      if (alive.current) {setDraft(next); setBaseline(next); setConflict(false); setSaved(false); setReloadConfirmation(false);}
    } catch (reason) {if (alive.current) setError((reason as Error).message);}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  const allZero = [draft.input_per_million, draft.output_per_million, draft.cached_input_per_million].every(value => Number(value) === 0);
  return <><form className="admin-pricing-form" onSubmit={event => {event.preventDefault(); void save();}}>
    <section className="admin-section"><h2>按百万 Token 计价</h2><p className="account-fineprint">金额单位为人民币。单价由管理员填写，不代表模型服务商已核验的实际账单。</p>
      {baseline.configured === false && <p className="account-notice">模型计价尚未确认。设置并保存单价后，有金额额度的用户才能发起对话。</p>}
      {allZero && <p className="account-notice">当前单价均为零。确认保存后，后续模型请求按零金额计算，不会扣减用户金额额度。请先核对供应商计价。</p>}
      {([['input_per_million', '输入单价'], ['output_per_million', '输出单价'], ['cached_input_per_million', '缓存命中输入单价']] as const).map(([key, label]) => <label className="account-field" key={key}><span>{label}</span><div className="admin-price-input"><input aria-label={label} type="number" min="0" max="1000000" step="0.000001" required value={draft[key]} disabled={busy} onChange={event => {setDraft(previous => ({...previous, [key]: event.target.value})); setSaved(false);}}/><span>元 / 百万 Token</span></div></label>)}
      <div className="account-notice">每次请求会保存当时的计价快照。修改价格后，已有记录继续保留原来的金额。</div>
      {error && <p className="error-box" role="alert">{error}{conflict && <span>当前单价草稿已保留。</span>}</p>}{saved && <p className="account-success" role="status">单价已保存。</p>}
      <div className="account-form-actions"><button type="button" className="text-button" disabled={busy} onClick={() => dirty ? setReloadConfirmation(true) : void readLatest()}>重新读取单价</button><button type="submit" className="primary-button" disabled={busy || (!dirty && baseline.configured !== false) || conflict}>{busy ? '正在处理…' : baseline.configured === false ? '确认并启用计价' : '保存单价'}</button></div>
    </section>
  </form><AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
    {reloadConfirmation && <Dialog title="读取最新单价？" onClose={() => {if (!busy) setReloadConfirmation(false);}}><div className="account-leave-prompt"><p>这会放弃当前未保存的单价，读取服务器中的最新版本。</p>{error && <p className="error-box" role="alert">{error}</p>}<div className="account-form-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => setReloadConfirmation(false)}>保留草稿</button><button type="button" className="primary-button" disabled={busy} onClick={() => void readLatest()}>{busy ? '正在读取…' : '放弃并读取'}</button></div></div></Dialog>}
  </>;
}

function UserEditor({user, onClose, onSaved}: {user: AccountUser; onClose: () => void; onSaved: (user: AccountUser) => Promise<void>}) {
  const [disabled, setDisabled] = useState(user.disabled), [developer, setDeveloper] = useState(user.developer_allowed), [role, setRole] = useState(user.role);
  const [costLimit, setCostLimit] = useState(user.quota.cost_limit_micros == null ? '' : String(user.quota.cost_limit_micros / 1_000_000));
  const [password, setPassword] = useState(''), [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState('');
  const working = useRef(false), alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  async function save() {
    if (working.current) return;
    working.current = true; setBusy('save'); setError(''); setMessage('');
    try {const result = await api<{user: AccountUser}>(`/api/admin/users/${encodeURIComponent(user.id)}`, {method: 'PATCH', body: JSON.stringify({disabled, developer_allowed: developer, role, cost_limit_micros: costLimit === '' ? null : Math.round(Number(costLimit) * 1_000_000)})}); if (alive.current) await onSaved(result.user);}
    catch (reason) {if (alive.current) setError((reason as Error).message);}
    finally {working.current = false; if (alive.current) setBusy('');}
  }
  return <Dialog title={`管理 @${user.username}`} onClose={() => {if (!busy) onClose();}}><div className="admin-user-editor"><p className="account-fineprint">权限和额度由服务端执行。停用账号会撤销该用户的登录状态。</p><form onSubmit={event => {event.preventDefault(); void save();}}>
    <div className="account-field"><label htmlFor="admin-user-role">账号角色</label><Select id="admin-user-role" value={role} onValueChange={value => setRole(value as 'admin' | 'user')} disabled={Boolean(busy)} options={[{value: 'user', label: '用户'}, {value: 'admin', label: '管理员'}]}/></div><label className="admin-check"><span><strong>允许开发者模式</strong><small>可查看脱敏请求轨迹与工具调试信息。</small></span><input type="checkbox" role="switch" checked={developer} disabled={Boolean(busy)} onChange={event => setDeveloper(event.target.checked)}/></label><label className="admin-check"><span><strong>停用账号</strong><small>停用后不能登录或发起新的请求。</small></span><input type="checkbox" role="switch" checked={disabled} disabled={Boolean(busy)} onChange={event => setDisabled(event.target.checked)}/></label>
    <label className="account-field"><span>累计金额额度（元）</span><input aria-label="累计金额额度（元）" type="number" min="0" max="1000000000" step="0.000001" value={costLimit} disabled={Boolean(busy)} onChange={event => setCostLimit(event.target.value)} placeholder="留空为不限"/><small>已用金额 {money(quotaAmounts(user.quota).charged)} · 预留 {money(user.quota.reserved_cost_micros)}{Boolean(user.quota.estimated_cost_micros) && <> · 其中预估 {money(user.quota.estimated_cost_micros)}</>}</small></label>{error && <p className="error-box" role="alert">{error}</p>}<div className="account-form-actions"><button type="button" className="text-button" disabled={Boolean(busy)} onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={Boolean(busy)}>{busy === 'save' ? '正在保存…' : '保存用户设置'}</button></div></form>
    <details className="admin-password-reset"><summary>重置用户密码</summary><p className="account-fineprint">重置后，这个用户在所有设备上都需要重新登录。请通过你们约定的方式交付新密码。</p><form onSubmit={event => {event.preventDefault(); if (working.current) return; working.current = true; setBusy('password'); setError(''); setMessage(''); void api(`/api/admin/users/${encodeURIComponent(user.id)}/password`, {method: 'POST', body: JSON.stringify({new_password: password})}).then(() => {if (alive.current) {setPassword(''); setMessage('密码已重置，该用户需要重新登录。');}}).catch(reason => {if (alive.current) setError(reason.message);}).finally(() => {working.current = false; if (alive.current) setBusy('');});}}><label className="account-field"><span>新的临时密码</span><input type="password" autoComplete="new-password" minLength={10} maxLength={128} value={password} required disabled={Boolean(busy)} onChange={event => setPassword(event.target.value)}/></label>{message && <p className="account-success" role="status">{message}</p>}<button className="secondary-button" type="submit" disabled={Boolean(busy)}>{busy === 'password' ? '正在重置…' : '重置密码并撤销登录'}</button></form></details></div></Dialog>;
}

function DataReset({user, onClose, onComplete}: {user: AccountUser; onClose: () => void; onComplete: () => Promise<void>}) {
  const [confirmation, setConfirmation] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const working = useRef(false), alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  return <Dialog title={`备份并重置 @${user.username} 的数据`} onClose={() => {if (!busy) onClose();}}><form className="admin-data-reset" onSubmit={event => {event.preventDefault(); if (working.current || confirmation !== user.username) return; working.current = true; setBusy(true); setError(''); void api(`/api/admin/users/${encodeURIComponent(user.id)}/reset-data`, {method: 'POST', body: JSON.stringify({confirm_username: confirmation})}).then(async () => {if (alive.current) await onComplete();}).catch(reason => {if (alive.current) setError(reason.message);}).finally(() => {working.current = false; if (alive.current) setBusy(false);});}}><p>这会先备份，再清空这位用户的对话、厨房进度、个人菜谱、手记、图片、自定义记忆和助手总结。账号厨房档案、账号、累计用量和额度会保留；该用户需要重新登录。</p><p className="account-fineprint">有请求运行时不能重置。备份保存在服务的数据目录，可按部署文档恢复。</p><label className="account-field"><span>输入账号 {user.username} 确认</span><input autoComplete="off" value={confirmation} required maxLength={32} disabled={busy} onChange={event => setConfirmation(event.target.value)}/></label>{error && <p className="error-box" role="alert">{error}</p>}<div className="account-form-actions"><button type="button" className="text-button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={busy || confirmation !== user.username}>{busy ? '正在备份并重置…' : '备份并重置数据'}</button></div></form></Dialog>;
}
