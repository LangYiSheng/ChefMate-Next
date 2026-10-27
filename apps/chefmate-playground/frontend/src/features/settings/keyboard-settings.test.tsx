import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {Settings} from './Settings';
import {ComponentSettings} from './ComponentSettings';
import {normalizeAppearance} from './appearance-presets';
import {readKeyboardPreferences} from './keyboard-preferences';

const {request, readPreferences} = vi.hoisted(() => ({request: vi.fn(), readPreferences: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
vi.mock('../preferences/client', () => ({readUiPreferences: readPreferences, saveUiPreferences: vi.fn()}));
const component = {id: 'meal-planning', name: '选菜与餐单', description: '查询菜谱并安排餐单。', tools: ['search_recipes', 'third_party_extension'], ui: {kinds: ['meal_plan'], pages: ['recipes']}, instructions: {entrypoint: 'skills/meal.md', loading: 'on_demand'}, dependencies: ['retrieval'], available: true};
const fixture = () => ({revision: 'revision-1', model: {}, context: {}, runtime: {}, cooking: {timer_limit: 3, clear_timer_on_step_change: false}, interaction: {}, available_components: [component.id], enabled_components: [component.id], components: [component]});
beforeEach(() => {
  localStorage.clear(); request.mockReset(); readPreferences.mockReset();
  readPreferences.mockResolvedValue({revision: 'preferences-1', initialized: true, values: {...normalizeAppearance()}});
  request.mockImplementation((path: string, options: RequestInit = {}) => Promise.resolve(options.method === 'PATCH' ? {...fixture(), revision: 'revision-2'} : path === '/api/settings' ? fixture() : {ready: true}));
});
const choose = (name: string, value: string) => {fireEvent.click(screen.getByRole('combobox', {name})); fireEvent.click(screen.getByRole('option', {name: value}));};

describe('settings organization and keyboard transactions', () => {
  it('keeps missing component availability unknown instead of claiming readiness', () => {
    render(<ComponentSettings components={[{...component, available: undefined}]} values={{[component.id]: true}} change={vi.fn()} disabled={false}/>);
    fireEvent.click(screen.getByText('技术信息')); expect(screen.getByText('尚未报告')).toBeVisible(); expect(screen.queryByText('已就绪')).not.toBeInTheDocument();
  });
  it('saves a keyboard draft locally, keeps other drafts and restores it after reopening', async () => {
    const view = render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式');
    fireEvent.click(screen.getByRole('button', {name: '模型连接'})); fireEvent.change(screen.getByLabelText('模型名称'), {target: {value: 'keep-model-draft'}});
    fireEvent.click(screen.getByRole('button', {name: '快捷键'})); choose('发送快捷键', 'Ctrl / ⌘ + Enter');
    expect(readKeyboardPreferences().send_mode).toBe('enter');
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await screen.findByText('快捷键已保存，当前浏览器立即生效。', {selector: '.settings-success'}); expect(readKeyboardPreferences().send_mode).toBe('mod-enter');
    expect(request.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(false);
    fireEvent.click(screen.getByRole('tab', {name: '浏览与返回'})); expect(screen.getByText('Esc')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: /模型连接/})); expect(screen.getByLabelText('模型名称')).toHaveValue('keep-model-draft');
    view.unmount(); render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式'); fireEvent.click(screen.getByRole('button', {name: '快捷键'}));
    expect(screen.getByRole('combobox', {name: '发送快捷键'})).toHaveTextContent('Ctrl / ⌘ + Enter');
  });

  it('retains a keyboard draft and displays an error if browser storage rejects the save', async () => {
    render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式'); fireEvent.click(screen.getByRole('button', {name: '快捷键'})); choose('发送快捷键', 'Ctrl / ⌘ + Enter');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {throw new Error('浏览器无法保存快捷键');});
    fireEvent.click(screen.getByRole('button', {name: '保存修改'})); await screen.findAllByText('浏览器无法保存快捷键');
    expect(screen.getByRole('combobox', {name: '发送快捷键'})).toHaveTextContent('Ctrl / ⌘ + Enter'); expect(readKeyboardPreferences().send_mode).toBe('enter');
    await waitFor(() => expect(screen.getByRole('button', {name: '保存修改'})).toBeEnabled());
  });

  it('groups timer rules and sound with a contextual preview action while preserving a hidden draft', async () => {
    render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式'); fireEvent.click(screen.getByRole('button', {name: '烹饪与计时'}));
    choose('同时进行的计时器', '单计时器'); fireEvent.click(screen.getByRole('tab', {name: '声音提醒'}));
    const sound = screen.getByRole('switch', {name: '到时声音提醒'}), row = sound.closest('.setting-field')!;
    expect(within(row as HTMLElement).getByRole('button', {name: '试听提醒声'})).toBeVisible(); fireEvent.click(sound);
    fireEvent.click(screen.getByRole('tab', {name: '计时规则'})); expect(screen.getByRole('combobox', {name: '同时进行的计时器'})).toHaveTextContent('单计时器');
    fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(request.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(true));
    const patch = JSON.parse(request.mock.calls.find(([, options]) => options?.method === 'PATCH')![1].body);
    expect(patch).toEqual({revision: 'revision-1', section: 'cooking', values: {timer_limit: 1, clear_timer_on_step_change: false}});
    expect(JSON.parse(localStorage.getItem('chefmate:kitchen-preferences')!)).toEqual({timer_sound: false});
  });

  it('renders real component capabilities and hides source identifiers in expandable technical details', async () => {
    render(<Settings onClose={vi.fn()}/>); await screen.findByLabelText('显示模式'); fireEvent.click(screen.getByRole('button', {name: '系统组件'}));
    expect(screen.getByText('筛选菜谱')).toBeVisible(); expect(screen.getByText('餐单与份量')).toBeVisible();
    expect(screen.getByText('search_recipes')).not.toBeVisible(); expect(screen.getByText('third_party_extension')).not.toBeVisible();
    fireEvent.click(screen.getByText('技术信息'));
    expect(screen.getByText('search_recipes')).toBeVisible(); expect(screen.getByText('skills/meal.md')).toBeVisible();
    fireEvent.click(screen.getByRole('switch', {name: '选菜与餐单'})); fireEvent.click(screen.getByRole('button', {name: '保存修改'}));
    await waitFor(() => expect(request.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(true));
    const patch = JSON.parse(request.mock.calls.find(([, options]) => options?.method === 'PATCH')![1].body);
    expect(patch.values).toEqual({enabled_components: []});
  });
});
