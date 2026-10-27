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
