// Use the server's clock plus elapsed monotonic time. A clock change or a
// backgrounded tab must not turn a kitchen timer into an interval counter.
export function timerClock(serverNow, elapsedNow = () => performance.now(), received = elapsedNow()) {
  const server = Date.parse(serverNow || '');
  if (!Number.isFinite(server)) return () => Date.now();
  return () => server + Math.max(0, elapsedNow() - received);
}

export function workspaceTimers(views) {
  const unique = new Map();
  // The dedicated view also includes timers for other dishes and manual timers.
  for (const view of [...views.filter(view => view.kind === 'cooking_guide'),
    ...views.filter(view => view.kind === 'timers')]) {
    for (const timer of view.data?.timers || []) unique.set(timer.id, timer);
  }
  return [...unique.values()];
}
