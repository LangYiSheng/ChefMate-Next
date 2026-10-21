import test from 'node:test';
import assert from 'node:assert/strict';
import { focusConversation, focusStartIndex, focusTransform, FocusMotion } from '../focus-state.js';

test('reopening during a reply retains its question and skips unrelated earlier turns', () => {
  const question = { role:'user', content:'刚才那份清单怎么替换？' };
  const answer = { role:'assistant', content:'可以先', streaming:true };
  const session = { running:true, messages:[{role:'user',content:'旧问题'},{role:'assistant',content:'旧回复'},question,answer] };
  const state = focusConversation(session, focusStartIndex(session));
  assert.deepEqual(state.messages,[question,answer]);
  assert.equal(state.running,true);
  assert.equal(focusStartIndex({messages:[]}),0);
});

test('focus conversation follows actual tools, streaming text and retryable terminal states', () => {
  const prior = { role: 'assistant', content: 'earlier unrelated answer' };
  const user = { role: 'user', content: '怎么替换食材？' };
  const reply = { role: 'assistant', content: '', streaming: true, tool_calls: [{ name: 'get_recipe', running: true }] };
  const session = { running: true, messages: [prior, user, reply] };
  let state = focusConversation(session, 1);
  assert.deepEqual(state.messages, [user, reply]);
  assert.equal(state.status, '读取菜谱…');
  assert.equal(state.retry, null);
  reply.tool_calls[0].running = false; reply.content = '可以用';
  assert.equal(focusConversation(session, 1).status, '正在回复…');
  session.running = false; reply.streaming = false; reply.error = '连接暂时不可用'; reply.retryMessage = user.content;
  state = focusConversation(session, 1);
  assert.equal(state.running, false);
  assert.equal(state.status, reply.error);
  assert.equal(state.retry, reply);
  assert.equal(focusConversation(session, 1, { running: true, cancelling: true }).status, '正在停止本次回复…');
});

test('source transition uses the original card bounds and skips offscreen or missing sources', () => {
  const target = { left: 12, top: 12, right: 1012, bottom: 812, width: 1000, height: 800 };
  const source = { left: 212, top: 212, right: 712, bottom: 612, width: 500, height: 400 };
  assert.equal(focusTransform(source, target), 'translate(200px, 200px) scale(0.5, 0.5)');
  assert.equal(focusTransform(null, target), null);
  assert.equal(focusTransform({ ...source, top: 900, bottom: 1300 }, target), null);
});

test('a late close completion cannot hide a reopened workspace, including reduced motion', async () => {
  const motion = new FocusMotion();
  let finishClose, finishOpen, oldCancelled = 0, closed = 0, opened = 0;
  const close = { finished: new Promise(resolve => { finishClose = resolve; }), cancel() { oldCancelled++; } };
  const open = { finished: new Promise(resolve => { finishOpen = resolve; }), cancel() {} };
  motion.start(close, () => { closed++; });
  motion.start(open, () => { opened++; });
  finishClose(); await Promise.resolve();
  assert.equal(closed, 0);
  assert.equal(oldCancelled, 1);
  finishOpen(); await Promise.resolve();
  assert.equal(opened, 1);
  motion.start(null, () => { closed++; });
  assert.equal(closed, 1);
});
