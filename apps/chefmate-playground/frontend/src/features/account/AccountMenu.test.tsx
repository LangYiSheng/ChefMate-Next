import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {AccountMenu} from './AccountMenu';
import type {AccountUser} from './types';
const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const account: AccountUser = {id: 'a', username: 'testcook', display_name: '小厨', role: 'user', developer_allowed: false, disabled: false, onboarding_completed: true, created_at: '', profile: {}, quota: {cost_limit_micros: 5_000_000, used_cost_micros: 500_000, reserved_cost_micros: 0, used_tokens: 10, reserved_tokens: 0}};
const actions = () => ({onPreferences: vi.fn(), onProfile: vi.fn(), onQuota: vi.fn(), onSettings: vi.fn(), onAdmin: vi.fn(), onLogout: vi.fn()});
beforeEach(() => {request.mockReset(); request.mockResolvedValue({quota: account.quota});});
describe('account shortcut card', () => {
  it('opens beside the account trigger, updates quota and restores focus with Escape', async () => {
    render(<AccountMenu account={account} {...actions()}/>); const trigger = screen.getByRole('button', {name: '账号菜单：小厨'});
    fireEvent.keyDown(trigger, {key: 'ArrowDown'}); const dialog = screen.getByRole('dialog', {name: '账号快捷菜单'});
    expect(trigger).toHaveAttribute('aria-expanded', 'true'); expect(within(dialog).getByRole('button', {name: '查看我的额度'})).toHaveFocus();
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/auth/quota', expect.objectContaining({signal: expect.any(AbortSignal)})));
    fireEvent.keyDown(dialog, {key: 'End'}); expect(within(dialog).getByRole('button', {name: '退出账户'})).toHaveFocus();
    fireEvent.keyDown(dialog, {key: 'Escape'}); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(trigger).toHaveFocus();
  });
  it('navigates to preferences and closes, while exposing administration only to administrators', () => {
    const callbacks = actions(), view = render(<AccountMenu account={account} {...callbacks}/>); fireEvent.click(screen.getByRole('button', {name: '账号菜单：小厨'}));
    expect(screen.queryByRole('button', {name: '管理平台'})).not.toBeInTheDocument(); fireEvent.click(screen.getByRole('button', {name: '个人偏好'})); expect(callbacks.onPreferences).toHaveBeenCalledOnce(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    view.rerender(<AccountMenu account={{...account, role: 'admin'}} {...callbacks}/>); fireEvent.click(screen.getByRole('button', {name: '账号菜单：小厨'})); fireEvent.click(screen.getByRole('button', {name: '管理平台'})); expect(callbacks.onAdmin).toHaveBeenCalledOnce();
  });
  it('closes on an outside pointer and invokes logout only from its explicit action', () => {
    const callbacks = actions(); render(<><button>外面的控件</button><AccountMenu account={account} {...callbacks}/></>); fireEvent.click(screen.getByRole('button', {name: '账号菜单：小厨'})); fireEvent.pointerDown(screen.getByRole('button', {name: '外面的控件'})); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(callbacks.onLogout).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: '账号菜单：小厨'})); fireEvent.click(screen.getByRole('button', {name: '退出账户'})); expect(callbacks.onLogout).toHaveBeenCalledOnce();
  });
});
