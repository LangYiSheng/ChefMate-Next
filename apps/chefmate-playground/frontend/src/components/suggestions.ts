import type { ApiData } from '../runtime/types';
const pantry = { title: '已拥有所有小料，帮我勾选掉', text: '当前清单里的调味小料我都已有，请帮我勾选为已备齐；主料保持原来的状态，不确定属于小料的项目先问我。' };

export function replySuggestions(messages: ApiData[], status: ApiData | null, context?: ApiData | null) {
  const latest = messages.at(-1);
  if (status?.reply_suggestions === false || latest?.role !== 'assistant' || latest.streaming || latest.error || latest.cancelled) return [];
  let replies: typeof pantry[] = (Array.isArray(latest.suggested_replies) ? latest.suggested_replies : [])
    .filter((item: ApiData) => typeof item?.title === 'string' && item.title.trim() && typeof item.text === 'string' && item.text.trim())
    .map((item: ApiData) => ({ title: item.title.trim().slice(0, 20), text: item.text.trim().slice(0, 8000) }));
  if (context) {
    replies = replies.filter(reply => reply.title !== pantry.title && reply.text !== pantry.text);
    if (context.stage === 'prep' && context.views?.some((view: ApiData) => view.kind === 'prep_checklist' && view.data?.items?.some((item: ApiData) => item.status === 'pending'))) replies.unshift(pantry);
  }
  const titles = new Set(), texts = new Set();
  return replies.filter(item => { if (titles.has(item.title) || texts.has(item.text)) return false; titles.add(item.title); texts.add(item.text); return true; })
    .slice(0, Number.isInteger(status?.reply_suggestion_count) ? Math.max(1, Math.min(6, status!.reply_suggestion_count)) : 3);
}
