import {readAppearance} from './appearance';

export type Values = Record<string, any>;
export type Drafts = Record<string, Values>;
export const editableSections = ['display', 'cooking', 'model', 'components', 'context', 'runtime', 'developer'];
export const readLocal = (key: string, defaults: Values = {}): Values => {
  try { return {...defaults, ...JSON.parse(localStorage.getItem(key) || '{}')}; }
  catch { return {...defaults}; }
};
export function createDrafts(settings: Values): Drafts {
  return {
    model: {...settings.model, api_key: ''},
    components: Object.fromEntries((settings.available_components || []).map((id: string) => [id, (settings.enabled_components || []).includes(id)])),
    context: {...settings.context}, runtime: {...settings.runtime},
    cooking: {timer_limit: settings.cooking?.timer_limit === 1 ? 1 : 3, clear_timer_on_step_change: settings.cooking?.clear_timer_on_step_change === true, ...readLocal('chefmate:kitchen-preferences', {timer_sound: true})},
    display: {inline_limit: 2, density: 'comfortable', ...readAppearance(), reply_suggestions: settings.interaction?.reply_suggestions !== false, reply_suggestion_count: settings.interaction?.reply_suggestion_count ?? 3},
    developer: readLocal('chefmate:developer-settings', {enabled: false, trace_mode: 'compact'}),
  };
}

export const isDirty = (draft: unknown, baseline: unknown) => JSON.stringify(draft) !== JSON.stringify(baseline);

export function advanceRevisions(revisions: Record<string, number>, previous: number, next: number) {
  return Object.fromEntries(Object.entries(revisions).map(([key, revision]) => [key, revision === previous ? next : revision]));
}
