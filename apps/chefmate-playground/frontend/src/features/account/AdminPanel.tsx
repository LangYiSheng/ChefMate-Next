import { useCallback, useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {usePageArrival} from '../settings/page-arrival';
import {Icon} from '../../components/Icon';
import {Dialog} from '../../components/Dialog';
import {Select} from '../../components/ui/Select';
import type {AccountUser, Pricing} from './types';
import {money, tokens, quotaAmounts} from './types';
import {ModelManager} from './ModelManager';
import {AdminConfigSection} from './AdminConfigSection';
import {AuditLog} from './AuditLog';
import {EntitlementManager} from './EntitlementManager';
import {RegistrationPolicy, type RegistrationPolicyValue} from './RegistrationPolicy';
import {UsageDetails} from './UsageDetails';
import './admin-layout.css';
import {AdminListControls, AdminPagination, paginateAdminRows} from './AdminListControls';


type Tab = 'overview' | 'users' | 'runtime' | 'context' | 'components' | 'models' | 'data' | 'audit';
type UsageRow = {user: AccountUser; turns: number; total_tokens: number; input_tokens: number; output_tokens: number; cost_micros: number; incomplete_turns: number; estimated_tokens: number; estimated_cost_micros: number; reported_cost_micros?: number; reconciled_turns?: number};
type Summary = Omit<UsageRow, 'user'>;
type UsageReport = {summary: Summary; users: UsageRow[]; daily: (Summary & {date: string})[]; pricing: Pricing};
type UserData = {user_id: string; username: string; counts: {sessions: number; recipes: number; journal: number; memory: number}; bytes: number; active: boolean};
type DataReport = {items: UserData[]; legacy_data_present: boolean; notice: string};
const tabs: [Tab, string, string][] = [['overview', '用量与成本', 'chart-no-axes-combined'], ['users', '用户与额度', 'users-round'], ['models', '模型连接与计价', 'plug'], ['runtime', '运行参数', 'sliders-horizontal'], ['context', '上下文', 'message-circle'], ['components', '工具与组件', 'layers'], ['data', '数据与状态', 'database'], ['audit', '操作记录', 'list-checks']];
const cleanSummary: Summary = {turns: 0, total_tokens: 0, input_tokens: 0, output_tokens: 0, cost_micros: 0, incomplete_turns: 0, estimated_tokens: 0, estimated_cost_micros: 0};
export function AdminPanel({user, refreshAccount, notify}: {user: AccountUser; refreshAccount: () => Promise<void>; notify: (message: string) => void; onClose: () => void}) {
  const [tab, setTab] = useState<Tab>('overview'), [users, setUsers] = useState<AccountUser[]>([]), [usage, setUsage] = useState<UsageReport | null>(null), [data, setData] = useState<DataReport | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [query, setQuery] = useState(''), [edit, setEdit] = useState<AccountUser | null>(null), [reset, setReset] = useState<AccountUser | null>(null), [fundsUser, setFundsUser] = useState<AccountUser | null>(null);
  const [listFilter, setListFilter] = useState(''), [roleFilter, setRoleFilter] = useState(''), [page, setPage] = useState(1), [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const autonomous = ['models', 'runtime', 'context', 'components', 'audit'].includes(tab);
  const [generation, setGeneration] = useState(0);
  const reading = useRef<HTMLDivElement>(null);
  usePageArrival(reading, `${tab}-${!autonomous && loading ? 'loading' : 'ready'}`);
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
      if (autonomous) return;
      if (tab === 'overview') {const value = await api<UsageReport>('/api/admin/usage', {signal: controller.signal}); if (!controller.signal.aborted) setUsage(value);}
      else {
        const [accounts, report, registration] = await Promise.all([api<{items: AccountUser[]}>('/api/admin/users', {signal: controller.signal}), tab === 'data' ? api('/api/admin/data', {signal: controller.signal}) : Promise.resolve(null), tab === 'users' ? api<RegistrationPolicyValue>('/api/admin/policy', {signal: controller.signal}) : Promise.resolve(null)]);
        if (!controller.signal.aborted) {setUsers(accounts.items); if (report) {setData(report); const health = await api<Record<string, any>>('/api/status', {signal: controller.signal}); if (!controller.signal.aborted) setStatus(health);} if (registration) setPolicy(registration);}
      }
    };
    void load().catch(reason => {if (!controller.signal.aborted && version === sequence.current) setError(reason.message);}).finally(() => {if (!controller.signal.aborted && version === sequence.current) setLoading(false);});
    return () => controller.abort();
  }, [tab, generation, autonomous]);
  const changed = async (next?: AccountUser) => {
    setEdit(null); reload();
    if (next?.id === user.id) await refreshAccount();
    notify('用户设置已保存。');
  };
  const summary = usage?.summary || cleanSummary;
  const matchesUser = (item: AccountUser) => `${item.username} ${item.display_name}`.toLocaleLowerCase().includes(query.toLocaleLowerCase().trim()) && (!roleFilter || item.role === roleFilter);
  const userRows = users.filter(item => matchesUser(item) && (!listFilter || (listFilter === 'disabled' ? item.disabled : listFilter === 'developer' ? item.developer_allowed : !item.disabled)));
  const usageRows = (usage?.users || []).filter(item => matchesUser(item.user) && (!listFilter || (listFilter === 'incomplete' ? item.incomplete_turns > 0 : listFilter === 'active' ? item.turns > 0 : item.turns === 0)));
  const dataByUser = new Map(data?.items.map(item => [item.user_id, item]));
  const dataRows = users.filter(item => {const record = dataByUser.get(item.id); return matchesUser(item) && (!listFilter || (listFilter === 'active' ? record?.active : listFilter === 'uncreated' ? !record : record && !record.active));});
  const listRows = tab === 'users' ? userRows : tab === 'data' ? dataRows : usageRows;
  const userPagination = paginateAdminRows(tab === 'data' ? dataRows : userRows, page, pageSize);
  const usagePagination = paginateAdminRows(usageRows, page, pageSize);
  const pagination = tab === 'overview' ? usagePagination : userPagination;
  const shownUsers = userPagination.items;
  const clearFilters = () => {setQuery(''); setListFilter(''); setRoleFilter(''); setPage(1);};
  const listLabel = tab === 'overview' ? '用户统计' : tab === 'data' ? '用户数据' : '用户';
  const controls = <AdminListControls label={listLabel} query={query} onQueryChange={value => {setQuery(value); setPage(1);}} onClear={clearFilters}>
    <label>账号角色<Select aria-label="账号角色筛选" value={roleFilter} onValueChange={value => {setRoleFilter(value); setPage(1);}} options={[{value: '', label: '全部角色'}, {value: 'user', label: '用户'}, {value: 'admin', label: '管理员'}]}/></label>
    <label>{tab === 'overview' ? '使用情况' : tab === 'data' ? '厨房状态' : '账号状态'}<Select aria-label="列表状态筛选" value={listFilter} onValueChange={value => {setListFilter(value); setPage(1);}} options={tab === 'overview' ? [{value: '', label: '全部使用情况'}, {value: 'active', label: '已有用量'}, {value: 'empty', label: '暂无用量'}, {value: 'incomplete', label: '报告待核对'}] : tab === 'data' ? [{value: '', label: '全部厨房'}, {value: 'active', label: '有请求运行中'}, {value: 'idle', label: '当前空闲'}, {value: 'uncreated', label: '尚未创建'}] : [{value: '', label: '全部状态'}, {value: 'enabled', label: '正常账号'}, {value: 'disabled', label: '已停用'}, {value: 'developer', label: '开发者模式可用'}]}/></label>
  </AdminListControls>;
  const listCount = <p className="admin-list-count" role="status">{query || listFilter || roleFilter ? '筛选结果' : '全部用户'} · 共 {listRows.length} 位</p>;
  const pager = <AdminPagination label={listLabel} page={pagination.page} pages={pagination.pages} pageSize={pageSize} onPage={setPage} onPageSize={value => {setPageSize(value); setPage(1);}}/>;
  const selected = tabs.find(item => item[0] === tab)!;
  const usageCurrency = usage?.pricing?.currency || 'CNY';
  const unpriced = usage?.pricing && [usage.pricing.input_per_million, usage.pricing.output_per_million, usage.pricing.cached_input_per_million].every(value => Number(value) === 0);
  const chooseTab = (next: Tab) => {if (next === tab) return; const resume = () => {if (!['models','runtime','context','components','audit'].includes(next)) setLoading(true); setTab(next); clearFilters();}; const event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin', resume}}); if (window.dispatchEvent(event)) resume();};
  const requestReload = () => {const resume = () => {setPolicy(null); reload();}; const event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin-refresh', resume}}); if (window.dispatchEvent(event)) resume();};
  const openUsage = (item: AccountUser) => {const resume = () => {setPolicy(null); reload(); setUsageUser(item);}; const event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin-usage', resume}}); if (window.dispatchEvent(event)) setUsageUser(item);};
  return <section className="admin-page admin-console" data-tab={tab} aria-label="ChefMate 管理平台"><nav className="admin-nav" aria-label="管理分类"><div className="admin-nav-title"><Icon name="shield-check"/><span>管理平台</span></div>{tabs.map(([id, label, icon]) => <button type="button" key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => chooseTab(id)}><Icon name={icon}/><span>{label}</span></button>)}</nav>
    <div className="admin-workspace"><header className="account-page-heading admin-header" key={`heading-${tab}`}><div className="admin-heading-inner"><div><p className="account-eyebrow">ChefMate · 管理员</p><h1>{selected[1]}</h1><p>{({overview: '从真实请求记录了解使用情况与估算成本。', users: '分配额度，管理账号状态与开发者权限。', models: '保存多个模型连接，独立计价与测试，选择其中一个用于对话。', runtime: '配置请求时限、重试次数与工具执行上限。', context: '控制模型上下文与工具结果的大小。', components: '选择助手可以使用的工具与组件。', data: '查看数据状态，按用户备份并重置厨房资料。', audit: '查看账号、权限、额度与计价的管理记录。'} as Record<Tab, string>)[tab]}</p></div>{!autonomous && <button type="button" className="secondary-button" disabled={loading} onClick={requestReload}><Icon name="refresh-cw"/>{loading ? '读取中…' : '刷新'}</button>}</div></header>
      <div className="admin-content" key={tab}><div className="admin-reading" ref={reading}>
      {error && <div className="error-box" role="alert">{error}<button type="button" onClick={requestReload}>重新读取</button></div>}
      {loading && !autonomous && <p className="panel-loading" role="status">正在读取管理信息…</p>}
      {!loading && !error && tab === 'overview' && usage && <>
        <dl className="account-metrics admin-summary"><div><dt>模型已报告 Token</dt><dd>{tokens(summary.total_tokens)}</dd><small>输入 {tokens(summary.input_tokens)} · 输出 {tokens(summary.output_tokens)}</small></div><div><dt>计入金额（含管理员核对）</dt><dd>{money(summary.cost_micros, usageCurrency)}</dd><small>{summary.reconciled_turns ? `${tokens(summary.reconciled_turns)} 轮已人工核对 · ` : ''}原报告金额 {money(summary.reported_cost_micros ?? summary.cost_micros, usageCurrency)}</small></div><div><dt>请求轮数</dt><dd>{tokens(summary.turns)}</dd><small>{summary.incomplete_turns ? `${tokens(summary.incomplete_turns)} 轮用量报告不完整` : '当前记录的用量报告完整'}</small></div></dl>
        {summary.incomplete_turns > 0 && <p className="account-notice">模型未完整报告的请求共 {tokens(summary.incomplete_turns)} 轮，原未知用量预估 {tokens(summary.estimated_tokens || 0)} Token。当前未核对金额预估 {money(summary.estimated_cost_micros || 0, usageCurrency)} 已占用金额额度；核对金额不会修改原 Token 报告。</p>}
        {usage.pricing.configured === false ? <p className="account-notice">管理员尚未确认模型计价，有金额额度的用户暂时不能发起模型请求。<button type="button" className="text-button" onClick={() => chooseTab('models')}>设置并确认计价</button></p> : unpriced && <p className="account-notice">当前模型单价已设为零，后续请求不会扣减用户金额额度。这里的金额仍需与模型服务商实际账单核对。<button type="button" className="text-button" onClick={() => chooseTab('models')}>核对模型单价</button></p>}
        <section className="admin-section"><h2>近期每日已报告用量</h2>{usage.daily.length ? <div className="admin-daily-chart" role="img" aria-label={usage.daily.slice(-14).map(item => `${item.date}，已报告 ${tokens(item.total_tokens)} Token${item.estimated_tokens ? `，另计预估 ${tokens(item.estimated_tokens)} Token` : ''}`).join('；')}>{usage.daily.slice(-14).map(item => <div key={item.date}><span className="admin-daily-number">{tokens(item.total_tokens)}</span><div className="admin-daily-track"><span style={{height: `${item.total_tokens > 0 ? Math.max(2, item.total_tokens / Math.max(1, ...usage.daily.slice(-14).map(day => day.total_tokens)) * 100) : 0}%`}}/></div><small>{item.date.slice(5)}</small></div>)}</div> : <div className="admin-empty"><Icon name="chart-no-axes-combined"/><h3>从第一段对话开始积累</h3><p>用户发起模型请求后，这里会显示每日用量。</p></div>}</section>
        <section className="admin-section"><h2>按用户统计</h2>{controls}{listCount}<div className="admin-table-scroll" role="region" tabIndex={0} aria-label="管理数据表格"><table className="admin-table"><thead><tr><th>用户</th><th>轮数</th><th>已报告 Token</th><th>计入金额</th><th>用量报告</th></tr></thead><tbody>{usagePagination.items.map(item => <tr key={item.user.id}><th scope="row"><strong>{item.user.display_name || item.user.username}</strong><small>@{item.user.username}</small></th><td>{tokens(item.turns)}</td><td>{tokens(item.total_tokens)}{Boolean(item.estimated_tokens) && <small>另计预估 {tokens(item.estimated_tokens)}</small>}</td><td>{money(item.cost_micros, usageCurrency)}{Boolean(item.estimated_cost_micros) && <small>另计预估 {money(item.estimated_cost_micros, usageCurrency)}</small>}</td><td>{item.incomplete_turns ? `${tokens(item.incomplete_turns)} 轮不完整` : '完整'}</td></tr>)}{!usageRows.length && <tr><td colSpan={5} className="admin-table-empty">没有符合条件的用户用量。</td></tr>}</tbody></table></div>{pager}</section>
      </>}
      {tab === 'users' && policy && <details className="admin-policy-disclosure" inert={loading || Boolean(error)}><summary><Icon name="users-round"/><span><strong>注册与新用户额度</strong><small>{policy.registration_enabled ? '开放注册' : '已关闭注册'} · 点击调整新用户默认额度</small></span><Icon name="chevron-right"/></summary><RegistrationPolicy policy={policy} onSaved={setPolicy}/></details>}
      {!loading && !error && tab === 'users' && <>
        {controls}{listCount}
        <div className="admin-table-scroll" role="region" tabIndex={0} aria-label="管理数据表格"><table className="admin-table"><thead><tr><th>用户</th><th>状态与权限</th><th>累计计入金额</th><th>预留金额</th><th>剩余额度</th><th><span className="visually-hidden">操作</span></th></tr></thead><tbody>{shownUsers.map(item => {const amounts = quotaAmounts(item.quota); return <tr key={item.id}>
          <th scope="row"><strong>{item.display_name || item.username}{item.id === user.id && <span className="ui-badge">你</span>}</strong><small>@{item.username}</small></th>
          <td><span className="ui-badge" data-state={item.disabled ? 'disabled' : 'active'}>{item.disabled ? '已停用' : '正常'}</span><span className="ui-badge">{item.role === 'admin' ? '管理员' : '用户'}</span>{item.developer_allowed && <small>开发者模式可用</small>}</td>
          <td>{money(amounts.charged)}{Boolean(item.quota.estimated_cost_micros) && <small>含预估 {money(item.quota.estimated_cost_micros)}</small>}</td>
          <td>{money(amounts.reserved)}</td><td>{money(amounts.remaining)}</td><td><div className="admin-user-actions"><button type="button" className="secondary-button" aria-label={`管理 ${item.username}`} onClick={() => setEdit(item)}>管理</button><button type="button" className="secondary-button" aria-label={`管理 ${item.username} 的额度`} onClick={() => setFundsUser(item)}>额度</button><button ref={button => {if (button) usageButtons.current.set(item.id, button); else usageButtons.current.delete(item.id);}} type="button" className="text-button" aria-label={`查看 ${item.username} 的用量明细`} onClick={() => openUsage(item)}>用量明细</button></div></td>
        </tr>;})}{!shownUsers.length && <tr><td colSpan={6} className="admin-table-empty">没有找到符合条件的用户。</td></tr>}</tbody></table></div>
        {pager}<p className="account-fineprint">优先使用会员限时额度，再使用永久的新用户额度与充值余额。正在执行的请求会预留金额，窗口到期不会改变已有请求的归属。</p>
      </>}
      {tab === 'models' && <ModelManager key={generation} notify={notify}/>}
      {(['runtime', 'context', 'components'] as string[]).includes(tab) && <AdminConfigSection key={tab} section={tab as 'runtime'|'context'|'components'} notify={notify}/>}
      {!loading && !error && tab === 'data' && <><SystemStatus status={status}/><div className="account-notice"><Icon name="database"/>个人资料按账号隔离。重置前会先保存备份，原账号、额度与用量记录会保留。</div>{data && <div className="admin-data-overview"><h2>存储概览</h2><p>{tokens(users.length)} 个账号 · {tokens(data.items.filter(item => item.active).length)} 个厨房正在执行请求</p><p>{data.notice}</p>{data.legacy_data_present && <p className="account-notice">检测到旧工作台资料，已保留在原处。迁移方式见部署文档。</p>}</div>}{controls}{listCount}<div className="admin-table-scroll" role="region" tabIndex={0} aria-label="管理数据表格"><table className="admin-table"><thead><tr><th>用户</th><th>对话 / 菜谱</th><th>手记 / 记忆</th><th>本地附件占用</th><th>操作</th></tr></thead><tbody>{shownUsers.map(item => {const record = dataByUser.get(item.id); return <tr key={item.id}><th scope="row"><strong>{item.display_name || item.username}</strong><small>@{item.username}</small></th><td>{tokens(record?.counts.sessions ?? 0)} / {tokens(record?.counts.recipes ?? 0)}</td><td>{tokens(record?.counts.journal ?? 0)} / {tokens(record?.counts.memory ?? 0)}</td><td>{record ? `${(record.bytes / 1024 / 1024).toLocaleString('zh-CN', {maximumFractionDigits: 2})} MB` : '尚未创建'}</td><td><button type="button" className="secondary-button" disabled={record?.active} aria-label={`备份并重置 ${item.username} 的数据`} onClick={() => setReset(item)}>{record?.active ? '有请求运行中' : '备份并重置'}</button></td></tr>;})}{!shownUsers.length && <tr><td colSpan={5} className="admin-table-empty">没有找到符合条件的用户。</td></tr>}</tbody></table></div>{pager}<p className="account-fineprint">全量服务备份与恢复方法见部署文档。重置不会删除共享菜谱库。</p></>}
      {tab === 'audit' && <AuditLog key={generation}/>}
      </div></div>
    </div>
    {fundsUser && <EntitlementManager user={fundsUser} onClose={() => setFundsUser(null)} onChanged={async next => {reload(); if (next.id === user.id) await refreshAccount();}}/>}
    {edit && <UserEditor user={edit} onClose={() => setEdit(null)} onSaved={changed}/>}
    {usageUser && <UsageDetails key={usageUser.id} user={usageUser} onClose={() => {closedUsageUser.current = usageUser.id; setUsageUser(null);}} onReconciled={next => {reload(); notify('核对金额已保存。'); if (next.id === user.id) void refreshAccount().catch(problem => notify(problem.message));}}/>}
    {reset && <DataReset user={reset} onClose={() => setReset(null)} onComplete={async () => {setReset(null); reload(); notify('已备份并重置这位用户的厨房资料。'); if (reset.id === user.id) window.location.reload();}}/>}
  </section>;
}

function UserEditor({user, onClose, onSaved}: {user: AccountUser; onClose: () => void; onSaved: (user: AccountUser) => Promise<void>}) {
  const [disabled, setDisabled] = useState(user.disabled), [developer, setDeveloper] = useState(user.developer_allowed), [role, setRole] = useState(user.role);
  const [password, setPassword] = useState(''), [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState('');
  const working = useRef(false), alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  async function save() {
    if (working.current) return;
    working.current = true; setBusy('save'); setError(''); setMessage('');
    try {const result = await api<{user: AccountUser}>(`/api/admin/users/${encodeURIComponent(user.id)}`, {method: 'PATCH', body: JSON.stringify({disabled, developer_allowed: developer, role})}); if (alive.current) await onSaved(result.user);}
    catch (reason) {if (alive.current) setError((reason as Error).message);}
    finally {working.current = false; if (alive.current) setBusy('');}
  }
  return <Dialog title={`管理 @${user.username}`} onClose={() => {if (!busy) onClose();}}><div className="admin-user-editor"><p className="account-fineprint">权限和额度由服务端执行。停用账号会撤销该用户的登录状态。</p><form onSubmit={event => {event.preventDefault(); void save();}}>
    <div className="account-field"><label htmlFor="admin-user-role">账号角色</label><Select id="admin-user-role" value={role} onValueChange={value => setRole(value as 'admin' | 'user')} disabled={Boolean(busy)} options={[{value: 'user', label: '用户'}, {value: 'admin', label: '管理员'}]}/></div><label className="admin-check"><span><strong>允许开发者模式</strong><small>可查看脱敏请求轨迹与工具调试信息。</small></span><input type="checkbox" role="switch" checked={developer} disabled={Boolean(busy)} onChange={event => setDeveloper(event.target.checked)}/></label><label className="admin-check"><span><strong>停用账号</strong><small>停用后不能登录或发起新的请求。</small></span><input type="checkbox" role="switch" checked={disabled} disabled={Boolean(busy)} onChange={event => setDisabled(event.target.checked)}/></label>
    {error && <p className="error-box" role="alert">{error}</p>}<div className="account-form-actions"><button type="button" className="text-button" disabled={Boolean(busy)} onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={Boolean(busy)}>{busy === 'save' ? '正在保存…' : '保存用户设置'}</button></div></form>
    <details className="admin-password-reset"><summary>重置用户密码</summary><p className="account-fineprint">重置后，这个用户在所有设备上都需要重新登录。请通过你们约定的方式交付新密码。</p><form onSubmit={event => {event.preventDefault(); if (working.current) return; working.current = true; setBusy('password'); setError(''); setMessage(''); void api(`/api/admin/users/${encodeURIComponent(user.id)}/password`, {method: 'POST', body: JSON.stringify({new_password: password})}).then(() => {if (alive.current) {setPassword(''); setMessage('密码已重置，该用户需要重新登录。');}}).catch(reason => {if (alive.current) setError(reason.message);}).finally(() => {working.current = false; if (alive.current) setBusy('');});}}><label className="account-field"><span>新的临时密码</span><input type="password" autoComplete="new-password" minLength={10} maxLength={128} value={password} required disabled={Boolean(busy)} onChange={event => setPassword(event.target.value)}/></label>{message && <p className="account-success" role="status">{message}</p>}<button className="secondary-button" type="submit" disabled={Boolean(busy)}>{busy === 'password' ? '正在重置…' : '重置密码并撤销登录'}</button></form></details></div></Dialog>;
}

function DataReset({user, onClose, onComplete}: {user: AccountUser; onClose: () => void; onComplete: () => Promise<void>}) {
  const [confirmation, setConfirmation] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const working = useRef(false), alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  return <Dialog title={`备份并重置 @${user.username} 的数据`} onClose={() => {if (!busy) onClose();}}><form className="admin-data-reset" onSubmit={event => {event.preventDefault(); if (working.current || confirmation !== user.username) return; working.current = true; setBusy(true); setError(''); void api(`/api/admin/users/${encodeURIComponent(user.id)}/reset-data`, {method: 'POST', body: JSON.stringify({confirm_username: confirmation})}).then(async () => {if (alive.current) await onComplete();}).catch(reason => {if (alive.current) setError(reason.message);}).finally(() => {working.current = false; if (alive.current) setBusy(false);});}}><p>这会先备份，再清空这位用户的对话、厨房进度、个人菜谱、手记、图片、自定义记忆和助手总结。账号厨房档案、账号、累计用量和额度会保留；该用户需要重新登录。</p><p className="account-fineprint">有请求运行时不能重置。备份保存在服务的数据目录，可按部署文档恢复。</p><label className="account-field"><span>输入账号 {user.username} 确认</span><input autoComplete="off" value={confirmation} required maxLength={32} disabled={busy} onChange={event => setConfirmation(event.target.value)}/></label>{error && <p className="error-box" role="alert">{error}</p>}<div className="account-form-actions"><button type="button" className="text-button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={busy || confirmation !== user.username}>{busy ? '正在备份并重置…' : '备份并重置数据'}</button></div></form></Dialog>;
}

function SystemStatus({status}: {status: Record<string, any> | null}) {
  if (!status) return null;
  const database = status.database || {};
  return <section className="admin-section"><h2>服务与共享数据</h2><dl className="account-metrics"><div><dt>应用数据</dt><dd>{status.personal_storage === 'postgresql' ? 'PostgreSQL' : status.personal_storage === 'sqlite' ? 'SQLite' : '待就绪'}</dd><small>账号与个人业务库</small></div><div><dt>菜谱数据库</dt><dd>{database.ready ? '已连接' : '待就绪'}</dd><small>{database.recipe_count != null ? `${tokens(database.recipe_count)} 道菜谱` : '共享基础菜谱'}</small></div><div><dt>模型</dt><dd>{status.model_configured ? '已配置' : '待配置'}</dd><small>{status.model || '请在模型连接中配置'}</small></div></dl>{database.error && <p className="account-notice">{database.error}</p>}</section>;
}
