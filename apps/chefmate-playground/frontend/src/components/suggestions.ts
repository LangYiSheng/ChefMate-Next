import type { ApiData } from '../runtime/types';
const pantry = { title: '核对未确认食材', text: '请帮我核对清单里尚未确认的食材，我会补充哪些已经有了；明确缺少的项目保留待购买，不把未说明的食材标为备齐。' };
const oldPantryTitle = '已拥有所有小料，帮我勾选掉';

export function replySuggestions(messages: ApiData[], status: ApiData | null, context?: ApiData | null) {
  const latest = messages.at(-1);
  if (status?.reply_suggestions === false || latest?.role !== 'assistant' || latest.streaming || latest.error || latest.cancelled) return [];
  let replies: typeof pantry[] = (Array.isArray(latest.suggested_replies) ? latest.suggested_replies : [])
    .filter((item: ApiData) => typeof item?.title === 'string' && item.title.trim() && typeof item.text === 'string' && item.text.trim())
    .map((item: ApiData) => ({ title: item.title.trim().slice(0, 20), text: item.text.trim().slice(0, 8000) }))
    .filter((item: typeof pantry) => item.title !== oldPantryTitle);
  if (context) {
    replies = replies.filter(reply => reply.title !== pantry.title && reply.text !== pantry.text);
    if (context.stage === 'prep' && context.views?.some((view: ApiData) => view.kind === 'prep_checklist'
      && view.data?.items?.some((item: ApiData) => item.status === 'pending' && (item.availability === undefined || item.availability === 'unknown')))) replies.unshift(pantry);
  }
  const titles = new Set(), texts = new Set();
  return replies.filter(item => { if (titles.has(item.title) || texts.has(item.text)) return false; titles.add(item.title); texts.add(item.text); return true; })
    .slice(0, Number.isInteger(status?.reply_suggestion_count) ? Math.max(1, Math.min(6, status!.reply_suggestion_count)) : 3);
}
