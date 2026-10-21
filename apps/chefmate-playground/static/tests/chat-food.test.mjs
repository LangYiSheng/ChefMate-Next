import test from 'node:test';
import assert from 'node:assert/strict';
import {ChatFood,foodPresentations} from '../chat-food.js';

class Element {
  constructor(tag,doc){this.tagName=tag.toUpperCase();this.ownerDocument=doc;this.children=[];this.attributes={};this.dataset={};this.className='';this.copy='';this.disabled=false;}
  get classList(){return {add:(...items)=>this.className+=` ${items.join(' ')}`};}
  setAttribute(name,value){this.attributes[name]=String(value);}
  append(...items){for(const item of items){item.remove();item.parentNode=this;this.children.push(item);}}
  replaceChildren(...items){for(const child of [...this.children])child.remove();this.copy='';this.append(...items);}
  remove(){if(this.parentNode){const siblings=this.parentNode.children;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
  get isConnected(){return this===this.ownerDocument.body || Boolean(this.parentNode?.isConnected);}
  set textContent(value){this.replaceChildren();this.copy=String(value);}
  get textContent(){return this.copy+this.children.map(child=>child.textContent).join('');}
  querySelectorAll(selector){return descendants(this).slice(1).filter(item=>item.tagName===selector.toUpperCase());}
}
const descendants=root=>[root,...root.children.flatMap(descendants)];
const findButton=(root,label)=>descendants(root).find(item=>item.tagName==='BUTTON'&&item.textContent===label);
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const record=(updates={})=>({id:'food-one',revision:1,status:'draft',date:'2026-10-21',nutrition:{total:{calories_kcal:230,protein_g:5,fat_g:null,carbohydrate_g:null},estimated:true},document:{title:'午饭',meal_type:'lunch',items:[{name:'米饭',portion:'一碗',nutrition:{calories_kcal:230},evidence:'根据餐盘估计',assumptions:['约200克']}],notes:''},...updates});
function install(t,api,options={}){const previous=globalThis.document;t.after(()=>globalThis.document=previous);const doc={};doc.createElement=tag=>new Element(tag,doc);doc.body=doc.createElement('body');globalThis.document=doc;let current='chat-one';const asks=[];
  const chat=new ChatFood({api,sessionId:()=>current,ask:(...args)=>asks.push(args),...options});
  const render=(value=record(),context={})=>{const root=chat.render({tool_calls:[{name:'create_food_log_draft',result:{status:'ok',draft:value}}]},{sessionId:'chat-one',...context});if(root)doc.body.append(root);return root;};
  return {doc,chat,render,asks,switchSession:value=>current=value};
}

test('duplicate create/read/revise receipts show the latest record once and respect message ownership',t=>{
  const old=record(),latest=record({revision:3});const calls=[{name:'create_food_log_draft',result:{status:'ok',draft:old}},{name:'get_food_log',result:{status:'ok',record:old}},{name:'revise_food_log_draft',result:{status:'ok',draft:latest}},{name:'get_food_log',result:{status:'not_found'}}];
  assert.deepEqual(foodPresentations(calls).records,[latest]);const {chat}=install(t,async()=>{throw new Error('unexpected request');});
  const owners=new Map([[old.id,2]]);assert.equal(chat.render({tool_calls:calls},{sessionId:'chat-one',recordOwners:owners,index:0}),null);
  const root=chat.render({tool_calls:calls},{sessionId:'chat-one',recordOwners:owners,index:2});assert.equal(root.children.length,1);
});

test('double confirmation sends one CAS publication and changes the actual card to saved',async t=>{
  const response=deferred(),requests=[];const {render,chat}=install(t,async(path,options)=>{requests.push([path,JSON.parse(options.body)]);return response.promise;});
  const root=render(),confirm=findButton(root,'确认记录'),first=confirm.onclick(),second=confirm.onclick();await second;
  assert.equal(requests.length,1);assert.deepEqual(requests[0][1],{revision:1});assert.equal(chat.saving.has('food-one'),true);assert.equal(findButton(root,'正在确认…').disabled,true);
  response.resolve(record({status:'saved',revision:2}));await first;
  assert.equal(chat.saving.size,0);assert.equal(findButton(root,'确认记录'),undefined);assert.match(root.textContent,/已记录/);assert.match(root.textContent,/这顿饭记好了/);
});

for(const departure of ['session','host'])test(`a late initial record read never paints after ${departure} departure`,async t=>{
  const response=deferred(),{render,switchSession}=install(t,()=>response.promise);const receipt={id:'food-one',revision:1,status:'draft',title:'待读取',food_count:1};const root=render(receipt),before=root.textContent;
  if(departure==='session')switchSession('chat-two');else root.remove();response.resolve(record());await flush();
  assert.equal(root.textContent,before);assert.match(before,/正在读取/);
});

for(const departure of ['session','host'])test(`a late CAS refresh cannot paint after ${departure} departure`,async t=>{
  const response=deferred(),requests=[];const {render,switchSession,chat}=install(t,async(path,options)=>{requests.push([path,options]);if(options?.method==='POST')throw new Error('记录已更新');return response.promise;});
  const root=render(),operation=findButton(root,'确认记录').onclick();await flush();const before=root.textContent;
  assert.equal(requests.length,2);if(departure==='session')switchSession('chat-two');else root.remove();
  response.resolve(record({revision:2,document:{...record().document,title:'其他位置修改的记录'}}));await operation;
  assert.equal(root.textContent,before);assert.doesNotMatch(root.textContent,/其他位置修改/);assert.equal(chat.saving.size,0);
});

test('CAS conflicts refresh the canonical draft and restore the confirmation label before retry',async t=>{
  const requests=[],fresh=record({revision:4,document:{...record().document,title:'已修改的午饭'}});
  const {render}=install(t,async(path,options)=>{requests.push([path,options]);if(options?.method==='POST')throw new Error('记录已更新，请复核');return fresh;});
  const root=render();await findButton(root,'确认记录').onclick();const retry=findButton(root,'确认记录');
  assert.ok(retry);assert.equal(retry.disabled,false);assert.equal(findButton(root,'正在确认…'),undefined);assert.match(root.textContent,/已修改的午饭/);assert.match(root.textContent,/请复核/);
  await retry.onclick();assert.equal(JSON.parse(requests[2][1].body).revision,4);
});

test('a timed-out publish recovered as saved cannot offer another confirmation',async t=>{
  const {render}=install(t,async(_path,options)=>{if(options?.method==='POST')throw new Error('请求超时');return record({revision:2,status:'saved'});});
  const root=render();await findButton(root,'确认记录').onclick();assert.equal(findButton(root,'确认记录'),undefined);assert.match(root.textContent,/已经记录好了/);
});

test('readonly cards cannot confirm and detail/statistic actions stay in the conversation',async t=>{
  let requested=0;const {render,asks,chat,doc}=install(t,async()=>{requested++;});const root=render(record(),{canAct:false});
  assert.equal(findButton(root,'确认记录').disabled,true);await findButton(root,'确认记录').onclick();assert.equal(requested,0);
  const summary=chat.summary({summary:{record_count:1,total:{calories_kcal:null},known_total:{calories_kcal:230}},items:[{id:'food-one',date:'2026-10-21',title:'午饭'}]},{sessionId:'chat-one',canAct:true});doc.body.append(summary);
  findButton(summary,'2026-10-21 · 午饭').onclick();assert.match(asks[0][0],/2026-10-21/);assert.match(asks[0][0],/午饭/);assert.equal(asks[0][1],'send');assert.match(summary.textContent,/已知部分/);assert.match(summary.textContent,/未知营养未填零/);
});

test('leaving and returning during publication repaints the current host after its saving lock clears',async t=>{
  const response=deferred(),repaints=[];let mounted;
  const env=install(t,()=>response.promise,{onChange:id=>{repaints.push(id);mounted.remove();mounted=env.render();}});
  const original=env.render(),operation=findButton(original,'确认记录').onclick();original.remove();env.switchSession('chat-two');
  env.switchSession('chat-one');mounted=env.render();assert.equal(findButton(mounted,'正在确认…').disabled,true);
  response.resolve(record({status:'saved',revision:2}));await operation;
  assert.deepEqual(repaints,['chat-one']);assert.equal(env.chat.saving.size,0);assert.equal(mounted.isConnected,true);
  assert.equal(findButton(mounted,'正在确认…'),undefined);assert.equal(findButton(mounted,'确认记录'),undefined);assert.match(mounted.textContent,/已记录/);
});

test('a rendered confirmation rechecks the current busy state at click time',async t=>{
  let available=true,requests=0;const {render,chat}=install(t,async()=>{requests++;return record({revision:2,status:'saved'});},{canAct:()=>available});
  const root=render(),confirm=findButton(root,'确认记录');assert.equal(confirm.disabled,false);
  available=false;await confirm.onclick();assert.equal(requests,0);assert.equal(chat.saving.size,0);
  available=true;await confirm.onclick();assert.equal(requests,1);assert.equal(findButton(root,'确认记录'),undefined);assert.match(root.textContent,/已记录/);
});
