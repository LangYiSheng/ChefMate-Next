import test from 'node:test';
import assert from 'node:assert/strict';
import {renderView} from '../ui-components.js';

// The component uses ordinary DOM controls. Keep this harness small and test
// the actual event handlers, including controls moved by the fullscreen pager.
class Element {
  constructor(tag) {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.listeners={};this.className='';this.disabled=false;this.value='';this.validations=0;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name];}
  get classList(){return {contains:name=>this.className.split(' ').includes(name),add:name=>{this.className+=` ${name}`;}};}
  append(...items){for(const item of items){if(item.parentNode)item.parentNode.children.splice(item.parentNode.children.indexOf(item),1);item.parentNode=this;this.children.push(item);}}
  prepend(...items){this.append(...items);this.children.unshift(...this.children.splice(-items.length));}
  set textContent(value){this.copy=String(value);this.children=[];}
  get textContent(){return (this.copy || '')+this.children.map(child=>child.textContent).join('');}
  get form(){let current=this.parentNode;while(current){if(current.tagName==='FORM')return current;current=current.parentNode;}return null;}
  addEventListener(name,callback){(this.listeners[name] ||= []).push(callback);}
  reportValidity(){this.validations++;return true;}
  async emit(name){for(const listener of this.listeners[name] || [])await listener({preventDefault(){}});}
}
const descendants=root=>[root,...root.children.flatMap(descendants)];
const byClass=(root,name)=>descendants(root).find(element=>element.classList.contains(name));
const input=(root,key)=>descendants(root).find(element=>element.dataset.controlKey===key);
function context(t,extra={}){
  const previous=globalThis.document;
  globalThis.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),
    createTextNode:text=>{const element=new Element('#text');element.textContent=text;return element;}};
  t.after(()=>{globalThis.document=previous;});
  return {historical:false,busy:false,stage:'cooking',viewState:{},action:async()=>{},...extra};
}
const timerView=()=>({id:'timer-view',kind:'timers',revision:1,editable:true,data:{timers:[]}});

for(const detached of [false,true])test(`manual timer submits exact seconds with its button ${detached?'moved outside':'inside'} the form`,async t=>{
  const starts=[],drafts=[];
  const ctx=context(t,{onStartTimer:async(view,payload)=>starts.push({id:view.id,...payload}),onTimer:(...args)=>drafts.push(args)});
  const root=renderView(timerView(),ctx),form=byClass(root,'timer-create-form'),start=byClass(root,'timer-submit');
  const minutes=input(root,'timer-minutes'),seconds=input(root,'timer-seconds'),label=input(root,'timer-label');
  minutes.value='1';seconds.value='5';label.value='焖煮';await seconds.emit('input');
  if(detached){new Element('div').append(start);await start.emit('click');}else await form.emit('submit');
  assert.deepEqual(starts,[{id:'timer-view',seconds:65,label:'焖煮'}]);
  assert.equal(drafts.at(-1)[2],65/60);
  assert.deepEqual([minutes,seconds,label].map(control=>control.validations),[1,1,1]);
});

test('manual timer does not submit zero duration or start outside cooking',async t=>{
  const starts=[],ctx=context(t,{onStartTimer:async(_,payload)=>starts.push(payload)});
  const root=renderView(timerView(),ctx),form=byClass(root,'timer-create-form');
  input(root,'timer-minutes').value='0';input(root,'timer-seconds').value='0';
  await input(root,'timer-seconds').emit('input');await form.emit('submit');
  assert.equal(byClass(root,'timer-submit').disabled,true);assert.deepEqual(starts,[]);
  const prep=renderView(timerView(),{...ctx,stage:'prep'});
  assert.equal(byClass(prep,'timer-submit').disabled,true);
  await byClass(prep,'timer-create-form').emit('submit');assert.deepEqual(starts,[]);
});

test('cooking steps retain the whole instruction and open their timer without a large nested panel',async t=>{
  const opened=[],ctx=context(t,{openTimers:id=>opened.push(id)});
  const instruction='轻轻翻动锅里的食材，保持小火。'.repeat(30);
  const view={id:'cooking',kind:'cooking_guide',editable:true,revision:2,data:{recipe_id:'r1',recipe_name:'测试菜',
    active_step_id:'s1',steps:[{id:'s1',ordinal:0,status:'pending',instruction,timer_seconds:30}],
    timers:[{id:'timer-one',recipe_id:'r1',step_id:'s1',label:'焖煮',seconds:30,status:'paused',remaining_seconds:17}]}};
  const root=renderView(view,ctx);
  assert.equal(byClass(root,'ui-step-instruction').textContent,instruction);
  assert.equal(byClass(root,'step-timer-time').textContent,'0:17');
  assert.equal(byClass(root,'ui-timer'),undefined);
  await byClass(root,'step-timer-shortcut').emit('click');assert.deepEqual(opened,['timer-one']);
  assert.equal(byClass(root,'timer-start-button'),undefined);
  const prep=renderView({...view,data:{...view.data,timers:[]}}, {...ctx,stage:'prep'});
  assert.equal(byClass(prep,'timer-start-button').disabled,true);
});

test('finishing one dish does not offer whole-meal completion until the trusted snapshot allows it',async t=>{
  const ctx=context(t,{sessionId:'meal-session'});
  const view={id:'cooking',kind:'cooking_guide',editable:true,revision:3,data:{recipe_id:'r1',recipe_name:'滑蛋',
    active_step_id:'s1',steps:[{id:'s1',ordinal:0,status:'done',instruction:'出锅'}],timers:[]}};
  assert.equal(byClass(renderView(view,ctx),'complete-meal-button'),undefined);
  assert.ok(byClass(renderView(view,{...ctx,canCompleteMeal:true}),'complete-meal-button'));
  assert.equal(byClass(renderView(view,{...ctx,canCompleteMeal:true,focused:true}),'complete-meal-button'),undefined);
  assert.equal(byClass(renderView(view,{...ctx,canCompleteMeal:true,historical:true}),'complete-meal-button'),undefined);
});

test('meal plan distinguishes shared diners from each plate quantity, including fractional allocations',t=>{
  const ctx=context(t,{stage:'planning'});
  const view={id:'plan',kind:'meal_plan',editable:true,revision:3,data:{meal_diners:5,
    portion_note:'5人共享4道菜，分别调整每盘用量。',recipes:[{recipe_id:'r1',name:'滑蛋',original_servings:2,
      target_servings:2.25,total_time_min:10,difficulty:'normal'}]}};
  const root=renderView(view,ctx);
  assert.match(byClass(root,'meal-summary').textContent,/5 人共享/);
  assert.match(root.textContent,/配方等效份/);
  assert.doesNotMatch(root.textContent,/2.25 人份/);
  const quantity=input(root,'servings:r1');
  assert.equal(quantity.value,2.25);assert.equal(quantity.step,'any');
  assert.equal(quantity.getAttribute('aria-label'),'滑蛋的配方等效份量');
});

test('a planning checklist accepts explicit user confirmations without requiring an extra model turn',async t=>{
  const actions=[],ctx=context(t,{stage:'planning',action:async(type,payload)=>actions.push({type,payload})});
  const view={id:'prep',kind:'prep_checklist',editable:true,revision:1,data:{items:[{
    id:'tofu',recipe_id:'r1',ingredient_id:'tofu',canonical_name:'北豆腐',amount_text:'300 克',status:'pending'}]}};
  const root=renderView(view,ctx),checkbox=descendants(root).find(element=>element.type==='checkbox');
  assert.equal(checkbox.disabled,false);
  checkbox.checked=true;await checkbox.emit('change');
  assert.deepEqual(actions,[{type:'check_prep_item',payload:{item_id:'tofu',status:'ready'}}]);
  assert.doesNotMatch(root.textContent,/进入备菜阶段后/);
  const cooked=renderView(view,{...ctx,stage:'cooking'});
  assert.equal(descendants(cooked).find(element=>element.type==='checkbox').disabled,true);
});
