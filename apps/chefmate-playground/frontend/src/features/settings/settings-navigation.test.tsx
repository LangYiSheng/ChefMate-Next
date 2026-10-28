import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {useRef, useState} from 'react';
import {describe, expect, it, vi} from 'vitest';
import {Field, SettingsGroup, SettingsTabs} from './SettingsFields';
import {SettingsIndex} from './SettingsIndex';
import {SettingsInsights} from './SettingsInsights';
import {ComponentSettings} from './ComponentSettings';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));

describe('settings grouped navigation', () => {
  it('keeps long tool lists compact while making every capability and source detail available', () => {
    const change = vi.fn(), tools = ['create_food_log_draft', 'search_food_logs', 'get_food_log', 'revise_food_log_draft', 'confirm_food_log', 'get_cooked_meal_intake', 'record_cooked_meal_intake'];
    render(<ComponentSettings components={[{id: 'food-journal', name: '饮食记录', description: '记下这一餐', tools}]} values={{'food-journal': true}} change={change} disabled={false}/>);
    expect(screen.getByText('整理饮食记录')).toBeVisible(); expect(screen.getByText('登记本餐食用份额')).not.toBeVisible();
    fireEvent.click(screen.getByText('查看其余 1 项能力')); expect(screen.getByText('登记本餐食用份额')).toBeVisible();
    fireEvent.click(screen.getByRole('switch', {name: '饮食记录'})); expect(change).toHaveBeenCalledWith('food-journal', false);
    fireEvent.click(screen.getByText(/技术信息/)); expect(screen.getByText('record_cooked_meal_intake')).toBeVisible();
  });
  it('shows a single group directly without a redundant tab', () => {
    render(<SettingsTabs items={['更新记录']} label="更新日志内容"><SettingsGroup title="更新记录"><p>本次更新</p></SettingsGroup></SettingsTabs>);
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '更新记录' })).toBeInTheDocument();
    expect(screen.getByRole('region', {name: '更新记录'})).toHaveAttribute('aria-labelledby', screen.getByRole('heading', {name: '更新记录'}).id);
    expect(screen.getByText('本次更新')).toBeVisible();
  });

  it('keeps product, privacy and nutrition information visible together on the about page', async () => {
    request.mockResolvedValue({ name: 'ChefMate', version: '0.2.1', recipe_count: 358 });
    render(<SettingsInsights section="about" refresh={0} />);
    expect(await screen.findByText(/聊天、个人菜谱、饮食记录和长期记忆按账号隔离保存/)).toBeVisible();
    expect(screen.getByRole('heading', { name: 'ChefMate' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '营养数据' })).toBeVisible();
  });

  it('uses the same tabs for related groups, keeping draft fields through mouse and keyboard navigation', () => {
    function Example() {
      const [value, setValue] = useState('未保存的名称');
      return <SettingsTabs items={['连接信息', '连接方式']} label="模型连接内容"><SettingsGroup title="连接信息"><Field label="名称" value={value} onChange={setValue}/></SettingsGroup><SettingsGroup title="连接方式"><p>图片与协议</p></SettingsGroup></SettingsTabs>;
    }
    render(<Example/>);
    fireEvent.change(screen.getByRole('textbox', {name: '名称'}), {target: {value: '保留我的草稿'}});
    fireEvent.keyDown(screen.getByRole('tab', {name: '连接信息'}), {key: 'ArrowRight'});
    expect(screen.getByRole('tab', {name: '连接方式'})).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('textbox', {name: '名称'})).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('tab', {name: '连接方式'}), {key: 'Home'});
    expect(screen.getByRole('textbox', {name: '名称'})).toHaveValue('保留我的草稿');
    expect(screen.getByRole('tab', {name: '连接信息'})).toHaveFocus();
  });

  it('reveals and focuses an invalid field in another group instead of silently blocking save', async () => {
    const submit = vi.fn();
    render(<form onSubmit={event => {event.preventDefault(); submit();}}><SettingsTabs items={['基础', '预算']} label="执行内容"><SettingsGroup title="基础"><Field label="名称" value="已填写" onChange={vi.fn()} required/></SettingsGroup><SettingsGroup title="预算"><Field label="轮次" type="number" value="" onChange={vi.fn()} min={2} max={30}/></SettingsGroup></SettingsTabs><button type="submit">保存</button></form>);
    fireEvent.click(screen.getByRole('button', {name: '保存'}));
    expect(submit).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole('spinbutton', {name: '轮次'})).toHaveFocus());
    expect(screen.getByRole('tab', {name: '预算'})).toHaveAttribute('aria-selected', 'true');
  });

  it('indexes only the visible document, scrolls its own container, and follows the current location', async () => {
    const positions: Record<string, number> = {root: 100, first: 120, second: 700, third: 1000};
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {const top = positions[this.id] || 0; return {x: 0, y: top, width: 900, height: 80, top, left: 0, right: 900, bottom: top + 80, toJSON: () => ({})};});
    function Example({more = false}: {more?: boolean}) {
      const container = useRef<HTMLDivElement>(null);
      return <div ref={container} id="root"><div data-settings-document><section id="first" data-settings-index="0.2.1 更新">第一段</section><section id="second" data-settings-index="0.2.0 更新">第二段</section><section hidden id="hidden" data-settings-index="隐藏的分类">不应进入目录</section>{more && <section id="third" data-settings-index="更早记录">新加载段落</section>}</div><SettingsIndex container={container} section="memory"/></div>;
    }
    const rendered = render(<Example/>), root = rendered.container.querySelector<HTMLDivElement>('#root')!, scroll = vi.fn();
    root.scrollTo = scroll; root.scrollTop = 40;
    const index = screen.getByRole('navigation', {name: '当前设置目录'});
    expect(within(index).queryByRole('button', {name: '隐藏的分类'})).not.toBeInTheDocument();
    fireEvent.click(within(index).getByRole('button', {name: '0.2.0 更新'}));
    expect(scroll).toHaveBeenCalledWith({top: 624, behavior: 'smooth'});
    expect(within(index).getByRole('button', {name: '0.2.0 更新'})).toHaveAttribute('aria-current', 'location');
    positions.first = -500; positions.second = 120; fireEvent.scroll(root);
    expect(within(index).getByRole('button', {name: '0.2.0 更新'})).toHaveAttribute('aria-current', 'location');
    rendered.rerender(<Example more/>);
    expect(await within(index).findByRole('button', {name: '更早记录'})).toBeInTheDocument();
  });

  it('indexes release versions without repeating subtitles or child sections', async () => {
    const titles = ['对话', '界面', '菜谱', '开发者'];
    request.mockResolvedValue({current_version: '0.2.1', releases: [{version: '0.2.1', title: '本次更新', sections: titles.map(title => ({title, items: ['具体变化']}))}, {version: '0.2.0', title: '之前更新', sections: [{title: '历史分节', items: ['保留历史内容']}]}]});
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {const top = this.id === 'log-root' ? 100 : this.id === 'settings-release-1' ? 1000 : 0; return {x: 0, y: top, width: 900, height: 80, top, left: 0, right: 900, bottom: top + 80, toJSON: () => ({})};});
    function Example() {
      const container = useRef<HTMLDivElement>(null);
      return <div ref={container} id="log-root"><div data-settings-document><SettingsInsights section="changelog" refresh={0}/></div><SettingsIndex container={container} section="changelog"/></div>;
    }
    const rendered = render(<Example/>), root = rendered.container.querySelector<HTMLDivElement>('#log-root')!, scroll = vi.fn(); root.scrollTo = scroll; root.scrollTop = 40;
    const index = await screen.findByRole('navigation', {name: '当前设置目录'});
    expect(within(index).getAllByRole('button').map(button => button.textContent)).toEqual(['0.2.1', '0.2.0']);
    for (const title of [...titles, '历史分节', '本次更新', '之前更新', '更新记录']) {
      expect(within(index).queryByRole('button', {name: title})).not.toBeInTheDocument();
    }
    expect(root.querySelector('#settings-release-0-group-3')).toHaveTextContent('开发者');
    fireEvent.click(within(index).getByRole('button', {name: '0.2.0'}));
    expect(scroll).toHaveBeenCalledWith({top: 924, behavior: 'smooth'});
    expect(within(index).getByRole('button', {name: '0.2.0'})).toHaveAttribute('aria-current', 'location');
  });
});
