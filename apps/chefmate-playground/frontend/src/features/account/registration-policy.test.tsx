import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {RegistrationPolicy, type RegistrationPolicyValue} from './RegistrationPolicy';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const initial: RegistrationPolicyValue = {revision: 7, registration_enabled: true, default_cost_limit_micros: 5_000_000};
const moneyInput = () => screen.getByLabelText('新用户默认金额额度（元）');
const save = () => fireEvent.click(screen.getByRole('button', {name: '保存注册与默认额度'}));
beforeEach(() => {
  request.mockReset();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value() {this.setAttribute('open', '');}});
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value() {this.removeAttribute('open');}});
});

describe('Registration and future account money quotas', () => {
  it('saves the registration flag and exact future limits with the server revision', async () => {
    const saved = {...initial, revision: 8, registration_enabled: false, default_cost_limit_micros: 1_234_567};
    request.mockResolvedValue(saved); const onSaved = vi.fn(); render(<RegistrationPolicy policy={initial} onSaved={onSaved}/>);
    expect(screen.getByText(/默认额度只用于之后注册的新用户/)).toHaveTextContent('现有用户的额度保持不变');
    expect(screen.getByRole('button', {name: '保存注册与默认额度'})).toBeDisabled(); expect(screen.queryByLabelText(/Token/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', {name: '开放新用户注册'}));
    fireEvent.change(moneyInput(), {target: {value: ''}}); fireEvent.change(screen.getByLabelText('新用户默认金额额度（元）'), {target: {value: '1.234567'}}); save();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(request).toHaveBeenCalledWith('/api/admin/policy', {method: 'PATCH', body: JSON.stringify({revision: 7, registration_enabled: false, default_cost_limit_micros: 1_234_567})});
    expect(screen.getByRole('status')).toHaveTextContent('现有用户额度不变'); expect(screen.getByRole('button', {name: '保存注册与默认额度'})).toBeDisabled();
  });
  it('keeps an unlimited money allowance as null', async () => {
    request.mockResolvedValue({...initial, revision: 8, default_cost_limit_micros: null}); render(<RegistrationPolicy policy={initial} onSaved={vi.fn()}/>);
    fireEvent.change(moneyInput(), {target: {value: ''}}); save(); await waitFor(() => expect(request).toHaveBeenCalledOnce());
    expect(JSON.parse(request.mock.calls[0][1].body).default_cost_limit_micros).toBeNull();
  });
  it('keeps a conflicting draft until explicit reload and then writes the fresh revision', async () => {
    const latest = {...initial, revision: 8, default_cost_limit_micros: 2_000_000}; let writes = 0;
    request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'PATCH' ? ++writes === 1 ? Promise.reject(Object.assign(new Error('注册设置已变化，请刷新后重试。'), {status: 409})) : Promise.resolve({...latest, revision: 9, default_cost_limit_micros: 3_000_000}) : Promise.resolve(latest));
    render(<RegistrationPolicy policy={initial} onSaved={vi.fn()}/>); fireEvent.change(moneyInput(), {target: {value: '1.5'}}); save();
    expect(await screen.findByRole('alert')).toHaveTextContent('复核最新政策'); expect(moneyInput()).toHaveValue(1.5); expect(screen.getByRole('button', {name: '保存注册与默认额度'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '重新读取注册设置'})); fireEvent.click(screen.getByRole('button', {name: '保留草稿'})); expect(moneyInput()).toHaveValue(1.5); expect(request).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', {name: '重新读取注册设置'})); fireEvent.click(screen.getByRole('button', {name: '放弃并读取'})); await waitFor(() => expect(moneyInput()).toHaveValue(2));
    fireEvent.change(moneyInput(), {target: {value: '3'}}); save(); await waitFor(() => expect(writes).toBe(2));
    expect(request.mock.calls.filter(([, options]) => options?.method === 'PATCH').map(([, options]) => JSON.parse(options.body).revision)).toEqual([7, 8]);
  });
  it('preserves a dirty draft when a background user-list reload supplies newer policy', () => {
    const onSaved = vi.fn(), view = render(<RegistrationPolicy policy={initial} onSaved={onSaved}/>); fireEvent.change(moneyInput(), {target: {value: '1.5'}});
    view.rerender(<RegistrationPolicy policy={{...initial, revision: 8, default_cost_limit_micros: 2_000_000}} onSaved={onSaved}/>);
    expect(moneyInput()).toHaveValue(1.5); expect(screen.getByRole('alert')).toHaveTextContent('当前草稿已保留'); expect(screen.getByRole('button', {name: '保存注册与默认额度'})).toBeDisabled();
  });
  it('adopts a background policy update when there is no local draft', () => {
    const onSaved = vi.fn(), view = render(<RegistrationPolicy policy={initial} onSaved={onSaved}/>);
    view.rerender(<RegistrationPolicy policy={{...initial, revision: 8, registration_enabled: false, default_cost_limit_micros: 2_000_000}} onSaved={onSaved}/>);
    expect(moneyInput()).toHaveValue(2); expect(screen.getByRole('switch')).not.toBeChecked(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
  it('protects unsaved limits during navigation and while a save is in flight', async () => {
    let resolve!: (value: RegistrationPolicyValue) => void; request.mockReturnValue(new Promise(done => {resolve = done;}));
    render(<RegistrationPolicy policy={initial} onSaved={vi.fn()}/>); fireEvent.change(moneyInput(), {target: {value: '2'}});
    const resume = vi.fn(), attempt = () => {let accepted = true; act(() => {accepted = window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'chat', resume}}));}); return accepted;};
    expect(attempt()).toBe(false); fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(resume).not.toHaveBeenCalled(); expect(moneyInput()).toHaveValue(2);
    save(); expect(attempt()).toBe(false); expect(screen.getByRole('alert')).toHaveTextContent('正在保存'); expect(resume).not.toHaveBeenCalled();
    await act(async () => resolve({...initial, revision: 8, default_cost_limit_micros: 2_000_000})); expect(attempt()).toBe(true);
  });
});
