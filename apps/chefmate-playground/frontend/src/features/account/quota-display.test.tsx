import {render, screen} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {QuotaOverview} from './QuotaOverview';
import {availableQuotaPercent, personalQuotaLabel, quotaPercent, quotaPercentText} from './quota-display';
import type {AccountQuota, FundsOverview, FundWindow} from './types';

const window: FundWindow = {starts_at: null, resets_at: null, limit_micros: 2_000_000, used_micros: 1_000_000, reserved_micros: 0, estimated_micros: 0, remaining_micros: 1_000_000};
const funds: FundsOverview = {currency: 'CNY', window_mode: 'first_use', available_micros: 15_000_000, deduction_order: ['membership', 'base', 'topup'], permanent: {total_micros: 15_000_000, used_micros: 1_000_000, reserved_micros: 0, estimated_micros: 0, remaining_micros: 14_000_000, overdrawn_micros: 0, sources: [
  {id:'base',kind:'base',label:'5元新用户额度',amount_micros:5_000_000,used_micros:1_000_000,reserved_micros:0,estimated_micros:0,remaining_micros:4_000_000,overdrawn_micros:0,created_at:'2026-10-28T00:00:00Z'},
  {id:'topup',kind:'topup',label:'¥10.00 额外充值',amount_micros:10_000_000,used_micros:0,reserved_micros:0,estimated_micros:0,remaining_micros:10_000_000,overdrawn_micros:0,created_at:'2026-10-28T00:00:00Z'},
]}, memberships: [{id:'member',label:'30天会员 · 20元',status:'active',starts_at:'2026-10-28T00:00:00Z',expires_at:'2099-11-28T00:00:00Z',revoked_at:null,revision:1,weekly_limit_micros:10_000_000,five_hour_limit_micros:2_000_000,available_micros:1_000_000,week:{...window,limit_micros:10_000_000,remaining_micros:9_000_000},five_hour:window}]};
const quota: AccountQuota = {funds,cost_limit_micros:15_000_000,used_tokens:30,used_cost_micros:2_000_000,reserved_tokens:0,reserved_cost_micros:0};
beforeEach(() => {const original = globalThis.window.matchMedia; vi.spyOn(globalThis.window, 'matchMedia').mockImplementation(query => ({...original(query), matches: query.includes('prefers-reduced-motion')}));});
afterEach(() => vi.restoreAllMocks());

describe('Personal quota percentages', () => {
  it('uses current membership capacity once, rather than adding both overlapping windows', () => {
    expect(availableQuotaPercent(quota)).toBeCloseTo(15 / 17 * 100);
    expect(availableQuotaPercent({...quota,funds:{...funds,memberships:[{...funds.memberships[0],status:'expired',available_micros:0}],available_micros:14_000_000}})).toBeCloseTo(14 / 15 * 100);
  });
  it('keeps unlimited and empty budgets honest and does not round tiny use into full availability', () => {
    expect(quotaPercent(null,null)).toBeNull(); expect(quotaPercent(0,0)).toBe(0);
    expect(quotaPercent(-10,10)).toBe(0); expect(quotaPercent(20,10)).toBe(100);
    expect(quotaPercentText(.0002)).toBe('<0.1%'); expect(quotaPercentText(99.9998)).toBe('>99.9%');
    expect(quotaPercentText(null)).toBe('不限');
  });
  it('shows window and source percentages with no monetary text or monetary progress values', () => {
    const {container} = render(<QuotaOverview quota={quota}/>);
    expect(screen.getByRole('heading',{name:'88.2%'})).toBeVisible();
    expect(screen.getByRole('progressbar',{name:'周额度可用比例'})).toHaveValue(90);
    expect(screen.getByRole('progressbar',{name:'5 小时额度可用比例'})).toHaveValue(50);
    expect(container.textContent).not.toMatch(/[¥￥]|\d+(?:\.\d+)?\s*元|CNY/);
    expect(screen.getByText('额外充值')).toBeVisible();
    for (const meter of screen.getAllByRole('progressbar')) expect(Number(meter.getAttribute('max'))).toBe(100);
  });
  it('uses the shared weekly/five-hour availability in the personal account popover', () => {
    const {container} = render(<QuotaOverview compact quota={{...quota,funds:{...funds,memberships:[{...funds.memberships[0],available_micros:0,week:{...window,used_micros:window.limit_micros,remaining_micros:0}}]}}}/>);
    expect(screen.getByText('本窗口可用 0%')).toBeVisible();
    expect(container.textContent).not.toMatch(/[¥￥]|\d+(?:\.\d+)?\s*元/);
  });
  it('keeps administrator monetary management explicit and unchanged', () => {
    render(<QuotaOverview presentation="amount" quota={quota}/>);
    expect(screen.getByRole('heading',{name:'¥15.00'})).toBeVisible();
    expect(screen.getByText('已用 ¥1.00 / ¥10.00')).toBeVisible();
  });
  it('removes administrator-entered prices from labels without changing a plan duration', () => {
    expect(personalQuotaLabel('30天会员 · 20元','会员')).toBe('30天会员');
    expect(personalQuotaLabel('¥5.00','基础额度')).toBe('基础额度');
  });
  it('does not spend a personal percentage before provisional reservations or estimates are confirmed', () => {
    const reserved: AccountQuota = {cost_limit_micros:5_000_000,used_cost_micros:4_000_000,estimated_cost_micros:300_000,quota_used_cost_micros:4_300_000,reserved_cost_micros:700_000,used_tokens:0,reserved_tokens:0};
    expect(availableQuotaPercent(reserved)).toBe(20);
    expect(availableQuotaPercent({...reserved,used_cost_micros:5_100_000})).toBe(0);
    const source = {...funds.permanent.sources[0], used_micros:4_100_000, estimated_micros:100_000, reserved_micros:500_000, remaining_micros:400_000};
    const reservedFunds = {...quota, reserved_cost_micros:500_000, estimated_cost_micros:100_000, funds:{...funds,memberships:[],permanent:{...funds.permanent,total_micros:5_000_000,used_micros:4_100_000,estimated_micros:100_000,reserved_micros:500_000,sources:[source]}}};
    render(<QuotaOverview quota={reservedFunds}/>);
    expect(screen.getByRole('heading',{name:'20%'})).toBeVisible();
    expect(screen.getByRole('progressbar',{name:'剩余额度可用比例'})).toHaveValue(20);
    expect(screen.getByText('对话处理中，完成后更新额度')).toBeVisible();
    expect(screen.getByText('部分用量正在核对，确认后更新比例。')).toBeVisible();
  });
});
