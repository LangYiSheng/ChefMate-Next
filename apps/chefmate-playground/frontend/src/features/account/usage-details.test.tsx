import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {UsageDetails, type UsageEntry} from './UsageDetails';
import type {AccountUser} from './types';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const user: AccountUser = {id: 'user-a', username: 'supper', display_name: '小晚', role: 'user', developer_allowed: false, disabled: false, onboarding_completed: true, profile: {}, created_at: '2026-10-27T10:00:00Z', quota: {cost_limit_micros: 5_000_000, used_tokens: 0, used_cost_micros: 0, reserved_tokens: 0, reserved_cost_micros: 0}};
const entry = (change: Partial<UsageEntry> = {}): UsageEntry => ({turn_id: 'turn-a', session_id: 'session-a', model: 'test-model', status: 'error', active: false, created_at: '2026-10-27T10:00:00Z', total_tokens: null, cost_micros: 10_000, estimated_cost_micros: 1_000_000, incomplete: true, effective_cost_micros: 10_000, effective_estimated_cost_micros: 1_000_000, reconciliation: null, ...change});
const base = '/api/admin/users/user-a/usage';
const open = async () => fireEvent.click(await screen.findByRole('button', {name: '核对 turn-a 的金额'}));
const fill = (amount = '0.123456', reason = ' 已核对供应商记录 ') => {fireEvent.change(screen.getByLabelText('核对后总金额（元）'), {target: {value: amount}}); fireEvent.change(screen.getByLabelText('核对原因'), {target: {value: reason}});};
beforeEach(() => {
  request.mockReset();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value() {this.setAttribute('open', '');}});
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value() {this.removeAttribute('open');}});
});

describe('Administrator usage reconciliation', () => {
  it('keeps raw unknown-usage estimates visible and blocks running or reserved requests', async () => {
    request.mockResolvedValue({user, items: [entry(), entry({turn_id: 'turn-running', status: 'running'}), entry({turn_id: 'turn-reserved', status: 'completed', active: true})]});
    render(<UsageDetails user={user} onClose={vi.fn()} onReconciled={vi.fn()}/>);
    expect(await screen.findAllByText('另计未知用量预估 ¥1.00')).toHaveLength(3);
    expect(screen.getByRole('button', {name: '核对 turn-running 的金额'})).toBeDisabled(); expect(screen.getByRole('button', {name: '核对 turn-reserved 的金额'})).toBeDisabled();
    expect(screen.getAllByText(/用量未报告/)).toHaveLength(3); expect(screen.queryByText('已报告 0 Token')).not.toBeInTheDocument();
  });
  it('saves exact micro amounts with a mandatory reason and keeps original records', async () => {
    const original = entry(), updated = {...original, effective_cost_micros: 123456, effective_estimated_cost_micros: 0, reconciliation: {revision: 1, final_cost_micros: 123456, reason: '已核对供应商记录', actor_id: 'admin-a', created_at: '2026-10-27T11:00:00Z'}};
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'POST' ? {ok: true, user, entry: updated} : {user, items: [original]}));
    const reconciled = vi.fn(); render(<UsageDetails user={user} onClose={vi.fn()} onReconciled={reconciled}/>); await open();
    expect(screen.getByRole('button', {name: '保存核对金额'})).toBeDisabled(); fill('0.123456', '   '); expect(screen.getByRole('button', {name: '保存核对金额'})).toBeDisabled(); fill(); fireEvent.click(screen.getByRole('button', {name: '保存核对金额'}));
    await waitFor(() => expect(reconciled).toHaveBeenCalledWith(user));
    expect(request).toHaveBeenCalledWith(`${base}/turn-a/reconcile`, {method: 'POST', body: JSON.stringify({revision: 0, final_cost_micros: 123456, reason: '已核对供应商记录'})});
    expect(screen.getByText('另计未知用量预估 ¥1.00')).toBeVisible(); expect(screen.getByText('¥0.123456')).toBeVisible(); expect(screen.getByText('已人工核对')).toBeVisible();
    expect(screen.queryByRole('form', {name: '核对本轮金额'})).not.toBeInTheDocument();
  });
  it('retains a conflicting draft and requires explicit reload before using the current revision', async () => {
    const fresh = entry({effective_cost_micros: 2_000_000, effective_estimated_cost_micros: 0, reconciliation: {revision: 4, final_cost_micros: 2_000_000, reason: '其他管理员已核对', actor_id: 'admin-b', created_at: '2026-10-27T11:00:00Z'}});
    let reads = 0, writes = 0; request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'POST' ? ++writes === 1 ? Promise.reject(Object.assign(new Error('记录已变化，请刷新后复核。'), {status: 409})) : Promise.resolve({ok: true, user, entry: {...fresh, reconciliation: {...fresh.reconciliation!, revision: 5}}}) : Promise.resolve({user, items: [++reads === 1 ? entry() : fresh]}));
    render(<UsageDetails user={user} onClose={vi.fn()} onReconciled={vi.fn()}/>); await open(); fill(); fireEvent.click(screen.getByRole('button', {name: '保存核对金额'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('当前金额和原因草稿已保留'); expect(screen.getByLabelText('核对后总金额（元）')).toHaveValue(0.123456); expect(screen.getByLabelText('核对原因')).toHaveValue(' 已核对供应商记录 '); expect(screen.getByRole('button', {name: '保存核对金额'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '读取最新明细'})); fireEvent.click(screen.getByRole('button', {name: '保留草稿'})); expect(reads).toBe(1);
    fireEvent.click(screen.getByRole('button', {name: '读取最新明细'})); fireEvent.click(screen.getByRole('button', {name: '放弃并读取'})); await waitFor(() => expect(screen.queryByRole('form', {name: '核对本轮金额'})).not.toBeInTheDocument());
    await open(); expect(screen.getByLabelText('核对后总金额（元）')).toHaveValue(2); expect(screen.getByText('其他管理员已核对')).toBeVisible(); fill('2.5', '重新核对账单'); fireEvent.click(screen.getByRole('button', {name: '保存核对金额'}));
    await waitFor(() => expect(writes).toBe(2)); expect(request.mock.calls.filter(([, options]) => options?.method === 'POST').map(([, options]) => JSON.parse(options.body).revision)).toEqual([0, 4]);
  });
  it('protects a draft when closing or leaving and allows an explicit discard', async () => {
    request.mockResolvedValue({user, items: [entry()]}); const close = vi.fn(); render(<UsageDetails user={user} onClose={close} onReconciled={vi.fn()}/>); await open(); fill();
    fireEvent.click(screen.getByRole('button', {name: '关闭明细'})); fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(close).not.toHaveBeenCalled(); expect(screen.getByLabelText('核对原因')).toHaveValue(' 已核对供应商记录 ');
    const resume = vi.fn(); let accepted = true; act(() => {accepted = window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'chat', resume}}));});
    expect(accepted).toBe(false); fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(resume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: '关闭明细'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改'})); expect(close).toHaveBeenCalledOnce();
  });
  it('blocks closing and repeated submissions while an actual save is in flight', async () => {
    let finish!: (value: unknown) => void; request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'POST' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve({user, items: [entry()]}));
    const close = vi.fn(); render(<UsageDetails user={user} onClose={close} onReconciled={vi.fn()}/>); await open(); fill('0', '供应商确认没有产生费用'); fireEvent.click(screen.getByRole('button', {name: '保存核对金额'}));
    expect(screen.getByRole('button', {name: '正在保存核对…'})).toBeDisabled(); const dialog = screen.getByRole('dialog', {name: '@supper 的用量明细'}); fireEvent(dialog, new Event('cancel', {cancelable: true})); expect(close).not.toHaveBeenCalled();
    expect(request.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
    await act(async () => finish({ok: true, user, entry: {...entry(), effective_cost_micros: 0, effective_estimated_cost_micros: 0}})); expect(screen.getByRole('status')).toHaveTextContent('累计金额与余额已更新');
  });
  it('recovers from a read error and shows an empty ledger without an editable record', async () => {
    request.mockRejectedValueOnce(new Error('暂时没有连接上')).mockResolvedValue({user, items: []}); render(<UsageDetails user={user} onClose={vi.fn()} onReconciled={vi.fn()}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时没有连接上'); fireEvent.click(screen.getByRole('button', {name: '读取最新明细'})); expect(await screen.findByText('还没有模型请求记录。')).toBeVisible(); expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });
  it('prevents editing a clean form during reload so a delayed read cannot discard newly typed input', async () => {
    let finish!: (value: unknown) => void; request.mockResolvedValueOnce({user, items: [entry()]}).mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    render(<UsageDetails user={user} onClose={vi.fn()} onReconciled={vi.fn()}/>); await open(); fireEvent.click(screen.getByRole('button', {name: '读取最新明细'}));
    expect(screen.getByLabelText('核对后总金额（元）')).toBeDisabled(); expect(screen.getByLabelText('核对原因')).toBeDisabled(); expect(screen.getByRole('button', {name: '保存核对金额'})).toBeDisabled();
    await act(async () => finish({user, items: [entry()]})); expect(screen.queryByRole('form')).not.toBeInTheDocument();
  });
  it('shows missing historical amounts and metadata honestly and locks records with unknown state', async () => {
    request.mockResolvedValue({user, items: [{turn_id: 'legacy-a', total_tokens: null, reconciliation: null}]}); render(<UsageDetails user={user} onClose={vi.fn()} onReconciled={vi.fn()}/>);
    expect(await screen.findByText('时间未记录')).toBeVisible(); expect(screen.getByText('未报告模型')).toBeVisible(); expect(screen.getByRole('button', {name: '核对 legacy-a 的金额'})).toBeDisabled();
    expect(screen.getAllByText('未报告')).toHaveLength(2); expect(screen.queryByText(/NaN|不限/)).not.toBeInTheDocument();
  });
  it('recovers from malformed ledger payloads with an explicit retry', async () => {
    request.mockResolvedValueOnce({user, items: null}).mockResolvedValue({user, items: []}); render(<UsageDetails user={user} onClose={vi.fn()} onReconciled={vi.fn()}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('用量明细格式不完整'); fireEvent.click(screen.getByRole('button', {name: '读取最新明细'})); expect(await screen.findByText('还没有模型请求记录。')).toBeVisible();
  });
});
