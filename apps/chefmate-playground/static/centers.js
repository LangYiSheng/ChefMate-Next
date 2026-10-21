import {node} from './ui-components.js';
import {icon} from './icons.js';

const nutrients=[['calories_kcal','热量','kcal'],['protein_g','蛋白质','g'],['fat_g','脂肪','g'],['carbohydrate_g','碳水','g']];
const value=(number,unit='')=>Number.isFinite(number)?`${Math.round(number*10)/10}${unit ? ` ${unit}`:''}`:'暂无数据';
function button(text,action,primary=false){const el=node('button',`center-button${primary?' primary':''}`,text);el.type='button';el.onclick=action;return el;}
function heading(title,description){const el=node('div');el.append(node('h1','',title),node('p','',description));return el;}
function field(label,control){const el=node('label','center-field');el.append(node('span','',label),control);return el;}

export class KitchenCenters {
  constructor({api,onChat,onReturn}) {
    this.api=api;this.onChat=onChat;this.onReturn=onReturn;this.page='chat';this.source='all';this.query='';this.offset=0;this.days=7;this.generation=0;
    this.host=node('section','center-host');this.host.id='center-host';this.host.hidden=true;this.host.setAttribute('aria-label','个人厨房');
    document.querySelector('.main').insertBefore(this.host,document.getElementById('settings-host'));
    const style=node('link');style.rel='stylesheet';style.href='/static/centers.css';document.head.append(style);
  }
  async show(page) {
    this.page=page;this.host.hidden=!['recipes','journal'].includes(page);
    if(!this.host.hidden)await this.render();
  }
  async render() {
    const epoch=++this.generation;this.host.replaceChildren(node('p','center-muted','正在读取…'));
    try {
      if(this.page==='recipes')await this.recipes(epoch);else if(this.page==='journal')await this.journal(epoch);
    } catch(error) {
      if(epoch!==this.generation)return;
      this.host.replaceChildren(node('p','center-error',error.message),button('重新读取',()=>this.render()));
    }
  }
  frame(title,description) {
    const inner=node('div','center-inner'),top=node('div','center-heading');
    top.append(heading(title,description),button('返回对话',this.onReturn));inner.append(top);this.host.replaceChildren(inner);return {inner,top};
  }
  async recipes(epoch) {
    const result=await this.api(`/api/cookbook?source=${this.source}&query=${encodeURIComponent(this.query)}&offset=${this.offset}&limit=12`);
    if(epoch!==this.generation)return;
    const {inner,top}=this.frame('菜谱中心','把喜欢的味道收在一起，随时开始做。');
    top.append(button('制作自己的菜谱',()=>this.onChat('我想制作自己的菜谱。我会提供截图或文字，请帮我整理成可核对的菜谱草稿。'),true));
    const toolbar=node('form','center-toolbar'),input=node('input','center-input center-search');input.type='search';input.value=this.query;input.placeholder='搜索菜名或我的菜谱食材';input.setAttribute('aria-label','搜索菜谱');
    const search=button('搜索',()=>{});search.type='submit';toolbar.append(input,search);
    toolbar.onsubmit=event=>{event.preventDefault();this.query=input.value.trim();this.offset=0;this.render();};
    const tabs=node('div','center-tabs');tabs.setAttribute('aria-label','菜谱来源');
    for(const [key,label] of [['all','全部菜谱'],['user','我的菜谱'],['favorites','已收藏'],['drafts','待核对']]) {
      const tab=button(label,()=>{this.source=key;this.offset=0;this.render();});tab.setAttribute('aria-pressed',String(this.source===key));tabs.append(tab);
    }
    toolbar.append(tabs);inner.append(toolbar,node('p','center-muted',`共 ${result.total} 道${this.source==='drafts'?'待核对草稿':'菜谱'}`));
    const grid=node('div','center-grid');
    for(const item of result.items) {
      const card=node('article','recipe-center-card');card.append(node('span','center-tag',item.status==='draft'?'待核对':item.origin==='user'?'我的菜谱':'精选菜谱'),node('h2','',item.name));
      card.append(node('p','center-muted',[Number.isFinite(item.total_time_min)?`${item.total_time_min} 分钟`:'时间待确认',Number.isFinite(item.servings)?`原配方 ${item.servings} 份`:'原份量未知'].join(' · ')));
      if(item.description)card.append(node('p','center-muted',item.description.slice(0,90)));
      const actions=node('div','center-card-actions');actions.append(button(item.status==='draft'?'核对草稿':'查看做法',()=>this.detail(item,inner)));
      if(item.status!=='draft') {
        const favorite=button(item.favorite?'已收藏':'收藏',async()=>{
          favorite.disabled=true;try {const data=await this.api(`/api/favorites/${encodeURIComponent(item.recipe_id)}`,{method:'PUT',body:JSON.stringify({favorite:!item.favorite})});item.favorite=data.favorite;favorite.textContent=data.favorite?'已收藏':'收藏';favorite.setAttribute('aria-pressed',String(data.favorite));}catch(error){this.error(card,error);}finally{favorite.disabled=false;}
        });favorite.setAttribute('aria-pressed',String(item.favorite));actions.append(favorite);
      }
      card.append(actions);grid.append(card);
    }
    if(!result.items.length)grid.append(node('p','center-empty',this.source==='drafts'?'发一张菜谱截图，或粘贴一段做法，助手会把待核对的草稿放在这里。':'这里还没有菜谱。换个关键词，或把你喜欢的做法交给助手。'));
    inner.append(grid);const pager=node('div','center-pager');
    const previous=button('上一页',()=>{this.offset=Math.max(0,this.offset-12);this.render();});previous.disabled=this.offset===0;
    const next=button('下一页',()=>{this.offset+=12;this.render();});next.disabled=this.offset+12>=result.total;
    pager.append(previous,node('span','center-muted',`${Math.floor(this.offset/12)+1} / ${Math.max(1,Math.ceil(result.total/12))}`),next);inner.append(pager);
  }
  error(host,error){host.querySelector('.center-error')?.remove();const message=node('p','center-error',error.message);message.setAttribute('role','alert');host.append(message);}
  async detail(item,inner) {
    inner.querySelector('.center-detail')?.remove();const section=node('section','center-detail');section.setAttribute('aria-label','菜谱详情');inner.append(section);section.append(node('p','center-muted','正在读取菜谱…'));section.scrollIntoView({behavior:'smooth',block:'start'});
    try {
      if(item.status==='draft') {const record=await this.api(`/api/cookbook/${item.recipe_id}`);if(section.isConnected)this.draftEditor(record,section);return;}
      const result=await this.api(`/api/recipes/${encodeURIComponent(item.recipe_id)}`);if(!section.isConnected)return;const recipe=result.recipe;
      section.replaceChildren(node('h2','',recipe.name));
      section.append(node('p','center-muted',item.origin==='user'?'你保存的个人菜谱':'来自菜谱库的原配方'));
      const actions=node('div','center-card-actions');actions.append(button('就做这道菜',()=>this.onChat(`我想做${item.origin==='user'?'我保存的自定义菜谱':'菜谱中心里的'}「${recipe.name}」。请先检索并读取真实菜谱，再帮我安排。`),true),button('收起详情',()=>section.remove()));section.append(actions);
      const ingredients=node('ul');for(const food of recipe.ingredients || [])ingredients.append(node('li','',food.raw_text || `${food.canonical_name} ${food.raw_amount || '适量'}`));
      section.append(node('h3','','食材'),ingredients,node('h3','','做法'));const steps=node('ol');for(const step of recipe.steps || [])steps.append(node('li','',typeof step==='string'?step:step.instruction || step.description));section.append(steps);
      for(const warning of recipe.warnings || [])section.append(node('p','center-muted',warning));
      const nutrition=node('div','nutrition-grid');for(const [key,label,unit] of nutrients){const cell=node('div','nutrition-value',label);cell.append(node('strong','',value(recipe.nutrition?.per_serving?.[key],unit)));nutrition.append(cell);}
      section.append(node('h3','','原配方每份营养'),nutrition,node('p','center-muted',recipe.nutrition?.estimated?'含估算，仅供参考；未知项不补零。':'按原配方计算，未知项不补零。'));
    }catch(error){section.replaceChildren(node('p','center-error',error.message));}
  }
  draftEditor(record,section) {
    const documentRecipe=record.document;
    section.replaceChildren(node('h2','','核对你的菜谱'),node('p','center-muted','看看名字、用量和步骤是否准确。没有写清的人数或时间可以留空，确认保存后就能用于烹饪。'));
    const form=node('form');const controls={};
    const add=(key,label,text,type='text')=>{const input=node(type==='textarea'?'textarea':'input','center-input');if(type!=='textarea')input.type=type;input.value=text??'';controls[key]=input;form.append(field(label,input));return input;};
    add('name','菜名',documentRecipe.name).required=true;
    add('source_text','来源原文（可修正辨认错误）',record.source_text,'textarea');
    const ingredientEditor=node('div');const ingredientInputs=[];
    for(const food of documentRecipe.ingredients) {
      const row=node('div','center-toolbar');const name=node('input','center-input');name.value=food.canonical_name;name.required=true;name.setAttribute('aria-label','食材名称');
      const amount=node('input','center-input');amount.value=food.raw_amount ? `${food.raw_amount}${food.raw_unit || ''}` : '适量';amount.setAttribute('aria-label',`${food.canonical_name}的用量`);row.append(name,amount);ingredientEditor.append(row);ingredientInputs.push({name,amount,original:food,originalAmount:amount.value});
    }
    form.append(node('h3','','食材与用量'),ingredientEditor);add('steps','做法（每行一步）',documentRecipe.steps.join('\n'),'textarea');
    add('servings','原配方份数（未知可留空）',documentRecipe.servings,'number');controls.servings.min='.1';controls.servings.step='.1';
    add('servings_evidence','份数依据',documentRecipe.servings_evidence);
    add('total_time_min','总耗时（分钟，未知可留空）',documentRecipe.total_time_min,'number');controls.total_time_min.min='0';controls.total_time_min.step='.5';
    add('time_evidence','耗时依据',record.time_evidence);add('warnings','注意事项（每行一条）',(documentRecipe.warnings || []).join('\n'),'textarea');
    let dirty=false;form.addEventListener('input',()=>dirty=true);
    const actions=node('div','center-card-actions'),save=button('保存修改',()=>{}),publish=button('确认保存到我的菜谱',()=>{},true);actions.append(save,publish,button('收起草稿',()=>{if(dirty){this.error(form,new Error('草稿还有修改，请先保存。'));return;}section.remove();}));form.append(actions);
    const payload=()=>({name:controls.name.value,description:documentRecipe.description || '',source_text:controls.source_text.value,source_kind:record.source_kind,
      servings:controls.servings.value===''?null:Number(controls.servings.value),servings_evidence:controls.servings_evidence.value,
      prep_time_min:documentRecipe.prep_time_min,cook_time_min:documentRecipe.cook_time_min,total_time_min:controls.total_time_min.value===''?null:Number(controls.total_time_min.value),time_evidence:controls.time_evidence.value,
      ingredients:ingredientInputs.map(({name,amount,original,originalAmount})=>({name:name.value,amount:amount.value,grams:name.value===original.canonical_name && amount.value===originalAmount?original.grams:null,
        amount_evidence:name.value===original.canonical_name && amount.value===originalAmount?original.amount_evidence:'',scalable:original.scalable,usda_query:original.usda_query,allergens:original.allergens})),
      steps:controls.steps.value.split('\n').map(s=>s.trim()).filter(Boolean),warnings:controls.warnings.value.split('\n').map(s=>s.trim()).filter(Boolean)});
    const persist=async()=>{if(dirty){record=await this.api(`/api/cookbook/${record.recipe_id}`,{method:'PUT',body:JSON.stringify({revision:record.revision,recipe:payload()})});dirty=false;}return record;};
    save.onclick=async()=>{save.disabled=publish.disabled=true;try{await persist();this.draftEditor(record,section);section.prepend(node('p','center-muted','修改已保存。'));}catch(error){this.error(form,error);}finally{save.disabled=publish.disabled=false;}};
    publish.onclick=async()=>{save.disabled=publish.disabled=true;try{await persist();await this.api(`/api/cookbook/${record.recipe_id}/publish`,{method:'POST',body:JSON.stringify({revision:record.revision})});this.source='user';this.offset=0;await this.render();}catch(error){this.error(form,error);}finally{save.disabled=publish.disabled=false;}};
    form.onsubmit=event=>{event.preventDefault();save.click();};section.append(form);
  }
  async journal(epoch) {
    const data=await this.api(`/api/journal?days=${this.days}`);if(epoch!==this.generation)return;
    const {inner}=this.frame('食光手记','记下认真做过的饭，慢慢找到适合自己的味道。');
    const toolbar=node('div','center-toolbar'),period=node('select','center-input');period.setAttribute('aria-label','记录时间范围');
    for(const days of [7,30,90]){const option=node('option','',`最近 ${days} 天`);option.value=String(days);option.selected=days===this.days;period.append(option);}period.onchange=()=>{this.days=Number(period.value);this.render();};toolbar.append(period,button('刷新记录',()=>this.render()));inner.append(toolbar);
    const summary=data.summary,stats=node('div','center-stats');
    for(const [label,count] of [['完成餐次',summary.completed_meals],['完成菜品',summary.completed_dishes],['尝试菜谱',summary.unique_recipes],['开始餐次',summary.attempted_meals]]){const cell=node('div','center-stat',label);cell.append(node('strong','',String(count)));stats.append(cell);}inner.append(stats);
    inner.append(node('h2','center-section-title','每天的厨房足迹'));const spark=node('div','center-spark');spark.setAttribute('role','img');spark.setAttribute('aria-label',summary.days.map(day=>`${day.date} 完成${day.completed_meals}餐`).join('；'));
    const days=summary.days.slice(-14),maximum=Math.max(1,...days.map(day=>day.completed_meals));
    for(const day of days){const column=node('div','center-spark-column'),bar=node('div','center-spark-bar');bar.style.height=`${Math.max(2,day.completed_meals/maximum*70)}px`;column.append(node('span','',String(day.completed_meals)),bar,node('span','',day.date.slice(5)));spark.append(column);}inner.append(spark);
    inner.append(node('h2','center-section-title','已完成餐食的营养汇总'));
    const totalNutrition=node('div','nutrition-grid');for(const [key,label,unit] of nutrients){const cell=node('div','nutrition-value',label);cell.append(node('strong','',value(summary.nutrition?.meal_total?.[key],unit)));totalNutrition.append(cell);}
    inner.append(totalNutrition,node('p','center-muted',`${summary.nutrition?.estimated?'含估算。':''}显示这段时间整餐制作量的累计，不能当成一个人的实际摄入；资料缺失时保留未知。`));
    inner.append(node('h2','center-section-title','这段时间做了什么'));const list=node('div','journal-list');
    for(const entry of data.entries){
      const card=node('article','journal-card'),names=entry.recipes.map(recipe=>recipe.name).join('、');
      card.append(node('time','',entry.date),node('h3','',names),node('span','center-tag',entry.status==='completed'?'已完成':'已尝试'),node('p','center-muted',entry.portion_note || `${entry.diners || '未知'} 人共享`));
      const tabs=node('div','center-tabs'),nutrition=node('div','nutrition-grid');
      const paint=basis=>{nutrition.replaceChildren();for(const [key,label,unit] of nutrients){const metric=node('div','nutrition-value',label);metric.append(node('strong','',value(entry.nutrition[basis]?.[key],unit)));nutrition.append(metric);}
        for(const el of tabs.children)el.setAttribute('aria-pressed',String(el.dataset.basis===basis));};
      if(entry.status==='completed'){
        for(const [basis,label] of [['meal_total','整餐制作量'],['per_diner_reference','均分参考']]){const tab=button(label,()=>paint(basis));tab.dataset.basis=basis;tabs.append(tab);}paint('meal_total');
        card.append(tabs,nutrition,node('p','center-muted',`${entry.nutrition.estimated?'含估算。':''}${entry.nutrition.notice}`));
      } else card.append(node('p','center-muted','这餐还没有确认完成，不计入已完成营养汇总。'));
      const form=node('form','journal-feedback'),rating=node('select','center-input');rating.setAttribute('aria-label','这次做菜的满意度');
      for(const [score,label] of [['','暂不评分'],['5','很满意'],['4','挺好吃'],['3','还可以'],['2','待改进'],['1','没成功']]){const option=node('option','',label);option.value=score;option.selected=String(entry.rating || '')===score;rating.append(option);}
      const notes=node('textarea','center-input');notes.rows=1;notes.maxLength=2000;notes.value=entry.notes || '';notes.placeholder='记点下次想调整的口味、火候…';notes.setAttribute('aria-label','做菜笔记');
      const save=button('保存感受',()=>{});save.type='submit';form.append(rating,notes,save);
      form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{await this.api(`/api/journal/${entry.id}`,{method:'PATCH',body:JSON.stringify({rating:rating.value?Number(rating.value):null,notes:notes.value})});save.textContent='已保存';}catch(error){this.error(card,error);}finally{save.disabled=false;}};card.append(form);list.append(card);
    }
    if(!data.entries.length)list.append(node('p','center-empty','还没有记录。开始烹饪后，会记下一次尝试；所有菜做好后，点“完成本次菜肴”就能留下这顿饭。'));
    inner.append(list,node('p','center-muted',summary.notice));
  }
}
