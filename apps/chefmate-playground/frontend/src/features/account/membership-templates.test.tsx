import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {MembershipTemplates, type MembershipTemplate} from './MembershipTemplates';
import {EntitlementManager} from './EntitlementManager';
import type {AccountUser} from './types';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const base = '/api/admin/membership-templates';
const template = (values: Partial<MembershipTemplate> = {}): MembershipTemplate => ({id: 'a'.repeat(32), name: '标准月卡', label: 'ChefMate 月度会员', duration_days: 30, weekly_limit_micros: 10_000_000, five_hour_limit_micros: 2_000_000, created_at: '2026-10-28T00:00:00Z', updated_at: '2026-10-28T00:00:00Z', ...values});
const initial = () => ({revision: 1, items: [template(), template({id: 'b'.repeat(32), name: '体验周卡', label: 'ChefMate 体验会员', duration_days: 7, weekly_limit_micros: 3_000_000, five_hour_limit_micros: 500_000})]});
const manage = async () => {const button = await screen.findByRole('button', {name: '管理模板'}); await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);};
const callbacks = () => ({disabled: false, onApply: vi.fn(), onBusyChange: vi.fn(), onDirtyChange: vi.fn()});
const user: AccountUser = {id: 'user-1', username: 'supper', display_name: '小晚', role: 'user', disabled: false, developer_allowed: false, onboarding_completed: true, created_at: '', profile: {}, quota: {cost_limit_micros: 5_000_000, used_cost_micros: 0, used_tokens: 0, reserved_cost_micros: 0, reserved_tokens: 0}};
beforeEach(() => {
  request.mockReset(); request.mockResolvedValue(initial());
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value() {this.setAttribute('open', '');}});
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value() {this.removeAttribute('open');}});
});

describe('Membership templates', () => {
  it('keeps management disabled until a failed template read is recovered', async () => {
    request.mockRejectedValueOnce(new Error('模板服务不可用')).mockResolvedValue(initial());
    render(<MembershipTemplates {...callbacks()}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('模板服务不可用');
    expect(screen.getByRole('button', {name: '管理模板'})).toBeDisabled();
    expect(screen.getByText(/可手动填写本次会员参数/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '重新读取模板'}));
    await waitFor(() => expect(screen.getByRole('button', {name: '管理模板'})).toBeEnabled());
    expect(screen.getByRole('combobox', {name: '会员模板'})).toBeVisible();
  });

  it('applies a selected template to the current form without granting and allows this grant to differ', async () => {
    const changed = vi.fn().mockResolvedValue(undefined);
    const funds = {currency: 'CNY', window_mode: 'first_use', available_micros: 5_000_000, deduction_order: [], permanent: {total_micros: 5_000_000, used_micros: 0, reserved_micros: 0, estimated_micros: 0, remaining_micros: 5_000_000, overdrawn_micros: 0, sources: []}, memberships: []};
    const report = {user: {...user, quota: {...user.quota, funds}}, entitlements: funds};
    request.mockImplementation((path: string) => Promise.resolve(path === base ? initial() : report));
    render(<EntitlementManager user={user} onClose={vi.fn()} onChanged={changed}/>);
    fireEvent.click(await screen.findByRole('button', {name: /开通限时会员/}));
    fireEvent.click(await screen.findByRole('combobox', {name: '会员模板'}));
    fireEvent.click(screen.getByRole('option', {name: '体验周卡'}));
    fireEvent.click(screen.getByRole('button', {name: '套用模板'}));
    expect(screen.getByLabelText('会员名称')).toHaveValue('ChefMate 体验会员');
    expect(screen.getByLabelText('有效期（天）')).toHaveValue(7);
    expect(screen.getByLabelText('每周额度（元）')).toHaveValue(3);
    expect(screen.getByLabelText('每 5 小时额度（元）')).toHaveValue(0.5);
    expect(request.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
    fireEvent.change(screen.getByLabelText('每周额度（元）'), {target: {value: '4.000001'}});
    fireEvent.submit(screen.getByRole('form', {name: '开通会员'}));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    const submitted = request.mock.calls.find(([path, options]) => path.endsWith('/memberships') && options?.method === 'POST')!;
    expect(JSON.parse(submitted[1].body)).toMatchObject({label: 'ChefMate 体验会员', duration_days: 7, weekly_limit_micros: 4_000_001, five_hour_limit_micros: 500_000});
  });

  it('creates a saved template, then supports editing and deleting with the latest revision', async () => {
    let data = initial();
    request.mockImplementation((path: string, options?: RequestInit) => {
      if (!options?.method) return Promise.resolve(data);
      if (options.method === 'POST') {const {revision, ...values} = JSON.parse(String(options.body)); expect(revision).toBe(data.revision); data = {revision: data.revision + 1, items: [...data.items, template({id: 'c'.repeat(32), ...values})]};}
      if (options.method === 'PATCH') {const {revision, values} = JSON.parse(String(options.body)); expect(revision).toBe(data.revision); data = {revision: data.revision + 1, items: data.items.map(item => path.endsWith(item.id) ? {...item, ...values} : item)};}
      if (options.method === 'DELETE') {expect(path).toContain(`revision=${data.revision}`); data = {revision: data.revision + 1, items: data.items.filter(item => !path.includes(item.id))};}
      return Promise.resolve(data);
    });
    render(<MembershipTemplates {...callbacks()}/>);
    await manage();
    fireEvent.click(screen.getByRole('button', {name: '新增模板'}));
    fireEvent.change(screen.getByLabelText('模板名称'), {target: {value: '节日会员'}});
    fireEvent.submit(screen.getByRole('form', {name: '保存会员模板'}));
    await screen.findByText('会员模板已保存，可选择套用。');
    const row = screen.getByRole('heading', {name: '节日会员'}).closest('article')!;
    fireEvent.click(within(row).getByRole('button', {name: '编辑'}));
    fireEvent.change(screen.getByLabelText('模板有效期（天）'), {target: {value: '15'}});
    fireEvent.submit(screen.getByRole('form', {name: '保存会员模板'}));
    await screen.findAllByText('15 天 · 每周 ¥10.00 · 每 5 小时 ¥2.00');
    fireEvent.click(within(screen.getByRole('heading', {name: '节日会员'}).closest('article')!).getByRole('button', {name: '删除'}));
    fireEvent.click(screen.getByRole('button', {name: '确认删除模板'}));
    await screen.findByText('模板已删除，已开通的会员不受影响。');
    expect(screen.queryByRole('heading', {name: '节日会员'})).not.toBeInTheDocument();
    expect(request.mock.calls.every(([path]) => path.startsWith(base))).toBe(true);
  });

  it('keeps a conflicting edit draft, loads the latest revision and saves only on an explicit second submit', async () => {
    let data = initial(), posts = 0;
    request.mockImplementation((_path: string, options?: RequestInit) => {
      if (options?.method === 'PATCH') {
        if (++posts === 1) {data = {revision: 2, items: [template({label: '另一个管理员的会员', weekly_limit_micros: 20_000_000})]}; return Promise.reject(Object.assign(new Error('模板已被修改'), {status: 409}));}
        expect(JSON.parse(String(options.body))).toMatchObject({revision: 2, values: {name: '我的月卡', weekly_limit_micros: 10_000_000}});
        data = {revision: 3, items: [template({name: '我的月卡'})]};
      }
      return Promise.resolve(data);
    });
    render(<MembershipTemplates {...callbacks()}/>);
    await manage();
    fireEvent.click(screen.getAllByRole('button', {name: '编辑'})[0]);
    fireEvent.change(screen.getByLabelText('模板名称'), {target: {value: '我的月卡'}});
    fireEvent.submit(screen.getByRole('form', {name: '保存会员模板'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('草稿已保留');
    expect(screen.getByRole('button', {name: '保存模板'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '读取最新版本并保留草稿'}));
    await screen.findByText(/另一个管理员的会员/);
    expect(screen.getByLabelText('模板名称')).toHaveValue('我的月卡');
    expect(posts).toBe(1);
    fireEvent.submit(screen.getByRole('form', {name: '保存会员模板'}));
    await screen.findByText('会员模板已保存，可选择套用。'); expect(posts).toBe(2);
  });

  it('confirms a save whose response was lost without creating the same template twice', async () => {
    let data = initial(), posts = 0;
    request.mockImplementation((_path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {posts++; const {revision: _revision, ...values} = JSON.parse(String(options.body)); data = {revision: 2, items: [...data.items, template({id: 'd'.repeat(32), ...values})]}; return Promise.reject(new Error('响应丢失'));}
      return Promise.resolve(data);
    });
    render(<MembershipTemplates {...callbacks()}/>);
    await manage(); fireEvent.click(screen.getByRole('button', {name: '新增模板'}));
    fireEvent.change(screen.getByLabelText('模板名称'), {target: {value: '一次保存'}}); fireEvent.submit(screen.getByRole('form', {name: '保存会员模板'}));
    await screen.findByRole('alert'); expect(screen.getByLabelText('模板名称')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '读取最新版本并保留草稿'}));
    await screen.findByText('已确认上次模板已保存。'); expect(posts).toBe(1);
    expect(screen.getByRole('heading', {name: '一次保存'})).toBeVisible();
  });

  it('protects a dirty draft and prevents duplicate saves or closing during a request', async () => {
    let done!: (value: unknown) => void;
    request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'POST' ? new Promise(resolve => {done = resolve;}) : Promise.resolve(initial()));
    const props = callbacks(); render(<MembershipTemplates {...props}/>);
    await manage(); fireEvent.click(screen.getByRole('button', {name: '新增模板'}));
    fireEvent.change(screen.getByLabelText('模板名称'), {target: {value: '草稿保留'}});
    fireEvent.click(screen.getByRole('button', {name: '关闭模板'}));
    expect(screen.getByRole('dialog', {name: '保留未保存的修改？'})).toBeVisible(); fireEvent.click(screen.getByRole('button', {name: '继续编辑'}));
    expect(props.onDirtyChange).toHaveBeenLastCalledWith(true);
    const form = screen.getByRole('form', {name: '保存会员模板'}); fireEvent.submit(form); fireEvent.submit(form);
    fireEvent(screen.getByRole('dialog', {name: '新增会员模板'}), new Event('cancel', {cancelable: true}));
    expect(screen.getByRole('alert')).toHaveTextContent('正在保存会员模板');
    expect(request.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
    expect(props.onBusyChange).toHaveBeenLastCalledWith(true);
    await act(async () => done({revision: 2, items: [...initial().items, template({id: 'e'.repeat(32), name: '草稿保留'})]}));
    expect(props.onBusyChange).toHaveBeenLastCalledWith(false);
    expect(props.onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it('blocks parent navigation once for an unsaved template and preserves the grant form', async () => {
    const funds = {currency: 'CNY', window_mode: 'first_use', available_micros: 5_000_000, deduction_order: [], permanent: {total_micros: 5_000_000, used_micros: 0, reserved_micros: 0, estimated_micros: 0, remaining_micros: 5_000_000, overdrawn_micros: 0, sources: []}, memberships: []};
    request.mockImplementation((path: string) => Promise.resolve(path === base ? initial() : {user: {...user, quota: {...user.quota, funds}}, entitlements: funds}));
    render(<EntitlementManager user={user} onClose={vi.fn()} onChanged={vi.fn().mockResolvedValue(undefined)}/>);
    fireEvent.click(await screen.findByRole('button', {name: /开通限时会员/})); await manage();
    fireEvent.click(screen.getByRole('button', {name: '新增模板'}));
    fireEvent.change(screen.getByLabelText('模板名称'), {target: {value: '未保存方案'}});
    const resume = vi.fn();
    act(() => {window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'another-page', resume}}));});
    expect(screen.getAllByRole('dialog', {name: '保留未保存的修改？'})).toHaveLength(1);
    expect(resume).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', {name: '继续编辑'}));
    expect(screen.getByLabelText('模板名称')).toHaveValue('未保存方案');
    expect(screen.getByLabelText('有效期（天）')).toHaveValue(30);
    expect(request.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  });
});
