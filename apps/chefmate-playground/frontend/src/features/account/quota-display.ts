import type {AccountQuota, FundWindow, MembershipFund} from './types';

export function quotaPercent(remaining: number | null, limit: number | null): number | null {
  if (remaining == null || limit == null) return null;
  if (limit <= 0) return 0;
  return Math.min(100, Math.max(0, remaining / limit * 100));
}
export function quotaPercentText(value: number | null): string {
  if (value == null) return '不限';
  if (value > 0 && value < .1) return '<0.1%';
  if (value > 99.9 && value < 100) return '>99.9%';
  return `${new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 1}).format(value)}%`;
}
export function availableQuotaPercent(quota: AccountQuota): number | null {
  const funds = quota.funds;
  const capacity = funds ? funds.permanent.total_micros == null ? null : funds.permanent.total_micros + funds.memberships
    .filter(member => member.status === 'active')
    .reduce((total, member) => total + Math.min(member.weekly_limit_micros, member.five_hour_limit_micros), 0) : quota.cost_limit_micros;
  // Personal balances change when actual usage is settled, not when the next
  // provider call is provisionally reserved. Estimates remain a separate state.
  const remaining = funds ? funds.permanent.total_micros == null ? null : Math.max(0,
    funds.permanent.total_micros - confirmedUse(funds.permanent) + funds.memberships
      .filter(member => member.status === 'active').reduce((sum, member) => sum + membershipConfirmedRemaining(member), 0))
    : quota.cost_limit_micros == null ? null : Math.max(0, quota.cost_limit_micros - quota.used_cost_micros);
  return quotaPercent(remaining, capacity);
}
export function confirmedUse(value: {used_micros: number; estimated_micros: number}) {
  return Math.max(0, value.used_micros - value.estimated_micros);
}
export function confirmedQuotaPercent(limit: number | null, value: {used_micros: number; estimated_micros: number}) {
  return quotaPercent(limit == null ? null : Math.max(0, limit - confirmedUse(value)), limit);
}
export function windowConfirmedRemaining(value: FundWindow) {return Math.max(0, value.limit_micros - confirmedUse(value));}
export function membershipConfirmedRemaining(value: MembershipFund) {
  return Math.min(windowConfirmedRemaining(value.week), windowConfirmedRemaining(value.five_hour));
}
// Administrator labels may include a monetary literal. Personal quota surfaces
// redact that literal too, without changing the stored name.
export function personalQuotaLabel(label: string, fallback: string): string {
  return label.replace(/[¥￥]\s*[\d,.]+(?:\s*元)?|[\d,.]+\s*(?:元|人民币|CNY)/gi, '')
    .replace(/^[\s/·:：-]+|[\s/·:：-]+$/g, '').trim() || fallback;
}
