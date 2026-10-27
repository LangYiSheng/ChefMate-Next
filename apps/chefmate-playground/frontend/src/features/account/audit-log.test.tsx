import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {AuditLog, type AuditReport} from './AuditLog';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const base: AuditReport = {items: [{id: 1, created_at: '2026-10-27T16:00:00+00:00', actor_label: '@owner', target_label: '小厨师 (@cook)', action: 'grant_credit', action_label: '增加永久额度', field_labels: ['增加金额']}], total: 25, page: 1, page_size: 20, total_pages: 2, actions: [{value: 'grant_credit', label: '增加永久额度'}], actors: [{value: 'admin-id', label: '@owner'}], targets: [{value: 'cook-id', label: '@cook'}], timezone: 'Asia/Shanghai'};
const parameters = (index = request.mock.calls.length - 1) => new URL(request.mock.calls[index][0], 'http://test').searchParams;
const filter = () => fireEvent.click(screen.getByRole('button', {name: /^筛选$/}));
beforeEach(() => {request.mockReset(); request.mockResolvedValue(base);});

describe('Audit history', () => {
  it('renders human labels, local time, bounded pagination, and metadata', async () => {
    render(<AuditLog/>);
    expect(await screen.findByText('增加永久额度')).toBeInTheDocument();
    expect(screen.getByText('2026/10/28 00:00:00')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('共 25 条');
    expect(parameters().get('page_size')).toBe('20');
    expect(screen.getByRole('button', {name: '上一页'})).toBeDisabled();
    request.mockResolvedValue({...base, page: 2, items: []});
    fireEvent.click(screen.getByRole('button', {name: '下一页'}));
    await waitFor(() => expect(parameters().get('page')).toBe('2'));
    await waitFor(() => expect(screen.getByText('第 2 / 2 页')).toBeInTheDocument());
    expect(screen.getByRole('button', {name: '下一页'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '上一页'}));
    await waitFor(() => expect(parameters().get('page')).toBe('1'));
  });

  it('applies literal search, named filters, and inclusive calendar dates together and resets pages', async () => {
    request.mockImplementation((path: string) => Promise.resolve({...base, page: Number(new URL(path, 'http://test').searchParams.get('page'))}));
    render(<AuditLog/>); await screen.findByText('增加永久额度');
    fireEvent.click(screen.getByRole('button', {name: '下一页'})); await screen.findByText('第 2 / 2 页');
    fireEvent.change(screen.getByRole('searchbox'), {target: {value: '  50%_cook\\  '}});
    fireEvent.change(screen.getByLabelText('开始日期'), {target: {value: '2026-10-01'}});
    fireEvent.change(screen.getByLabelText('结束日期'), {target: {value: '2026-10-27'}});
    fireEvent.click(screen.getByRole('combobox', {name: '操作类型'}));
    fireEvent.click(screen.getByRole('option', {name: '增加永久额度'}));
    fireEvent.click(screen.getByRole('combobox', {name: '操作者'})); fireEvent.click(screen.getByRole('option', {name: '@owner'}));
    fireEvent.click(screen.getByRole('combobox', {name: '操作对象'})); fireEvent.click(screen.getByRole('option', {name: '@cook'}));
    filter();
    await waitFor(() => expect(parameters().get('page')).toBe('1'));
    expect(parameters().get('q')).toBe('50%_cook\\'); expect(parameters().get('action')).toBe('grant_credit');
    expect(parameters().get('actor')).toBe('admin-id'); expect(parameters().get('target')).toBe('cook-id');
    expect(parameters().get('date_from')).toBe('2026-10-01'); expect(parameters().get('date_to')).toBe('2026-10-27');
    expect(screen.getByRole('status')).toHaveTextContent('筛选结果');
    fireEvent.click(screen.getByRole('button', {name: '清空'}));
    await waitFor(() => expect(parameters().has('q')).toBe(false));
    expect(screen.getByRole('searchbox')).toHaveValue('');
  });

  it('ignores aborted old responses even when the transport still resolves them', async () => {
    let old: (value: AuditReport) => void = () => {};
    request.mockImplementationOnce(() => new Promise<AuditReport>(resolve => {old = resolve;}));
    render(<AuditLog/>); await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByRole('searchbox'), {target: {value: '新记录'}}); filter();
    await screen.findByText('增加永久额度');
    await act(async () => old({...base, total: 999, items: []}));
    expect(screen.getByRole('status')).toHaveTextContent('共 25 条');
    expect(request.mock.calls[0][1].signal.aborted).toBe(true);
    expect(screen.queryByText('共 999 条')).not.toBeInTheDocument();
  });

  it('shows errors and retries while preserving the last successful report', async () => {
    render(<AuditLog/>); await screen.findByText('增加永久额度');
    request.mockRejectedValueOnce(new Error('网络暂时不可用'));
    fireEvent.click(screen.getByRole('button', {name: '刷新操作记录'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('网络暂时不可用');
    expect(screen.getByText('增加永久额度')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '重新读取'}));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent('共 25 条');
  });

  it('changes page size through the shared keyboard-accessible Select', async () => {
    render(<AuditLog/>); await screen.findByText('增加永久额度');
    fireEvent.click(screen.getByRole('combobox', {name: '每页条数'})); fireEvent.click(screen.getByRole('option', {name: '100 条'}));
    await waitFor(() => expect(parameters().get('page_size')).toBe('100'));
    expect(parameters().get('page')).toBe('1');
  });
});
