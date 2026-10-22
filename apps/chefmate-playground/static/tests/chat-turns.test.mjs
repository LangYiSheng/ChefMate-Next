import test from 'node:test';
import assert from 'node:assert/strict';
import {createTurnController} from '../chat-turns.js';

function setup(t,handle) {
  const previous=globalThis.fetch;t.after(()=>globalThis.fetch=previous);
  const requests=[],effects={poll:[],running:[],announcements:[],renders:0};
  globalThis.fetch=async(path,options={})=>{requests.push({path,...options});return handle(path,options);};
  const state={currentId:'chat',blankId:'draft:initial',selectionEpoch:1,initializing:false,creating:false,preparing:false,
    status:{ready:true},cache:new Map([['chat',{id:'chat',title:'新对话',messages:[]}]]),runs:new Map(),
    drafts:new Map([['chat','给我推荐番茄菜']]),imageDrafts:new Map(),imageUploads:new Map(),failedDrafts:new Map(),
    confirmationQueue:{get:()=>null}};
  const noop=()=>{},ui={'composer-input':{value:'给我推荐番茄菜'}};
  const workspace={prepareTurn:async()=>({revision:3}),setRunning:(...args)=>effects.running.push(args),load:async()=>{},select:noop,remove:noop};
  const controller=createTurnController({state,ui,workspace,busy:id=>state.runs.has(id)||state.cache.get(id)?.running,
    pendingQuestion:()=>null,savedAnswer:()=>null,draftKey:id=>id??state.currentId??state.blankId,maxImages:()=>3,
    toast:noop,announce:text=>effects.announcements.push(text),persistDraft:noop,updateComposer:noop,resizeInput:noop,
    blankTitle:title=>title==='新对话',scheduleRender:()=>effects.renders++,renderConversation:noop,renderSessions:noop,
    renderQueryPanel:noop,renderComponentsPanel:noop,loadSessions:async()=>{},loadComponents:noop,
    pollRemoteSession:(...args)=>effects.poll.push(args)});
  return {state,controller,effects,requests,workspace,ui};
}
function json(value){return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});}
function stream(events){return new Response(events.map(event=>`data: ${JSON.stringify(event)}\n\n`).join(''),{headers:{'Content-Type':'text/event-stream'}});}

test('a submitted turn consumes public events and yields to the canonical saved session',async t=>{
  const saved={id:'chat',title:'番茄菜',last_status:'completed',messages:[{role:'user',content:'给我推荐番茄菜'},{role:'assistant',content:'来一份番茄炒蛋吧。'}]};
  const {controller,state,effects,requests}=setup(t,(path,options)=>options.method==='POST'?stream([
    {type:'assistant_delta',text:'先查一下'},
    {type:'tool_start',call_id:'search',name:'search_recipes'},
    {type:'tool_end',call_id:'search',name:'search_recipes',result:{status:'ok'}},
    {type:'result',result:{status:'completed',text:'来一份番茄炒蛋吧。'}},
    {type:'done',status:'completed'},
  ]):json({...saved,last_run:{request_id:JSON.parse(requests[0].body).request_id}}));
  await controller.sendMessage('给我推荐番茄菜');
  assert.equal(state.runs.size,0);assert.equal(state.preparing,false);
  assert.equal(state.cache.get('chat').messages.at(-1).content,'来一份番茄炒蛋吧。');
  assert.equal(state.drafts.has('chat'),false);assert.equal(uiValue(requests),'给我推荐番茄菜');
  assert.equal(effects.renders,5);assert.equal(effects.announcements.at(-1),'查询完成。');
  assert.deepEqual(effects.running,[['chat',true],['chat',false]]);
});
function uiValue(requests){return JSON.parse(requests[0].body).message;}

test('the first message creates its server session before preparing a workspace or submitting a turn',async t=>{
  const previous=globalThis.localStorage;t.after(()=>globalThis.localStorage=previous);
  const stored=new Map();globalThis.localStorage={setItem:(key,value)=>stored.set(key,value)};
  const {controller,state,requests,workspace,ui}=setup(t,(path,options)=>{
    if(path==='/api/sessions')return json({id:'fresh',title:'新对话',messages:[]});
    if(path.endsWith('/turn'))return stream([{type:'result',result:{status:'completed',text:'查好了。'}},{type:'done',status:'completed'}]);
    return json({id:'fresh',last_status:'completed',last_run:{request_id:JSON.parse(requests.find(request=>request.path.endsWith('/turn')).body).request_id},messages:[{role:'assistant',content:'查好了。'}]});
  });
  state.currentId=null;state.cache.clear();state.drafts.clear();state.drafts.set(state.blankId,ui['composer-input'].value);
  const prepared=[];workspace.prepareTurn=async id=>{prepared.push(id);return {revision:9};};
  await controller.sendMessage('给我推荐番茄菜');
  assert.equal(requests[0].path,'/api/sessions');assert.equal(requests[0].method,'POST');
  assert.equal(requests[1].path,'/api/sessions/fresh/turn');
  assert.deepEqual(JSON.parse(requests[1].body).ui_snapshot,{revision:9});assert.deepEqual(prepared,['fresh']);
  assert.equal(state.currentId,'fresh');assert.equal(stored.get('chefmate:active-session'),'fresh');
  assert.equal(state.cache.get('fresh').messages.at(-1).content,'查好了。');
  assert.equal(ui['composer-input'].value,'');assert.equal(state.creating,false);assert.equal(state.preparing,false);
});

test('a dropped stream discovers the still-running server turn and resumes polling without retrying it',async t=>{
  const {controller,state,effects,requests}=setup(t,(_path,options)=>options.method==='POST'?stream([{type:'assistant_delta',text:'还在处理'}]):json({id:'chat',last_status:'running',active_turn:{id:'active',text:'仍在查询'},messages:[{role:'user',content:'给我推荐番茄菜'}]}));
  await controller.sendMessage('给我推荐番茄菜');
  assert.equal(requests.filter(request=>request.method==='POST').length,1);
  assert.equal(state.cache.get('chat').running,true);assert.equal(state.cache.get('chat').messages.at(-1).streaming,true);
  assert.deepEqual(effects.poll,[['chat',1]]);assert.deepEqual(effects.running.at(-1),['chat',true]);
  assert.match(effects.announcements.at(-1),/恢复本次执行/);
});

test('stop requests server cancellation once and keeps its stream available for the final result',async t=>{
  const {controller,state,requests}=setup(t,()=>json({status:'cancelling'}));
  const abort=new AbortController(),run={controller:abort};state.runs.set('chat',run);
  await controller.cancelTurn();await controller.cancelTurn();
  assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/sessions/chat/cancel');
  assert.equal(run.cancelling,true);assert.equal(abort.signal.aborted,false);
});
