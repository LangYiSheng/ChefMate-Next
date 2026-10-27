import {quotaAmounts, type AccountQuota} from './types';

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
  return quotaPercent(quotaAmounts(quota).remaining, capacity);
}
// Administrator labels may include a monetary literal. Personal quota surfaces
// redact that literal too, without changing the stored name.
export function personalQuotaLabel(label: string, fallback: string): string {
  return label.replace(/[¥￥]\s*[\d,.]+(?:\s*元)?|[\d,.]+\s*(?:元|人民币|CNY)/gi, '')
    .replace(/^[\s/·:：-]+|[\s/·:：-]+$/g, '').trim() || fallback;
}
