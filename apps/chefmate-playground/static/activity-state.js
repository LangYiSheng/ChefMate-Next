// Re-rendered SSE updates share a clock so each public activity is typed only once.
export class ActivityTimeline {
  constructor() { this.objects = new WeakMap(); this.keys = new Map(); }
  start(key, text, now) {
    const objects = key !== null && (typeof key === 'object' || typeof key === 'function');
    const cache = objects ? this.objects : this.keys;
    let entry = cache.get(key);
    if (!entry || entry.text !== text) {
      entry = {text, start:now};
      cache.set(key, entry);
    }
    if (!objects && cache.size > 64) cache.delete(cache.keys().next().value);
    return entry.start;
  }
}

export function activityFrame(text, elapsed, reducedMotion = false) {
  const characters = Array.from(text);
  const duration = Math.min(1800, characters.length * 72);
  const count = reducedMotion || duration === 0 ? characters.length :
    Math.min(characters.length, Math.max(1, Math.ceil(characters.length * Math.max(0,elapsed) / duration)));
  return {text:characters.slice(0,count).join(''), done:count >= characters.length};
}
