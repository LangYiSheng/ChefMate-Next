import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {Settings} from './Settings';
import {MemorySettings} from './MemorySettings';
import {advanceRevisions} from './settings-state';
import {PALETTES, FONT_PAIRINGS, normalizeAppearance, resolveAppearance} from './appearance-presets';
import {readAppearance} from './appearance';
import {usageCalendar} from './SettingsInsights';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const fixture = () => ({revision: 1, api_key_set: true, model: {base_url: 'https://example.test/v1', model: 'test-model', api_format: 'responses', supports_images: true, max_images: 4, stream_tool_calls: true}, runtime: {turn_timeout_s: 90, model_timeout_s: 60, model_max_retries: 1, max_model_rounds: 10, max_tool_calls: 20}, context: {max_history_chars: 400000, max_tool_result_chars: 60000}, interaction: {reply_suggestions: true, reply_suggestion_count: 3}, cooking: {timer_limit: 3, clear_timer_on_step_change: false}, components: [], available_components: [], enabled_components: []});
beforeEach(() => {localStorage.clear(); request.mockReset(); request.mockImplementation((path: string) => Promise.resolve(path === '/api/settings' ? fixture() : {ready: true, model: 'test-model', database: {ready: true}}));});

describe('React settings transactions', () => {
  it('keeps dirty drafts in other categories and advances only accepted revision baselines', async () => {
    let settings = fixture();
    request.mockImplementation((path: string, options: RequestInit = {}) => {
      if (options.method === 'PATCH') {const body = JSON.parse(options.body as string); expect(body.revision).toBe(settings.revision); settings = {...settings, revision: settings.revision + 1, [body.section]: body.values}; return Promise.resolve(settings);}
      return Promise.resolve(path === '/api/settings' ? settings : {database: {ready: true}});
    });
    render(<Settings onClose={vi.fn()}/>);
    await screen.findByLabelText('显示模式');
    fireEvent.click(screen.getByRole('button', {name: '模型连接'}));
    fireEvent.change(screen.getByLabelText('模型名称'), {target: {value: 'next-model'}});
    fireEvent.click(screen.getByRole('button', {name: '烹饪与计时'}));
    fireEvent.change(screen.getByLabelText('同时进行的计时器'), {target: {value: '1'}});
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(settings.revision).toBe(2));
    await waitFor(() => expect(screen.getByRole('button', {name: /模型连接/})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: /模型连接/}));
    expect(screen.getByLabelText('模型名称')).toHaveValue('next-model');
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(settings.revision).toBe(3));
    expect(settings.model.model).toBe('next-model');
    expect(advanceRevisions({one: 1, two: 8}, 1, 2)).toEqual({one: 2, two: 8});
  });

  it('preserves the model draft after a conflict and asks before resuming external navigation', async () => {
    request.mockImplementation((path: string, options: RequestInit = {}) => options.method === 'PATCH' ? Promise.reject(new Error('设置已在另一页面更新；当前草稿保留。')) : Promise.resolve(path === '/api/settings' ? fixture() : {database: {ready: true}}));
    render(<Settings onClose={vi.fn()}/>);
    await screen.findByLabelText('显示模式'); fireEvent.click(screen.getByRole('button', {name: '模型连接'}));
    fireEvent.change(screen.getByLabelText('模型名称'), {target: {value: 'my-draft'}}); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await screen.findAllByText('设置已在另一页面更新；当前草稿保留。');
    expect(screen.getByLabelText('模型名称')).toHaveValue('my-draft');
    const resume = vi.fn(), event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'recipes', resume}});
    act(() => {window.dispatchEvent(event);}); expect(event.defaultPrevented).toBe(true); expect(resume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: '继续编辑'})); expect(screen.getByLabelText('模型名称')).toHaveValue('my-draft');
    act(() => {window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'recipes', resume}}));});
    fireEvent.click(screen.getByRole('button', {name: '放弃修改'})); expect(resume).toHaveBeenCalledOnce();
  });

  it('confirms an internal return once even when the app dispatches its navigation guard', async () => {
    let navigation: CustomEvent | undefined;
    const close = vi.fn(() => {navigation = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'chat', resume: vi.fn()}}); window.dispatchEvent(navigation);});
    render(<Settings onClose={close}/>); await screen.findByLabelText('显示模式');
    fireEvent.change(screen.getByLabelText('内容间距'), {target: {value: 'compact'}});
    fireEvent.click(screen.getByRole('button', {name: '返回工作台'}));
    expect(close).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', {name: '放弃修改'}));
    expect(close).toHaveBeenCalledOnce(); expect(navigation?.defaultPrevented).toBe(false); expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('retains memory edits between sections and uses the memory revision for save and clear', async () => {
    let memory = {revision: 4, user_notes: '不吃香菜', summary: '偏好清淡', enabled: true};
    request.mockImplementation((path: string, options: RequestInit = {}) => {
      if (path === '/api/memory' && options.method === 'PATCH') {const payload = JSON.parse(options.body as string); expect(payload.revision).toBe(4); memory = {...memory, ...payload, revision: 5}; return Promise.resolve(memory);}
      if (path.startsWith('/api/memory?')) {expect(path).toBe('/api/memory?revision=5'); memory = {...memory, user_notes: '', summary: '', revision: 6}; return Promise.resolve(memory);}
      return Promise.resolve(memory);
    });
    const dirty = vi.fn(), onBusy = vi.fn(); const {rerender} = render(<MemorySettings active onDirty={dirty} onBusy={onBusy}/>);
    fireEvent.change(await screen.findByLabelText('你希望记住的事'), {target: {value: '不吃香菜，少辣'}});
    fireEvent.click(screen.getByRole('switch')); rerender(<MemorySettings active={false} onDirty={dirty} onBusy={onBusy}/>); rerender(<MemorySettings active onDirty={dirty} onBusy={onBusy}/>);
    expect(screen.getByLabelText('你希望记住的事')).toHaveValue('不吃香菜，少辣');
    fireEvent.click(screen.getByRole('button', {name: '保存记忆'})); await screen.findByText('记忆已保存。');
    expect(memory.enabled).toBe(false); expect(dirty).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole('button', {name: '清空记忆'})); fireEvent.click(screen.getByRole('button', {name: '确认清空'})); await screen.findByText('记忆已清空。'); expect(memory.user_notes).toBe('');
  });
});

describe('appearance and usage semantics', () => {
  it('retains every palette and font choice and safely normalizes corrupt preferences', () => {
    expect(PALETTES).toHaveLength(12); expect(FONT_PAIRINGS).toHaveLength(4);
    expect(resolveAppearance({theme: 'system', dark_palette: 'deep-blue', font_pairing: 'wenkai'}, true).palette.id).toBe('deep-blue');
    expect(normalizeAppearance({theme: 'invalid', light_palette: 'graphite'})).toMatchObject({theme: 'light', light_palette: 'ink'});
    localStorage.setItem('chefmate:ui-settings', '{broken'); expect(readAppearance().font_pairing).toBe('mixed');
  });
  it('distinguishes partial, missing and zero reported token days', () => {
    const calendar = usageCalendar({period: {from: '2026-10-20', to: '2026-10-23'}, daily: [{date: '2026-10-20', model_calls: 2, known_totals: {total_tokens: 50}, coverage: {total_tokens: {known: 1, calls: 2}}}, {date: '2026-10-21', model_calls: 1}, {date: '2026-10-22', model_calls: 1, known_totals: {total_tokens: 0}}]});
    expect(calendar.days.map(day => day.status)).toEqual(['partial', 'unknown', 'complete', 'empty']);
    expect(calendar.days[1].value).toBeNull(); expect(calendar.days[2].value).toBe(0);
  });
});
