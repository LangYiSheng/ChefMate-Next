export type AccountQuota = {
  cost_limit_micros: number | null; balance_micros?: number | null; remaining_cost_micros?: number | null;
  used_tokens: number; used_cost_micros: number; reserved_tokens: number; reserved_cost_micros: number;
  estimated_tokens?: number; estimated_cost_micros?: number; quota_used_tokens?: number; quota_used_cost_micros?: number;
};
export type AccountUser = {
  id: string; username: string; display_name: string; role: 'admin' | 'user';
  developer_allowed: boolean; onboarding_completed: boolean; disabled: boolean; created_at: string;
  profile: {cooking_level?: string; household_size?: number; preferences?: string[]; theme?: string; use_for_ai?: boolean};
  quota: AccountQuota;
};
export type AccountSession = {user: AccountUser | null; registration_enabled?: boolean};
export type Pricing = {revision: number; currency: string; input_per_million: string; output_per_million: string; cached_input_per_million: string; configured?: boolean};
export const PROFILE_PREFERENCES = ['清淡一点', '喜欢家常菜', '快手省时间', '多一些蔬菜', '想学新做法', '一人食'];
export const tokens = (value: number | null | undefined) => value == null ? '不限' : new Intl.NumberFormat('zh-CN').format(value);
export const money = (micros: number | null | undefined, currency = 'CNY') => micros == null ? '不限' : new Intl.NumberFormat('zh-CN', {style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 6}).format(micros / 1_000_000);
export function quotaAmounts(quota: AccountQuota) {
  const charged = quota.quota_used_cost_micros ?? quota.used_cost_micros + (quota.estimated_cost_micros || 0);
  const reserved = quota.reserved_cost_micros;
  const remaining = quota.cost_limit_micros == null ? null : quota.remaining_cost_micros ?? quota.balance_micros ?? Math.max(0, quota.cost_limit_micros - charged - reserved);
  return {charged, reserved, remaining};
}
