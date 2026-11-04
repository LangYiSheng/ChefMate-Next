import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useSpeech } from './useSpeech';

let socket: FakeSocket;
let node: FakeNode;
const trackStop = vi.fn();
class FakeSocket {
  static OPEN = 1; readyState = 1; bufferedAmount = 0;
  send = vi.fn(); close = vi.fn(); onmessage?: (e: {data: string}) => void;
  constructor() { socket = this; }
  emit(type: string, text = '') { this.onmessage?.({data: JSON.stringify({type, text})}); }
}
class FakeNode {
  port = { onmessage: null as null | ((e: {data: object}) => void), postMessage: vi.fn() };
  connect = vi.fn(); disconnect = vi.fn();
  constructor() { node = this; }
}
beforeEach(() => {
  vi.stubGlobal('WebSocket', FakeSocket); vi.stubGlobal('AudioWorkletNode', FakeNode);
  vi.stubGlobal('AudioContext', class {
    state = 'running'; destination = {}; audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
    resume = vi.fn().mockResolvedValue(undefined); close = vi.fn().mockResolvedValue(undefined);
    createMediaStreamSource() { return { connect: vi.fn() }; }
  });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
    getUserMedia: vi.fn().mockResolvedValue({getTracks: () => [{stop: trackStop}]}) } });
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it('replaces partial text, flushes tail audio, and waits for final before resolving send', async () => {
  const onText = vi.fn(); const notify = vi.fn();
  const {result, unmount} = renderHook(() => useSpeech('a', onText, notify));
  await act(() => result.current.start('已有草稿'));
  act(() => socket.emit('ready'));
  act(() => { socket.emit('partial', '鸡'); socket.emit('partial', '鸡肉'); });
  expect(onText).toHaveBeenLastCalledWith('已有草稿\n鸡肉');
  let finish!: Promise<string | null>;
  act(() => { finish = result.current.stop(); });
  expect(result.current.phase).toBe('finishing'); expect(trackStop).toHaveBeenCalled();
  expect(node.port.postMessage).toHaveBeenCalledWith('flush');
  act(() => node.port.onmessage?.({data: {pcm: new ArrayBuffer(12), level: 0, final: true}}));
  expect(socket.send.mock.calls.map(call => typeof call[0])).toEqual(['object', 'string']);
  act(() => socket.emit('final', '鸡肉饭。'));
  expect(await finish).toBe('已有草稿\n鸡肉饭。'); expect(result.current.phase).toBe('idle');
  expect(notify).not.toHaveBeenCalled(); unmount();
});

it('closes recording on conversation change and ignores late text', async () => {
  const onText = vi.fn();
  const {result, rerender, unmount} = renderHook(({id}) => useSpeech(id, onText, vi.fn()), {initialProps: {id:'a'}});
  await act(() => result.current.start('')); act(() => socket.emit('ready'));
  rerender({id:'b'}); act(() => socket.emit('partial', '不应写入'));
  expect(onText).not.toHaveBeenCalled(); expect(socket.close).toHaveBeenCalled(); unmount();
});
