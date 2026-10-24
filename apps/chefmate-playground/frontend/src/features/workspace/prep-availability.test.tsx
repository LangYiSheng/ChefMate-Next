import { render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ViewCard } from './views';
import { workspaceStore } from './store';
import type { View } from './protocol';

let id: string;
afterEach(() => { workspaceStore.remove(id); });

it('marks only explicit missing stock as waiting for purchase without checking unknown ingredients', () => {
  id = `prep-availability-${crypto.randomUUID()}`;
  const view: View = { id: 'prep', kind: 'prep_checklist', revision: 1, editable: true, data: { items: [
    { id: 'beef', canonical_name: '牛肉', status: 'pending', availability: 'missing', amount_text: '250 g' },
    { id: 'potato', canonical_name: '土豆', status: 'pending', availability: 'unknown', amount_text: '1 个' },
    { id: 'tomato', canonical_name: '番茄', status: 'ready', availability: 'available', amount_text: '2 个' },
  ] } };
  workspaceStore.receive(id, { workspace: { revision: 1, stage: 'prep' }, views: [view] });
  const rendered = render(<ViewCard view={view} entry={workspaceStore.entry(id)} sessionId={id} />);
  const missing = rendered.container.querySelector('[data-prep-id=beef]') as HTMLElement;
  const unknown = rendered.container.querySelector('[data-prep-id=potato]') as HTMLElement;
  expect(within(missing).getByText('待购买')).toBeInTheDocument();
  expect(within(unknown).queryByText('待购买')).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '牛肉已备齐' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '土豆已备齐' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '番茄已备齐' })).toBeChecked();
});
