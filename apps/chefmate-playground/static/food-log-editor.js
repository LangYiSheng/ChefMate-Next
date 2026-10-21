import {node,estimateMarker} from './ui-components.js';
import {enhanceControl} from './form-controls.js';

const macros=[['calories_kcal','热量','kcal',20000],['protein_g','蛋白质','g',3000],['fat_g','脂肪','g',3000],['carbohydrate_g','碳水','g',3000]];
const nutritionKeys=[...macros.map(([key])=>key),'sugar_g','sodium_mg'];
const mealNames={breakfast:'早餐',lunch:'午餐',dinner:'晚餐',snack:'加餐',unspecified:'未分类'};
const numeric=input=>input.value===''?null:Number(input.value);
const rounded=value=>Math.round(value*1000)/1000;
const lines=input=>input.value.split('\n').map(value=>value.trim()).filter(Boolean);
const clone=value=>structuredClone(value);
let sequence=0;

function button(text,action,style='quiet') {
  const item=node('button',`center-button ${style}`,text);item.type='button';item.onclick=action;return item;
}
function field(label,input,{hint='',stepper=true}={}) {
  const root=node('div','center-field'),caption=node('label','field-label',label);
  input.id||=`food-log-field-${++sequence}`;caption.htmlFor=input.id;root.append(caption,enhanceControl(input,{label,stepper}));
  if(hint){const help=node('span','center-field-hint',hint);help.id=`${input.id}-help`;input.setAttribute('aria-describedby',help.id);root.append(help);}return root;
}
function input(value,type='text',key='') {
  const item=node(type==='textarea'?'textarea':'input','center-input');if(type!=='textarea')item.type=type;
  item.value=value??'';if(key)item.dataset.foodField=key;return item;
}
function macroValues(values) {
  const grid=node('dl','center-nutrition');
  for(const [key,label,unit] of macros){const cell=node('div');cell.append(node('dt','',label),node('dd','',Number.isFinite(values?.[key])?`${rounded(values[key])} ${unit}`:'未知'));grid.append(cell);}return grid;
}
function setBasis(note,item) {
  note.replaceChildren();
  note.append(item.nutrition_basis==='estimated'?estimateMarker('营养含估算'):node('span','',item.nutrition_basis==='user_provided'?'营养由用户提供':'营养待补充'));
  if(item.portion_basis==='estimated')note.append(estimateMarker(' · 份量为估算'));
  return note;
}
function basisNote(item,className='center-muted') {return setBasis(node('p',className),item);}
function sourceImages(record,source) {
  if(record.document.source_kind!=='image')return;
  source.append(node('h3','','来源照片'));const images=node('div','food-log-source-images');
  for(const imageId of record.source_image_ids || []){
    const figure=node('figure','food-log-source-image'),image=node('img');image.alt='本次饮食记录的来源照片';image.loading='lazy';
    image.src=`/api/sessions/${encodeURIComponent(record.source_session_id)}/images/${encodeURIComponent(imageId)}`;
    image.addEventListener('error',()=>figure.replaceChildren(node('p','center-muted','原图已清理，已确认的饮食记录仍保留。')));
    figure.append(image);images.append(figure);
  }
  if(!images.children.length)images.append(node('p','center-muted','原图已清理，饮食记录仍保留。'));source.append(images);
}

// The same mounted form survives tab switches and failed requests. Only its
// accepted server revision becomes a new baseline; publication may then retry.
export function renderFoodLogEditor(centers,editor,initial) {
  let record=initial;const saved=record.status==='saved',doc=record.document;
  editor.record=record;editor.title.textContent=doc.title;
  if(editor.eyebrow)editor.eyebrow.textContent=saved?'个人饮食记录':'待核对饮食';centers.adaptInspector?.();
  const basic=node('section','center-tab-panel'),foods=node('section','center-tab-panel'),source=node('section','center-tab-panel');
  const panels={basic,foods,source};
  sourceImages(record,source);
  if(saved){
    basic.append(node('p','center-muted',`${record.date} · ${mealNames[doc.meal_type] || '未分类'} · 已确认的个人摄入`),node('h3','',doc.title));
    if(doc.notes)basic.append(node('p','center-source-text',doc.notes));
    foods.append(node('p','center-muted','以下营养是你记录的这一份总量；估算与未知项分别保留。'));
    for(const item of doc.items){const section=node('section','food-log-item food-log-readonly');section.append(node('h3','',item.name),node('p','center-muted',`${item.portion || '份量未知'}${Number.isFinite(item.amount_g)?` · ${item.amount_g} 克`:''}`),basisNote(item,'food-log-item-note'),macroValues(item.nutrition));foods.append(section);}
    if(doc.source_text)source.append(node('h3','','原始描述'),node('p','center-source-text',doc.source_text));
    for(const item of doc.items){const section=node('section','food-log-item');section.append(node('h3','',item.name),basisNote(item));if(item.evidence)section.append(node('p','center-source-text',item.evidence));for(const assumption of item.assumptions || [])section.append(node('p','center-warning',assumption));source.append(section);}
    centers.inspectorTabs(editor,[['basic','这次饮食'],['foods','食物与营养'],['source','来源与依据']],panels);
    const actions=node('div','center-footer-actions');actions.append(button('移到回收站',()=>centers.deleteFoodLog(record),'danger quiet'));editor.footer.replaceChildren(actions);return;
  }

  basic.append(node('p','center-muted','只记录你实际吃的这一份。核对后确认，才会计入个人营养统计。'));
  const controls=[],items=[],title=input(doc.title,'text','title'),date=input(record.date || doc.date,'date','date'),meal=node('select','center-input'),notes=input(doc.notes,'textarea','notes'),sourceText=input(doc.source_text,'textarea','source_text');
  title.required=true;title.maxLength=120;date.required=true;meal.dataset.foodField='meal_type';notes.maxLength=2000;notes.rows=4;sourceText.maxLength=8000;sourceText.rows=6;
  for(const [key,label] of Object.entries(mealNames)){const option=node('option','',label);option.value=key;option.selected=doc.meal_type===key;meal.append(option);}meal.value=doc.meal_type;
  let dateEvidence=doc.date_evidence || '';date.addEventListener('input',()=>{dateEvidence='用户在核对页选择日期';});
  basic.append(field('记录名称',title),field('日期',date),field('餐次',meal),field('备注',notes));
  controls.push([title,'basic'],[date,'basic'],[meal,'basic'],[notes,'basic'],[sourceText,'source']);
  foods.append(node('p','center-muted','每项填写你吃下的这一份总量。未知可以留空；修改食物或文字份量后，会清空原有营养。'));
  const itemList=node('div','food-log-items'),itemSources=node('div','food-log-item-sources');foods.append(itemList);
  source.append(field('原始描述',sourceText),itemSources);
  const dirty=()=>{editor.state.change();centers.status(editor,'有未保存的修改');};
  const syncActions=()=>{for(const item of items)item.remove.disabled=items.length<=1;add.disabled=items.length>=30;};
  const addItem=initialItem=>{
    const state={item:clone(initialItem),manual:new Set(),allProvided:initialItem.nutrition_basis==='user_provided'};
    const section=node('section','food-log-item'),header=node('div','food-log-item-header'),heading=node('h3','',initialItem.name || `食物 ${items.length+1}`);heading.id=`food-log-item-${++sequence}`;section.setAttribute('aria-labelledby',heading.id);
    const remove=button('移除食物',()=>{if(editor.state.busy || items.length<=1)return;section.remove();sourceSection.remove();for(const pair of controlPairs)controls.splice(controls.indexOf(pair),1);items.splice(items.indexOf(state),1);syncActions();dirty();});
    header.append(heading,remove);section.append(header);state.remove=remove;
    const name=input(initialItem.name,'text','name'),portion=input(initialItem.portion,'text','portion'),grams=input(initialItem.amount_g,'number','amount_g');name.required=true;name.maxLength=120;portion.maxLength=160;grams.min='0';grams.max='10000';grams.step='any';
    const grid=node('div','food-log-item-grid');grid.append(field('食物名称',name),field('我吃的份量',portion,{hint:'例如半碗、一小盘，或实际称量的克重。'}),field('克重（可留空）',grams,{stepper:false}));section.append(grid);
    const nutrition=node('div','food-log-macros'),macroInputs={};
    for(const [key,label,unit,max] of macros){const control=input(initialItem.nutrition[key],'number',key);control.min='0';control.max=String(max);control.step='any';macroInputs[key]=control;nutrition.append(field(`${label}（${unit}）`,control,{stepper:false}));}
    const itemNote=basisNote(initialItem,'food-log-item-note');itemNote.setAttribute('role','status');section.append(nutrition,itemNote);itemList.append(section);
    const sourceSection=node('section','food-log-item'),sourceHeading=node('h3','',initialItem.name || `食物 ${items.length+1}`),evidence=input(initialItem.evidence,'textarea','evidence'),assumptions=input((initialItem.assumptions || []).join('\n'),'textarea','assumptions');evidence.maxLength=1000;evidence.rows=3;assumptions.rows=4;assumptions.maxLength=4812;
    sourceSection.append(sourceHeading,field('营养依据',evidence),field('估算假设（每行一条）',assumptions,{hint:'保留碗盘大小、食材组成、用油等估算依据；最多12条。'}));itemSources.append(sourceSection);
    const controlPairs=[[name,'foods'],[portion,'foods'],[grams,'foods'],...Object.values(macroInputs).map(control=>[control,'foods']),[evidence,'source'],[assumptions,'source']];controls.push(...controlPairs);
    state.payload=()=>({...state.item,name:name.value.trim(),portion:portion.value.trim(),amount_g:numeric(grams),evidence:evidence.value.trim(),assumptions:lines(assumptions)});
    state.adopt=accepted=>{state.item=clone(accepted);state.allProvided=accepted.nutrition_basis==='user_provided';state.manual.clear();reference=Number.isFinite(accepted.amount_g)&&accepted.amount_g>0?{grams:accepted.amount_g,nutrition:clone(accepted.nutrition)}:null;};
    state.name=name;state.portion=portion;state.evidence=evidence;state.assumptions=assumptions;
    let reference=Number.isFinite(initialItem.amount_g)&&initialItem.amount_g>0?{grams:initialItem.amount_g,nutrition:clone(initialItem.nutrition)}:null;
    const updateMacros=()=>{for(const [key,control] of Object.entries(macroInputs))control.value=state.item.nutrition[key]??'';};
    const invalidate=kind=>{
      for(const key of nutritionKeys)state.item.nutrition[key]=null;updateMacros();state.item.nutrition_basis='unknown';state.item.evidence='';evidence.value='';state.manual.clear();state.allProvided=false;reference=null;
      if(kind==='name'){grams.value='';state.item.amount_g=null;if(state.item.portion_basis==='estimated')assumptions.value='份量描述沿用原估计；食物名称已修改，仍需重新核对。';else assumptions.value='';}
      if(kind==='portion'){grams.value='';state.item.amount_g=null;state.item.portion_basis=portion.value.trim()?'user_provided':'unknown';assumptions.value='';}
      itemNote.textContent='食物或份量已修改，原营养已清空。';
    };
    name.addEventListener('input',()=>{if(editor.state.busy)return;if(name.value.trim()!==state.item.name)invalidate('name');state.item.name=name.value.trim();heading.textContent=sourceHeading.textContent=state.item.name || '待填写食物';});
    portion.addEventListener('input',()=>{if(editor.state.busy)return;if(portion.value.trim()!==state.item.portion)invalidate('portion');state.item.portion=portion.value.trim();});
    grams.addEventListener('input',()=>{
      if(editor.state.busy)return;const amount=numeric(grams);
      if(Number.isFinite(amount)&&amount>0&&reference){
        for(const key of nutritionKeys)state.item.nutrition[key]=Number.isFinite(reference.nutrition[key])?rounded(reference.nutrition[key]*amount/reference.grams):null;
        const hasValues=nutritionKeys.some(key=>state.item.nutrition[key]!==null);state.item.nutrition_basis=hasValues?'estimated':'unknown';state.allProvided=false;state.manual.clear();updateMacros();
        if(hasValues){evidence.value='按原克重与营养同比例换算，未重新测量食材或烹饪差异。';const assumption=`假定食材组成和烹饪用油比例不变，按 ${reference.grams} 克换算至 ${amount} 克。`;assumptions.value=[...new Set([...lines(assumptions).filter(line=>!line.startsWith('假定食材组成和烹饪用油比例不变，按 ')),assumption])].join('\n');}
      }else{for(const key of nutritionKeys)state.item.nutrition[key]=null;updateMacros();state.item.nutrition_basis='unknown';evidence.value='';state.manual.clear();state.allProvided=false;reference=null;}
      state.item.amount_g=amount;if(Number.isFinite(amount)&&amount>0){portion.value=`${amount} 克`;state.item.portion=portion.value;state.item.portion_basis='user_provided';}
      itemNote.textContent=state.item.nutrition_basis==='estimated'?'营养已按克重同比例估算，请核对。':'克重已修改，营养保持未知。';
    });
    for(const [key,control] of Object.entries(macroInputs))control.addEventListener('input',()=>{
      if(editor.state.busy)return;state.item.nutrition[key]=numeric(control);state.manual.add(key);const known=nutritionKeys.filter(key=>state.item.nutrition[key]!==null);
      state.item.nutrition_basis=known.length?(state.allProvided || known.every(key=>state.manual.has(key))?'user_provided':'estimated'):'unknown';
      if(state.item.nutrition_basis==='user_provided')evidence.value='用户在核对页手动填写';
      else if(state.item.nutrition_basis==='estimated'){evidence.value='部分营养由用户在核对页修正，其余数值沿用原估算。';if(!lines(assumptions).length)assumptions.value='未手动修改的营养仍按原食物组成和份量估算。';}
      else evidence.value='';reference=numeric(grams)>0?{grams:numeric(grams),nutrition:clone(state.item.nutrition)}:null;setBasis(itemNote,state.item);
    });
    items.push(state);return state;
  };
  const add=button('添加食物',()=>{if(editor.state.busy || items.length>=30)return;const item=addItem({name:'',portion:'',amount_g:null,portion_basis:'unknown',nutrition:Object.fromEntries(nutritionKeys.map(key=>[key,null])),nutrition_basis:'unknown',evidence:'',assumptions:[]});syncActions();dirty();item.name.focus();});foods.append(add);
  for(const item of doc.items)addItem(item);syncActions();
  centers.inspectorTabs(editor,[['basic','基本信息'],['foods','食物与营养'],['source','来源与依据']],panels);
  const form=node('form','center-editor-form');form.noValidate=true;form.append(...editor.body.childNodes);editor.body.append(form);
  form.addEventListener('input',dirty);form.addEventListener('change',dirty);
  const payload=()=>({title:title.value.trim(),date:date.value || null,date_evidence:dateEvidence,meal_type:meal.value,source_kind:record.document.source_kind,source_text:sourceText.value,items:items.map(item=>item.payload()),notes:notes.value});
  const validate=()=>{
    const invalid=controls.find(([control])=>!control.checkValidity());
    if(invalid){editor.tabs.select(invalid[1]);invalid[0].reportValidity();return false;}
    for(let index=0;index<items.length;index++){
      const state=items[index],item=state.payload(),hasNutrition=nutritionKeys.some(key=>item.nutrition[key]!==null);
      let error='',tab='foods',target=state.portion;
      if(!item.name)error='请填写食物名称。';
      else if((item.amount_g!==null || hasNutrition)&&(!item.portion || item.portion_basis==='unknown'))error='请先写明你吃的份量，或将未知的克重、营养留空。';
      else if(item.amount_g!==null&&(!Number.isFinite(item.amount_g)||item.amount_g<=0))error='克重需要大于0，未知可以留空。';
      else if(hasNutrition&&!item.evidence){error='请补充这项营养的依据。';tab='source';target=state.evidence;}
      else if((item.portion_basis==='estimated' || hasNutrition&&item.nutrition_basis==='estimated')&&!item.assumptions.length){error='请说明估算份量或营养时采用的假设。';tab='source';target=state.assumptions;}
      else if(item.assumptions.length>12 || item.assumptions.some(line=>line.length>400)){error='估算假设最多12条，每条不超过400字。';tab='source';target=state.assumptions;}
      if(error){editor.tabs.select(tab);target.focus();centers.status(editor,`第 ${index+1} 项：${error}`,true);return false;}
    }
    return true;
  };
  const persist=async publish=>{
    if(editor.state.busy || !validate())return;const token=editor.state.begin();if(token===null)return;
    const interactive=[...editor.panel.querySelectorAll('input,textarea,select,button')];for(const control of interactive)control.disabled=true;
    centers.status(editor,publish?'正在确认饮食记录…':'正在保存饮食草稿…');
    try{
      if(editor.state.dirty){record=await centers.api(`/api/food-logs/${encodeURIComponent(record.id)}`,{method:'PUT',body:JSON.stringify({revision:record.revision,record:payload()})});if(centers.editor!==editor || !editor.state.alive)return;editor.record=record;editor.state.dirty=false;items.forEach((item,index)=>item.adopt(record.document.items[index]));}
      if(publish)record=await centers.api(`/api/food-logs/${encodeURIComponent(record.id)}/publish`,{method:'POST',body:JSON.stringify({revision:record.revision})});
      if(!editor.state.finish(token,{saved:true}) || centers.editor!==editor)return;editor.record=record;editor.title.textContent=record.document.title;
      if(publish){centers.disposeInspector();centers.setJournalTab('overview');centers.message('饮食已记录，个人营养统计已更新。');await centers.refresh();}
      else{centers.status(editor,'饮食草稿已保存，确认后才会计入统计。');centers.refresh();}
    }catch(error){if(editor.state.finish(token))centers.status(editor,error.message,true);}
    finally{if(centers.editor===editor){for(const control of interactive)control.disabled=false;syncActions();}}
  };
  const actions=node('div','center-footer-actions');actions.append(button('移到回收站',()=>centers.deleteFoodLog(record),'danger quiet'),button('保存草稿',()=>persist(false)),button('确认记录',()=>persist(true),'primary'));editor.footer.replaceChildren(actions);
  form.onsubmit=event=>{event.preventDefault();persist(false);};
}
