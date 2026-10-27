import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {ModelManager, type ModelProfile} from './ModelManager';
const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const profile = (changes: Partial<ModelProfile> = {}): ModelProfile => ({id: 'daily', revision: 1, name: '日常助手', provider: 'test-provider', model: 'test-model', base_url: 'https://example.test/v1', api_format: 'chat_completions', stream_tool_calls: false, supports_images: false, max_images: 3, api_key_set: true, pricing: {currency: 'CNY', input_per_million: '2', output_per_million: '3', cached_input_per_million: '0.2', configured: true}, active: true, last_test: null, ...changes});
const registry = (item = profile(), revision = 1) => ({revision, active_id: item.id, items: [item]});
beforeEach(() => {request.mockReset(); Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value() {this.setAttribute('open', '');}}); Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value() {this.removeAttribute('open');}});});
const edit = async () => {fireEvent.click(await screen.findByRole('button', {name: '编辑连接与单价'})); return screen.getByRole('dialog', {name: '编辑 日常助手'});};
describe('model connections with their own prices', () => {
  it('persists decimal price strings and omits unchanged secret values', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'PATCH' ? registry(profile({pricing: {...profile().pricing, input_per_million: '0.123456'}}), 2) : registry()));
    render(<ModelManager notify={vi.fn()}/>); const dialog = await edit(); fireEvent.change(within(dialog).getByRole('spinbutton', {name: '输入单价'}), {target: {value: '0.123456'}}); fireEvent.click(within(dialog).getByRole('button', {name: '保存连接与价格'}));
    await waitFor(() => expect(screen.queryByRole('dialog', {name: '编辑 日常助手'})).not.toBeInTheDocument()); const write = request.mock.calls.find(([, options]) => options?.method === 'PATCH')!, payload = JSON.parse(write[1].body);
    expect(write[0]).toBe('/api/admin/models/daily'); expect(payload.revision).toBe(1); expect(payload.values.pricing).toEqual({...profile().pricing, input_per_million: '0.123456'}); expect(payload.values).not.toHaveProperty('api_key'); expect(payload.values).not.toHaveProperty('api_key_set'); expect(payload.values).not.toHaveProperty('last_test');
  });
  it('requires an explicit pricing confirmation even when every price is zero', async () => {
    const item = profile({pricing: {currency: 'CNY', input_per_million: '0', output_per_million: '0', cached_input_per_million: '0', configured: false}});
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'PATCH' ? registry(profile({pricing: {...item.pricing, configured: true}}), 2) : registry(item)));
    render(<ModelManager notify={vi.fn()}/>); expect(await screen.findByText(/单价待确认/)).toBeVisible(); const dialog = await edit(), check = within(dialog).getByRole('checkbox', {name: /已核对并确认此模型计价/}); expect(check).not.toBeChecked();
    fireEvent.click(check); fireEvent.click(within(dialog).getByRole('button', {name: '保存连接与价格'})); await waitFor(() => expect(request.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)); expect(JSON.parse(request.mock.calls.find(([, options]) => options?.method === 'PATCH')![1].body).values.pricing).toEqual({...item.pricing, configured: true});
  });
  it('keeps a dirty price through cancellation and intercepts administrator category navigation', async () => {
    request.mockResolvedValue(registry()); render(<ModelManager notify={vi.fn()}/>); const dialog = await edit(); fireEvent.change(within(dialog).getByRole('spinbutton', {name: '输入单价'}), {target: {value: '0.5'}}); fireEvent.click(within(dialog).getByRole('button', {name: '取消'}));
    fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(screen.getByRole('spinbutton', {name: '输入单价'})).toHaveValue(0.5);
    const resume = vi.fn(); let accepted = true; act(() => {accepted = window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin', resume}}));}); expect(accepted).toBe(false); fireEvent.click(screen.getByRole('button', {name: '放弃修改'})); expect(resume).toHaveBeenCalledOnce();
  });
  it('retains a conflicting draft and uses a newly read registry revision after explicit discard', async () => {
    let reads = 0, writes = 0; const latest = profile({pricing: {...profile().pricing, input_per_million: '4'}});
    request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'PATCH' ? ++writes === 1 ? Promise.reject(Object.assign(new Error('模型配置已变化'), {status: 409})) : Promise.resolve(registry(latest, 3)) : Promise.resolve(++reads === 1 ? registry() : registry(latest, 2)));
    render(<ModelManager notify={vi.fn()}/>); let dialog = await edit(); fireEvent.change(within(dialog).getByRole('spinbutton', {name: '输入单价'}), {target: {value: '1.25'}}); fireEvent.click(within(dialog).getByRole('button', {name: '保存连接与价格'})); expect(await screen.findByRole('alert')).toHaveTextContent('草稿已保留'); expect(screen.getByRole('spinbutton', {name: '输入单价'})).toHaveValue(1.25); expect(screen.getByRole('button', {name: '保存连接与价格'})).toBeDisabled(); expect(writes).toBe(1);
    fireEvent.click(screen.getByRole('button', {name: '取消'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改'})); fireEvent.click(screen.getByRole('button', {name: '重新读取模型'})); await waitFor(() => expect(screen.getByText('¥4')).toBeVisible()); dialog = await edit(); expect(within(dialog).getByRole('spinbutton', {name: '输入单价'})).toHaveValue(4); fireEvent.change(within(dialog).getByRole('spinbutton', {name: '输入单价'}), {target: {value: '4.5'}}); fireEvent.click(within(dialog).getByRole('button', {name: '保存连接与价格'}));
    await waitFor(() => expect(writes).toBe(2)); expect(request.mock.calls.filter(([, options]) => options?.method === 'PATCH').map(([, options]) => JSON.parse(options.body).revision)).toEqual([1, 2]);
  });
  it('tests and activates the selected saved connection independently using the registry revision', async () => {
    const spare = profile({id: 'spare', name: '备用模型', active: false, provider: 'other', api_format: 'responses'}); request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'POST' ? {ok: true} : {...registry(), items: [profile(), spare]}));
    render(<ModelManager notify={vi.fn()}/>); const card = (await screen.findByRole('heading', {name: '备用模型'})).closest('article')!; fireEvent.click(within(card).getByRole('button', {name: '测试'})); await waitFor(() => expect(request).toHaveBeenCalledWith('/api/admin/models/spare/test', {method: 'POST', body: JSON.stringify({revision: 1})})); await waitFor(() => expect(within(card).getByRole('button', {name: '启用'})).toBeEnabled()); fireEvent.click(within(card).getByRole('button', {name: '启用'})); await waitFor(() => expect(request).toHaveBeenCalledWith('/api/admin/models/spare/activate', {method: 'POST', body: JSON.stringify({revision: 1})}));
  });
  it('creates a connection with its own protocol, secret, capabilities and all three rates', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'POST' ? registry(profile({name: '新连接'}), 2) : registry()));
    render(<ModelManager notify={vi.fn()}/>); fireEvent.click(await screen.findByRole('button', {name: '添加模型'}));
    const dialog = screen.getByRole('dialog', {name: '添加模型连接'}), fields = within(dialog);
    for (const [label, value] of [['连接名称', '新连接'], ['提供商', '供应商B'], ['模型名称', 'model-b'], ['Base URL', 'https://provider-b.test/v1'], ['API 密钥', 'new-private-key']]) fireEvent.change(fields.getByLabelText(label), {target: {value}});
    expect(fields.getByLabelText('API 密钥')).toHaveAttribute('type', 'password');
    fireEvent.click(fields.getByRole('combobox', {name: '协议'})); fireEvent.click(fields.getByRole('option', {name: 'Responses'}));
    fireEvent.click(fields.getByRole('checkbox', {name: '流式工具调用'})); fireEvent.click(fields.getByRole('checkbox', {name: '支持图片输入'}));
    fireEvent.change(fields.getByRole('spinbutton', {name: '单轮图片上限'}), {target: {value: '4'}});
    for (const [label, value] of [['输入单价', '1.2'], ['缓存命中输入单价', '0.25'], ['输出单价', '4']]) fireEvent.change(fields.getByRole('spinbutton', {name: label}), {target: {value}});
    fireEvent.click(fields.getByRole('checkbox', {name: /已核对并确认此模型计价/})); fireEvent.click(fields.getByRole('button', {name: '保存连接与价格'}));
    await waitFor(() => expect(screen.queryByRole('dialog', {name: '添加模型连接'})).not.toBeInTheDocument());
    const payload = JSON.parse(request.mock.calls.find(([, options]) => options?.method === 'POST')![1].body);
    expect(payload).toMatchObject({revision: 1, name: '新连接', provider: '供应商B', model: 'model-b', base_url: 'https://provider-b.test/v1', api_key: 'new-private-key', api_format: 'responses', stream_tool_calls: true, supports_images: true, max_images: 4, pricing: {currency: 'CNY', input_per_million: '1.2', cached_input_per_million: '0.25', output_per_million: '4', configured: true}});
    expect(payload).not.toHaveProperty('clear_api_key');
  });
  it('clears a saved secret only explicitly and blocks navigation or repeat saves while writing', async () => {
    let finish: (value: ReturnType<typeof registry>) => void = () => {};
    request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'PATCH' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve(registry()));
    render(<ModelManager notify={vi.fn()}/>); const dialog = await edit(), fields = within(dialog), secret = fields.getByLabelText('API 密钥');
    fireEvent.change(secret, {target: {value: 'replacement-draft'}}); fireEvent.click(fields.getByRole('checkbox', {name: '清除已保存的密钥'}));
    expect(secret).toHaveValue(''); expect(secret).toBeDisabled();
    fireEvent.click(fields.getByRole('button', {name: '保存连接与价格'}));
    await waitFor(() => expect(fields.getByRole('button', {name: '正在保存…'})).toBeDisabled());
    expect(fields.getByLabelText('连接名称')).toBeDisabled(); expect(fields.getByRole('button', {name: '取消'})).toBeDisabled();
    const resume = vi.fn(); let accepted = true;
    act(() => {accepted = window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin', resume}}));});
    expect(accepted).toBe(false); expect(resume).not.toHaveBeenCalled();
    const writes = request.mock.calls.filter(([, options]) => options?.method === 'PATCH'); expect(writes).toHaveLength(1);
    const payload = JSON.parse(writes[0][1].body); expect(payload.values.clear_api_key).toBe(true); expect(payload.values).not.toHaveProperty('api_key');
    await act(async () => finish(registry(profile({api_key_set: false}), 2)));
    await waitFor(() => expect(screen.queryByRole('dialog', {name: '编辑 日常助手'})).not.toBeInTheDocument());
  });
  it('shows cache pricing and an outdated test honestly while protecting the active connection', async () => {
    request.mockResolvedValue(registry(profile({last_test: {ok: true, outdated: true, tested_at: '2026-10-28T00:00:00Z'}})));
    render(<ModelManager notify={vi.fn()}/>); const card = await screen.findByRole('article', {name: '日常助手'});
    expect(within(card).getByText('¥0.2')).toBeVisible(); expect(within(card).getByRole('status')).toHaveTextContent('配置已修改，请重新测试');
    expect(within(card).queryByRole('button', {name: '启用'})).not.toBeInTheDocument(); expect(within(card).queryByRole('button', {name: /删除模型/})).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', {name: '模型连接'})).not.toBeInTheDocument();
  });
});
