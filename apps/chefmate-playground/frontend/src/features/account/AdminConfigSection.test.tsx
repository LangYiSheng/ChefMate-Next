import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {StrictMode} from 'react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {AdminConfigSection} from './AdminConfigSection';
const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const snapshot = () => ({revision: 'a'.repeat(64), runtime: {turn_timeout_s: 90, model_timeout_s: 60, model_max_retries: 1, max_model_rounds: 10, max_tool_calls: 20}, context: {max_history_chars: 400000, max_tool_result_chars: 60000}, available_components: ['one'], enabled_components: ['one'], components: [{id: 'one', name: '做饭工具', description: '厨房工具', available: true}]});
beforeEach(() => {request.mockReset(); Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value() {this.setAttribute('open', '');}}); Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value() {this.removeAttribute('open');}});});
describe('administrator flat runtime configuration', () => {
  it('edits and saves a category with its current server revision and no nested navigation', async () => {
    const notify = vi.fn(); request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'PATCH' ? {...snapshot(), revision: 'b'.repeat(64), runtime: JSON.parse(options.body as string).values} : snapshot()));
    render(<StrictMode><AdminConfigSection section="runtime" notify={notify}/></StrictMode>);
    fireEvent.change(await screen.findByRole('spinbutton', {name: '单轮任务时限'}), {target: {value: '120'}}); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(notify).toHaveBeenCalledOnce()); expect(request).toHaveBeenCalledWith('/api/admin/settings', {method: 'PATCH', body: JSON.stringify({revision: 'a'.repeat(64), section: 'runtime', values: {...snapshot().runtime, turn_timeout_s: 120}})}); expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });
  it('preserves a conflicting context draft, guards category changes and requires explicit reload', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'PATCH' ? Promise.reject(Object.assign(new Error('配置已变化'), {status: 409})) : Promise.resolve(snapshot()));
    render(<AdminConfigSection section="context" notify={vi.fn()}/>); fireEvent.change(await screen.findByRole('spinbutton', {name: '会话上下文上限'}), {target: {value: '80000'}}); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('当前草稿已保留'); expect(screen.getByRole('spinbutton', {name: '会话上下文上限'})).toHaveValue(80000); expect(screen.getByRole('button', {name: '保存修改'})).toBeDisabled();
    const resume = vi.fn(); let accepted = true; act(() => {accepted = window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin', resume}}));}); expect(accepted).toBe(false); fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(resume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: '重新读取'})); fireEvent.click(screen.getByRole('button', {name: '放弃并读取'})); await waitFor(() => expect(screen.getByRole('spinbutton', {name: '会话上下文上限'})).toHaveValue(400000));
  });
  it('saves enabled component identifiers without writing unrelated configuration', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'PATCH' ? {...snapshot(), enabled_components: []} : snapshot())); render(<AdminConfigSection section="components" notify={vi.fn()}/>);
    fireEvent.click(await screen.findByRole('switch', {name: '做饭工具'})); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/admin/settings', {method: 'PATCH', body: JSON.stringify({revision: 'a'.repeat(64), section: 'components', values: {enabled_components: []}})}));
  });
});
