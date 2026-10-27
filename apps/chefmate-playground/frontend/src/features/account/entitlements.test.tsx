import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {EntitlementManager} from './EntitlementManager';
import {QuotaOverview} from './QuotaOverview';
import type {AccountUser, FundsOverview, MembershipFund} from './types';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const window = {starts_at: null, resets_at: null, limit_micros: 2_000_000, used_micros: 0, reserved_micros: 0, estimated_micros: 0, remaining_micros: 2_000_000};
const membership: MembershipFund = {id: 'member-1', label: 'ChefMate 会员', status: 'active', starts_at: '2026-10-27T00:00:00Z', expires_at: '2099-11-26T00:00:00Z', revoked_at: null, revision: 3, weekly_limit_micros: 10_000_000, five_hour_limit_micros: 2_000_000, available_micros: 2_000_000, week: {...window, limit_micros: 10_000_000, remaining_micros: 10_000_000}, five_hour: window};
const funds: FundsOverview = {currency: 'CNY', window_mode: 'first_use', available_micros: 5_000_000, deduction_order: ['membership', 'base', 'topup'], memberships: [], permanent: {total_micros: 5_000_000, used_micros: 0, reserved_micros: 0, estimated_micros: 0, remaining_micros: 5_000_000, overdrawn_micros: 0, sources: [{id: 'base', kind: 'base', label: '新用户基础额度', amount_micros: 5_000_000, used_micros: 0, reserved_micros: 0, estimated_micros: 0, remaining_micros: 5_000_000, overdrawn_micros: 0, created_at: '2026-10-27T00:00:00Z'}]}};
const user: AccountUser = {id: 'user-1', username: 'supper', display_name: '小晚', role: 'user', developer_allowed: false, disabled: false, onboarding_completed: true, created_at: '2026-10-27T00:00:00Z', profile: {}, quota: {funds, cost_limit_micros: 5_000_000, used_cost_micros: 0, reserved_cost_micros: 0, used_tokens: 0, reserved_tokens: 0}};
const report = (value = funds) => ({user: {...user, quota: {...user.quota, funds: value}}, entitlements: value});
beforeEach(() => {
  request.mockReset(); request.mockImplementation((path: string) => Promise.resolve(path === '/api/admin/membership-templates' ? {revision: 1, items: []} : report()));
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value() {this.setAttribute('open', '');}});
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value() {this.removeAttribute('open');}});
});

describe('Financial administration', () => {
  it('shows membership availability after both caps and keeps expired windows historical', () => {
    const limited = {...membership, available_micros: 0, week: {...membership.week, remaining_micros: 0}};
    const quota = {...user.quota, funds: {...funds, memberships: [limited]}};
    const view = render(<QuotaOverview quota={quota} presentation="amount" compact/>);
    expect(screen.getByText('本窗口可用 ¥0.00')).toBeVisible();
    expect(screen.getByRole('progressbar', {name: '会员本窗口额度占用'})).toHaveValue(2_000_000);
    const historical = {...limited, status: 'expired' as const, five_hour: {...window, resets_at: '2026-10-27T05:00:00Z'}};
    view.rerender(<QuotaOverview quota={{...quota, funds: {...funds, memberships: [historical]}}} presentation="amount"/>);
    expect(screen.getByText(/历史窗口截至/)).toBeVisible();
    expect(screen.queryByText(/重置/)).not.toBeInTheDocument();
  });

  it('grants configurable membership using money limits and an idempotency key', async () => {
    const changed = vi.fn().mockResolvedValue(undefined);
    render(<EntitlementManager user={user} onClose={vi.fn()} onChanged={changed}/>);
    fireEvent.click(await screen.findByRole('button', {name: /开通限时会员/}));
    fireEvent.change(screen.getByLabelText('每周额度（元）'), {target: {value: '12.345678'}});
    fireEvent.submit(screen.getByRole('form', {name: '开通会员'}));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    const body = JSON.parse(request.mock.calls.find(([, options]) => options?.method === 'POST')![1].body);
    expect(body).toMatchObject({label: 'ChefMate 会员', duration_days: 30, weekly_limit_micros: 12_345_678, five_hour_limit_micros: 2_000_000});
    expect(body.request_id).toMatch(/^[\da-f-]{36}$/);
    expect(screen.queryByRole('form', {name: '开通会员'})).not.toBeInTheDocument();
  });

  it('retries an unknown network result with the exact original payload', async () => {
    let attempts = 0;
    request.mockImplementation((_path, options) => options?.method === 'POST' && ++attempts === 1 ? Promise.reject(new Error('网络中断')) : Promise.resolve(report()));
    render(<EntitlementManager user={user} onClose={vi.fn()} onChanged={vi.fn().mockResolvedValue(undefined)}/>);
    fireEvent.click(await screen.findByRole('button', {name: /增加永久额度/}));
    fireEvent.submit(screen.getByRole('form', {name: '增加永久充值额度'}));
    await screen.findByRole('button', {name: '重试同一操作'});
    expect(screen.getByLabelText('充值金额（元）')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '重试同一操作'}));
    await screen.findByText('永久充值额度已增加。');
    const posts = request.mock.calls.filter(([, options]) => options?.method === 'POST');
    expect(posts).toHaveLength(2); expect(posts[0][1].body).toBe(posts[1][1].body);
  });

  it('keeps a saved grant committed if refreshing the parent account fails', async () => {
    render(<EntitlementManager user={user} onClose={vi.fn()} onChanged={vi.fn().mockRejectedValue(new Error('刷新失败'))}/>);
    fireEvent.click(await screen.findByRole('button', {name: /增加永久额度/}));
    fireEvent.submit(screen.getByRole('form', {name: '增加永久充值额度'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('额度已保存');
    expect(screen.queryByRole('form', {name: '增加永久充值额度'})).not.toBeInTheDocument();
    expect(request.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  });

  it('retains a conflicting revoke draft with its original revision and reason', async () => {
    request.mockImplementation((_path, options) => options?.method === 'POST' ? Promise.reject(Object.assign(new Error('会员状态已改变'), {status: 409})) : Promise.resolve(report({...funds, memberships: [membership]})));
    render(<EntitlementManager user={user} onClose={vi.fn()} onChanged={vi.fn().mockResolvedValue(undefined)}/>);
    fireEvent.click(await screen.findByRole('button', {name: '撤销会员'}));
    fireEvent.change(screen.getByLabelText('撤销原因'), {target: {value: '用户申请停用'}});
    fireEvent.submit(screen.getByRole('form', {name: '撤销会员'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('草稿已保留');
    expect(screen.getByLabelText('撤销原因')).toHaveValue('用户申请停用');
    expect(screen.getByRole('button', {name: '确认撤销会员'})).toBeDisabled();
    expect(JSON.parse(request.mock.calls.find(([, options]) => options?.method === 'POST')![1].body)).toEqual({revision: 3, reason: '用户申请停用'});
  });

  it('blocks double submissions and closing while a grant is pending', async () => {
    let done!: (value: unknown) => void;
    request.mockImplementation((_path, options) => options?.method === 'POST' ? new Promise(resolve => {done = resolve;}) : Promise.resolve(report()));
    const close = vi.fn(); render(<EntitlementManager user={user} onClose={close} onChanged={vi.fn().mockResolvedValue(undefined)}/>);
    fireEvent.click(await screen.findByRole('button', {name: /增加永久额度/}));
    const form = screen.getByRole('form', {name: '增加永久充值额度'});
    fireEvent.submit(form); fireEvent.submit(form);
    fireEvent(screen.getByRole('dialog', {name: '@supper 的额度管理'}), new Event('cancel', {cancelable: true}));
    expect(close).not.toHaveBeenCalled(); expect(screen.getByRole('alert')).toHaveTextContent('正在保存');
    expect(request.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
    await act(async () => done(report()));
  });
});
