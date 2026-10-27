import { useSyncExternalStore } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChefStore } from '../runtime/store';
import { Composer, type EditorPositions } from './Composer';
import { saveKeyboardPreferences } from '../features/settings/keyboard-preferences';

const runtime = vi.hoisted(() => ({ current: null as ChefStore | null }));
vi.mock('../runtime/store', async importOriginal => {
  const actual = await importOriginal<typeof import('../runtime/store')>();
  return { ...actual, chefStore: new Proxy({}, { get(_target, key) {
    const value = runtime.current?.[key as keyof ChefStore];
    return typeof value === 'function' ? value.bind(runtime.current) : value;
  } }) };
});
// The form below is the unit under test; question transactions have their own suite.
vi.mock('../features/workspace', () => ({ QuestionPanel: () => null }));

const json = (data: any) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
const requests: { path: string; init: RequestInit }[] = [];
const notify = vi.fn();
let requestId = '';

function ConnectedComposer({ positions }: { positions?: EditorPositions }) {
  const store = runtime.current!;
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return <Composer state={state} notify={notify} positions={positions} />;
}

beforeEach(async () => {
  requests.length = 0; requestId = ''; notify.mockReset(); localStorage.clear();
  const fetcher = (async (path: string, init: RequestInit = {}) => {
    requests.push({ path, init });
    if (path === '/api/status') return json({ ready: true, supports_images: true });
    if (path.startsWith('/api/sessions?')) return json({ items: [] });
    if (path.endsWith('/workspace')) return json({ ui_snapshot: { revision: 1 } });
    if (path.endsWith('/turn')) {
      requestId = JSON.parse(init.body as string).request_id;
      return new Response(`data: ${JSON.stringify({ type: 'result', result: { status: 'completed', text: '可以这样做' } })}\n\ndata: ${JSON.stringify({ type: 'done', status: 'completed' })}\n\n`);
    }
    return json({ id: path.split('/')[3], title: '对话', messages: requestId ? [{ role: 'assistant', content: '可以这样做' }] : [],
      last_status: requestId ? 'completed' : 'idle', last_run: requestId ? { request_id: requestId } : null });
  }) as typeof fetch;
  runtime.current = new ChefStore({ fetch: fetcher, storage: null, attachments: { load: async () => ({}), save: async () => {} },
    prepareImage: async file => ({ id: `local:${file.name}`, name: file.name, blob: file, local: true, previewUrl: 'data:image/jpeg;base64,' }) });
  await runtime.current.start();
});
afterEach(() => { cleanup(); runtime.current?.dispose(); runtime.current = null; });

describe('Composer with the real external store', () => {
  it('keeps the textarea controlled by the correct chat draft across selections', async () => {
    const store = runtime.current!; await store.openSession('a'); render(<ConnectedComposer />);
    let input = screen.getByRole('textbox', { name: '告诉 ChefMate 你想吃什么' });
    fireEvent.change(input, { target: { value: 'A 的未发送内容' } });
    expect(store.getSnapshot().drafts.a).toBe('A 的未发送内容');
    await act(() => store.openSession('b'));
    input = screen.getByRole('textbox', { name: '告诉 ChefMate 你想吃什么' });
    expect(input).toHaveValue(''); fireEvent.change(input, { target: { value: 'B 的未发送内容' } });
    await act(() => store.openSession('a'));
    input = screen.getByRole('textbox', { name: '告诉 ChefMate 你想吃什么' });
    expect(input).toHaveValue('A 的未发送内容'); expect(store.getSnapshot().drafts.b).toBe('B 的未发送内容');
  });
  it('restores per-session selection before the shorter next draft can clamp it', async () => {
    const store = runtime.current!, positions: EditorPositions = new Map();
    await store.openSession('a'); store.setDraft('aaaaaaaaaa');
    const view = render(<ConnectedComposer positions={positions} />);
    let input = screen.getByRole('textbox') as HTMLTextAreaElement;
    input.setSelectionRange(7, 9); fireEvent.select(input);
    await act(() => store.openSession('b'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'bb' } });
    expect(positions.get('a')).toEqual({ start: 7, end: 9 });
    await act(() => store.openSession('a'));
    input = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect([input.selectionStart, input.selectionEnd]).toEqual([7, 9]);
    view.unmount(); render(<ConnectedComposer positions={positions} />);
    input = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect([input.selectionStart, input.selectionEnd]).toEqual([7, 9]);
  });

  it('submits Enter once, keeps Shift+Enter for editing, and clears an accepted draft', async () => {
    const store = runtime.current!; await store.openSession('a'); render(<ConnectedComposer />);
    const input = screen.getByRole('textbox'); fireEvent.change(input, { target: { value: '推荐一份早餐' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(requests.some(item => item.path.endsWith('/turn'))).toBe(false);
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(store.getSnapshot().session?.messages.at(-1)?.content).toBe('可以这样做'));
    expect(input).toHaveValue('');
    expect(requests.filter(item => item.path.endsWith('/turn'))).toHaveLength(1);
    expect(JSON.parse(requests.find(item => item.path.endsWith('/turn'))!.init.body as string).message).toBe('推荐一份早餐');
  });

  it('applies saved send shortcuts without sending IME composition or editing key combinations', async () => {
    const store = runtime.current!; await store.openSession('a'); render(<ConnectedComposer />);
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '保留这段输入' } });
    act(() => { saveKeyboardPreferences({ send_mode: 'mod-enter' }); });
    expect(input).toHaveAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter');
    expect(screen.queryByText('Enter 发送 · Shift + Enter 换行')).not.toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true, ctrlKey: true });
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true, isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true, keyCode: 229 });
    expect(requests.filter(item => item.path.endsWith('/turn'))).toHaveLength(0);
    expect(input).toHaveValue('保留这段输入');
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(requests.filter(item => item.path.endsWith('/turn'))).toHaveLength(1));
    expect(JSON.parse(requests.find(item => item.path.endsWith('/turn'))!.init.body as string).message).toBe('保留这段输入');
  });

  it('adds and removes a file through the runtime attachment actions', async () => {
    await runtime.current!.openSession('a'); const { container } = render(<ConnectedComposer />);
    const input = container.querySelector('input[type="file"]')!;
    fireEvent.change(input, { target: { files: [new File(['photo'], 'egg.jpg', { type: 'image/jpeg' })] } });
    expect(await screen.findByRole('button', { name: '预览图片：egg.jpg' })).toBeInTheDocument();
    expect(runtime.current!.getSnapshot().imageDrafts.a[0].name).toBe('egg.jpg');
    fireEvent.click(screen.getByRole('button', { name: '移除图片：egg.jpg' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '预览图片：egg.jpg' })).not.toBeInTheDocument());
    expect(runtime.current!.getSnapshot().imageDrafts.a).toEqual([]);
  });

  it('retains the stop action without duplicating assistant progress below the input', async () => {
    const store = runtime.current!; await store.openSession('a');
    const snapshot = store.getSnapshot();
    const state = { ...snapshot, runs: { a: { sessionId: 'a', requestId: 'r', input: '做菜', status: '回复位置显示的进度' } },
      session: { ...snapshot.session!, running: true } };
    const view = render(<Composer state={state} notify={notify} />);
    expect(screen.getByRole('button', { name: '停止本次执行' })).toBeInTheDocument();
    expect(view.container.querySelector('.turn-status')).toBeNull(); expect(view.container).not.toHaveTextContent('回复位置显示的进度');
  });
});
