import test from 'node:test';
import assert from 'node:assert/strict';
import {createSessionController} from '../chat-sessions.js';

class Element extends EventTarget {
  constructor(tag){super();this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.scrollTop=0;this.classList={contains:()=>false};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  contains(){return false;}
  querySelector(){return null;}
}
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};}
function setup(t) {
  const previous={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch,localStorage:globalThis.localStorage};
  t.after(()=>Object.assign(globalThis,previous));
  globalThis.document={createElement:tag=>new Element(tag),body:new Element('body'),activeElement:null};
  globalThis.window={addEventListener(){}};
  const stored=new Map([['chefmate:active-session','missing']]);
  globalThis.localStorage={setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)};
  const response=deferred();globalThis.fetch=()=>response.promise;
  const state={currentId:null,blankId:'draft:original',selectionEpoch:0,componentsEpoch:0,sessions:[],cache:new Map(),runs:new Map(),
    drafts:new Map([['missing','丢失会话草稿']]),imageDrafts:new Map(),imageUploads:new Map(),failedDrafts:new Map(),confirmationQueue:new Map()};
  const ui={sessions:new Element('nav'),'composer-input':{value:'原来的草稿'}};
  const effects={removed:[],cleared:[],selected:[],toasts:[]},noop=()=>{};
  const controller=createSessionController({state,ui,workspace:{setRunning:noop,select:id=>effects.selected.push(id),remove:id=>effects.removed.push(id)},
    cookingFocus:{close:noop},currentSession:()=>state.cache.get(state.currentId),busy:()=>false,pendingQuestion:()=>null,
    toast:message=>effects.toasts.push(message),announce:noop,clearQuestion:id=>effects.cleared.push(id),draftKey:()=>state.currentId??state.blankId,
    persistDraft:()=>state.drafts.set(state.currentId??state.blankId,ui['composer-input'].value),resizeInput:noop,renderConversation:noop,
    renderQueryPanel:noop,updateComposer:noop,updateConversationState:noop,resetRecipe:noop,loadComponents:noop,setSidebar:noop,renderWelcomeTemplates:noop});
  return {controller,state,ui,effects,response,stored};
}
const missing=()=>new Response(JSON.stringify({detail:'对话不存在。'}),{status:404});

test('a stale browser selection returns to a new draft without losing either unsent draft or local image',async t=>{
  const {controller,state,ui,effects,response,stored}=setup(t);
  const image={id:'uploaded-before-restart',local:false,blob:new Blob(['image']),previewUrl:'blob:preview'};
  state.imageDrafts.set('missing',[image]);
  const opening=controller.openSession('missing');
  ui['composer-input'].value='刚输入的新内容';
  response.resolve(missing());await opening;
  assert.equal(state.currentId,null);assert.equal(stored.has('chefmate:active-session'),false);
  assert.equal(ui['composer-input'].value,'刚输入的新内容');
  assert.equal(state.drafts.get('draft:original'),'原来的草稿');
  assert.equal(state.failedDrafts.has('draft:original'),true);
  assert.equal(state.drafts.get(state.blankId),'刚输入的新内容');
  assert.equal(state.imageDrafts.get(state.blankId)[0].local,true);
  assert.equal(state.imageDrafts.get(state.blankId)[0].blob,image.blob);
  assert.deepEqual(effects.removed,['missing']);assert.deepEqual(effects.cleared,['missing']);
  assert.deepEqual(effects.selected,['missing',null]);assert.match(effects.toasts[0],/已打开新对话/);
});

test('a late missing-session response cannot replace a newer selected conversation',async t=>{
  const {controller,state,ui,effects,response}=setup(t);
  const opening=controller.openSession('missing');
  state.currentId='newer';state.selectionEpoch++;ui['composer-input'].value='新会话草稿';
  response.resolve(missing());await opening;
  assert.equal(state.currentId,'newer');assert.equal(ui['composer-input'].value,'新会话草稿');
  assert.deepEqual(effects.removed,[]);assert.deepEqual(effects.toasts,[]);
});
