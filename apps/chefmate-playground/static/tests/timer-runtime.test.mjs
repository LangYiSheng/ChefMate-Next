import test from 'node:test';
import assert from 'node:assert/strict';
import { timerClock, workspaceTimers } from '../timer-runtime.js';
import { WorkspaceController } from '../workspace.js';

test('deadline time uses the server clock and catches up after tab suspension', () => {
  let elapsed = 100;
  const now = timerClock('2026-10-21T10:00:00Z', () => elapsed);
  assert.equal(now(), Date.parse('2026-10-21T10:00:00Z'));
  elapsed += 40000;
  assert.equal(now(), Date.parse('2026-10-21T10:00:40Z'));
  elapsed -= 60000;
  assert.equal(now(), Date.parse('2026-10-21T10:00:00Z'));
});

test('a model presentation held until the answer ends retains elapsed countdown time', () => {
  const now = timerClock('2026-10-21T10:00:00Z', () => 18000, 3000);
  assert.equal(now(), Date.parse('2026-10-21T10:00:15Z'));
});

test('step timers work without a dedicated view and the complete view takes precedence', () => {
  const guide = {kind:'cooking_guide',data:{timers:[{id:'a',status:'running'}]}};
  const timers = {kind:'timers',data:{timers:[{id:'a',status:'paused'},{id:'b',status:'running'}]}};
  assert.deepEqual(workspaceTimers([guide]), [{id:'a',status:'running'}]);
  assert.deepEqual(workspaceTimers([timers,guide]), [{id:'a',status:'paused'},{id:'b',status:'running'}]);
});

function setup(t, api) {
  const controller = new WorkspaceController({api,notify(){},onChange(){},openPanel(){}});
  controller.activeId = 'session'; t.after(() => clearInterval(controller.timer));
  return controller;
}
const timerView = (revision, id='timers') => ({id,kind:'timers',editable:true,revision,data:{timers:[]}});
const response = (revision, views, status='ok') => ({status,workspace:{revision,stage:'cooking'},views});

test('manual seconds commit immediately, clear a migrated draft and keep other drafts', async t => {
  let request;
  const controller = setup(t, async (path, options) => {
    request = JSON.parse(options.body); return response(2,[timerView(2,'replacement')]);
  });
  const view = timerView(1); controller.receive('session',response(1,[view]));
  controller.setTimerDraft(view,'离火静置',1/60);
  controller.entry('session').drafts.other = {kind:'preference_form',values:{servings:2},baseRevision:1};
  await controller.context(view).onStartTimer(view,{seconds:1,label:'离火静置'});
  assert.deepEqual(request.payload,{seconds:1,label:'离火静置'});
  assert.equal(request.base_revision,1);
  assert.deepEqual(Object.keys(controller.entry('session').drafts),['other']);
});

test('rejected timer start keeps input and rejects invalid durations before HTTP', async t => {
  let requests = 0;
  const controller = setup(t, async () => { requests++; return response(1,[timerView(1)],'stage_conflict'); });
  const view = timerView(1); controller.receive('session',response(1,[view]));
  controller.setTimerDraft(view,'焖煮',.5);
  await assert.rejects(controller.startTimer(view,{seconds:30,label:'焖煮'}));
  assert.equal(controller.entry('session').drafts.timers.minutes,.5);
  await assert.rejects(controller.startTimer(view,{seconds:0,label:'焖煮'}));
  await assert.rejects(controller.startTimer(view,{seconds:86401,label:'焖煮'}));
  assert.equal(requests,1);
});

test('a stored timer draft without a nonce does not erase an unrelated form', async t => {
  const controller = setup(t, async () => response(2,[timerView(2,'replacement')]));
  const view=timerView(1);controller.receive('session',response(1,[view]));
  controller.entry('session').drafts = {
    timers:{kind:'timers',label:'焖煮',minutes:1,baseRevision:1},
    preferences:{kind:'preference_form',values:{servings:3},baseRevision:1},
  };
  await controller.startTimer(view,{seconds:60,label:'焖煮'});
  assert.deepEqual(Object.keys(controller.entry('session').drafts),['preferences']);
});

test('intermediate zero or empty duration fields survive rerenders without restoring the default three minutes', t => {
  const controller = setup(t, async () => response(1,[timerView(1)]));
  const view=timerView(1);controller.receive('session',response(1,[view]));
  controller.context(view).onTimer(view,'',null,{minutes:'0',seconds:'0'});
  assert.deepEqual(controller.context(view).draft.duration,{minutes:'0',seconds:'0'});
  assert.equal(controller.canSubmit(),true);
  controller.context(view).onTimer(view,'',null,{minutes:'',seconds:''});
  assert.deepEqual(controller.context(view).draft.duration,{minutes:'',seconds:''});
});

test('an uncertain start recovered by action ID clears the migrated timer draft', async t => {
  let sent;
  const controller=setup(t,async(path,options)=>{sent=JSON.parse(options.body);throw new Error('连接中断');});
  const view=timerView(1);controller.receive('session',response(1,[view]));
  controller.setTimerDraft(view,'焖煮',1);
  await assert.rejects(controller.startTimer(view,{seconds:60,label:'焖煮'}));
  const recovered=response(2,[timerView(2,'replacement')]);
  recovered.workspace.recent_actions=[{action_id:sent.action_id}];
  controller.receive('session',recovered);
  assert.deepEqual(controller.entry('session').drafts,{});
  assert.equal(controller.canSubmit(),true);
});

test('confirmed cooking progress follows the next step while rejected changes retain browsing', async t => {
  const guide = revision => ({id:'guide',kind:'cooking_guide',editable:true,revision,data:{steps:[]}});
  let accepted = false;
  const controller = setup(t, async () => response(accepted?2:1,[guide(accepted?2:1)],accepted?'ok':'stage_conflict'));
  controller.receive('session',response(1,[guide(1)]));
  controller.entry('session').viewState.guide = {stepIndex:2};
  await assert.rejects(controller.dispatch('complete_step',{completed:true},guide(1)));
  assert.equal(controller.entry('session').viewState.guide.stepIndex,2);
  accepted = true;
  await controller.dispatch('complete_step',{completed:true},guide(1));
  assert.equal(controller.entry('session').viewState.guide.stepIndex,undefined);
});

test('starting a timer during a model turn shows the confirmed timer without revealing other forms or rewinding the clock', async t => {
  const old = timerView(1); old.data.server_now = '2026-10-21T10:00:00Z';
  const fresh = timerView(2,'replacement'); fresh.data.server_now = '2026-10-21T10:00:10Z';
  fresh.data.timers = [{id:'manual',status:'running',seconds:30,deadline_at:'2026-10-21T10:00:40Z'}];
  const unrelated = {id:'hidden',kind:'preference_form',editable:true,revision:2,data:{values:{}}};
  const controller = setup(t, async () => response(2,[fresh,unrelated]));
  controller.receive('session',response(1,[old]));
  controller.setRunning('session',true);
  await controller.startTimer(old,{seconds:30,label:'煮蛋'});
  assert.ok(controller.entry('session').timerNow() >= Date.parse('2026-10-21T10:00:10Z'));
  assert.equal(controller.entry('session').views.some(view=>view.id==='replacement'),true);
  assert.deepEqual(controller.entry('session').views.filter(view=>view.kind==='timers').map(view=>view.id),['replacement']);
  assert.equal(controller.entry('session').views.some(view=>view.id==='hidden'),false);
  controller.setRunning('session',false);
  assert.equal(controller.entry('session').views.some(view=>view.id==='hidden'),true);
});
