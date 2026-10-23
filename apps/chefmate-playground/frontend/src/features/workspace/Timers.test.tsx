import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KitchenTimers } from './Timers';
import { workspaceStore } from './store';
import type { Timer, View } from './protocol';

const ids: string[] = [];
function seed() {
  const id = `timer-host-${crypto.randomUUID()}`;
  ids.push(id);
  workspaceStore.receive(id, { workspace: { revision: 1, stage: 'cooking' }, views: [] });
  return id;
}
function receiveTimer(id: string, revision: number, timer: Timer) {
  const view: View = { id: 'timers', kind: 'timers', revision, editable: true, data: { timers: [timer] } };
  workspaceStore.receive(id, { workspace: { revision, stage: 'cooking' }, views: [view] });
}
function Hosts({ sessionId, focus, onSceneKey }: { sessionId: string; focus: boolean; onSceneKey?: () => void }) {
  return <>
    <main data-testid="main-host" inert={focus}><KitchenTimers sessionId={sessionId} active={!focus} /></main>
    <section data-testid="focus-host" className="react-cooking-focus" inert={!focus} aria-hidden={!focus} onKeyDown={onSceneKey}>
      <KitchenTimers sessionId={sessionId} active={focus} /><textarea aria-label="专注草稿" />
    </section>
  </>;
}

beforeEach(() => {
  vi.spyOn(workspaceStore, 'load').mockImplementation(async id => workspaceStore.entry(id).snapshot);
});
afterEach(() => { ids.forEach(id => workspaceStore.remove(id)); ids.length = 0; });

describe('timer hosts retained across scene changes', () => {
  it('opens new timers only in the active host, closes the old popup, and accepts the next event after switching', () => {
    const id = seed(), rendered = render(<Hosts sessionId={id} focus={false} />);
    const main = screen.getByTestId('main-host'), focus = screen.getByTestId('focus-host');
    act(() => receiveTimer(id, 2, { id: 'first', label: '第一锅汤', seconds: 60, remaining_seconds: 60, status: 'running' }));
    expect(within(main).getByRole('dialog', { name: '厨房计时' })).toBeInTheDocument();
    expect(focus.querySelector('[role="dialog"]')).toBeNull();

    rendered.rerender(<Hosts sessionId={id} focus />);
    expect(main.querySelector('[role="dialog"]')).toBeNull();
    expect(focus.querySelector('[role="dialog"]')).toBeNull();

    act(() => receiveTimer(id, 3, { id: 'second', label: '第二锅汤', seconds: 90, remaining_seconds: 90, status: 'running' }));
    expect(within(focus).getByRole('dialog', { name: '厨房计时' })).toBeInTheDocument();
    expect(main.querySelector('[role="dialog"]')).toBeNull();

    rendered.rerender(<Hosts sessionId={id} focus={false} />);
    expect(focus.querySelector('[role="dialog"]')).toBeNull();
    expect(main.querySelector('[role="dialog"]')).toBeNull();
    act(() => workspaceStore.openTimers(id, 'second'));
    expect(within(main).getByRole('dialog', { name: '厨房计时' })).toBeInTheDocument();
    expect(focus.querySelector('[role="dialog"]')).toBeNull();
  });

  it('routes expiry to one host without taking the composer focus and consumes Escape before the scene', () => {
    const id = seed(); let now = Date.parse('2026-10-23T12:00:00Z');
    receiveTimer(id, 2, { id: 'soup', label: '热汤', seconds: 5, deadline_at: '2026-10-23T12:00:05Z', status: 'running' });
    workspaceStore.entry(id).clock = () => now;
    const onSceneKey = vi.fn(); render(<Hosts sessionId={id} focus onSceneKey={onSceneKey} />);
    const composer = screen.getByRole('textbox', { name: '专注草稿' }); composer.focus();
    act(() => { now += 6000; workspaceStore.tick(); });
    expect(screen.getAllByRole('dialog', { name: '厨房计时' })).toHaveLength(1);
    expect(within(screen.getByTestId('focus-host')).getByRole('dialog', { name: '厨房计时' })).toBeInTheDocument();
    expect(screen.getByTestId('main-host').querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(composer);
    fireEvent.keyDown(composer, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: '厨房计时' })).not.toBeInTheDocument();
    expect(document.activeElement).toBe(composer); expect(onSceneKey).not.toHaveBeenCalled();
    act(() => workspaceStore.tick());
    expect(screen.queryByRole('dialog', { name: '厨房计时' })).not.toBeInTheDocument();
  });
});
