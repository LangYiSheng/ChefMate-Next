import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {AccountGate, switchBrowserAccount} from './AccountGate';
import {AdminPanel} from './AdminPanel';
import {AccountPanel} from './AccountPanel';
import type {AccountUser} from './types';

const {request, resetChef, resetWorkspace, resetPreferences, applyAppearance} = vi.hoisted(() => ({request: vi.fn(), resetChef: vi.fn(), resetWorkspace: vi.fn(), resetPreferences: vi.fn(), applyAppearance: vi.fn()}));
vi.mock('../../runtime/http', async () => {const actual = await vi.importActual('../../runtime/http'); return {...actual, api: request};});
vi.mock('../../runtime/store', () => ({chefStore: {resetForAccount: resetChef, dispose: vi.fn()}}));
vi.mock('../workspace/store', () => ({workspaceStore: {resetForAccount: resetWorkspace}}));
vi.mock('../preferences/client', () => ({resetUiPreferenceCache: resetPreferences}));
vi.mock('../settings/appearance', () => ({applyAppearance}));
const user = (changes: Partial<AccountUser> = {}): AccountUser => ({id: 'user-a', username: 'supper', display_name: '小晚', role: 'user', developer_allowed: false, disabled: false, onboarding_completed: true, created_at: '2026-10-27T09:00:00+00:00', profile: {cooking_level: 'beginner', household_size: 1, preferences: [], theme: 'system'}, quota: {cost_limit_micros: 5000000, used_tokens: 3000, used_cost_micros: 10000, reserved_tokens: 0, reserved_cost_micros: 0}, ...changes});
const authenticated = (account: AccountUser, _refresh: () => Promise<void>, logout: () => Promise<void>) => <section aria-label="私人厨房"><h1>{account.username} 的对话</h1><button type="button" onClick={() => void logout()}>退出测试账号</button></section>;
const login = () => {fireEvent.change(screen.getByLabelText('账号'), {target: {value: 'supper'}}); fireEvent.change(screen.getByLabelText('密码'), {target: {value: 'a-long-password'}}); fireEvent.submit(screen.getByRole('form', {name: '账号登录'}));};
beforeEach(() => {
  localStorage.clear(); request.mockReset(); resetChef.mockReset(); resetChef.mockResolvedValue(undefined); resetWorkspace.mockReset(); resetPreferences.mockReset(); applyAppearance.mockReset();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value() {this.setAttribute('open', '');}});
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value() {this.removeAttribute('open');}});
});
afterEach(() => vi.restoreAllMocks());

describe('Account boundary', () => {
  it('reads the latest wallet on entering the account page after earlier usage', async () => {
    let reads = 0; const updated = user({quota: {...user().quota, used_cost_micros: 500000}});
    request.mockImplementation(() => Promise.resolve({user: ++reads === 1 ? user() : updated}));
    render(<AccountGate>{(account, refresh, logout) => <AccountPanel section="quota" user={account} refresh={refresh} logout={logout} notify={vi.fn()}/>}</AccountGate>);
    expect(await screen.findByRole('heading', {name: '90%'})).toBeVisible(); expect(reads).toBe(2);
  });
  it('reports a real manual wallet refresh failure without claiming success', async () => {
    let reads = 0; const notify = vi.fn(); request.mockImplementation(() => ++reads <= 2 ? Promise.resolve({user: user()}) : Promise.reject(new Error('金额信息暂时无法读取')));
    render(<AccountGate>{(account, refresh, logout) => <AccountPanel section="quota" user={account} refresh={refresh} logout={logout} notify={notify}/>}</AccountGate>);
    await screen.findByRole('heading', {name: '99.8%'}); await waitFor(() => expect(reads).toBe(2)); fireEvent.click(screen.getByRole('button', {name: '读取最新'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('金额信息暂时无法读取'); expect(screen.queryByText('账号信息已更新。')).not.toBeInTheDocument(); expect(notify).not.toHaveBeenCalled();
  });
  it('ignores an older wallet response after a newer refresh has completed', async () => {
    let reads = 0, old!: (value: unknown) => void, latest!: (value: unknown) => void;
    request.mockImplementation(() => ++reads === 1 ? Promise.resolve({user: user()}) : new Promise(resolve => {if (reads === 2) old = resolve; else latest = resolve;}));
    render(<AccountGate>{account => <p aria-label="当前已用金额">{account.quota.used_cost_micros}</p>}</AccountGate>); await screen.findByLabelText('当前已用金额');
    act(() => window.dispatchEvent(new Event('focus'))); act(() => window.dispatchEvent(new Event('focus')));
    await act(async () => latest({user: user({quota: {...user().quota, used_cost_micros: 200000}})})); expect(screen.getByLabelText('当前已用金额')).toHaveTextContent('200000');
    await act(async () => old({user: user({quota: {...user().quota, used_cost_micros: 900000}})})); expect(screen.getByLabelText('当前已用金额')).toHaveTextContent('200000');
  });
  it('does not mount the workspace before authentication and preserves unrelated browser data', async () => {
    let release!: (value: unknown) => void;
    request.mockReturnValue(new Promise(resolve => {release = resolve;}));
    localStorage.setItem('chefmate:browser-account', 'previous-user'); localStorage.setItem('chefmate:react-drafts', 'private-draft'); localStorage.setItem('other-product', 'keep');
    const child = vi.fn(authenticated); render(<AccountGate>{child}</AccountGate>);
    expect(child).not.toHaveBeenCalled(); expect(screen.getByRole('status')).toHaveTextContent('正在打开');
    await act(async () => release({user: null}));
    await screen.findByRole('heading', {name: '欢迎回来'});
    expect(child).not.toHaveBeenCalled(); expect(resetChef).toHaveBeenCalledOnce(); expect(resetWorkspace).toHaveBeenCalledOnce();
    expect(localStorage.getItem('chefmate:react-drafts')).toBeNull(); expect(localStorage.getItem('other-product')).toBe('keep');
  });
  it('routes a newly registered account to onboarding before the private workspace', async () => {
    request.mockImplementation((path: string) => Promise.resolve(path === '/api/auth/me' ? {user: null} : {user: user({onboarding_completed: false})}));
    const child = vi.fn(authenticated); render(<AccountGate>{child}</AccountGate>); await screen.findByRole('heading', {name: '欢迎回来'});
    fireEvent.click(screen.getByRole('button', {name: '注册一个账号'}));
    fireEvent.change(screen.getByLabelText('账号'), {target: {value: 'supper'}}); fireEvent.change(screen.getByLabelText('密码'), {target: {value: 'a-long-password'}}); fireEvent.change(screen.getByLabelText('再确认一次密码'), {target: {value: 'a-long-password'}});
    fireEvent.submit(screen.getByRole('form', {name: '注册账号'}));
    await screen.findByRole('heading', {name: '欢迎来到你的厨房'}); expect(child).not.toHaveBeenCalled();
    expect(request).toHaveBeenCalledWith('/api/auth/register', expect.objectContaining({method: 'POST', body: JSON.stringify({username: 'supper', password: 'a-long-password'})}));
  });
  it('does not submit mismatched registration passwords and respects disabled registration', async () => {
    request.mockResolvedValue({user: null}); const view = render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('button', {name: '注册一个账号'});
    fireEvent.click(screen.getByRole('button', {name: '注册一个账号'})); fireEvent.change(screen.getByLabelText('密码'), {target: {value: 'a-long-password'}}); fireEvent.change(screen.getByLabelText('再确认一次密码'), {target: {value: 'another-password'}}); fireEvent.submit(screen.getByRole('form', {name: '注册账号'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('两次输入的密码不一致'); expect(request).toHaveBeenCalledOnce();
    view.unmount(); request.mockResolvedValue({user: null, registration_enabled: false}); render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('heading', {name: '欢迎回来'});
    expect(screen.queryByRole('button', {name: '注册一个账号'})).not.toBeInTheDocument();
  });
  it('saves onboarding choices, then opens the authenticated workspace', async () => {
    request.mockImplementation((path: string) => Promise.resolve({user: user({onboarding_completed: path !== '/api/auth/me'})}));
    render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('heading', {name: '欢迎来到你的厨房'}); fireEvent.click(screen.getByRole('button', {name: '一起开始吧'}));
    fireEvent.change(screen.getByLabelText('怎么称呼你'), {target: {value: '小厨'}}); fireEvent.click(screen.getByRole('radio', {name: /日常下厨/})); fireEvent.click(screen.getByRole('button', {name: '快手省时间'})); fireEvent.click(screen.getByRole('button', {name: '下一步'}));
    fireEvent.click(screen.getByRole('radio', {name: '安静的夜晚'})); fireEvent.click(screen.getByRole('button', {name: '进入我的厨房'}));
    await screen.findByRole('region', {name: '私人厨房'});
    const write = request.mock.calls.find(([path]) => path === '/api/auth/onboarding')!;
    expect(JSON.parse(write[1].body)).toEqual({display_name: '小厨', cooking_level: 'comfortable', household_size: 1, preferences: ['快手省时间'], theme: 'dark', use_for_ai: false});
    expect(localStorage.getItem('chefmate:browser-account')).toBe('user-a'); expect(JSON.parse(localStorage.getItem('chefmate:ui-settings')!)).toEqual({theme: 'dark'});
  });
  it('reloads the registration policy after logout instead of reusing the signed-in flag', async () => {
    let signedIn = true;
    request.mockImplementation((path: string) => {if (path === '/api/auth/logout') {signedIn = false; return Promise.resolve({ok: true});} return Promise.resolve({user: signedIn ? user() : null, registration_enabled: signedIn});});
    render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('region', {name: '私人厨房'}); fireEvent.click(screen.getByRole('button', {name: '退出测试账号'}));
    await screen.findByRole('heading', {name: '欢迎回来'}); await waitFor(() => expect(request.mock.calls.filter(([path]) => path === '/api/auth/me')).toHaveLength(2));
    expect(screen.queryByRole('button', {name: '注册一个账号'})).not.toBeInTheDocument(); expect(screen.queryByRole('region', {name: '私人厨房'})).not.toBeInTheDocument();
  });
  it('returns an open registration form to login when refreshed policy closes registration', async () => {
    request.mockResolvedValue({user: null, registration_enabled: true}); render(<AccountGate>{authenticated}</AccountGate>); fireEvent.click(await screen.findByRole('button', {name: '注册一个账号'}));
    fireEvent.change(screen.getByLabelText('密码'), {target: {value: 'unsaved-password'}}); request.mockResolvedValue({user: null, registration_enabled: false}); act(() => window.dispatchEvent(new Event('focus')));
    await screen.findByRole('form', {name: '账号登录'}); expect(screen.getByLabelText('密码')).toHaveValue(''); expect(screen.queryByRole('button', {name: '注册一个账号'})).not.toBeInTheDocument();
  });
  it('removes private content immediately on expiry and clears browser caches before another login', async () => {
    request.mockResolvedValue({user: user()}); localStorage.setItem('chefmate:browser-account', 'user-a');
    render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('region', {name: '私人厨房'});
    localStorage.setItem('chefmate:active-session', 'old-private-session');
    request.mockResolvedValue({user: null});
    act(() => window.dispatchEvent(new CustomEvent('chefmate:session-expired')));
    expect(screen.queryByRole('region', {name: '私人厨房'})).not.toBeInTheDocument();
    await screen.findByRole('heading', {name: '欢迎回来'}); expect(screen.getByText(/登录已过期/)).toBeInTheDocument(); expect(localStorage.getItem('chefmate:active-session')).toBeNull();
  });
  it('rechecks the current cookie before logging out after a stale request returns 401', async () => {
    request.mockResolvedValue({user: user()}); localStorage.setItem('chefmate:browser-account', 'user-a');
    render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('region', {name: '私人厨房'}); resetChef.mockClear();
    act(() => window.dispatchEvent(new CustomEvent('chefmate:session-expired')));
    expect(screen.queryByRole('region', {name: '私人厨房'})).not.toBeInTheDocument(); await screen.findByRole('region', {name: '私人厨房'});
    expect(screen.queryByRole('heading', {name: '欢迎回来'})).not.toBeInTheDocument(); expect(resetChef).not.toHaveBeenCalled();
  });
  it('ignores a stale session refresh that resolves after a successful login', async () => {
    let resolveOld!: (value: unknown) => void, reads = 0;
    request.mockImplementation((path: string) => path !== '/api/auth/me' ? Promise.resolve({user: user()}) : ++reads === 1 ? Promise.resolve({user: null}) : new Promise(resolve => {resolveOld = resolve;}));
    render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('heading', {name: '欢迎回来'});
    act(() => window.dispatchEvent(new Event('focus'))); login(); await screen.findByRole('region', {name: '私人厨房'});
    await act(async () => resolveOld({user: null})); expect(screen.getByRole('region', {name: '私人厨房'})).toBeInTheDocument();
  });
  it('keeps a returning account draft without clearing its browser state', async () => {
    localStorage.setItem('chefmate:browser-account', 'user-a'); localStorage.setItem('chefmate:react-drafts', 'same-user-draft');
    await switchBrowserAccount('user-a'); resetChef.mockClear(); localStorage.setItem('chefmate:react-drafts', 'same-user-draft'); await switchBrowserAccount('user-a'); expect(resetChef).not.toHaveBeenCalled(); expect(localStorage.getItem('chefmate:react-drafts')).toBe('same-user-draft');
  });
  it('keeps profile sharing off by default and only includes consent after explicit selection', async () => {
    request.mockImplementation((path: string) => Promise.resolve({user: user({onboarding_completed: path !== '/api/auth/me'})}));
    render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('heading', {name: '欢迎来到你的厨房'}); fireEvent.click(screen.getByRole('button', {name: '一起开始吧'}));
    const consent = screen.getByRole('switch', {name: '在对话中使用个人偏好与长期记忆'}); expect(consent).not.toBeChecked();
    expect(screen.getByText('开启后，厨房习惯、口味与长期记忆会发送给所配置的模型，可在个人偏好中统一关闭。称呼始终用于 ChefMate 对你的称呼。')).toBeInTheDocument(); fireEvent.click(consent); fireEvent.click(screen.getByRole('button', {name: '下一步'})); fireEvent.click(screen.getByRole('button', {name: '进入我的厨房'}));
    await screen.findByRole('region', {name: '私人厨房'});
    expect(JSON.parse(request.mock.calls.find(([path]) => path === '/api/auth/onboarding')![1].body).use_for_ai).toBe(true);
  });
  it('switches this tab to the current cookie account without deleting another tab\'s new account drafts', async () => {
    let current = user(); request.mockImplementation(() => Promise.resolve({user: current}));
    localStorage.setItem('chefmate:browser-account', current.id);
    render(<AccountGate>{authenticated}</AccountGate>); await screen.findByRole('heading', {name: 'supper 的对话'});
    current = user({id: 'user-b', username: 'second'}); localStorage.setItem('chefmate:browser-account', current.id); localStorage.setItem('chefmate:react-drafts', 'new-account-draft');
    act(() => window.dispatchEvent(new StorageEvent('storage', {key: 'chefmate:browser-account', oldValue: 'user-a', newValue: 'user-b'})));
    expect(screen.queryByRole('heading', {name: 'supper 的对话'})).not.toBeInTheDocument(); await screen.findByRole('heading', {name: 'second 的对话'});
    expect(resetChef).toHaveBeenLastCalledWith('user-b'); expect(localStorage.getItem('chefmate:react-drafts')).toBe('new-account-draft'); expect(localStorage.getItem('chefmate:browser-account')).toBe('user-b');
  });
  it('does not repeatedly clear a signed-in account when browser storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {throw new DOMException('Storage unavailable', 'SecurityError');});
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {throw new DOMException('Storage unavailable', 'SecurityError');});
    await switchBrowserAccount('storage-blocked-user'); resetChef.mockClear(); await switchBrowserAccount('storage-blocked-user'); expect(resetChef).not.toHaveBeenCalled();
  });
});

describe('Account and administrator controls', () => {
  it('protects a money-policy draft before opening a user ledger and uses the scoped ledger endpoint', async () => {
    const policy = {revision: 1, registration_enabled: true, default_cost_limit_micros: 5000000};
    request.mockImplementation((path: string) => Promise.resolve(path === '/api/admin/users' ? {items: [user()]} : path === '/api/admin/policy' ? policy : path === '/api/admin/users/user-a/usage' ? {user: user(), items: []} : {summary: {turns: 0}, users: [], daily: [], pricing: {currency: 'CNY'}}));
    render(<AdminPanel user={user({role: 'admin'})} refreshAccount={vi.fn()} notify={vi.fn()} onClose={vi.fn()}/>); fireEvent.click(screen.getByRole('button', {name: '用户与额度'}));
    fireEvent.change(await screen.findByLabelText('新用户默认金额额度（元）'), {target: {value: '7.5'}}); fireEvent.click(screen.getByRole('button', {name: '查看 supper 的用量明细'}));
    fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(request.mock.calls.some(([path]) => path === '/api/admin/users/user-a/usage')).toBe(false);
    fireEvent.click(screen.getByRole('button', {name: '查看 supper 的用量明细'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改'}));
    expect(await screen.findByRole('dialog', {name: '@supper 的用量明细'})).toBeVisible(); expect(await screen.findByText('还没有模型请求记录。')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '关闭明细'})); expect(await screen.findByLabelText('新用户默认金额额度（元）')).toHaveValue(5);
  });
  it('protects the registration draft during refresh and discards it only after explicit confirmation', async () => {
    const policy = {revision: 1, registration_enabled: true, default_cost_limit_micros: 5000000};
    request.mockImplementation((path: string) => Promise.resolve(path === '/api/admin/users' ? {items: [user()]} : path === '/api/admin/policy' ? policy : {summary: {turns: 0}, users: [], daily: [], pricing: {currency: 'CNY'}}));
    render(<AdminPanel user={user({role: 'admin'})} refreshAccount={vi.fn()} notify={vi.fn()} onClose={vi.fn()}/>); fireEvent.click(screen.getByRole('button', {name: '用户与额度'}));
    fireEvent.change(await screen.findByLabelText('新用户默认金额额度（元）'), {target: {value: '7.5'}}); fireEvent.click(screen.getByRole('button', {name: '刷新'}));
    fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(screen.getByLabelText('新用户默认金额额度（元）')).toHaveValue(7.5);
    fireEvent.click(screen.getByRole('button', {name: '刷新'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改'}));
    await waitFor(() => expect(screen.getByLabelText('新用户默认金额额度（元）')).toHaveValue(5));
    expect(request.mock.calls.filter(([path]) => path === '/api/admin/policy')).toHaveLength(2);
  });
  it('keeps a registration draft while refreshing the user list after a user quota save', async () => {
    const policy = {revision: 1, registration_enabled: true, default_cost_limit_micros: 5000000};
    request.mockImplementation((path: string, options?: RequestInit) => Promise.resolve(path === '/api/admin/users' ? {items: [user()]} : path === '/api/admin/policy' ? policy : options?.method === 'PATCH' ? {user: user()} : {summary: {turns: 0}, users: [], daily: [], pricing: {currency: 'CNY'}}));
    render(<AdminPanel user={user({role: 'admin'})} refreshAccount={vi.fn()} notify={vi.fn()} onClose={vi.fn()}/>); fireEvent.click(screen.getByRole('button', {name: '用户与额度'}));
    fireEvent.change(await screen.findByLabelText('新用户默认金额额度（元）'), {target: {value: '7.5'}}); fireEvent.click(screen.getByRole('button', {name: '管理 supper'}));
    fireEvent.click(within(screen.getByRole('dialog', {name: '管理 @supper'})).getByRole('button', {name: '保存用户设置'}));
    await waitFor(() => expect(request.mock.calls.filter(([path]) => path === '/api/admin/policy')).toHaveLength(2));
    expect(screen.getByLabelText('新用户默认金额额度（元）')).toHaveValue(7.5); expect(screen.getByRole('button', {name: '保存注册与默认额度'})).toBeEnabled();
  });
  it('keeps an unsaved account profile until the owner explicitly discards it', () => {
    render(<AccountPanel user={user()} refresh={vi.fn()} logout={vi.fn()} notify={vi.fn()}/>); fireEvent.click(screen.getByRole('button', {name: '编辑称呼'})); fireEvent.change(screen.getByLabelText('称呼'), {target: {value: '还没保存的称呼'}});
    const resume = vi.fn(), attempt = () => {let accepted = true; act(() => {accepted = window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'chat', resume}}));}); return accepted;};
    expect(attempt()).toBe(false); fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(screen.getByLabelText('称呼')).toHaveValue('还没保存的称呼'); expect(resume).not.toHaveBeenCalled();
    expect(attempt()).toBe(false); fireEvent.click(screen.getByRole('button', {name: '放弃修改'})); expect(resume).toHaveBeenCalledOnce();
  });
  it('protects a profile draft on account refresh and reads once after an explicit discard', async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<AccountPanel user={user()} refresh={refresh} logout={vi.fn()} notify={vi.fn()}/>); await waitFor(() => expect(refresh).toHaveBeenCalledOnce()); refresh.mockClear(); fireEvent.click(screen.getByRole('button', {name: '编辑称呼'})); fireEvent.change(screen.getByLabelText('称呼'), {target: {value: '未保存的小厨'}});
    fireEvent.click(screen.getByRole('button', {name: '读取最新'})); fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(refresh).not.toHaveBeenCalled(); expect(screen.getByLabelText('称呼')).toHaveValue('未保存的小厨');
    fireEvent.click(screen.getByRole('button', {name: '读取最新'})); fireEvent.click(screen.getByRole('button', {name: '放弃并读取'})); await waitFor(() => expect(refresh).toHaveBeenCalledOnce()); expect(screen.getByLabelText('称呼')).toHaveValue('小晚');
  });
  it('keeps a local profile draft while a new wallet and remote profile arrive', () => {
    const refresh = vi.fn().mockResolvedValue(undefined), view = render(<AccountPanel user={user()} refresh={refresh} logout={vi.fn()} notify={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button', {name: '编辑称呼'})); fireEvent.change(screen.getByLabelText('称呼'), {target: {value: '未保存的小厨'}});
    view.rerender(<AccountPanel user={user({display_name: '服务器新称呼', quota: {...user().quota, used_cost_micros: 500000}})} refresh={refresh} logout={vi.fn()} notify={vi.fn()}/>);
    expect(screen.getByLabelText('称呼')).toHaveValue('未保存的小厨'); expect(screen.getByRole('button', {name: '保存资料'})).toBeEnabled();
  });
  it('incorporates remote consent revocation while preserving an unrelated nickname draft', () => {
    const first = user({profile: {...user().profile, use_for_ai: true}}), refresh = vi.fn().mockResolvedValue(undefined);
    const view = render(<AccountPanel user={first} refresh={refresh} logout={vi.fn()} notify={vi.fn()}/>); fireEvent.click(screen.getByRole('button', {name: '编辑称呼'})); fireEvent.change(screen.getByLabelText('称呼'), {target: {value: '未保存的小厨'}});
    view.rerender(<AccountPanel user={{...first, profile: {...first.profile, use_for_ai: false}}} refresh={refresh} logout={vi.fn()} notify={vi.fn()}/>);
    expect(screen.getByLabelText('称呼')).toHaveValue('未保存的小厨'); expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  });
  it('marks an existing draft clean when a refreshed server profile has the same values', () => {
    const refresh = vi.fn().mockResolvedValue(undefined), view = render(<AccountPanel user={user()} refresh={refresh} logout={vi.fn()} notify={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button', {name: '编辑称呼'})); fireEvent.change(screen.getByLabelText('称呼'), {target: {value: '已经保存的小厨'}});
    view.rerender(<AccountPanel user={user({display_name: '已经保存的小厨'})} refresh={refresh} logout={vi.fn()} notify={vi.fn()}/>);
    let accepted = false; act(() => {accepted = window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'chat', resume: vi.fn()}}));});
    expect(accepted).toBe(true); expect(screen.queryByRole('dialog', {name: '保留未保存的修改？'})).not.toBeInTheDocument();
  });
  it('saves the nickname independently without rewriting kitchen preferences or their shared consent', async () => {
    request.mockResolvedValue({user: user()}); const account = user(); account.profile = {...account.profile, use_for_ai: true, preferences: ['喜欢家常菜']};
    render(<AccountPanel user={account} refresh={vi.fn()} logout={vi.fn()} notify={vi.fn()}/>);
    expect(screen.queryByRole('switch')).not.toBeInTheDocument(); expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '编辑称呼'})); expect(screen.getByText(/不受个人偏好开关影响/)).toBeVisible(); fireEvent.change(screen.getByLabelText('称呼'), {target: {value: '新的称呼'}}); fireEvent.click(screen.getByRole('button', {name: '保存资料'}));
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/auth/profile', {method: 'PATCH', body: JSON.stringify({display_name: '新的称呼'})}));
  });
  it('keeps nickname editing compact and retains Escape drafts while explicit Cancel discards them', () => {
    render(<AccountPanel user={user()} refresh={vi.fn().mockResolvedValue(undefined)} notify={vi.fn()}/>);
    expect(screen.queryByLabelText('称呼')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'编辑称呼'}));
    fireEvent.change(screen.getByLabelText('称呼'),{target:{value:'临时称呼'}});
    expect(screen.getByRole('heading',{name:'小晚'})).toBeVisible();
    fireEvent(screen.getByRole('dialog',{name:'编辑称呼'}),new Event('cancel',{bubbles:true,cancelable:true}));
    expect(screen.queryByRole('dialog',{name:'编辑称呼'})).not.toBeInTheDocument();
    expect(screen.getByLabelText('有未保存修改')).toBeVisible();
    fireEvent.click(screen.getByRole('button',{name:'编辑称呼'}));
    expect(screen.getByLabelText('称呼')).toHaveValue('临时称呼');
    fireEvent.click(screen.getByRole('button',{name:'取消'}));
    expect(screen.queryByRole('dialog',{name:'编辑称呼'})).not.toBeInTheDocument();
    expect(screen.queryByLabelText('有未保存修改')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'编辑称呼'})); expect(screen.getByLabelText('称呼')).toHaveValue('小晚');
  });
  it('keeps a failed nickname write in one editable dialog with one error', async () => {
    request.mockImplementation((path: string) => path === '/api/auth/profile' ? Promise.reject(new Error('保存暂时失败')) : Promise.resolve({daily:[]}));
    render(<AccountPanel user={user()} refresh={vi.fn().mockResolvedValue(undefined)} notify={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'编辑称呼'})); fireEvent.change(screen.getByLabelText('称呼'),{target:{value:'新的小厨'}});
    fireEvent.click(screen.getByRole('button',{name:'保存资料'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('保存暂时失败'); expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.getByLabelText('称呼')).toHaveValue('新的小厨'); expect(screen.getByRole('button',{name:'保存资料'})).toBeEnabled();
    expect(screen.getByRole('heading',{name:'小晚'})).toBeVisible();
  });
  it('preserves a server-confirmed nickname when the follow-up account refresh fails', async () => {
    request.mockImplementation((path: string) => Promise.resolve(path === '/api/auth/profile' ? {user:user({display_name:'已经保存的小厨'})} : {daily:[]}));
    const refresh = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('账号同步断线'));
    render(<AccountPanel user={user()} refresh={refresh} notify={vi.fn()}/>);
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole('button',{name:'编辑称呼'})); fireEvent.change(screen.getByLabelText('称呼'),{target:{value:'已经保存的小厨'}});
    fireEvent.click(screen.getByRole('button',{name:'保存资料'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('称呼已保存，账号信息暂未同步');
    expect(screen.getByRole('heading',{name:'已经保存的小厨'})).toBeVisible(); expect(screen.getByLabelText('称呼')).toHaveValue('已经保存的小厨');
    fireEvent.click(screen.getByRole('button',{name:'取消'})); fireEvent.click(screen.getByRole('button',{name:'编辑称呼'}));
    expect(screen.getByLabelText('称呼')).toHaveValue('已经保存的小厨'); expect(screen.getByRole('button',{name:'保存资料'})).toBeDisabled();
  });
  it('keeps password confirmation and server refresh intact with the shared settings fields', async () => {
    request.mockResolvedValue({ok: true}); const refresh = vi.fn().mockResolvedValue(undefined); render(<AccountPanel section="security" user={user()} refresh={refresh} notify={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText('当前密码'), {target: {value: 'current-test-password'}}); fireEvent.change(screen.getByLabelText('新密码'), {target: {value: 'next-test-password'}}); fireEvent.change(screen.getByLabelText('确认新密码'), {target: {value: 'different-test-password'}});
    expect(screen.getByLabelText('新密码')).toHaveAttribute('minlength', '10'); fireEvent.submit(screen.getByRole('form', {name: '更新密码'})); expect(screen.getByRole('alert')).toHaveTextContent('两次新密码不一致'); expect(request).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('确认新密码'), {target: {value: 'next-test-password'}}); fireEvent.submit(screen.getByRole('form', {name: '更新密码'})); await screen.findByText('密码已更新。'); expect(request).toHaveBeenCalledWith('/api/auth/password', {method: 'PATCH', body: JSON.stringify({current_password: 'current-test-password', new_password: 'next-test-password'})});
    for (const label of ['当前密码', '新密码', '确认新密码']) expect(screen.getByLabelText(label)).toHaveValue('');
  });
  it('updates personal allowance only from confirmed usage, keeping reservations provisional', () => {
    const account = user(); account.quota = {...account.quota, cost_limit_micros: 10_000_000, used_cost_micros: 1_000_000, reserved_cost_micros: 1_000_000, estimated_tokens: 2000, estimated_cost_micros: 2_000_000, quota_used_cost_micros: 3_000_000};
    render(<AccountPanel section="quota" user={account} refresh={vi.fn()} logout={vi.fn()} notify={vi.fn()}/>);
    expect(screen.getByRole('heading', {name: '90%'})).toBeVisible(); expect(screen.getByRole('progressbar', {name: '基础额度可用比例'})).toHaveAttribute('aria-valuetext', '90%可用'); expect(screen.queryByText(/¥/)).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar', {name: /Token/})).not.toBeInTheDocument(); expect(screen.getByText(/预占额度不会提前减少/)).toBeInTheDocument();
  });
  it('edits user permissions separately from the dedicated monetary entitlement editor', async () => {
    const maximumQuotaUser = user({quota: {...user().quota, cost_limit_micros: 1_000_000_000_000_000}});
    request.mockImplementation((path: string, options?: RequestInit) => Promise.resolve(path === '/api/admin/users' ? {items: [maximumQuotaUser]} : options?.method === 'PATCH' ? {user: user({developer_allowed: true})} : {summary: {turns: 0}, users: [], daily: [], pricing: {currency: 'CNY'}}));
    render(<AdminPanel user={user({role: 'admin'})} refreshAccount={vi.fn()} notify={vi.fn()} onClose={vi.fn()}/>); fireEvent.click(screen.getByRole('button', {name: '用户与额度'})); await screen.findByRole('button', {name: '管理 supper'});
    fireEvent.click(screen.getByRole('button', {name: '管理 supper'})); const dialog = screen.getByRole('dialog', {name: '管理 @supper'});
    expect(within(dialog).queryByLabelText('累计金额额度（元）')).not.toBeInTheDocument(); expect(screen.getByRole('button', {name: '管理 supper 的额度'})).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/Token/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('switch', {name: /允许开发者模式/})); fireEvent.click(within(dialog).getByRole('button', {name: '保存用户设置'}));
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/admin/users/user-a', expect.objectContaining({method: 'PATCH', body: JSON.stringify({disabled: false, developer_allowed: true, role: 'user'})})));
  });
  it('requires the exact username before backing up and resetting personal data', async () => {
    request.mockImplementation((path: string) => Promise.resolve(path === '/api/admin/users' ? {items: [user()]} : path === '/api/admin/data' ? {items: [{user_id: 'user-a', username: 'supper', counts: {sessions: 2, recipes: 3, journal: 1, memory: 1}, bytes: 2048, active: false}], notice: '资料已隔离', legacy_data_present: false} : {summary: {turns: 0}, users: [], daily: [], pricing: {currency: 'CNY'}}));
    render(<AdminPanel user={user({id: 'admin-a', role: 'admin'})} refreshAccount={vi.fn()} notify={vi.fn()} onClose={vi.fn()}/>); fireEvent.click(screen.getByRole('button', {name: '数据与状态'})); fireEvent.click(await screen.findByRole('button', {name: '备份并重置 supper 的数据'}));
    const dialog = screen.getByRole('dialog', {name: '备份并重置 @supper 的数据'}), submit = within(dialog).getByRole('button', {name: '备份并重置数据'});
    expect(submit).toBeDisabled(); fireEvent.change(within(dialog).getByLabelText('输入账号 supper 确认'), {target: {value: 'wrong'}}); expect(submit).toBeDisabled(); fireEvent.change(within(dialog).getByLabelText('输入账号 supper 确认'), {target: {value: 'supper'}}); expect(submit).toBeEnabled(); fireEvent.click(submit);
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/admin/users/user-a/reset-data', expect.objectContaining({method: 'POST', body: JSON.stringify({confirm_username: 'supper'})})));
  });
  it('shows exhausted and unlimited money allowances without introducing Token limits', () => {
    const account = user(); account.quota = {...account.quota, cost_limit_micros: 1_000_000, used_cost_micros: 2_000_000};
    const view = render(<AccountPanel section="quota" user={account} refresh={vi.fn()} logout={vi.fn()} notify={vi.fn()}/>);
    expect(screen.getByRole('heading', {name: '0%'})).toBeVisible();
    view.rerender(<AccountPanel section="quota" user={{...account, quota: {...account.quota, cost_limit_micros: null}}} refresh={vi.fn()} logout={vi.fn()} notify={vi.fn()}/>);
    expect(screen.getByRole('heading', {name: '不限'})).toBeVisible(); expect(screen.queryByRole('progressbar')).not.toBeInTheDocument(); expect(screen.queryByText(/Token 额度|¥/)).not.toBeInTheDocument();
  });
  it('keeps provider observations and unreported-usage estimates visible as separate quantities', async () => {
    const pricing = {revision: 1, currency: 'CNY', input_per_million: '1', output_per_million: '2', cached_input_per_million: '0.1'}, summary = {turns: 2, total_tokens: 333, input_tokens: 300, output_tokens: 33, cost_micros: 1000, incomplete_turns: 1, estimated_tokens: 2000, estimated_cost_micros: 3000};
    request.mockResolvedValue({summary, users: [{user: user(), ...summary}], daily: [], pricing});
    render(<AdminPanel user={user({role: 'admin'})} refreshAccount={vi.fn()} notify={vi.fn()} onClose={vi.fn()}/>);
    expect(await screen.findByText(/模型未完整报告的请求共/)).toHaveTextContent('2,000 Token。当前未核对金额预估 ¥0.003'); expect(screen.getByText('模型已报告 Token')).toBeVisible(); expect(screen.getByText('另计预估 2,000')).toBeVisible(); expect(screen.getByText('另计预估 ¥0.003')).toBeVisible();
  });
});
