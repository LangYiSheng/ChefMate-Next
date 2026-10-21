import test from 'node:test';
import assert from 'node:assert/strict';
import {KitchenCenters} from '../centers.js';

// Only the DOM operations used by the real center handlers are represented.
// Requests stay deferred even after abort so stale-response guards are tested.
class Element extends EventTarget {
  constructor(tag, document) {
    super();this.tagName=tag.toUpperCase();this.ownerDocument=document;this.children=[];
    this.dataset={};this.attributes={};this.className='';this.copy='';this.value='';
    this.disabled=false;this.hidden=false;this.readOnly=false;this.min='';this.max='';
    this.type=tag==='textarea'?'textarea':'text';this.validity={valid:true};this.clientWidth=1200;
  }
  get classList() {
    const tokens=()=>new Set(this.className.split(/\s+/).filter(Boolean));
    return {
      add:(...names)=>{const set=tokens();names.forEach(name=>set.add(name));this.className=[...set].join(' ');},
      remove:(...names)=>{const set=tokens();names.forEach(name=>set.delete(name));this.className=[...set].join(' ');},
      toggle:(name,force)=>{const set=tokens(),active=force??!set.has(name);active?set.add(name):set.delete(name);this.className=[...set].join(' ');return active;},
    };
  }
  setAttribute(name,value) {this.attributes[name]=String(value);if(name==='class')this.className=String(value);if(name==='id')this.id=String(value);}
  getAttribute(name) {
    if(name==='class')return this.className;if(name==='id')return this.id;
    if(name.startsWith('data-'))return this.dataset[name.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())];
    return this.attributes[name];
  }
  removeAttribute(name) {delete this.attributes[name];}
  append(...items) {
    for(const item of items){
      if(item.tagName==='#FRAGMENT'){this.append(...item.children);continue;}
      item.remove();item.parentNode=this;this.children.push(item);
    }
  }
  insertBefore(item,reference) {
    item.remove();const index=this.children.indexOf(reference);item.parentNode=this;
    if(index<0)this.children.push(item);else this.children.splice(index,0,item);return item;
  }
  remove() {if(this.parentNode){const siblings=this.parentNode.children;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
  replaceChildren(...items) {for(const child of [...this.children])child.remove();this.copy='';this.append(...items);}
  get childNodes() {return this.children;}
  get firstChild() {return this.children[0]??null;}
  set textContent(value) {this.replaceChildren();this.copy=String(value);}
  get textContent() {return this.copy+this.children.map(child=>child.textContent).join('');}
  get isConnected() {return this===this.ownerDocument.body || this===this.ownerDocument.head || Boolean(this.parentNode?.isConnected);}
  querySelectorAll(selector) {return this.children.flatMap(descendants).filter(item=>selector.split(',').some(part=>matches(item,part.trim())));}
  querySelector(selector) {return this.querySelectorAll(selector)[0]??null;}
  focus() {this.ownerDocument.activeElement=this;}
  getClientRects() {return this.hidden?[]:[{}];}
  checkValidity() {return this.validity.valid && (!this.required || this.value!=='');}
  reportValidity() {return this.checkValidity();}
  dispatchEvent(event) {
    const accepted=super.dispatchEvent(event);
    return event.bubbles && !event.cancelBubble && this.parentNode
      ? this.parentNode.dispatchEvent(event) && accepted : accepted;
  }
}
const descendants=root=>[root,...root.children.flatMap(descendants)];
function matches(element,selector) {
  const shape=selector.replace(/\[[^\]]*\]/g,''),tag=shape.match(/^[\w-]+/)?.[0];
  if(tag && element.tagName!==tag.toUpperCase())return false;
  for(const [,name] of shape.matchAll(/\.([\w-]+)/g))if(!element.className.split(/\s+/).includes(name))return false;
  for(const [,name,value] of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)){
    const actual=element.getAttribute(name);if(actual===undefined || (value!==undefined && actual!==value))return false;
  }
  return true;
}
function installDOM(t) {
  const previous={document:globalThis.document,window:globalThis.window,ResizeObserver:globalThis.ResizeObserver};
  t.after(()=>Object.assign(globalThis,previous));
  const document={};document.createElement=tag=>new Element(tag,document);
  document.createElementNS=(_,tag)=>document.createElement(tag);
  document.createDocumentFragment=()=>document.createElement('#fragment');
  document.head=document.createElement('head');document.body=document.createElement('body');document.activeElement=document.body;
  document.querySelector=selector=>document.head.querySelector(selector) || document.body.querySelector(selector);
  document.getElementById=id=>[...descendants(document.head),...descendants(document.body)].find(item=>item.id===id)??null;
  const main=document.createElement('main');main.className='main';const settings=document.createElement('section');settings.id='settings-host';main.append(settings);document.body.append(main);
  globalThis.document=document;globalThis.window=new EventTarget();
  window.matchMedia=query=>({matches:query.includes('prefers-reduced-motion'),addEventListener(){}});
  globalThis.ResizeObserver=class {observe(){} disconnect(){}};
}
function deferred() {
  let resolve,reject;const promise=new Promise((accept,fail)=>{resolve=accept;reject=fail;});
  return {promise,resolve,reject};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const draft=()=>({
  recipe_id:'custom-lifecycle',status:'draft',revision:1,source_kind:'text',
  source_text:'两人份番茄炒蛋，总耗时10分钟。鸡蛋2个。炒熟即可。',time_evidence:'总耗时10分钟',
  document:{name:'番茄炒蛋',description:'家常做法',servings:2,servings_evidence:'两人份',prep_time_min:null,cook_time_min:null,total_time_min:10,
    ingredients:[{canonical_name:'鸡蛋',raw_amount:'2',raw_unit:'个',grams:null,amount_evidence:'',scalable:true,usda_query:'',allergens:['egg']}],
    steps:['把鸡蛋炒熟。'],warnings:[]},
});
const card=record=>({recipe_id:record.recipe_id,name:record.document.name,origin:'user',status:record.status,servings:2,total_time_min:10});
const listing=record=>({items:record?[card(record)]:[],total:record?1:0});
const control=(root,text)=>root.querySelectorAll('button').find(item=>item.textContent===text);
async function openDraft(centers,record) {
  await centers.show('recipes');await control(centers.content,record.document.name).onclick();await flush();
  assert.equal(centers.editor?.record.recipe_id,record.recipe_id);
  const name=centers.editor.body.querySelector('input');name.value='少油番茄炒蛋';name.dispatchEvent(new Event('input',{bubbles:true}));
  assert.equal(centers.editor.state.dirty,true);return {editor:centers.editor,name};
}

test('late list and detail fetches cannot write back after leaving the center',async t=>{
  installDOM(t);const list=deferred(),detail=deferred(),record=draft();let firstList=true,listSignal,detailSignal;
  const centers=new KitchenCenters({api:(path,options={})=>{
    if(path.startsWith('/api/cookbook?')){if(firstList){firstList=false;listSignal=options.signal;return list.promise;}return Promise.resolve(listing(record));}
    if(path===`/api/cookbook/${record.recipe_id}`){detailSignal=options.signal;return detail.promise;}
    throw new Error(`Unexpected request ${path}`);
  }});
  const loading=centers.show('recipes'),oldContent=centers.content;
  await centers.show('chat');assert.equal(listSignal.aborted,true);list.resolve(listing(record));await loading;
  assert.equal(centers.host.hidden,true);assert.equal(oldContent.textContent,'正在读取…');
  await centers.show('recipes');const reading=centers.loadRecipe(card(record)),editor=centers.editor,oldBody=editor.body;
  await centers.show('chat');assert.equal(detailSignal.aborted,true);detail.resolve(record);await reading;
  assert.equal(centers.editor,null);assert.equal(editor.state.alive,false);assert.equal(editor.panel.isConnected,false);
  assert.equal(oldBody.textContent,'正在读取菜谱…');assert.equal(oldBody.querySelector('input'),null);
});

test('the real draft save handler locks every field and restores the same draft on failure',async t=>{
  installDOM(t);const record=draft(),save=deferred();let writes=0;
  const centers=new KitchenCenters({api:async(path,options={})=>{
    if(options.method==='PUT'){writes++;return save.promise;}
    return path.includes('?')?listing(record):structuredClone(record);
  }});
  const {editor,name}=await openDraft(centers,record),button=control(editor.footer,'保存草稿');
  const operation=button.onclick();assert.equal(editor.state.busy,true);
  const interactive=editor.panel.querySelectorAll('input,textarea,select,button');
  assert.ok(interactive.length>12);assert.ok(interactive.every(item=>item.disabled));
  await button.onclick();assert.equal(writes,1,'a repeated save cannot submit the pending revision again');
  save.reject(new Error('保存连接中断'));await operation;
  assert.equal(centers.editor,editor);assert.equal(editor.body.querySelector('input'),name);
  assert.equal(name.value,'少油番茄炒蛋');assert.equal(editor.state.busy,false);assert.equal(editor.state.dirty,true);
  assert.ok(interactive.every(item=>!item.disabled));assert.match(editor.status.textContent,/保存连接中断/);
});

test('publish retry after a committed PUT does not PUT that revision again',async t=>{
  installDOM(t);const record=draft(),writes=[];let attempts=0,published=false;
  const updated={...structuredClone(record),revision:2};updated.document.name='少油番茄炒蛋';
  const saved={...structuredClone(updated),status:'saved',revision:3};
  const centers=new KitchenCenters({api:async(path,options={})=>{
    if(options.method){writes.push({path,method:options.method,body:JSON.parse(options.body)});
      if(options.method==='PUT')return structuredClone(updated);
      if(++attempts===1)throw new Error('发布响应暂时不可用');published=true;return structuredClone(saved);
    }
    return path.includes('?')?listing(published?saved:record):structuredClone(record);
  }});
  const {editor}=await openDraft(centers,record),publish=control(editor.footer,'确认保存菜谱');
  await publish.onclick();assert.equal(centers.editor,editor);assert.equal(editor.record.revision,2);
  assert.equal(editor.state.dirty,false);assert.equal(editor.state.busy,false);assert.equal(publish.disabled,false);
  assert.match(editor.status.textContent,/发布响应暂时不可用/);
  await publish.onclick();
  assert.deepEqual(writes.map(item=>item.method),['PUT','POST','POST']);
  assert.deepEqual(writes.map(item=>item.path),[`/api/cookbook/${record.recipe_id}`,`/api/cookbook/${record.recipe_id}/publish`,`/api/cookbook/${record.recipe_id}/publish`]);
  assert.deepEqual(writes.map(item=>item.body.revision),[1,2,2]);
  assert.equal(writes[0].body.recipe.name,'少油番茄炒蛋');assert.equal(centers.editor,null);
  assert.equal(centers.source,'user');assert.match(centers.notice.textContent,/菜谱已保存/);
});
