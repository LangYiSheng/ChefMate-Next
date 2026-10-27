import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {StrictMode} from 'react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {Settings} from './Settings';
import {MemorySettings} from './MemorySettings';
import {advanceRevisions, createDrafts} from './settings-state';
import {PALETTES, FONT_PAIRINGS, FONT_SIZES, normalizeAppearance, resolveAppearance} from './appearance-presets';
import {applyAppearance, readAppearance} from './appearance';
import {SettingsInsights, usageCalendar} from './SettingsInsights';

const {request, readPreferences, savePreferences} = vi.hoisted(() => ({request: vi.fn(), readPreferences: vi.fn(), savePreferences: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
vi.mock('../preferences/client', () => ({readUiPreferences: readPreferences, saveUiPreferences: savePreferences}));
const revision = (value: number) => value.toString(16).padStart(64, '0');
const fixture = () => ({revision: revision(1), api_key_set: true, model: {base_url: 'https://example.test/v1', model: 'test-model', api_format: 'responses', supports_images: true, max_images: 4, stream_tool_calls: true}, runtime: {turn_timeout_s: 90, model_timeout_s: 60, model_max_retries: 1, max_model_rounds: 10, max_tool_calls: 20}, context: {max_history_chars: 400000, max_tool_result_chars: 60000}, interaction: {reply_suggestions: true, reply_suggestion_count: 3}, cooking: {timer_limit: 3, clear_timer_on_step_change: false}, components: [], available_components: [], enabled_components: []});
const preferences = () => ({revision: revision(100), initialized: true, values: {...normalizeAppearance(), reply_suggestions: true, reply_suggestion_count: 3}});
const choose = (label: string, option: string) => {fireEvent.click(screen.getByRole('combobox', {name: label})); fireEvent.click(screen.getByRole('option', {name: option}));};
beforeEach(() => {localStorage.clear(); request.mockReset(); readPreferences.mockReset(); savePreferences.mockReset(); readPreferences.mockResolvedValue(preferences()); savePreferences.mockImplementation(async changes => ({...preferences(), revision: revision(101), values: {...preferences().values, ...changes}})); request.mockImplementation((path: string) => Promise.resolve(path === '/api/settings' ? fixture() : {ready: true, model: 'test-model', database: {ready: true}}));});

describe('React settings transactions', () => {
  it('previews the three-step font size draft and saves only that field without applying unsaved size globally', async () => {
    const view = render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式');
    const slider = screen.getByRole('slider', {name: '字号'}), initialRootSize = document.documentElement.style.fontSize;
    expect(slider).toHaveValue('1'); expect(slider).toHaveAttribute('aria-valuetext', '中');
    expect(slider).toHaveAttribute('min', '0'); expect(slider).toHaveAttribute('max', '2'); expect(slider).toHaveAttribute('step', '1');
    fireEvent.change(slider, {target: {value: '0'}});
    expect(slider).toHaveAttribute('aria-valuetext', '小');
    expect(view.container.querySelector('.appearance-preview')).toHaveAttribute('data-font-size', 'small');
    expect((view.container.querySelector('.appearance-preview') as HTMLElement).style.getPropertyValue('--font-size-base')).toBe('14px');
    expect(document.documentElement.style.fontSize).toBe(initialRootSize);
    expect(savePreferences).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', {name: '对话建议'}));
    fireEvent.click(screen.getByRole('tab', {name: '配色与字体'}));
    expect(screen.getByRole('slider', {name: '字号'})).toHaveValue('0');
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(savePreferences).toHaveBeenCalledExactlyOnceWith({font_size: 'small'}, revision(100)));
    await waitFor(() => expect(screen.getByRole('button', {name: '保存修改'})).toBeDisabled());
  });

  it('keeps the short about and data sections on one page instead of hiding each paragraph behind a tab', async () => {
    render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式');
    fireEvent.click(screen.getByRole('button', {name: '关于 ChefMate'}));
    expect(await screen.findByText(/聊天、个人菜谱、饮食记录和长期记忆保存在本机/)).toBeVisible();
    expect(screen.getByRole('heading', {name: '营养数据'})).toBeVisible();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '数据与状态'}));
    expect(screen.getByRole('heading', {name: '当前连接'})).toBeVisible();
    expect(screen.getByRole('heading', {name: '本机保存'})).toBeVisible();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });

  it('keeps dirty drafts in other categories and advances only accepted revision baselines', async () => {
    let settings = fixture(), writes = 1;
    request.mockImplementation((path: string, options: RequestInit = {}) => {
      if (options.method === 'PATCH') {const body = JSON.parse(options.body as string); expect(body.revision).toBe(settings.revision); settings = {...settings, revision: revision(++writes), [body.section]: body.values}; return Promise.resolve(settings);}
      return Promise.resolve(path === '/api/settings' ? settings : {database: {ready: true}});
    });
    render(<Settings onClose={vi.fn()}/>);
    await screen.findByLabelText('显示模式');
    fireEvent.click(screen.getByRole('button', {name: '模型连接'}));
    fireEvent.change(screen.getByLabelText('模型名称'), {target: {value: 'next-model'}});
    fireEvent.click(screen.getByRole('button', {name: '烹饪与计时'}));
    choose('同时进行的计时器', '单计时器');
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(settings.revision).toBe(revision(2)));
    await waitFor(() => expect(screen.getByRole('button', {name: /模型连接/})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: /模型连接/}));
    expect(screen.getByLabelText('模型名称')).toHaveValue('next-model');
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(settings.revision).toBe(revision(3)));
    expect(settings.model.model).toBe('next-model');
    expect(advanceRevisions({one: revision(1), two: revision(8)}, revision(1), revision(2))).toEqual({one: revision(2), two: revision(8)});
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
    fireEvent.click(screen.getByRole('tab', {name: '对话显示'})); choose('内容间距', '紧凑 · 同一屏显示更多内容');
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
    expect(memory.enabled).toBe(false); await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(false));
    fireEvent.click(screen.getByRole('button', {name: '清空记忆'})); fireEvent.click(screen.getByRole('button', {name: '确认清空'})); await screen.findByText('记忆已清空。'); expect(memory.user_notes).toBe('');
  });

  it('validates a cleared numeric field locally instead of posting an empty string', async () => {
    render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式');
    fireEvent.click(screen.getByRole('button', {name: '上下文预算'})); const input = screen.getByLabelText('会话上下文上限');
    fireEvent.change(input, {target: {value: ''}}); expect(input).toBeRequired(); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    expect(request.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(false); expect(input).toHaveValue(null);
  });

  it('keeps browser-only kitchen preference fields out of the strict server contract', () => {
    localStorage.setItem('chefmate:kitchen-preferences', JSON.stringify({timer_sound: false, obsolete_field: 'must-not-be-sent'}));
    expect(createDrafts(fixture()).cooking).toEqual({timer_limit: 3, clear_timer_on_step_change: false, timer_sound: false});
  });

  it('uses server display values and saves only the changed preference fields with their independent revision', async () => {
    localStorage.setItem('chefmate:ui-settings', JSON.stringify({theme: 'dark', density: 'compact'}));
    savePreferences.mockImplementation(async changes => {const accepted = {...preferences(), revision: revision(101), values: {...preferences().values, ...changes}}; window.dispatchEvent(new CustomEvent('chefmate:ui-preferences', {detail: accepted})); return accepted;});
    const onSaved = vi.fn(); render(<Settings onClose={vi.fn()} onSaved={onSaved}/>); await screen.findByRole('combobox', {name: '显示模式'});
    expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('浅色'); choose('显示模式', '深色');
    fireEvent.click(screen.getByRole('tab', {name: '对话建议'})); fireEvent.click(screen.getByRole('switch', {name: '显示推荐回复'})); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(screen.getByRole('button', {name: '保存修改'})).toBeDisabled());
    expect(savePreferences).toHaveBeenCalledExactlyOnceWith({theme: 'dark', reply_suggestions: false}, revision(100));
    expect(request.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(false); expect(onSaved).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('tab', {name: '配色与字体'})); expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('深色');
  });

  it('adopts clean external preferences, preserves dirty drafts and revision on conflict, and explicitly reloads only the selected draft', async () => {
    render(<Settings onClose={vi.fn()}/>); await screen.findByRole('combobox', {name: '显示模式'});
    const external = {...preferences(), revision: revision(110), values: {...preferences().values, theme: 'dark'}};
    act(() => {window.dispatchEvent(new CustomEvent('chefmate:ui-preferences', {detail: external}));});
    expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('深色'); choose('显示模式', '跟随系统');
    const newer = {...external, revision: revision(111), values: {...external.values, theme: 'light'}};
    act(() => {window.dispatchEvent(new CustomEvent('chefmate:ui-preferences', {detail: newer}));});
    expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('跟随系统');
    savePreferences.mockRejectedValueOnce(new Error('偏好已在其他位置更新，草稿已保留。'));
    fireEvent.click(screen.getByRole('button', {name: '保存修改'})); await screen.findAllByText('偏好已在其他位置更新，草稿已保留。');
    expect(savePreferences).toHaveBeenCalledWith({theme: 'system'}, revision(110));
    fireEvent.click(screen.getByRole('button', {name: '重新读取'})); fireEvent.click(screen.getByRole('button', {name: '继续编辑'}));
    expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('跟随系统');
    readPreferences.mockResolvedValue(newer);
    fireEvent.click(screen.getByRole('button', {name: '重新读取'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改'}));
    await waitFor(() => expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('浅色'));
    expect(screen.getByRole('button', {name: '保存修改'})).toBeDisabled();
  });

  it('queues an initial preferences seed event received during loading instead of keeping an uninitialized revision', async () => {
    let finish!: (value: any) => void;
    request.mockImplementation((path: string) => path === '/api/settings' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve({ready: true}));
    readPreferences.mockResolvedValueOnce({...preferences(), revision: revision(0), initialized: false});
    render(<Settings onClose={vi.fn()}/>);
    const seeded = {...preferences(), revision: revision(150), values: {...preferences().values, theme: 'dark'}};
    readPreferences.mockResolvedValue(seeded);
    act(() => {window.dispatchEvent(new CustomEvent('chefmate:ui-preferences', {detail: seeded}));});
    await act(async () => {finish(fixture());});
    expect(await screen.findByRole('combobox', {name: '显示模式'})).toHaveTextContent('深色');
    choose('显示模式', '跟随系统'); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({theme: 'system'}, revision(150)));
  });

  it('rechecks authoritative preferences when an older event is queued behind a newer GET and slow settings read', async () => {
    let finish!: (value: any) => void;
    const newer = {...preferences(), revision: revision(202), values: {...preferences().values, theme: 'dark'}};
    const older = {...preferences(), revision: revision(201), values: {...preferences().values, theme: 'light'}};
    readPreferences.mockResolvedValue(newer);
    request.mockImplementation((path: string) => path === '/api/settings' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve({ready: true}));
    render(<Settings onClose={vi.fn()}/>);
    await act(async () => {});
    act(() => {window.dispatchEvent(new CustomEvent('chefmate:ui-preferences', {detail: older}));});
    await act(async () => {finish(fixture());});
    expect(await screen.findByRole('combobox', {name: '显示模式'})).toHaveTextContent('深色');
    expect(readPreferences).toHaveBeenCalledTimes(2);
    choose('显示模式', '跟随系统'); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({theme: 'system'}, revision(202)));
  });

  it('keeps a dirty display baseline through queued-event revalidation while reloading another category', async () => {
    const baseline = {...preferences(), revision: revision(202)}, newer = {...preferences(), revision: revision(203), values: {...preferences().values, theme: 'dark'}};
    readPreferences.mockResolvedValue(baseline);
    render(<Settings onClose={vi.fn()}/>); await screen.findByRole('combobox', {name: '显示模式'}); choose('显示模式', '跟随系统');
    fireEvent.click(screen.getByRole('button', {name: '数据与状态'}));
    let finish!: (value: any) => void;
    request.mockImplementation((path: string) => path === '/api/settings' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve({ready: true}));
    readPreferences.mockResolvedValue(newer);
    fireEvent.click(screen.getByRole('button', {name: '重新读取'}));
    await act(async () => {});
    act(() => {window.dispatchEvent(new CustomEvent('chefmate:ui-preferences', {detail: {...baseline, revision: revision(201)}}));});
    await act(async () => {finish(fixture());});
    fireEvent.click(screen.getByRole('button', {name: /外观与对话/}));
    expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('跟随系统');
    savePreferences.mockRejectedValueOnce(new Error('偏好已在其他位置更新，草稿已保留。'));
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(savePreferences).toHaveBeenCalledWith({theme: 'system'}, revision(202)));
    await screen.findAllByText('偏好已在其他位置更新，草稿已保留。');
    expect(screen.getByRole('combobox', {name: '显示模式'})).toHaveTextContent('跟随系统');
  });

  it('does not let a local-only save promote other server drafts past an externally changed revision', async () => {
    let settings = fixture(); const writes: any[] = [];
    request.mockImplementation((path: string, options: RequestInit = {}) => {if (options.method === 'PATCH') {writes.push(JSON.parse(options.body as string)); return Promise.reject(new Error('设置已在另一页面更新；当前草稿保留。'));} return Promise.resolve(path === '/api/settings' ? settings : {database: {ready: true}});});
    render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式'); fireEvent.click(screen.getByRole('button', {name: '模型连接'}));
    fireEvent.change(screen.getByLabelText('模型名称'), {target: {value: 'kept-draft'}}); fireEvent.click(screen.getByRole('button', {name: '开发者'})); fireEvent.click(screen.getByRole('switch', {name: '开发者模式'}));
    settings = {...settings, revision: revision(2), runtime: {...settings.runtime, max_tool_calls: 25}};
    fireEvent.click(screen.getByRole('button', {name: '数据与状态'})); fireEvent.click(screen.getByRole('button', {name: '重新读取'})); await waitFor(() => expect(screen.getByRole('button', {name: '重新读取'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: /开发者/})); fireEvent.click(screen.getByRole('button', {name: '保存修改'})); await screen.findAllByText('设置已保存。接下来的对话使用新设置。');
    fireEvent.click(screen.getByRole('button', {name: /模型连接/})); expect(screen.getByLabelText('模型名称')).toHaveValue('kept-draft'); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await screen.findAllByText('设置已在另一页面更新；当前草稿保留。'); expect(writes).toHaveLength(1); expect(writes[0].revision).toBe(revision(1));
  });

  it('loads correctly under StrictMode and ignores the aborted first snapshot', async () => {
    const resolve: ((value: any) => void)[] = [];
    request.mockImplementation((path: string) => path === '/api/settings' ? new Promise(done => resolve.push(done)) : Promise.resolve({database: {ready: true}}));
    render(<StrictMode><Settings onClose={vi.fn()}/></StrictMode>);
    await waitFor(() => expect(resolve).toHaveLength(2)); const latest = {...fixture(), revision: revision(2), model: {...fixture().model, model: 'latest-model'}};
    await act(async () => {resolve[1](latest);}); await screen.findByLabelText('显示模式'); fireEvent.click(screen.getByRole('button', {name: '模型连接'}));
    expect(screen.getByLabelText('模型名称')).toHaveValue('latest-model'); await act(async () => {resolve[0](fixture());});
    expect(screen.getByLabelText('模型名称')).toHaveValue('latest-model');
  });

  it('recovers a memory conflict without silently losing the user draft', async () => {
    const original = {revision: 1, user_notes: '少盐', summary: '', enabled: true}, updated = {...original, revision: 2, user_notes: '少盐，少油'}; let reads = 0;
    request.mockImplementation((_path: string, options: RequestInit = {}) => options.method === 'PATCH' ? Promise.reject(new Error('记忆已更新，你的草稿仍保留；请重新读取并核对。')) : Promise.resolve(++reads === 1 ? original : updated));
    render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>);
    fireEvent.change(await screen.findByLabelText('你希望记住的事'), {target: {value: '少盐，不要香菜'}}); fireEvent.click(screen.getByRole('button', {name: '保存记忆'}));
    await screen.findByText('记忆已更新，你的草稿仍保留；请重新读取并核对。'); expect(screen.getByLabelText('你希望记住的事')).toHaveValue('少盐，不要香菜');
    fireEvent.click(screen.getByRole('button', {name: '读取最新'})); fireEvent.click(screen.getByRole('button', {name: '保留草稿'})); expect(reads).toBe(1);
    fireEvent.click(screen.getByRole('button', {name: '读取最新'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改并读取'})); await screen.findByText('已读取最新记忆。'); expect(screen.getByLabelText('你希望记住的事')).toHaveValue(updated.user_notes);
  });

  it('retries an initial memory read failure', async () => {
    request.mockRejectedValueOnce(new Error('本次读取失败')).mockResolvedValue({revision: 1, user_notes: '清淡', summary: '', enabled: true});
    render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>); await screen.findByText('本次读取失败');
    fireEvent.click(screen.getByRole('button', {name: '重新读取记忆'})); expect(await screen.findByLabelText('你希望记住的事')).toHaveValue('清淡');
  });
});

describe('appearance and usage semantics', () => {
  it('retains every palette and font choice and safely normalizes corrupt preferences', () => {
    expect(PALETTES).toHaveLength(12); expect(FONT_PAIRINGS).toHaveLength(4);
    expect(resolveAppearance({theme: 'system', dark_palette: 'deep-blue', font_pairing: 'wenkai'}, true).palette.id).toBe('deep-blue');
    expect(normalizeAppearance({theme: 'invalid', light_palette: 'graphite'})).toMatchObject({theme: 'light', light_palette: 'ink'});
    localStorage.setItem('chefmate:ui-settings', '{broken'); expect(readAppearance().font_pairing).toBe('mixed');
    expect(normalizeAppearance().font_size).toBe('medium');
    expect(normalizeAppearance({font_size: 'giant'}).font_size).toBe('medium');
    expect(FONT_SIZES.map(size => [size.id, size.pixels])).toEqual([['small', 14], ['medium', 15], ['large', 16]]);
  });
  it.each(FONT_SIZES)('applies $id size to the shared typography root without scaling the page geometry', size => {
    applyAppearance({font_size: size.id});
    expect(document.documentElement.dataset.fontSize).toBe(size.id);
    expect(document.documentElement.style.getPropertyValue('--font-size-base')).toBe(`${size.pixels}px`);
    expect(document.documentElement.style.fontSize).toBe('var(--font-size-base, 15px)');
    expect(document.documentElement.style.transform).toBe('');
    expect(document.documentElement.style.zoom).toBe('');
  });
  it('distinguishes partial, missing and zero reported token days', () => {
    const calendar = usageCalendar({period: {from: '2026-10-20', to: '2026-10-23'}, daily: [{date: '2026-10-20', model_calls: 2, known_totals: {total_tokens: 50}, coverage: {total_tokens: {known: 1, calls: 2}}}, {date: '2026-10-21', model_calls: 1}, {date: '2026-10-22', model_calls: 1, known_totals: {total_tokens: 0}}]});
    expect(calendar.days.map(day => day.status)).toEqual(['partial', 'unknown', 'complete', 'empty']);
    expect(calendar.days[1].value).toBeNull(); expect(calendar.days[2].value).toBe(0);
  });

  it('ignores a late insights response after changing categories', async () => {
    const responses: Record<string, (value: any) => void> = {};
    request.mockImplementation((path: string) => new Promise(resolve => {responses[path] = resolve;}));
    const {rerender} = render(<SettingsInsights section="about" refresh={0}/>); rerender(<SettingsInsights section="usage" refresh={0}/>);
    await act(async () => {responses['/api/usage?days=365']({summary: {model_calls: 1, known_totals: {input_tokens: 123, total_tokens: 123}}, models: []});});
    expect(screen.getAllByText('123', {selector: '.insight-metric dd'})).toHaveLength(2);
    await act(async () => {responses['/api/about']({name: 'stale-about-title', version: '0.2.0'});});
    expect(screen.queryByText('stale-about-title')).not.toBeInTheDocument(); expect(screen.getAllByText('123', {selector: '.insight-metric dd'})).toHaveLength(2);
  });

  it('applies the selected palette, semantic fonts, metadata and themed favicon together', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<svg><g color="#343d38" fill="currentColor"/></svg>'));
    document.head.innerHTML = '<meta name="theme-color"><link rel="icon" href="/static/brand/chef-buddy.svg">';
    applyAppearance({theme: 'dark', dark_palette: 'cocoa', font_pairing: 'wenkai', show_estimates: false, density: 'compact', inline_limit: 1});
    expect(document.documentElement.dataset).toMatchObject({theme: 'dark', palette: 'cocoa', fontPairing: 'wenkai', showEstimates: 'false', density: 'compact'});
    expect(document.documentElement.style.getPropertyValue('--color-accent')).toBe('#dfbd94'); expect(document.documentElement.style.getPropertyValue('--font-body')).toContain('ChefMate WenKai');
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute('content', '#241c19');
    await waitFor(() => expect(decodeURIComponent(document.querySelector('link[rel="icon"]')!.getAttribute('href')!)).toContain('color="#dfbd94"'));
    expect(fetcher).toHaveBeenCalledWith('/static/brand/chef-buddy.svg');
    expect(normalizeAppearance({density: 'giant', inline_limit: 9})).toMatchObject({density: 'comfortable', inline_limit: 2});
  });
});
