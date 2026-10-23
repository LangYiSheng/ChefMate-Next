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
    expect(replySuggestions(messages, { reply_suggestion_count: 1 }, context)[0].title).toContain('小料');
    context.views[0].data.items[0].status = 'checked';
    expect(replySuggestions(messages, {}, context)).toEqual(messages[0].suggested_replies);
    expect(replySuggestions(messages, {}, { ...context, stage: 'cooking' })).toEqual(messages[0].suggested_replies);
  });
});
