import test from 'node:test';
import assert from 'node:assert/strict';
import { makeAction, messageViews, remainingSeconds, clockText, changedValues, viewsFrom, canChangeStage, visibleWorkspaceViews, legacyRecipeItems } from '../ui-protocol.js';
import { WorkspaceController, ConfirmationQueue, visibleMessageViews, recoverRunningTurn } from '../workspace.js';

const preference = (revision, id = 'preferences') => ({ id, kind: 'preference_form', title: '偏好', revision, editable: true,
  data: { fields: [], values: { ingredient_terms: ['虾'], servings: 2 } } });
const response = (revision, id) => ({ status: 'ok', workspace: { revision, stage: 'idea', components: [], recent_actions: [] }, views: [preference(revision, id)] });
function controller(t, api = async () => response(0)) {
  const item = new WorkspaceController({ api, notify() {}, onChange() {}, openPanel() {} });
  item.activeId = 'session';
  t.after(() => clearInterval(item.timer));
  return item;
}

test('composer choices keep their originating session and discard resets focused view', async t=>{
  const item=controller(t);item.receive('session',response(0));
  const view=preference(0);item.setDraft(view,{...view.data.values,servings:3});
  const actions=item.composerActions('session');
  assert.deepEqual(actions.map(action=>action.label),['保存条件','放弃修改']);
  item.activeId='other';await actions[1].run();
  assert.deepEqual(item.entry('session').drafts,{});
  assert.equal(item.entry('session').viewState[view.id].draftEpoch,1);
  assert.deepEqual(item.composerActions('other'),[]);
});

test('servings choices commit sequential revisions and timer draft clears after a replaced view', async t=>{
  globalThis.document ||= {querySelectorAll:()=>[]};
  const requests=[];let revision=0;
  const meal={id:'meal',kind:'meal_plan',revision:0,editable:true,data:{recipes:[{recipe_id:'a',target_servings:1},{recipe_id:'b',target_servings:2}]}};
  const item=controller(t,async(path,options)=>{
    requests.push(JSON.parse(options.body));revision++;
    return {status:'ok',workspace:{revision,stage:'planning',components:[]},views:[{...meal,revision}]};
  });
  item.receive('session',{workspace:{revision:0,stage:'planning'},views:[meal]});
  item.setServingsDraft(meal,'a',3);item.setServingsDraft(meal,'b',4);
  assert.equal(item.canSubmit(),false);
  await item.composerActions()[0].run();
  assert.deepEqual(requests.map(request=>request.base_revision),[0,1]);assert.equal(item.canSubmit(),true);
  const timers={id:'timer-old',kind:'timers',editable:true,revision:2,data:{timers:[]}};
  item.receive('session',{workspace:{revision:2,stage:'cooking'},views:[timers]});
  item.api=async()=>({status:'ok',workspace:{revision:3,stage:'cooking'},views:[{...timers,id:'timer-new',revision:3}]});
  item.setTimerDraft(timers,'面条',2);
  assert.deepEqual(item.composerActions(),[]);
  assert.equal(item.canSubmit(),true);
  await item.startTimer(timers,{seconds:120,label:'面条'});assert.deepEqual(item.entry('session').drafts,{});
});

test('closed actions carry revision and preserve only supplied fields', () => {
  const action = makeAction('update_preferences', { values: { servings: 3 } }, 2, 'preferences', 'request');
  assert.deepEqual(action, { action_id: 'request', base_revision: 2, view_id: 'preferences', type: 'update_preferences', payload: { values: { servings: 3 } } });
  assert.throws(() => makeAction('eval', {}, 2, null, 'x'));
  assert.throws(() => makeAction('change_stage', {}, null, null, 'x'));
  assert.deepEqual(changedValues({ servings: 2, ingredient_terms: ['虾'] }, { servings: 3, ingredient_terms: ['虾'] }), { servings: 3 });
});

test('typed presentations are deduplicated without interpreting compact snapshots', () => {
  const view = preference(2);
  const message = { actions: [{ kind: 'show_component', component: view }],
    tool_calls: [{ result: { actions: [{ kind: 'show_component', component: view }], ui_snapshot: { components: [{...view,data:{}}] } } }] };
  assert.equal(messageViews(message).length, 1);
  assert.equal(messageViews(message)[0].data.values.servings, 2);
  assert.deepEqual(viewsFrom({views: 'bad'}), []);
});

test('timers recover from UTC deadline and retain paused time', () => {
  const now = Date.parse('2026-10-13T10:00:00Z');
  assert.equal(remainingSeconds({status:'running',deadline_at:'2026-10-13T18:01:05+08:00'},now),65);
  assert.equal(remainingSeconds({status:'paused',remaining_seconds:65,deadline_at:'2000-01-01'},now),65);
  assert.equal(remainingSeconds({status:'running',deadline_at:'2026-10-13T09:00:00Z'},now),0);
  assert.equal(clockText(65),'1:05');
  assert.equal(remainingSeconds({status:'running',deadline_at:'bad'}),null);
});

test('form edits remain drafts until a successful server action and enter the next snapshot', async t => {
  const requests=[];
  const item=controller(t, async (path,options) => {
    if (options?.method==='POST') { requests.push(JSON.parse(options.body)); return response(1); }
    return response(1);
  });
  item.receive('session',response(0));
  const view=preference(0);
  item.setDraft(view,{...view.data.values,servings:3});
  assert.equal(item.unsaved(),true);
  assert.equal(item.canSubmit(),true);
  await item.savePreferences(view,{...view.data.values,servings:3});
  assert.equal(item.canSubmit(),true);
  assert.deepEqual(requests[0].payload,{values:{servings:3}});
  assert.equal((await item.prepareTurn('session')).revision,1);
});

test('revision conflict is not automatically retried; changed component IDs retain the draft', async t => {
  let calls=0;
  const item=controller(t,async()=>{calls++;return {...response(3,'new-preferences'),status:'revision_conflict'};});
  item.receive('session',response(1));
  const view=preference(1);
  item.setDraft(view,{...view.data.values,servings:4});
  await assert.rejects(item.savePreferences(view,{...view.data.values,servings:4}));
  assert.equal(calls,1);
  assert.equal(item.snapshot().revision,3);
  assert.equal(item.entry('session').drafts['new-preferences'].values.servings,4);
  assert.equal(item.canSubmit(),false);
  item.receive('session',response(2));
  assert.equal(item.snapshot().revision,3);
});

test('actions remain owned by their original session while another session is selected', async t => {
  let resolve;
  const item=controller(t,() => new Promise(done=>{resolve=done;}));
  item.receive('session',response(0));
  const pending=item.dispatch('change_stage',{stage:'planning'},preference(0));
  item.activeId='other';
  resolve(response(1));
  await pending;
  assert.equal(item.snapshot('session').revision,1);
  assert.equal(item.snapshot('other'),undefined);
});

test('recipe selection is a draft until confirmation and the next turn receives the persisted IDs', async t => {
  const requests=[];
  const choice={id:'choices',kind:'recipe_choices',title:'候选',revision:0,editable:true,data:{recipes:[]}};
  const saved={...response(1),workspace:{...response(1).workspace,selected_recipe_ids:['chosen']},views:[{...choice,revision:1}]};
  const item=controller(t,async(path,options)=>{
    if(options?.method==='POST') requests.push(JSON.parse(options.body));
    return saved;
  });
  item.receive('session',{...response(0),views:[choice]});
  item.setSelectionDraft(choice,['chosen']);
  assert.equal(item.canSubmit(),false);
  await item.saveSelection(choice,['chosen']);
  assert.equal(item.canSubmit(),true);
  assert.equal(requests[0].type,'select_recipes');
  assert.deepEqual(requests[0].payload,{recipe_ids:['chosen']});
  assert.deepEqual((await item.prepareTurn('session')).selected_recipe_ids,['chosen']);
});

test('known validation rejection does not pretend the operation is still pending', async t => {
  const item=controller(t,async()=>{const error=new Error('invalid');error.status=422;throw error;});
  item.receive('session',response(0));
  await assert.rejects(item.dispatch('change_stage',{stage:'planning'},preference(0)));
  assert.equal(item.entry('session').failedRequest,null);
  assert.equal(item.canSubmit(),true);
});

test('uncertain network retry reuses the exact action ID instead of applying a duplicate operation', async t => {
  const requests=[];
  const item=controller(t,async(path,options)=>{
    requests.push(JSON.parse(options.body));
    if(requests.length===1)throw new Error('offline');
    return response(1);
  });
  item.receive('session',response(0));
  await assert.rejects(item.dispatch('change_stage',{stage:'planning'},preference(0)));
  assert.equal(item.canSubmit(),false);
  await item.retryFailed();
  assert.deepEqual(requests[0],requests[1]);
  assert.equal(item.canSubmit(),true);
});

test('preparing a natural turn saves only changed preference fields and then rereads trusted state', async t => {
  let revision=0; const order=[]; let current=response(0);
  const item=controller(t,async(path,options)=>{
    if(options?.method==='POST') {
      const request=JSON.parse(options.body); order.push(request);
      revision++; current=response(revision);
      current.views[0].data.values.servings=request.payload.values.servings;
      current.workspace.preferences={...current.views[0].data.values};
      return current;
    }
    order.push('GET'); return current;
  });
  item.receive('session',current);
  item.setDraft(preference(0),{ingredient_terms:['虾'],servings:4});
  const snapshot=await item.prepareTurn('session');
  assert.equal(order[0],'GET');
  assert.deepEqual(order[1].payload,{values:{servings:4}});
  assert.equal(order[2],'GET');
  assert.equal(snapshot.preferences.servings,4);
  assert.equal(item.unsaved(),false);
});

test('preference auto-save conflict retains edits and prevents the chat turn from being prepared', async t => {
  let posted=false;
  const item=controller(t,async(path,options)=>{
    if(options?.method==='POST'){posted=true;return {...response(1),status:'revision_conflict'};}
    return response(0);
  });
  item.receive('session',response(0));
  item.setDraft(preference(0),{ingredient_terms:['虾'],servings:4});
  await assert.rejects(item.prepareTurn('session'),/消息没有发送/);
  assert.equal(posted,true);
  assert.equal(item.entry('session').drafts.preferences.values.servings,4);
  assert.equal(item.canSubmit(),false);
});

test('execution transitions need a selected menu, acknowledged prep and completed steps', () => {
  const prep={stage:'prep',recipe_count:1,prep_ready:1,prep_total:2,steps_done:0,steps_total:3};
  assert.equal(canChangeStage(prep,'cooking'),false);
  assert.equal(canChangeStage({...prep,prep_ready:2},'cooking'),true);
  assert.equal(canChangeStage({...prep,recipe_count:0,prep_ready:2},'cooking'),false);
  assert.equal(canChangeStage({...prep,stage:'cooking',steps_done:2},'completed'),false);
  assert.equal(canChangeStage({...prep,stage:'cooking',steps_done:3},'completed'),true);
});

test('later workflow presentations focus the task without rewriting history or repeating old query rows', () => {
  const choices={...preference(2,'choices'),kind:'recipe_choices',data:{recipes:[]}};
  const checklist={...preference(2,'prep'),kind:'prep_checklist',data:{items:[]}};
  const progress={...preference(2,'progress'),kind:'task_progress',data:{stage:'prep'}};
  const original=[choices,checklist,preference(2),progress];
  const visible=visibleWorkspaceViews(original,'prep');
  assert.equal(visible[0].id,'progress');
  assert.equal(visible[1].id,'prep');
  assert.equal(visible.some(view=>view.kind==='recipe_choices'),false);
  assert.equal(original.length,4);
  assert.equal(messageViews({actions:[{kind:'show_component',component:choices}]}).length,1);
  assert.deepEqual(legacyRecipeItems({recipes:[{recipe_id:'previous'}],actions:[{component:checklist}]}),[]);
  assert.equal(legacyRecipeItems({recipes:[{recipe_id:'current-query'}],tool_calls:[{name:'search_recipes',result:{status:'ok'}}]}).length,1);
});

test('a workspace-only answer never presents inherited query cards without a fresh successful recipe query', () => {
  const prior=[{recipe_id:'previous-query'}];
  assert.deepEqual(legacyRecipeItems({recipes:prior,actions:[],tool_calls:[{name:'get_cooking_workspace',result:{status:'ok',ui_snapshot:{revision:9}}}]}),[]);
  assert.deepEqual(legacyRecipeItems({recipes:prior,actions:[],tool_calls:[]}),[]);
  assert.deepEqual(legacyRecipeItems({recipes:prior,tool_calls:[{name:'get_recipe',result:{status:'not_found'}}]}),[]);
  const detail={recipe_id:'fresh-detail'};
  assert.deepEqual(legacyRecipeItems({recipes:prior,selected_recipe:detail,tool_calls:[{name:'get_recipe',result:{status:'ok'}}]}),[detail]);
});

test('reading a workspace does not display every stored view; placements come from explicit actions',()=>{
  const view=preference(3);
  assert.deepEqual(messageViews({tool_calls:[{result:{views:[view],ui_snapshot:{components:[view]}}}]}),[]);
  const result=messageViews({actions:[{kind:'show_component',placement:'workspace',component:view}]});
  assert.equal(result[0].placement,'workspace');
});

test('inline callback remains bound to the original conversation after switching',async t=>{
  const paths=[];
  const item=controller(t,async(path)=>{paths.push(path);return response(1);});
  item.receive('session',response(0));
  const context=item.context(preference(0),'session');
  item.activeId='other';
  context.onDraft(preference(0),{ingredient_terms:['虾'],servings:4});
  await context.savePreferences(preference(0),{ingredient_terms:['虾'],servings:4});
  assert.deepEqual(paths,['/api/sessions/session/actions']);
  assert.equal(item.snapshot('other'),undefined);
});

test('model workspace updates wait for turn completion while existing views remain available', t => {
  const item=controller(t);item.receive('session',response(0));
  item.setRunning('session',true);
  item.receive('session',response(2,'model-form'));
  item.receive('session',response(1,'older-read'));
  assert.equal(item.snapshot().revision,0);
  assert.equal(item.entry('session').views[0].id,'preferences');
  item.setRunning('session',false);
  assert.equal(item.snapshot().revision,2);
  assert.equal(item.entry('session').views[0].id,'model-form');
  assert.equal(item.entry('session').pendingResponse,null);
});

test('reconnected running turn hides partial forms and cancellation releases committed views', t => {
  const item=controller(t);item.setRunning('session',true);
  item.receive('session',response(3,'partial'));
  assert.deepEqual(item.entry('session').views,[]);
  // The caller settles the presentation for success, failure and cancellation.
  item.setRunning('session',false);
  assert.equal(item.entry('session').views[0].id,'partial');
  item.setRunning('session',true);item.receive('session',response(4,'queued'));
  item.receive('session',response(5,'user-action'),{immediate:true});
  assert.equal(item.snapshot().revision,5);
  assert.equal(item.entry('session').views[0].id,'partial');
  item.setRunning('session',false);
  assert.equal(item.entry('session').views[0].id,'user-action');
});

test('confirming dishes saves once before continuing in the original session', async t => {
  const order=[];let finishSave;
  const choice={id:'choices',kind:'recipe_choices',revision:0,editable:true,data:{recipes:[{recipe_id:'a',name:'清蒸虾'}]}};
  const item=controller(t,async(path,options)=>{
    order.push(JSON.parse(options.body).type);
    return new Promise(resolve=>{finishSave=()=>resolve({...response(1),workspace:{revision:1,stage:'planning',selected_recipe_ids:['a']},views:[{...choice,revision:1}]});});
  });
  item.onConfirm=async(id,message)=>{
    assert.equal(item.snapshot(id).revision,1);assert.equal(item.unsaved(id),false);
    order.push({id,message});
  };
  item.receive('session',{...response(0),views:[choice]});item.setSelectionDraft(choice,['a']);
  const [confirm,discard]=item.composerActions();
  assert.equal(confirm.tone,'primary');assert.equal(discard.tone,'secondary');assert.equal(item.syncLabel(),'');
  const first=confirm.run();await confirm.run();item.activeId='other';finishSave();await first;
  assert.equal(order.length,2);assert.equal(order[0],'select_recipes');
  assert.equal(order[1].id,'session');assert.match(order[1].message,/清蒸虾/);
});

test('failed confirmation retains its draft and never starts a model turn', async t => {
  globalThis.document ||= {querySelectorAll:()=>[]};
  const item=controller(t,async()=>{const error=new Error('invalid');error.status=422;throw error;});
  let continued=false;item.onConfirm=async()=>{continued=true;};
  item.receive('session',response(0));item.setDraft(preference(0),{ingredient_terms:['虾'],servings:3});
  await assert.rejects(item.composerActions()[0].run(),/invalid/);
  assert.equal(continued,false);assert.equal(item.entry('session').confirming,false);
  assert.equal(item.entry('session').drafts.preferences.values.servings,3);
});

test('a failed continuation keeps its message and only explicit retry starts it once', async () => {
  const queue=new ConfirmationQueue();let calls=0;
  queue.enqueue('original','我已确认清蒸虾，请继续安排。');
  await queue.start('original',async()=>{calls++;return {error:'workspace offline'};});
  assert.equal(queue.get('original').status,'failed');
  assert.equal(queue.get('original').error,'workspace offline');
  await queue.start('original',async()=>{calls++;});
  assert.equal(calls,1);
  queue.retry('original');let finishPreparation;
  const pending=queue.start('original',async(message,prepared)=>{
    calls++;assert.match(message,/清蒸虾/);
    await new Promise(resolve=>{finishPreparation=resolve;});prepared();
  });
  await queue.start('original',async()=>{calls++;});
  assert.equal(queue.get('original').status,'preparing');
  finishPreparation();await pending;
  assert.equal(calls,2);assert.equal(queue.get('original'),undefined);
});

test('unknown recovery keeps model components hidden until a confirmed terminal status', t => {
  const item=controller(t),view=preference(2,'new-form');
  const message={streaming:false,error:'connection interrupted',actions:[{kind:'show_component',component:view}]};
  const session={running:false,messages:[message]};
  recoverRunningTurn(session,message);item.setRunning('session',session.running);
  item.receive('session',response(2,'new-form'));
  assert.equal(session.running,true);assert.equal(message.error,undefined);
  assert.deepEqual(visibleMessageViews(message),[]);assert.deepEqual(item.entry('session').views,[]);
  // A completed polling response replaces the temporary recovery message.
  const completed={actions:message.actions};item.setRunning('session',false);
  assert.equal(visibleMessageViews(completed)[0].id,'new-form');
  assert.equal(item.entry('session').views[0].id,'new-form');
});

test('presentation refreshes preserve business drafts and reject older display responses', t => {
  const item=controller(t);
  const first={...response(4,'first'),workspace:{revision:4,presentation_revision:1,stage:'idea'}};
  item.receive('session',first);
  item.setDraft(first.views[0],{...first.views[0].data.values,servings:3});
  const latest={...response(4,'latest'),workspace:{revision:4,presentation_revision:3,stage:'idea'}};
  assert.equal(item.receive('session',latest),true);
  assert.equal(item.entry('session').drafts.latest.values.servings,3);
  assert.equal(item.entry('session').drafts.latest.conflict,false);
  assert.equal(item.receive('session',{...response(4,'older'),workspace:{revision:4,presentation_revision:2,stage:'idea'}}),false);
  assert.equal(item.entry('session').views[0].id,'latest');
  item.receive('session',{...response(5,'changed'),workspace:{revision:5,presentation_revision:0,stage:'idea'}});
  assert.equal(item.entry('session').drafts.changed.conflict,true);
});

test('queued model presentation uses both revisions before revealing the terminal view', t => {
  const item=controller(t);
  item.receive('session',{...response(2,'existing'),workspace:{revision:2,presentation_revision:1,stage:'idea'}});
  item.setRunning('session',true);
  item.receive('session',{...response(2,'newest'),workspace:{revision:2,presentation_revision:4,stage:'idea'}});
  item.receive('session',{...response(2,'stale'),workspace:{revision:2,presentation_revision:2,stage:'idea'}});
  assert.equal(item.entry('session').views[0].id,'existing');
  item.setRunning('session',false);
  assert.equal(item.entry('session').views[0].id,'newest');
});
