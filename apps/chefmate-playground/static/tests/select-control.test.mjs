import test from 'node:test';
import assert from 'node:assert/strict';
import {enhanceControl} from '../form-controls.js';

// Exercise the real combobox handlers. The native value/index contract is
// modelled here; positioning and top-layer drawing also get browser acceptance.
class View extends EventTarget {
  constructor(){super();this.innerWidth=390;this.innerHeight=844;this.listeners=new Map();}
  addEventListener(type,handler,options){super.addEventListener(type,handler,options);const entries=this.listeners.get(type)||new Set();entries.add(handler);this.listeners.set(type,entries);}
  removeEventListener(type,handler,options){super.removeEventListener(type,handler,options);this.listeners.get(type)?.delete(handler);}
}
class Element extends EventTarget {
  constructor(tag,doc){super();this.ownerDocument=doc;this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.dataset={};this.style={};this.className='';this.copy='';this._value='';this.disabled=false;this.hidden=false;this.validity={valid:true};this.rect={left:240,right:380,top:730,bottom:776,width:140};}
  get classList(){return {add:(...values)=>this.className+=` ${values.join(' ')}`};}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  removeAttribute(key){delete this.attributes[key];}
  append(...children){for(const child of children){child.remove();child.parentNode=this;this.children.push(child);}}
  replaceChildren(...children){for(const child of [...this.children])child.remove();this.copy='';this.append(...children);}
  remove(){if(this.parentNode){const siblings=this.parentNode.children;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
  get parentElement(){return this.parentNode;}
  get isConnected(){return this===this.ownerDocument.documentElement || Boolean(this.parentNode?.isConnected);}
  get textContent(){return this.copy+this.children.map(child=>child.textContent).join('');}
  set textContent(value){this.replaceChildren();this.copy=String(value);}
  get options(){return this.children.flatMap(child=>child.tagName==='OPTION'?[child]:child.options);}
  get selectedIndex(){if(this.tagName!=='SELECT')return undefined;const index=this.options.findIndex(option=>option.selected);return index<0&&this.options.length?0:index;}
  set selectedIndex(index){this.options.forEach((option,item)=>option.selected=item===index);}
  get value(){return this.tagName==='SELECT'?this.options[this.selectedIndex]?.value??'':this._value;}
  set value(value){if(this.tagName==='SELECT')this.selectedIndex=this.options.findIndex(option=>option.value===value);else this._value=String(value);}
  contains(target){return this===target || this.children.some(child=>child.contains(target));}
  focus(){this.ownerDocument.activeElement=this;this.dispatchEvent(new Event('focus'));}
  getBoundingClientRect(){return this.rect;}
  scrollIntoView(){this.scrolled=true;}
  showPopover(){this.popoverOpen=true;}
  hidePopover(){this.popoverOpen=false;}
  matches(selector){return selector===':popover-open'&&this.popoverOpen;}
}
class Document extends EventTarget {
  constructor(){super();this.defaultView=new View();this.documentElement=new Element('html',this);this.activeElement=null;}
  createElement(tag){return new Element(tag,this);}
}
const walk=root=>[root,...root.children.flatMap(walk)];
const role=(root,name)=>walk(root).filter(item=>item.getAttribute('role')===name);
const key=(target,value)=>{const event=new Event('keydown',{cancelable:true});event.key=value;target.dispatchEvent(event);return event;};
const click=target=>target.dispatchEvent(new Event('click',{cancelable:true}));
function install(t,items=[['','请选择'],['one','One'],['blocked','Unavailable',true],['two','Two'],['three','Three']]){
  const previous=globalThis.document;t.after(()=>globalThis.document=previous);const doc=new Document();globalThis.document=doc;
  const input=doc.createElement('select');input.setAttribute('aria-label','餐次');input.type='select-one';
  for(const [value,label,disabled=false] of items){const option=doc.createElement('option');option.value=value;option.textContent=label;option.disabled=disabled;input.append(option);}
  const surface=enhanceControl(input,{label:'餐次'});doc.documentElement.append(surface);
  return {doc,input,surface,trigger:role(surface,'combobox')[0],list:role(surface,'listbox')[0]};
}

test('one combobox preserves the native form field, labels and original selected value',t=>{
  const {input,surface,trigger,list}=install(t);input.value='two';input.required=true;input.setAttribute('aria-describedby','meal-help');input._syncControl();
  assert.equal(role(surface,'combobox').length,1);assert.equal(input.tabIndex,-1);assert.equal(input.getAttribute('aria-hidden'),'true');
  assert.equal(trigger.getAttribute('aria-label'),'餐次');assert.equal(trigger.getAttribute('aria-describedby'),'meal-help');assert.equal(trigger.getAttribute('aria-required'),'true');
  assert.equal(trigger.getAttribute('aria-controls'),list.id);assert.match(trigger.textContent,/Two/);
  assert.equal(role(list,'option').filter(option=>option.getAttribute('aria-selected')==='true').length,1);
});

test('arrow navigation skips disabled choices and commits exactly one native input/change pair',t=>{
  const {input,trigger,list}=install(t);input.value='one';input._syncControl();const events=[];
  for(const type of ['input','change'])input.addEventListener(type,event=>events.push([event.type,input.value,event.bubbles]));
  key(trigger,'ArrowDown');assert.equal(trigger.getAttribute('aria-expanded'),'true');assert.equal(input.value,'one');
  key(trigger,'ArrowDown');const active=trigger.getAttribute('aria-activedescendant');assert.equal(role(list,'option').find(option=>option.id===active).textContent,'Two');
  key(trigger,'Enter');assert.equal(input.value,'two');assert.deepEqual(events,[['input','two',true],['change','two',true]]);assert.equal(trigger.getAttribute('aria-expanded'),'false');
  click(trigger);key(trigger,'Enter');assert.equal(events.length,2);
});

test('Home/End, typeahead and Escape keep browsing separate from accepted data',t=>{
  const {doc,input,trigger,list}=install(t);input.value='one';input._syncControl();click(trigger);
  key(trigger,'End');assert.match(role(list,'option').find(option=>option.id===trigger.getAttribute('aria-activedescendant')).textContent,/Three/);
  key(trigger,'Home');assert.match(role(list,'option').find(option=>option.id===trigger.getAttribute('aria-activedescendant')).textContent,/请选择/);
  key(trigger,'t');assert.match(role(list,'option').find(option=>option.id===trigger.getAttribute('aria-activedescendant')).textContent,/Two/);
  key(trigger,'t');assert.match(role(list,'option').find(option=>option.id===trigger.getAttribute('aria-activedescendant')).textContent,/Three/);
  key(doc,'Escape');assert.equal(input.value,'one');assert.equal(trigger.getAttribute('aria-expanded'),'false');assert.equal(doc.activeElement,trigger);
});

test('native label activation opens the shared menu instead of a second platform picker',t=>{
  const {doc,input,trigger}=install(t);const activation=new Event('click',{cancelable:true});input.dispatchEvent(activation);
  assert.equal(activation.defaultPrevented,true);assert.equal(doc.activeElement,trigger);assert.equal(trigger.getAttribute('aria-expanded'),'true');
});

test('pointer choice commits, while outside clicks and Tab close without changing the field',t=>{
  const {doc,input,trigger,list}=install(t);click(trigger);click(role(list,'option')[3]);assert.equal(input.value,'two');
  click(trigger);doc.dispatchEvent(new Event('pointerdown'));assert.equal(input.value,'two');assert.equal(trigger.getAttribute('aria-expanded'),'false');
  click(trigger);key(trigger,'ArrowUp');key(trigger,'Tab');assert.equal(input.value,'two');assert.equal(trigger.getAttribute('aria-expanded'),'false');
  assert.equal(doc.defaultView.listeners.get('resize').size,0);assert.equal(doc.defaultView.listeners.get('scroll').size,0);
});

test('disabled changes dismiss the popup and validation redirects focus to the visible field',t=>{
  const {doc,input,trigger}=install(t);click(trigger);input.disabled=true;input._syncControl();
  assert.equal(trigger.disabled,true);assert.equal(trigger.getAttribute('aria-expanded'),'false');key(trigger,'ArrowDown');assert.equal(trigger.getAttribute('aria-expanded'),'false');
  input.disabled=false;input.validity={valid:false};input._syncControl();const invalid=new Event('invalid',{cancelable:true});input.dispatchEvent(invalid);
  assert.equal(invalid.defaultPrevented,true);assert.equal(doc.activeElement,trigger);assert.equal(trigger.getAttribute('aria-invalid'),'true');
});

test('dynamic options and duplicate values retain the actual selected option identity',t=>{
  const {doc,input,trigger,list}=install(t,[['same','First'],['same','Second']]);click(trigger);key(trigger,'End');key(trigger,'Enter');
  assert.equal(input.selectedIndex,1);assert.equal(input.value,'same');assert.match(trigger.textContent,/Second/);
  const option=doc.createElement('option');option.value='new';option.textContent='New option';input.append(option);input._syncControl();click(trigger);
  assert.equal(role(list,'option').length,3);key(trigger,'End');key(trigger,'Enter');assert.equal(input.value,'new');
});

test('a narrow viewport opens above the field and constrains popup edges',t=>{
  const {doc,trigger,list}=install(t);click(trigger);const popover=list.parentNode;
  assert.ok(Number.parseFloat(popover.style.top)<trigger.rect.top);assert.ok(Number.parseFloat(popover.style.left)>=8);
  assert.ok(Number.parseFloat(popover.style.left)+Number.parseFloat(popover.style.width)<=doc.defaultView.innerWidth-8);
  assert.ok(Number.parseFloat(popover.style.maxHeight)<=340);
});

test('a field hidden or moved outside the viewport cannot leave an open menu or global handlers',t=>{
  const {doc,trigger}=install(t);trigger.rect={left:240,right:380,top:900,bottom:946,width:140};click(trigger);
  assert.equal(trigger.getAttribute('aria-expanded'),'false');assert.equal(doc.defaultView.listeners.get('scroll')?.size||0,0);
  trigger.rect={left:0,right:0,top:0,bottom:0,width:0};click(trigger);
  assert.equal(trigger.getAttribute('aria-expanded'),'false');assert.equal(doc.defaultView.listeners.get('resize')?.size||0,0);
});

test('removing an open field disconnects observers and page listeners',t=>{
  const previous=globalThis.MutationObserver,observers=[];t.after(()=>globalThis.MutationObserver=previous);
  globalThis.MutationObserver=class {constructor(callback){this.callback=callback;this.active=true;observers.push(this);}observe(target){this.target=target;}disconnect(){this.active=false;}};
  const {doc,surface,trigger}=install(t);click(trigger);const observer=observers.find(item=>item.target===doc.documentElement);assert.ok(observer.active);
  surface.remove();observer.callback();assert.equal(observer.active,false);assert.equal(trigger.getAttribute('aria-expanded'),'false');
  assert.equal(doc.defaultView.listeners.get('resize').size,0);assert.equal(doc.defaultView.listeners.get('scroll').size,0);
});
