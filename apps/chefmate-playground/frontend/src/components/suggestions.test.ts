import { describe, expect, it } from 'vitest';
import { replySuggestions } from './suggestions';
const messages = [{ role: 'assistant', suggested_replies: [{ title: '继续', text: '继续做饭' }] }];
describe('live reply suggestions', () => {
  it('uses saved server settings and never resurfaces an old assistant reply after user input', () => {
    expect(replySuggestions(messages, { reply_suggestions: false })).toEqual([]);
    expect(replySuggestions([...messages, { role: 'user', suggested_replies: [] }], {})).toEqual([]);
  });
  it('recomputes the pantry shortcut from authoritative checklist state and honors the count', () => {
    const context = { stage: 'prep', views: [{ kind: 'prep_checklist', data: { items: [{ status: 'pending' }] } }] };
    expect(replySuggestions(messages, { reply_suggestion_count: 1 }, context)[0].title).toBe('核对未确认食材');
    context.views[0].data.items[0].status = 'checked';
    expect(replySuggestions(messages, {}, context)).toEqual(messages[0].suggested_replies);
    expect(replySuggestions(messages, {}, { ...context, stage: 'cooking' })).toEqual(messages[0].suggested_replies);
  });
  it.each([
    [[{ status: 'pending', availability: 'missing' }, { status: 'ready', availability: 'available' }], false],
    [[{ status: 'pending', availability: 'missing' }, { status: 'pending', availability: 'unknown' }], true],
    [[{ status: 'pending' }], true],
    [[{ status: 'ready' }, { status: 'skipped', availability: 'unknown' }], false],
  ])('offers a neutral stock check only for unknown pending ingredients (%j)', (items, expected) => {
    const context = { stage: 'prep', views: [{ kind: 'prep_checklist', data: { items } }] };
    const replies = replySuggestions(messages, {}, context);
    expect(replies.some(reply => reply.title === '核对未确认食材')).toBe(expected);
    expect(replies.some(reply => reply.text.includes('小料我都已有'))).toBe(false);
    const rechecked = replySuggestions([{ role: 'assistant', suggested_replies: replies }], {}, context);
    expect(rechecked).toEqual(replies);
  });
  it('removes the old ownership template before or after the authoritative checklist loads', () => {
    const old = { title: '已拥有所有小料，帮我勾选掉', text: '当前清单里的调味小料我都已有，请帮我勾选为已备齐；主料保持原来的状态，不确定属于小料的项目先问我。' };
    const history = [{ role: 'assistant', suggested_replies: [old, ...messages[0].suggested_replies] }];
    expect(replySuggestions(history, {})).toEqual(messages[0].suggested_replies);
    const context = { stage: 'prep', views: [{ kind: 'prep_checklist', data: { items: [{ status: 'pending', availability: 'missing' }] } }] };
    expect(replySuggestions(history, {}, context)).toEqual(messages[0].suggested_replies);
  });
});
