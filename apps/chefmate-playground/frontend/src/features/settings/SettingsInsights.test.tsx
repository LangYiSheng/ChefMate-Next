import {useState} from 'react';
import {act, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {api} from '../../runtime/http';
import {SettingsInsights, UsageTimeRange} from './SettingsInsights';
import {SettingsTabs} from './SettingsFields';

vi.mock('../../runtime/http', async importOriginal => ({...await importOriginal<typeof import('../../runtime/http')>(), api: vi.fn()}));
const request = vi.mocked(api);
const usage = (tokens: number, date: string) => ({
  summary: {model_calls: 1, known_totals: {input_tokens: tokens, output_tokens: 10, total_tokens: tokens + 10}},
  period: {from: date, to: date}, daily: [{date, model_calls: 1, known_totals: {total_tokens: tokens + 10}}],
  models: [{model: 'test-model', known_totals: {input_tokens: tokens, output_tokens: 10}}],
});
beforeEach(() => {request.mockReset();});

describe('usage summary and daily trend', () => {
  it('keeps one range above the tabs and shares its selection across metrics, calendar and models', async () => {
    request.mockImplementation(path => Promise.resolve(usage(path.includes('days=7') ? 70 : 3650, path.includes('days=7') ? '2026-10-27' : '2026-10-26')));
    function Example() {
      const [days, setDays] = useState('365');
      return <><UsageTimeRange value={days} onChange={setDays}/><SettingsTabs items={['用量概览', '按模型统计']} label="Token 用量内容"><SettingsInsights section="usage" refresh={0} usageDays={days}/></SettingsTabs></>;
    }
    render(<Example/>);
    const range = screen.getByRole('combobox', {name: 'Token统计时间范围'});
    const tabs = screen.getByRole('tablist');
    expect(range.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(range).toHaveTextContent('最近一年');
    const overview = await screen.findByRole('tabpanel', {name: '用量概览'});
    expect(within(overview).getByText('3,650', {selector: '.insight-metric dd'})).toBeInTheDocument();
    expect(within(overview).getByRole('heading', {name: '每日用量'})).toBeInTheDocument();
    expect(within(overview).getByRole('button', {name: '2026-10-26 · 3,660 Token · 1 次请求'})).toBeInTheDocument();
    expect(screen.queryByRole('tab', {name: '每日趋势'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', {name: '按模型统计'}));
    expect(screen.getByRole('tabpanel', {name: '按模型统计'})).toHaveTextContent('test-model');
    expect(screen.getByRole('combobox', {name: 'Token统计时间范围'})).toBe(range);
    fireEvent.click(range); fireEvent.click(screen.getByRole('option', {name: '最近 7 天'}));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith('/api/usage?days=7', expect.anything()));
    await waitFor(() => expect(screen.getByRole('tabpanel', {name: '按模型统计'})).toHaveTextContent('70 / 10'));
    fireEvent.click(screen.getByRole('tab', {name: '用量概览'}));
    expect(screen.getByRole('tabpanel', {name: '用量概览'})).toHaveTextContent('70');
    expect(screen.getByRole('button', {name: '2026-10-27 · 80 Token · 1 次请求'})).toBeInTheDocument();
    expect(range).toHaveTextContent('最近 7 天');
    expect(screen.getAllByRole('combobox', {name: 'Token统计时间范围'})).toHaveLength(1);
  });

  it('keeps the range usable during loading and after an error while ignoring superseded responses', async () => {
    const pending: Record<string, {resolve: (value: any) => void; reject: (error: Error) => void}> = {};
    request.mockImplementation(path => new Promise((resolve, reject) => {pending[path] = {resolve, reject};}));
    render(<SettingsInsights section="usage" refresh={0}/>);
    const range = screen.getByRole('combobox', {name: 'Token统计时间范围'});
    expect(screen.getByRole('status')).toHaveTextContent('正在读取');
    fireEvent.click(range); fireEvent.click(screen.getByRole('option', {name: '最近 7 天'}));
    await act(async () => {pending['/api/usage?days=7'].reject(new Error('暂时无法读取'));});
    expect(screen.getByRole('status')).toHaveTextContent('暂时无法读取');
    expect(range).toBeEnabled();
    fireEvent.click(range); fireEvent.click(screen.getByRole('option', {name: '最近 30 天'}));
    await act(async () => {pending['/api/usage?days=30'].resolve(usage(30, '2026-10-27'));});
    expect(screen.getByText('30', {selector: '.insight-metric dd'})).toBeInTheDocument();
    await act(async () => {pending['/api/usage?days=365'].resolve(usage(9999, '2026-10-26'));});
    expect(screen.queryByText('9,999', {selector: '.insight-metric dd'})).not.toBeInTheDocument();
  });
});

describe('product information and release notes', () => {
  it('keeps release versions, dates and original plus subsequent implementation sources distinct', async () => {
    const original = 'bd242d99ddee51f0e760dd52cfd4736f7fc68a89';
    const included = '6694b5190ad83647a4f108a15fff0830b3849ccb';
    request.mockResolvedValue({current_version: '0.2.1', releases: [{version: '0.2.1', date: '2026-10-24', title: '界面与交互改进', summary: '已经发布的具体变化。',
      source: {branch: 'master', commit: original}, includes: [{branch: 'master', commit: included}],
      sections: [{title: '界面', items: ['更清楚的页面层次。']}]}]});
    const view = render(<SettingsInsights section="changelog" refresh={0}/>);
    const release = await screen.findByRole('article', {name: '0.2.1 版本更新'});
    expect(within(release).getByRole('heading', {name: '0.2.1'})).toBeVisible();
    expect(within(release).getByText('当前版本')).toBeVisible();
    expect(within(release).getByText('2026-10-24')).toHaveAttribute('datetime', '2026-10-24');
    const sources = release.querySelector('details')!;
    expect(sources.open).toBe(false);
    const sourceCommit = within(release).getByText('bd242d9');
    expect(sourceCommit).toHaveAttribute('title', original);
    expect(sourceCommit).not.toBeVisible();
    fireEvent.click(within(release).getByText('更新来源'));
    expect(sources.open).toBe(true);
    expect(within(sources).getByText('原始实现')).toBeVisible();
    expect(within(sources).getByText('后续实现')).toBeVisible();
    expect(within(sources).getByText('6694b51')).toHaveAttribute('title', included);
    expect(view.container.querySelector('#settings-release-0-group-0')).toHaveAttribute('data-settings-index', '界面');
  });

  it('shows the API build identity and real counts alongside the full privacy and nutrition boundaries', async () => {
    request.mockResolvedValue({name: 'ChefMate', subtitle: '您的个性化烹饪助手', version: '0.2.1', commit: 'd8cafce', runtime: '本地工作台', recipe_count: 358, component_count: 7});
    const view = render(<SettingsInsights section="about" refresh={0}/>);
    const product = await screen.findByRole('region', {name: 'ChefMate'});
    expect(within(product).getByText('版本 0.2.1')).toBeVisible();
    expect(within(product).getByText('d8cafce')).toHaveAttribute('title', 'd8cafce');
    expect(within(product).getByText('本地工作台')).toBeVisible();
    expect(within(product).getByText('358')).toBeVisible();
    expect(within(product).getByText('7')).toBeVisible();
    expect(view.container).toHaveTextContent('聊天、个人菜谱、饮食记录和长期记忆按账号隔离保存。');
    expect(view.container).toHaveTextContent('调用模型时，会发送本次对话需要的内容与图片。API Key 不回显。');
    expect(view.container).toHaveTextContent('多人餐先确认本人份额。');
    expect(view.container).toHaveTextContent('照片记餐需核对后保存，未知营养留空。');
  });

  it('leaves unknown product counts unreported while retaining a real reported zero', async () => {
    request.mockResolvedValue({name: 'ChefMate', version: '0.2.1', recipe_count: null, component_count: 0});
    const view = render(<SettingsInsights section="about" refresh={0}/>);
    await screen.findByRole('region', {name: 'ChefMate'});
    const facts = view.container.querySelector('.product-facts')!;
    expect(within(facts as HTMLElement).getByText('未提供')).toBeVisible();
    expect(within(facts as HTMLElement).getByText('0')).toBeVisible();
    expect(view.container.querySelector('.product-build-meta')).toHaveTextContent('运行方式未提供');
    expect(view.container.querySelector('.product-build-meta code')).toBeNull();
  });
});
