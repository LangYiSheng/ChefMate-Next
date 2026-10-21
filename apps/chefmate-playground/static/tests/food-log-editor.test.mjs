import test from 'node:test';
import assert from 'node:assert/strict';
import {renderFoodLogEditor} from '../food-log-editor.js';
import {CenterEditorState} from '../center-state.js';

// A small DOM surface exercises the generated editor's real input/save handlers.
// Networking remains deferred where request ownership and locking matter.
class Element extends EventTarget {
  constructor(tag){super();this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.value='';this.disabled=false;this.hidden=false;this.className='';this.copy='';this.type=tag==='textarea'?'textarea':'text';this.validity={valid:true};this.min='';this.max='';}
  get classList(){return {add:(...names)=>this.className+=` ${names.join(' ')}`};}
  setAttribute(key,value){this.attributes[key]=String(value);}
  removeAttribute(key){delete this.attributes[key];}
  getAttribute(key){return this.attributes[key];}
  append(...items){for(const item of items){item.remove();item.parentNode=this;this.children.push(item);}}
  replaceChildren(...items){for(const child of [...this.children])child.remove();this.copy='';this.append(...items);}
  get childNodes(){return this.children;}
  remove(){if(this.parentNode){const siblings=this.parentNode.children;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
  set textContent(value){this.replaceChildren();this.copy=String(value);}
  get textContent(){return this.copy+this.children.map(child=>child.textContent).join('');}
  querySelectorAll(selector){return descendants(this).slice(1).filter(item=>selector.split(',').some(tag=>item.tagName===tag.trim().toUpperCase()));}
  focus(){this.focused=true;}
  checkValidity(){return this.validity.valid&&(!this.required||this.value!=='');}
  reportValidity(){this.focus();return this.checkValidity();}
  dispatchEvent(event){const result=super.dispatchEvent(event);if(event.bubbles&&!event.cancelBubble)this.parentNode?.dispatchEvent(event);return result;}
}
const descendants=root=>[root,...root.children.flatMap(descendants)];
const field=(editor,key,index=0)=>descendants(editor.body).filter(item=>item.dataset.foodField===key)[index];
const button=(editor,label)=>descendants(editor.footer).find(item=>item.tagName==='BUTTON'&&item.textContent===label);
const change=(element,value)=>{element.value=value;element.dispatchEvent(new Event('input',{bubbles:true}));};
const blankNutrition=()=>({calories_kcal:null,protein_g:null,fat_g:null,carbohydrate_g:null,sugar_g:null,sodium_mg:null});
function record(item={}){return {id:'food-test',revision:1,status:'draft',date:'2026-10-21',source_session_id:'chat-one',source_image_ids:[],document:{title:'今天的午饭',date:'2026-10-21',date_evidence:'今天',meal_type:'lunch',source_kind:'text',source_text:'今天吃了一碗米饭。',notes:'',items:[{name:'米饭',portion:'一碗',amount_g:200,portion_basis:'estimated',nutrition:{calories_kcal:230,protein_g:5,fat_g:1,carbohydrate_g:50,sugar_g:2,sodium_mg:4},nutrition_basis:'estimated',evidence:'根据照片估计',assumptions:['假定这碗米饭约200克。'],...item}]}};}
function install(t,initial=record(),api=async()=>{throw new Error('unexpected request');}){
  const previous=globalThis.document;t.after(()=>globalThis.document=previous);globalThis.document={createElement:tag=>new Element(tag)};
  const editor={id:initial.id,state:new CenterEditorState(initial.id),panel:new Element('aside'),body:new Element('div'),footer:new Element('footer'),status:new Element('p'),title:new Element('h2'),eyebrow:new Element('span')};editor.panel.append(editor.body,editor.status,editor.footer);
  const centers={editor,api,refreshes:0,status(_editor,text){editor.status.textContent=text;},message(text){this.notice=text;},refresh(){this.refreshes++;return Promise.resolve();},setJournalTab(tab){this.tab=tab;},adaptInspector(){},deleteFoodLog(record){this.deleted=record;},disposeInspector(){editor.state.dispose();this.editor=null;},inspectorTabs(editor,items,panels){editor.tabs={select:key=>{this.selected=key;for(const [name,panel] of Object.entries(panels))panel.hidden=name!==key;}};editor.body.replaceChildren(...Object.values(panels));editor.tabs.select(items[0][0]);}};
  renderFoodLogEditor(centers,editor,initial);return {editor,centers};
}
const accepted=(initial,body)=>({...structuredClone(initial),revision:initial.revision+1,document:body.record});
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}

test('unknown food numbers stay null and decimal fields do not render unusable step buttons',async t=>{
  const initial=record({amount_g:null,portion:'半碗',portion_basis:'user_provided',nutrition:blankNutrition(),nutrition_basis:'unknown',evidence:'',assumptions:[]});let sent;
  const {editor}=install(t,initial,async(_,options)=>{sent=JSON.parse(options.body);return accepted(initial,sent);});
  assert.equal(field(editor,'calories_kcal').parentNode.querySelectorAll('button').length,0);
  change(field(editor,'title'),'核对后的午饭');await button(editor,'保存草稿').onclick();
  assert.deepEqual(sent.record.items[0].nutrition,blankNutrition());assert.equal(sent.record.items[0].amount_g,null);
  assert.equal(editor.state.dirty,false);assert.equal(editor.record.revision,2);
});

for(const changedField of ['name','portion'])test(`changing ${changedField} invalidates every old nutrient and gram estimate`,async t=>{
  const initial=record();let sent;
  const {editor}=install(t,initial,async(_,options)=>{sent=JSON.parse(options.body);return accepted(initial,sent);});
  change(field(editor,changedField),changedField==='name'?'豆腐':'半碗');await button(editor,'保存草稿').onclick();
  assert.equal(sent.record.items[0].amount_g,null);assert.deepEqual(sent.record.items[0].nutrition,blankNutrition());
  assert.equal(sent.record.items[0].nutrition_basis,'unknown');assert.equal(field(editor,'calories_kcal').value,'');
});

test('gram changes scale all six nutrient fields and retain explicit estimate provenance',async t=>{
  const initial=record();let sent;const {editor}=install(t,initial,async(_,options)=>{sent=JSON.parse(options.body);return accepted(initial,sent);});
  change(field(editor,'amount_g'),'300');await button(editor,'保存草稿').onclick();const item=sent.record.items[0];
  assert.equal(item.portion,'300 克');assert.equal(item.portion_basis,'user_provided');assert.equal(item.nutrition_basis,'estimated');
  assert.deepEqual(item.nutrition,{calories_kcal:345,protein_g:7.5,fat_g:1.5,carbohydrate_g:75,sugar_g:3,sodium_mg:6});
  assert.match(item.evidence,/同比例换算/);assert.match(item.assumptions.join('\n'),/200 克换算至 300 克/);
});

test('manually editing only one macro does not relabel untouched model estimates as user supplied',async t=>{
  const initial=record();let sent;const {editor}=install(t,initial,async(_,options)=>{sent=JSON.parse(options.body);return accepted(initial,sent);});
  change(field(editor,'calories_kcal'),'250');await button(editor,'保存草稿').onclick();
  assert.equal(sent.record.items[0].nutrition_basis,'estimated');assert.equal(sent.record.items[0].nutrition.protein_g,5);
  assert.match(sent.record.items[0].evidence,/其余数值沿用原估算/);
});

test('manual numbers can be user supplied without inventing zeroes for other macros',async t=>{
  const initial=record({amount_g:null,portion_basis:'user_provided',nutrition:blankNutrition(),nutrition_basis:'unknown',evidence:'',assumptions:[]});let sent;
  const {editor}=install(t,initial,async(_,options)=>{sent=JSON.parse(options.body);return accepted(initial,sent);});
  change(field(editor,'calories_kcal'),'0');await button(editor,'保存草稿').onclick();
  assert.equal(sent.record.items[0].nutrition.calories_kcal,0);assert.equal(sent.record.items[0].nutrition.protein_g,null);
  assert.equal(sent.record.items[0].nutrition_basis,'user_provided');assert.equal(sent.record.items[0].evidence,'用户在核对页手动填写');
});

test('pending save locks all controls, rejects duplicate submissions and preserves failed input nodes',async t=>{
  const response=deferred();let calls=0;const {editor}=install(t,record(),async()=>{calls++;return response.promise;});
  const title=field(editor,'title');change(title,'还没保存的午饭');const save=button(editor,'保存草稿'),operation=save.onclick();
  assert.ok(editor.panel.querySelectorAll('input,textarea,select,button').every(item=>item.disabled));await save.onclick();assert.equal(calls,1);
  response.reject(new Error('保存暂时失败'));await operation;
  assert.equal(field(editor,'title'),title);assert.equal(title.value,'还没保存的午饭');assert.equal(editor.state.dirty,true);assert.equal(editor.state.busy,false);
  assert.equal(title.disabled,false);assert.match(editor.status.textContent,/保存暂时失败/);
});

test('publish retries use the committed draft revision without repeating PUT',async t=>{
  const initial=record(),requests=[];let updated,posts=0;
  const {editor,centers}=install(t,initial,async(path,options)=>{const body=JSON.parse(options.body);requests.push({path,method:options.method,body});if(options.method==='PUT')return updated=accepted(initial,body);if(++posts===1)throw new Error('确认响应中断');return {...updated,status:'saved',revision:3};});
  change(field(editor,'title'),'核对完成的午饭');const publish=button(editor,'确认记录');await publish.onclick();
  assert.equal(editor.record.revision,2);assert.equal(editor.state.dirty,false);assert.equal(centers.editor,editor);
  await publish.onclick();assert.deepEqual(requests.map(item=>item.method),['PUT','POST','POST']);assert.deepEqual(requests.map(item=>item.body.revision),[1,2,2]);
  assert.equal(centers.editor,null);assert.equal(centers.tab,'overview');assert.equal(centers.refreshes,1);
});

test('saved logs are read only and image cleanup does not remove their content',t=>{
  const initial=record();initial.status='saved';initial.document.source_kind='image';initial.source_image_ids=['photo-one'];const {editor,centers}=install(t,initial);
  assert.equal(editor.body.querySelectorAll('input,textarea,select').length,0);
  const image=editor.body.querySelectorAll('img')[0];assert.equal(image.src,'/api/sessions/chat-one/images/photo-one');image.dispatchEvent(new Event('error'));
  assert.match(editor.body.textContent,/原图已清理/);assert.match(editor.body.textContent,/米饭/);button(editor,'移到回收站').onclick();assert.equal(centers.deleted,initial);
});
