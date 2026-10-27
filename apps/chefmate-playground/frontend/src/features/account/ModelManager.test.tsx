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
    fireEvent.click(screen.getByRole('button', {name: '取消'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改'})); fireEvent.click(screen.getByRole('button', {name: '重新读取模型'})); await waitFor(() => expect(screen.getByText('¥4 / ¥3')).toBeVisible()); dialog = await edit(); expect(within(dialog).getByRole('spinbutton', {name: '输入单价'})).toHaveValue(4); fireEvent.change(within(dialog).getByRole('spinbutton', {name: '输入单价'}), {target: {value: '4.5'}}); fireEvent.click(within(dialog).getByRole('button', {name: '保存连接与价格'}));
    await waitFor(() => expect(writes).toBe(2)); expect(request.mock.calls.filter(([, options]) => options?.method === 'PATCH').map(([, options]) => JSON.parse(options.body).revision)).toEqual([1, 2]);
  });
  it('tests and activates the selected saved connection independently using the registry revision', async () => {
    const spare = profile({id: 'spare', name: '备用模型', active: false, provider: 'other', api_format: 'responses'}); request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'POST' ? {ok: true} : {...registry(), items: [profile(), spare]}));
    render(<ModelManager notify={vi.fn()}/>); const card = (await screen.findByRole('heading', {name: '备用模型'})).closest('article')!; fireEvent.click(within(card).getByRole('button', {name: '测试'})); await waitFor(() => expect(request).toHaveBeenCalledWith('/api/admin/models/spare/test', {method: 'POST', body: JSON.stringify({revision: 1})})); await waitFor(() => expect(within(card).getByRole('button', {name: '启用'})).toBeEnabled()); fireEvent.click(within(card).getByRole('button', {name: '启用'})); await waitFor(() => expect(request).toHaveBeenCalledWith('/api/admin/models/spare/activate', {method: 'POST', body: JSON.stringify({revision: 1})}));
  });
});
