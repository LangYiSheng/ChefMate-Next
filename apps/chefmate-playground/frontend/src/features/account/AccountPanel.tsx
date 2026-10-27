import {useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Icon} from '../../components/Icon';
import {Select} from '../../components/ui/Select';
import type {AccountUser} from './types';
import {money, tokens, quotaAmounts, PROFILE_PREFERENCES} from './types';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';
const profileKey = (user: AccountUser) => JSON.stringify([user.display_name, user.profile.household_size || 1, user.profile.cooking_level || 'beginner', user.profile.preferences || [], user.profile.use_for_ai === true]);

export function AccountPanel({user, refresh, logout, notify}: {user: AccountUser; refresh: () => Promise<void>; logout: () => Promise<void>; notify: (message: string) => void}) {
  const [name, setName] = useState(user.display_name), [household, setHousehold] = useState(user.profile.household_size || 1), [level, setLevel] = useState(user.profile.cooking_level || 'beginner');
  const [preferences, setPreferences] = useState(user.profile.preferences || []), [useForAi, setUseForAi] = useState(user.profile.use_for_ai === true);
  const serverProfileKey = profileKey(user);
  const [currentPassword, setCurrentPassword] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState('');
  const alive = useRef(true), working = useRef(false);
  const [baseline, setBaseline] = useState(serverProfileKey);
  const profileOwner = useRef(user.id), refreshRef = useRef(refresh); refreshRef.current = refresh;
  const draftKey = JSON.stringify([name, household, level, preferences, useForAi]), profileDirty = draftKey !== baseline;
  const adoptProfile = (next: AccountUser) => {setBaseline(profileKey(next)); profileOwner.current = next.id; setName(next.display_name); setHousehold(next.profile.household_size || 1); setLevel(next.profile.cooking_level || 'beginner'); setPreferences([...(next.profile.preferences || [])]); setUseForAi(next.profile.use_for_ai === true);};
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(profileDirty, Boolean(busy), setError, 'account');
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {
    if (profileOwner.current !== user.id || !profileDirty || serverProfileKey === draftKey) {adoptProfile(user); return;}
    // Keep edited fields, but incorporate server changes to untouched fields.
    // In particular, a balance refresh must not re-enable revoked AI consent.
    const previous = JSON.parse(baseline);
    if (name === previous[0]) setName(user.display_name);
    if (household === previous[1]) setHousehold(user.profile.household_size || 1);
    if (level === previous[2]) setLevel(user.profile.cooking_level || 'beginner');
    if (JSON.stringify(preferences) === JSON.stringify(previous[3])) setPreferences([...(user.profile.preferences || [])]);
    if (useForAi === previous[4]) setUseForAi(user.profile.use_for_ai === true);
    setBaseline(serverProfileKey);
  }, [user.id, serverProfileKey]);
  useEffect(() => {void (async () => {try {await refreshRef.current();} catch (problem) {if (alive.current && (problem as Error).name !== 'AbortError') setError((problem as Error).message);}})();}, [user.id]);
  async function run(kind: string, action: () => Promise<unknown>, text: string) {
    if (working.current) return;
    working.current = true; setBusy(kind); setError(''); setMessage('');
    try {await action(); if (alive.current && kind !== 'refresh') await refresh(); if (alive.current) {setMessage(text); notify(text);}}
    catch (reason) {if (alive.current && (reason as Error).name !== 'AbortError') setError((reason as Error).message);}
    finally {working.current = false; if (alive.current) setBusy('');}
  }
  const requestRefresh = () => {
    const resume = () => {
      adoptProfile(user);
      void run('refresh', refresh, '账号信息已更新。');
    };
    if (window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'account-refresh', resume}}))) resume();
  };
  const quota = user.quota, amounts = quotaAmounts(quota), used = amounts.charged + amounts.reserved;
  return <section className="account-page" aria-label="我的账号"><header className="account-page-heading"><div><p className="account-eyebrow">你的厨房，慢慢打理</p><h1>我的账号</h1><p>管理个人资料，查看额度与使用情况。</p></div><button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={requestRefresh}><Icon name="refresh-cw"/>刷新</button></header>
    {error && <p className="error-box" role="alert">{error}</p>}{message && <p className="account-success" role="status">{message}</p>}
    <div className="account-profile-heading"><span className="account-avatar" aria-hidden="true">{(user.display_name || user.username).slice(0, 1).toLocaleUpperCase()}</span><div><h2>{user.display_name || user.username}</h2><p>@{user.username} <span className="ui-badge">{user.role === 'admin' ? '管理员' : '厨房伙伴'}</span>{user.developer_allowed && <span className="ui-badge">可用开发者模式</span>}</p></div></div>
    <section className="account-section" aria-labelledby="account-quota-heading"><div className="account-section-heading"><div><h2 id="account-quota-heading">我的金额额度</h2><p>金额由管理员分配，每轮对话按模型单价结算。</p></div><Icon name="coins"/></div><dl className="account-metrics account-money-metrics"><div><dt>剩余额度</dt><dd>{money(amounts.remaining)}</dd><small>已扣除正在执行的预留金额</small></div><div><dt>总金额额度</dt><dd>{money(quota.cost_limit_micros)}</dd><small>管理员设置的累计上限</small></div><div><dt>已用金额</dt><dd>{money(amounts.charged)}</dd><small>已报告、预估与人工核对金额</small></div><div><dt>预留金额</dt><dd>{money(amounts.reserved)}</dd><small>请求结束后结算或释放</small></div></dl>
      {quota.cost_limit_micros != null && <div className="account-quota-progress"><progress aria-label="金额额度使用比例" value={Math.min(used, quota.cost_limit_micros)} max={Math.max(1, quota.cost_limit_micros)}/><span>剩余额度 {money(amounts.remaining)}</span></div>}
      {Boolean(quota.estimated_cost_micros) && <p className="account-notice">模型未完整报告的请求中，待核对的预估金额 {money(quota.estimated_cost_micros)} 已计入已用金额。管理员核对后会更新金额与余额，原用量报告保留。</p>}
      <p className="account-fineprint">金额按管理员设置的单价和模型用量计算。预留用于正在执行的请求，结束后会调整。累计已报告用量 {tokens(quota.used_tokens)} Token，仅作使用统计。</p>
    </section>
    <section className="account-section" aria-labelledby="account-profile-heading"><div className="account-section-heading"><div><h2 id="account-profile-heading">个人资料</h2><p>你可以在对话里随时补充这顿饭的具体要求。</p></div><Icon name="user-round"/></div><form className="account-profile-form" onSubmit={event => {event.preventDefault(); void run('profile', async () => {const result = await api<{user: AccountUser}>('/api/auth/onboarding', {method: 'PATCH', body: JSON.stringify({display_name: name.trim(), cooking_level: level, household_size: household, preferences, theme: user.profile.theme || 'system', use_for_ai: useForAi})}); if (alive.current) adoptProfile(result.user);}, '个人资料已保存。');}}>
      <label className="account-field"><span>称呼</span><input value={name} required maxLength={40} onChange={event => setName(event.target.value)} disabled={Boolean(busy)}/></label><div className="account-field"><label htmlFor="account-household">平时用餐人数</label><Select id="account-household" value={String(household)} onValueChange={value => setHousehold(Number(value))} options={Array.from({length: 20}, (_, index) => ({value: String(index + 1), label: `${index + 1} 人`}))} disabled={Boolean(busy)}/></div><div className="account-field"><label htmlFor="account-cooking-level">下厨经验</label><Select id="account-cooking-level" value={level} onValueChange={setLevel} options={[{value: 'beginner', label: '刚刚开始'}, {value: 'comfortable', label: '日常下厨'}, {value: 'confident', label: '厨房熟手'}]} disabled={Boolean(busy)}/></div><fieldset className="account-profile-preferences"><legend>厨房偏好 <small>可多选</small></legend><div>{PROFILE_PREFERENCES.map(choice => <button type="button" key={choice} disabled={Boolean(busy)} aria-pressed={preferences.includes(choice)} onClick={() => setPreferences(current => current.includes(choice) ? current.filter(item => item !== choice) : [...current, choice])}>{preferences.includes(choice) && <Icon name="check"/>}{choice}</button>)}</div></fieldset><label className="account-consent"><span><strong>将厨房档案用于对话建议</strong><small>开启后会发送给当前模型服务商。关闭并保存后，后续对话不再加入这份档案。</small></span><input type="checkbox" role="switch" aria-label="将厨房档案用于对话建议" checked={useForAi} disabled={Boolean(busy)} onChange={event => setUseForAi(event.target.checked)}/></label><div className="account-form-actions"><button className="primary-button" type="submit" disabled={Boolean(busy) || !name.trim()}>{busy === 'profile' ? '正在保存…' : '保存资料'}</button></div>
    </form></section>
    <section className="account-section" aria-labelledby="account-password-heading"><div className="account-section-heading"><div><h2 id="account-password-heading">账号安全</h2><p>更新密码后，其他设备需要重新登录。</p></div><Icon name="shield-check"/></div><form className="account-password-form" onSubmit={event => {event.preventDefault(); if (password !== confirm) {setError('两次新密码不一致，请重新核对。'); return;} void run('password', async () => {await api('/api/auth/password', {method: 'PATCH', body: JSON.stringify({current_password: currentPassword, new_password: password})}); setCurrentPassword(''); setPassword(''); setConfirm('');}, '密码已更新。');}}>
      <label className="account-field"><span>当前密码</span><input type="password" autoComplete="current-password" value={currentPassword} required maxLength={128} onChange={event => setCurrentPassword(event.target.value)} disabled={Boolean(busy)}/></label><label className="account-field"><span>新密码</span><input type="password" autoComplete="new-password" aria-label="新密码" value={password} required minLength={10} maxLength={128} onChange={event => setPassword(event.target.value)} disabled={Boolean(busy)}/><small>至少 10 位，请使用独立的密码。</small></label><label className="account-field"><span>确认新密码</span><input type="password" autoComplete="new-password" value={confirm} required minLength={10} maxLength={128} onChange={event => setConfirm(event.target.value)} disabled={Boolean(busy)}/></label><div className="account-form-actions"><button className="secondary-button" type="submit" disabled={Boolean(busy)}>{busy === 'password' ? '正在更新…' : '更新密码'}</button></div>
    </form></section>
    <footer className="account-signout"><div><h2>结束这次下厨</h2><p>退出后，已保存的厨房资料会保留；当前浏览器的未发送草稿会清除。</p></div><button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => {if (working.current) return; working.current = true; setBusy('logout'); void logout().catch(reason => {setError(reason.message); setBusy(''); working.current = false;});}}><Icon name="log-out"/>{busy === 'logout' ? '正在退出…' : '退出登录'}</button></footer>
    <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
  </section>;
}
