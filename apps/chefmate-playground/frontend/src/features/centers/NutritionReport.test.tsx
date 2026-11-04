import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, expect, it, vi} from 'vitest';
import {NutritionReport, shiftReportDate, reportWeekStart} from './NutritionReport';

const request = vi.hoisted(() => vi.fn());
vi.mock('../../runtime/http', () => ({api: request}));
const nutrition = {total: {calories_kcal: null, protein_g: null, fat_g: null, carbohydrate_g: null}, known_total: {}, record_count: 0};
const data = () => ({kind: 'daily', today: '2026-11-04', period: {from: '2026-11-04', to: '2026-11-04'}, ongoing: true,
  summary: nutrition, recorded_days: 0, complete_days: 0, average: {}, comparison: {}, records: [], draft_count: 0,
  days: [{...nutrition, date: '2026-11-04', complete: false, revision: 'r1', draft_count: 0,
    meals: [{type: 'lunch', label: '午餐', record_count: 0}]}], facts: [{id: 'coverage', text: '尚无已确认记录。', dates: []}]});
const props = () => ({kind: 'daily' as const, date: '2026-11-04', onDate: vi.fn(), onDaily: vi.fn(), onFood: vi.fn(), onChat: vi.fn(), onDrafts: vi.fn(), refreshKey: 1});
beforeEach(() => request.mockReset());

it('shows empty intake as unknown and disables completion and AI review', async () => {
  request.mockResolvedValue(data()); render(<NutritionReport {...props()}/>);
  await screen.findByText('记下一餐后，就能生成回顾。');
  expect(screen.getAllByText('—')).toHaveLength(4);
  expect(screen.getByRole('button', {name: '这一天已记完'})).toBeDisabled();
  expect(screen.getByRole('button', {name: '整理 AI 回顾'})).toBeDisabled();
  expect(request).toHaveBeenCalledTimes(1);
});

it('does not let an older date response replace a newer report', async () => {
  let resolveOld!: (value: unknown) => void;
  request.mockImplementationOnce(() => new Promise(resolve => {resolveOld = resolve;})).mockResolvedValue(data());
  const initial = props(); const {rerender} = render(<NutritionReport {...initial} date="2026-11-03"/>);
  rerender(<NutritionReport {...initial}/>);
  await screen.findByText('记下一餐后，就能生成回顾。');
  resolveOld({...data(), facts: [{id: 'old', text: '过期回顾', dates: []}]});
  await waitFor(() => expect(screen.queryByText('过期回顾')).not.toBeInTheDocument());
});

it('shifts calendar dates across month and year boundaries', () => {
  expect(shiftReportDate('2026-12-31', 1)).toBe('2027-01-01');
  expect(shiftReportDate('2026-11-02', -7)).toBe('2026-10-26');
});

it('keeps Sunday in the preceding Monday week across the year boundary', () => {
  expect(reportWeekStart('2027-01-03')).toBe('2026-12-28');
  expect(reportWeekStart('2027-01-04')).toBe('2027-01-04');
});


it('reopens a background review and observes its result without submitting again', async () => {
  let started = false, completed = false;
  request.mockImplementation((_path, options) => {
    if (options?.method === 'POST') started = true;
    return Promise.resolve({...data(), summary: {...nutrition, record_count: 1},
      review_job: started ? {id: 'job-1', status: completed ? 'completed' : 'running'} : null,
      review: completed ? {text: '这几餐有了新的变化。'} : null});
  });
  const initial = render(<NutritionReport {...props()}/>);
  await screen.findByRole('button', {name: '整理 AI 回顾'});
  fireEvent.click(screen.getByRole('button', {name: '整理 AI 回顾'}));
  await screen.findByText('正在后台整理，可以先去看看其他页面。');
  initial.unmount();
  completed = true;
  render(<NutritionReport {...props()}/>);
  await screen.findByText('这几餐有了新的变化。');
  expect(request.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
});
