import test from 'node:test';
import assert from 'node:assert/strict';
import { focusConversation, focusStartIndex, focusSceneFrames, focusCompletion, FocusMotion } from '../focus-state.js';

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

test('workspace and chat move as neighboring scenes, and return reverses both tracks', () => {
  const open = focusSceneFrames(1024), close = focusSceneFrames(1024, true);
  assert.equal(open.focus[0].transform, 'translateX(1024px)');
  assert.equal(open.main[1].transform, 'translateX(-1024px)');
  assert.deepEqual(close.focus, [...open.focus].reverse());
  assert.deepEqual(close.main, [...open.main].reverse());
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

test('finish controls use the trusted whole-meal component, not the visible recipe scope', () => {
  const snapshot = {stage:'prep',components:[{kind:'prep_checklist',data:{items:[{status:'ready'}]}},
    {kind:'task_progress',data:{prep_total:7,prep_ready:6,steps_total:5,steps_done:0}}]};
  assert.equal(focusCompletion(snapshot,'prep_checklist'),null);
  snapshot.components[1].data.prep_ready=7;
  assert.equal(focusCompletion(snapshot,'prep_checklist'),'prep');
  assert.equal(focusCompletion({...snapshot,stage:'cooking'},'prep_checklist'),null);
  assert.equal(focusCompletion({...snapshot,stage:'cooking'},'cooking_guide'),null);
  snapshot.components[1].data.steps_done=5;
  assert.equal(focusCompletion({...snapshot,stage:'cooking'},'cooking_guide'),'meal');
  assert.equal(focusCompletion({stage:'completed'},'cooking_guide'),'completed');
  assert.equal(focusCompletion({stage:'completed'},'recipe_detail'),null);
  assert.equal(focusCompletion(null,'prep_checklist'),null);
});
