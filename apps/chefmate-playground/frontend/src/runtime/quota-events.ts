import {accountHeaders} from './http';

export const QUOTA_CHANGED = 'chefmate:quota-changed';
export const QUOTA_STORAGE_KEY = 'chefmate:quota-updated';
/** One notification per terminal turn; other tabs for this account refresh too. */
export function notifyQuotaChanged() {
  if (typeof window === 'undefined') return;
  const accountId = accountHeaders('/api/auth/quota').get('X-ChefMate-Account');
  window.dispatchEvent(new CustomEvent(QUOTA_CHANGED, {detail: {accountId}}));
  if (!accountId) return;
  try {localStorage.setItem(QUOTA_STORAGE_KEY, JSON.stringify({accountId, nonce: crypto.randomUUID()}));}
  catch { /* Same-tab synchronization still works when browser storage is unavailable. */ }
}
