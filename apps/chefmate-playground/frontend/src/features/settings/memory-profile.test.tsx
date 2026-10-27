import {fireEvent, render, screen} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {MemorySettings} from './MemorySettings';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const preference = () => ({revision: 'a'.repeat(64), household_size: 2, cooking_level: 'comfortable', preferences: ['喜欢家常菜', '快手省时间'], enabled: true, user_notes: '保留我手写的偏好', summary: '偏爱清淡家常菜'});
beforeEach(() => request.mockReset());
describe('Personal preferences and long-term memory', () => {
  it('edits kitchen preferences alongside notes with one shared conversation switch', async () => {
    request.mockResolvedValue(preference()); render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>);
    expect(await screen.findByRole('heading', {name: '厨房偏好'})).toBeVisible();
    expect(screen.getByRole('combobox', {name: '平时用餐人数'})).toHaveTextContent('2 人'); expect(screen.getByRole('combobox', {name: '下厨经验'})).toHaveTextContent('日常下厨');
    expect(screen.getByRole('button', {name: /快手省时间/})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('textbox', {name: '你希望记住的事'})).toHaveValue('保留我手写的偏好');
    expect(screen.getByRole('switch', {name: '在对话中使用个人偏好与长期记忆'})).toBeChecked();
    expect(screen.getByText(/关闭后仍会告诉模型你的称呼/)).toBeVisible();
    expect(request).toHaveBeenCalledWith('/api/personal-preferences', expect.objectContaining({signal: expect.any(AbortSignal)}));
  });
  it('keeps editable kitchen fields visible while sharing is disabled and saves their shared flag together', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve(options?.method === 'PATCH' ? {...preference(), ...JSON.parse(options.body as string), revision: 'b'.repeat(64)} : {...preference(), enabled: false}));
    render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>);
    const consent = await screen.findByRole('switch', {name: '在对话中使用个人偏好与长期记忆'}); expect(consent).not.toBeChecked();
    expect(screen.getByRole('button', {name: /喜欢家常菜/})).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(consent); fireEvent.click(screen.getByRole('button', {name: /快手省时间/})); fireEvent.click(screen.getByRole('button', {name: '保存个人偏好'}));
    await screen.findByText('个人偏好已保存。');
    const write = request.mock.calls.find(([, options]) => options?.method === 'PATCH')!;
    expect(JSON.parse(write[1].body)).toEqual({...preference(), summary: undefined, enabled: true, preferences: ['喜欢家常菜']});
  });
  it('clears only the summary and retains both saved preferences and an unrelated unsaved draft', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve({...preference(), ...(options?.method === 'DELETE' ? {revision: 'c'.repeat(64), summary: ''} : {})}));
    render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>); fireEvent.change(await screen.findByRole('textbox', {name: '你希望记住的事'}), {target: {value: '未保存的口味'}}); fireEvent.click(screen.getByRole('tab', {name: '长期记忆'}));
    fireEvent.click(screen.getByRole('button', {name: '清空长期记忆'})); expect(screen.getByRole('alertdialog', {name: '清空长期记忆'})).toHaveTextContent('手写偏好、厨房偏好和历史对话仍保留'); fireEvent.click(screen.getByRole('button', {name: '确认清空长期记忆'}));
    await screen.findByText('长期记忆已清空。'); expect(request).toHaveBeenLastCalledWith('/api/personal-preferences/summary?revision=' + 'a'.repeat(64), {method: 'DELETE'});
    fireEvent.click(screen.getByRole('tab', {name: '个人偏好'})); expect(screen.getByRole('textbox', {name: '你希望记住的事'})).toHaveValue('未保存的口味'); expect(screen.getByRole('button', {name: '保存个人偏好'})).toBeEnabled();
  });
  it('preserves a conflicting draft without silently retrying the stale revision', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => options?.method === 'PATCH' ? Promise.reject(Object.assign(new Error('个人偏好已更新，请重新读取'), {status: 409})) : Promise.resolve(preference()));
    render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>); fireEvent.change(await screen.findByRole('textbox', {name: '你希望记住的事'}), {target: {value: '自己的草稿'}}); fireEvent.click(screen.getByRole('button', {name: '保存个人偏好'}));
    await screen.findByText('个人偏好已更新，请重新读取'); expect(screen.getByRole('textbox', {name: '你希望记住的事'})).toHaveValue('自己的草稿'); expect(request.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', {name: '读取最新'})); fireEvent.click(screen.getByRole('button', {name: '保留草稿'})); expect(screen.getByRole('textbox', {name: '你希望记住的事'})).toHaveValue('自己的草稿');
  });
});
