import {fireEvent, render, screen, within} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {AdminPanel} from './AdminPanel';
import type {AccountUser} from './types';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const users = Array.from({length: 25}, (_, index): AccountUser => ({id: `user-${index}`, username: `cook${String(index).padStart(2, '0')}`, display_name: `厨友${index}`, role: index === 0 ? 'admin' : 'user', disabled: index === 2, developer_allowed: index === 3, onboarding_completed: true, created_at: '2026-10-01T00:00:00Z', profile: {cooking_level: 'beginner', household_size: 1, preferences: [], theme: 'system'}, quota: {used_tokens: 0, used_cost_micros: 0, reserved_tokens: 0, reserved_cost_micros: 0, cost_limit_micros: 5000000}}));
const summary = {turns: 1, total_tokens: 20, input_tokens: 10, output_tokens: 10, cost_micros: 1000, incomplete_turns: 0, estimated_tokens: 0, estimated_cost_micros: 0};
const open = () => render(<AdminPanel user={users[0]} refreshAccount={vi.fn()} notify={vi.fn()} onClose={vi.fn()}/>);
const table = () => screen.getByRole('table');
const choose = (label: string, option: string) => {fireEvent.click(screen.getByRole('combobox', {name: label})); fireEvent.click(screen.getByRole('option', {name: option}));};
beforeEach(() => {
  request.mockReset(); request.mockImplementation((path: string) => Promise.resolve(path === '/api/admin/users' ? {items: users} : path === '/api/admin/policy' ? {revision: 1, registration_enabled: true, default_cost_limit_micros: 5000000} : path === '/api/admin/data' ? {items: users.slice(0, 24).map((user, index) => ({user_id: user.id, username: user.username, counts: {sessions: 0, recipes: 0, journal: 0, memory: 0}, bytes: 0, active: index === 1})), notice: '资料按账号隔离', legacy_data_present: false} : path === '/api/status' ? {} : {summary, users: users.map((user, index) => ({user, ...summary, turns: index === 4 ? 0 : 1, incomplete_turns: index === 3 ? 1 : 0})), daily: [], pricing: {currency: 'CNY', configured: true}}));
});
describe('Shared admin list navigation', () => {
  it('paginates user statistics, filters reports and keeps global metrics unchanged', async () => {
    open(); await screen.findByText('按用户统计');
    expect(within(table()).getAllByRole('row')).toHaveLength(21);
    fireEvent.click(screen.getByRole('button', {name: '下一页'})); expect(screen.getByText('第 2 / 2 页')).toBeVisible(); expect(within(table()).getByText('@cook24')).toBeVisible();
    choose('列表状态筛选', '报告待核对'); expect(screen.getByText('第 1 / 1 页')).toBeVisible(); expect(within(table()).getByText('@cook03')).toBeVisible(); expect(within(table()).queryByText('@cook24')).not.toBeInTheDocument();
    expect(screen.getByText('模型已报告 Token')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '清空'})); fireEvent.change(screen.getByRole('searchbox'), {target: {value: 'COOK24'}}); expect(within(table()).getByText('@cook24')).toBeVisible(); expect(screen.getByRole('status')).toHaveTextContent('共 1 位');
  });
  it('collapses registration without losing its draft, and combines user filters', async () => {
    open(); fireEvent.click(screen.getByRole('button', {name: '用户与额度'})); await screen.findByText('@cook00');
    const disclosure = screen.getByText('开放注册 · 点击调整新用户默认额度').closest('details')!; expect(disclosure).not.toHaveAttribute('open');
    fireEvent.click(within(disclosure).getByText('注册与新用户额度', {selector: 'strong'})); fireEvent.change(screen.getByLabelText('新用户默认金额额度（元）'), {target: {value: '8'}});
    fireEvent.click(within(disclosure).getByText('注册与新用户额度', {selector: 'strong'})); expect(screen.getByLabelText('新用户默认金额额度（元）')).toHaveValue(8);
    choose('列表状态筛选', '已停用'); expect(within(table()).getByText('@cook02')).toBeVisible();
    choose('账号角色筛选', '管理员'); expect(screen.getByText('没有找到符合条件的用户。')).toBeVisible();
    expect(request.mock.calls.filter(([, options]) => options?.method)).toHaveLength(0);
  });
  it('searches and filters storage without enabling reset of an active kitchen', async () => {
    open(); fireEvent.click(screen.getByRole('button', {name: '数据与状态'})); await screen.findByText('存储概览');
    choose('列表状态筛选', '有请求运行中'); expect(within(table()).getByText('@cook01')).toBeVisible(); expect(screen.getByRole('button', {name: '备份并重置 cook01 的数据'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '清空'})); fireEvent.change(screen.getByRole('searchbox'), {target: {value: '厨友24'}}); expect(within(table()).getByText('@cook24')).toBeVisible(); expect(within(table()).getByText('尚未创建')).toBeVisible();
    choose('列表状态筛选', '当前空闲'); expect(screen.getByText('没有找到符合条件的用户。')).toBeVisible();
  });
});
