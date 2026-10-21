import {node} from './ui-components.js';
import {icon} from './icons.js';
import {enhanceControl} from './form-controls.js';
import {CenterEditorState,draftIngredient} from './center-state.js';
import {FocusMotion} from './focus-state.js';
import {renderJournalContent} from './nutrition-dashboard.js';
import {renderFoodLogEditor} from './food-log-editor.js';
import {resizeRightPane,panelBounds} from './pane-resize.js';

const nutrients=[['calories_kcal','热量','kcal'],['protein_g','蛋白质','g'],['fat_g','脂肪','g'],['carbohydrate_g','碳水','g']];
const value=(number,unit='')=>Number.isFinite(number)?`${Math.round(number*10)/10}${unit?` ${unit}`:''}`:'暂无数据';
let tabSequence=0;
function button(text,action,style='') {
  const el=node('button',`center-button ${style}`,text);el.type='button';el.onclick=action;return el;
}
function iconButton(name,label,action) {
  const el=button('',action,'icon-button');el.append(icon(name));el.setAttribute('aria-label',label);el.title=label;return el;
}
function field(label,control,hint='') {
  const el=node('div','center-field'),caption=node('label','field-label',label);control.id||=`center-input-${++tabSequence}`;caption.htmlFor=control.id;el.append(caption,enhanceControl(control,{label}));
  if(hint){const help=node('span','center-field-hint',hint);help.id=`${control.id}-help`;control.setAttribute('aria-describedby',help.id);el.append(help);}return el;
}
function metricGrid(nutrition) {
  const grid=node('dl','center-nutrition');
  for(const [key,label,unit] of nutrients){const metric=node('div');metric.append(node('dt','',label),node('dd','',value(nutrition?.[key],unit)));grid.append(metric);}return grid;
}
function tabs(items,selected,onSelect,{label='内容分类',panels=null}={}) {
  const id=`center-tabs-${++tabSequence}`,bar=node('div','center-tabs');bar.setAttribute('role','tablist');bar.setAttribute('aria-label',label);const buttons=[];
  const select=(key,focus=false)=>{
    for(const tab of buttons){const active=tab.dataset.key===key;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;}
    if(panels)for(const [panelKey,panel] of Object.entries(panels))panel.hidden=panelKey!==key;
    if(focus)buttons.find(tab=>tab.dataset.key===key)?.focus();onSelect?.(key);
  };
  for(const [key,text] of items){
    const tab=button(text,()=>select(key));tab.id=`${id}-${key}`;tab.dataset.key=key;tab.setAttribute('role','tab');
    if(panels?.[key]){tab.setAttribute('aria-controls',`${id}-panel-${key}`);panels[key].id=`${id}-panel-${key}`;panels[key].setAttribute('role','tabpanel');panels[key].setAttribute('aria-labelledby',tab.id);}
    tab.setAttribute('aria-selected',String(key===selected));tab.tabIndex=key===selected?0:-1;buttons.push(tab);bar.append(tab);
    tab.onkeydown=event=>{let index=buttons.indexOf(tab);if(event.key==='ArrowRight')index=(index+1)%buttons.length;else if(event.key==='ArrowLeft')index=(index-1+buttons.length)%buttons.length;else if(event.key==='Home')index=0;else if(event.key==='End')index=buttons.length-1;else return;event.preventDefault();select(buttons[index].dataset.key,true);};
  }
  if(panels)for(const [key,panel] of Object.entries(panels))panel.hidden=key!==selected;
  return {bar,select};
}

export class KitchenCenters {
  constructor({api,onChat,onReturn,onNavigate,onRecord}) {
    this.onRecord=onRecord;
    Object.assign(this,{api,onChat,onReturn,onNavigate});this.page='chat';this.source='all';this.query='';this.offset=0;this.days=7;this.journalTab='overview';this.generation=0;this.detailGeneration=0;
    this.host=node('section','center-host');this.host.id='center-host';this.host.hidden=true;this.host.setAttribute('aria-label','个人厨房');
    document.querySelector('.main').insertBefore(this.host,document.getElementById('settings-host'));
    for(const name of ['centers','form-controls'])if(!document.querySelector(`link[href="/static/${name}.css"]`)){const style=node('link');style.rel='stylesheet';style.href=`/static/${name}.css`;document.head.append(style);}
    this.motion=new FocusMotion();this.narrow=window.matchMedia('(max-width: 900px)');this.narrow.addEventListener('change',()=>this.adaptInspector());this.restorePending=new Set();
    this.resizeObserver=new ResizeObserver(()=>this.adaptInspector());this.resizeObserver.observe(this.host);
    window.addEventListener('chefmate:before-page-change',event=>{
      if(event.detail.page===this.page || !this.editor || (!this.editor.state.dirty && !this.editor.state.busy))return;
      event.preventDefault();this.leave(()=>this.onNavigate?.(event.detail.page));
    });
    window.addEventListener('beforeunload',event=>{if(this.editor?.state.dirty || this.editor?.state.busy){event.preventDefault();event.returnValue='';}});
    window.addEventListener('keydown',event=>{
      if(!this.editor || this.confirmation || this.host.hidden)return;
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();this.closeInspector();}
      if(event.key==='Tab' && this.compact){
        const targets=[...this.editor.panel.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(target=>target.tabIndex>=0 && target.getClientRects().length);
        const first=targets[0],last=targets.at(-1);if(!first)return;
        if(!this.editor.panel.contains(document.activeElement) || (event.shiftKey?document.activeElement===first:document.activeElement===last)){event.preventDefault();(event.shiftKey?last:first).focus();}
      }
    },true);
  }
  async show(page) {
    if(page===this.page && !this.host.hidden)return;
    this.generation++;this.listAbort?.abort();this.disposeInspector(false);this.page=page;this.host.hidden=!['recipes','journal'].includes(page);
    if(!this.host.hidden){this.frame();await this.refresh();}
  }
  frame() {
    this.layout=node('div','center-layout');this.pane=node('div','center-main-pane');const top=node('header','center-heading'),heading=node('div','center-title'),actions=node('div','center-heading-actions');
    heading.append(node('h1','',this.page==='recipes'?'菜谱中心':'食光手记'),node('p','center-muted',this.page==='recipes'?'收好喜欢的味道，随时开始做。':'记下每一顿饭，慢慢找到适合自己的味道。'));
    actions.append(button('返回对话',this.onReturn,'quiet'));if(this.page==='recipes')actions.append(button('新建菜谱',()=>this.chat('我想制作自己的菜谱。我会提供截图或文字，请帮我整理成可核对的菜谱草稿。'),'primary'));
    else actions.append(button('记一餐',()=>this.leave(()=>{this.disposeInspector(false);this.onRecord?.();}),'primary'));
    top.append(heading,actions);this.notice=node('p','center-notice');this.notice.hidden=true;this.notice.setAttribute('role','status');this.navigation=node('div','center-navigation');this.content=node('div','center-content');
    this.pane.append(top,this.navigation,this.notice,this.content);this.layout.append(this.pane);this.host.replaceChildren(this.layout);
    if(this.page==='recipes')this.recipeNavigation();else this.journalNavigation();
  }
  message(text,error=false) {this.notice.textContent=text;this.notice.classList.toggle('center-error',error);this.notice.hidden=false;}
  isCurrentPane(pane) {return this.pane===pane && !this.host.hidden;}
  chat(text) {return this.leave(()=>{this.disposeInspector(false);this.onChat(text);});}
  async refresh() {
    const epoch=++this.generation,page=this.page;this.listAbort?.abort();this.listAbort=new AbortController();this.content.setAttribute('aria-busy','true');
    if(!this.content.children.length)this.content.append(node('p','center-empty','正在读取…'));
    try{
      if(page==='recipes'){
        const result=await this.api(`/api/cookbook?source=${this.source}&query=${encodeURIComponent(this.query)}&offset=${this.offset}&limit=12`,{signal:this.listAbort.signal});
        if(epoch===this.generation && this.page===page)this.recipeList(result);
      }else{
        const data=await this.api(`/api/journal?days=${this.days}`,{signal:this.listAbort.signal});
        if(epoch===this.generation && this.page===page){this.journalData=data;this.journalContent();}
      }
    }catch(error){if(error.name!=='AbortError' && epoch===this.generation)this.content.replaceChildren(node('p','center-error',error.message),button('重新读取',()=>this.refresh()));}
    finally{if(epoch===this.generation)this.content.removeAttribute('aria-busy');}
  }
  recipeNavigation() {
    const sources=tabs([['all','全部菜谱'],['user','我的菜谱'],['favorites','已收藏'],['drafts','待核对'],['deleted','回收站']],this.source,key=>{this.source=key;this.offset=0;this.linkContentTab(sources.bar,key);this.refresh();},{label:'菜谱分类'});this.linkContentTab(sources.bar,this.source);
    const form=node('form','center-searchbar'),input=node('input','center-input');input.type='search';input.value=this.query;input.placeholder='搜索菜名或我的菜谱食材';input.setAttribute('aria-label','搜索菜谱');const search=button('搜索',()=>{});search.type='submit';form.append(enhanceControl(input,{label:'搜索菜谱'}),search);
    form.onsubmit=event=>{event.preventDefault();this.query=input.value.trim();this.offset=0;this.refresh();};this.navigation.append(sources.bar,form);
  }
  recipeList(result) {
    const fragment=document.createDocumentFragment();fragment.append(node('p','center-result-count',`共 ${result.total} ${this.source==='drafts'?'份草稿':'道菜谱'}${this.source==='deleted'?' · 可随时恢复':''}`));const list=node('div','center-recipe-list');
    for(const item of result.items){
      const row=node('article','center-recipe-row');row.dataset.recipeId=item.recipe_id;row.classList.toggle('is-selected',this.editor?.id===item.recipe_id);
      const identity=node('div','center-recipe-identity'),kind=node('span','center-eyebrow',item.status==='deleted'?'已移入回收站':item.status==='draft'?'待核对':item.origin==='user'?'我的菜谱':'精选菜谱');
      const title=item.status==='deleted'?node('h2','center-recipe-title',item.name):button(item.name,()=>this.openRecipe(item),'center-recipe-title');if(item.status!=='deleted')title.setAttribute('aria-label',`${item.status==='draft'?'核对':'查看'}${item.name}`);
      identity.append(kind,title,node('p','center-muted',[Number.isFinite(item.total_time_min)?`${item.total_time_min} 分钟`:'时间待确认',Number.isFinite(item.servings)?`原配方 ${item.servings} 份`:'原份量未知'].join(' · ')));
      if(item.description)identity.append(node('p','center-recipe-description',item.description.slice(0,100)));const actions=node('div','center-row-actions');
      if(item.status==='deleted'){const restore=button('恢复菜谱',()=>this.restoreRecipe(item,restore),'quiet');restore.disabled=this.restorePending.has(item.recipe_id);actions.append(restore);}
      else{
        actions.append(button(item.status==='draft'?'核对草稿':'查看做法',()=>this.openRecipe(item),'quiet'));
        if(item.status!=='draft'){
          const favorite=iconButton('book-open',item.favorite?'取消收藏':'收藏菜谱',async()=>{
            favorite.disabled=true;const pane=this.pane;
            try{const data=await this.api(`/api/favorites/${encodeURIComponent(item.recipe_id)}`,{method:'PUT',body:JSON.stringify({favorite:!item.favorite})});if(!this.isCurrentPane(pane))return;item.favorite=data.favorite;favorite.setAttribute('aria-pressed',String(data.favorite));favorite.setAttribute('aria-label',data.favorite?'取消收藏':'收藏菜谱');favorite.title=data.favorite?'取消收藏':'收藏菜谱';if(this.source==='favorites' && !data.favorite)this.refresh();}
            catch(error){if(this.isCurrentPane(pane))this.message(error.message,true);}finally{favorite.disabled=false;}
          });favorite.setAttribute('aria-pressed',String(item.favorite));actions.append(favorite);
        }
      }
      row.append(identity,actions);list.append(row);
    }
    if(!result.items.length)list.append(this.emptyRecipes());fragment.append(list);const pager=node('nav','center-pager');pager.setAttribute('aria-label','菜谱翻页');
    const previous=button('上一页',()=>{this.offset=Math.max(0,this.offset-12);this.refresh();},'quiet'),next=button('下一页',()=>{this.offset+=12;this.refresh();},'quiet');previous.disabled=this.offset===0;next.disabled=this.offset+12>=result.total;
    pager.append(previous,node('span','center-muted',`${Math.floor(this.offset/12)+1} / ${Math.max(1,Math.ceil(result.total/12))}`),next);fragment.append(pager);this.content.replaceChildren(fragment);
  }
  emptyRecipes() {
    const empty=node('div','center-empty');empty.append(node('h2','',this.source==='deleted'?'回收站是空的':this.source==='drafts'?'把想做的菜交给小锅搭子':'还没有找到菜谱'),node('p','center-muted',this.source==='deleted'?'移除的个人菜谱会保留在这里，之后还可以恢复。':this.source==='drafts'?'发一张截图，或粘贴一段做法。整理后的草稿会放在这里，核对并保存后就能开始做。':'换个关键词，或让助手帮你整理喜欢的做法。'));return empty;
  }
  async leave(action) {
    const editor=this.editor;
    if(editor?.state.busy){this.message('正在保存，请稍等片刻再离开。');window.dispatchEvent(new CustomEvent('chefmate:navigation-cancelled'));return false;}
    if(editor?.state.dirty){
      if(this.confirmation)return false;
      const discard=await this.confirm('还有未保存的修改','离开后，这次没有保存的修改会丢失。已经保存的草稿仍会保留。','放弃修改并离开','继续编辑');
      if(!discard){window.dispatchEvent(new CustomEvent('chefmate:navigation-cancelled'));return false;}if(this.editor!==editor)return false;editor.state.dirty=false;
    }
    action();return true;
  }
  confirm(title,description,confirmText='确认',cancelText='取消') {
    if(this.confirmation)return Promise.resolve(false);
    const dialog=node('dialog','center-confirm'),body=node('div','center-confirm-body'),actions=node('div','center-confirm-actions'),id=`center-confirm-${++tabSequence}`,label=node('h2','',title);label.id=id;dialog.setAttribute('aria-labelledby',id);
    const desc=node('p','center-muted',description);desc.id=`${id}-description`;dialog.setAttribute('aria-describedby',desc.id);body.append(label,desc);
    return new Promise(resolve=>{
      this.confirmation=dialog;const previous=document.activeElement;
      const finish=answer=>{dialog.close();dialog.remove();this.confirmation=null;if(previous?.isConnected)previous.focus({preventScroll:true});resolve(answer);};
      const cancel=button(cancelText,()=>finish(false),'quiet'),accept=button(confirmText,()=>finish(true),'danger');actions.append(cancel,accept);dialog.append(body,actions);document.body.append(dialog);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false);});dialog.addEventListener('keydown',event=>event.stopPropagation());dialog.showModal();cancel.focus();
    });
  }
  openRecipe(item) {return this.leave(()=>this.loadRecipe(item));}
  async loadRecipe(item) {
    this.disposeInspector(false);const epoch=++this.detailGeneration;this.detailAbort=new AbortController();const editor=this.createInspector(item.recipe_id,item.status==='draft'?'核对菜谱':item.name);editor.item=item;editor.body.append(node('p','center-empty','正在读取菜谱…'));
    try{
      if(item.status==='draft' || item.origin==='user'){
        const record=await this.api(`/api/cookbook/${encodeURIComponent(item.recipe_id)}`,{signal:this.detailAbort.signal});if(epoch!==this.detailGeneration || this.editor!==editor)return;editor.record=record;
        if(record.status==='draft')this.draftEditor(editor,record);else this.recipeDetail(editor,{...record.document,nutrition:record.nutrition},item);
      }else{const result=await this.api(`/api/recipes/${encodeURIComponent(item.recipe_id)}`,{signal:this.detailAbort.signal});if(epoch===this.detailGeneration && this.editor===editor)this.recipeDetail(editor,result.recipe,item);}
    }catch(error){if(error.name!=='AbortError' && this.editor===editor)editor.body.replaceChildren(node('p','center-error',error.message));}
  }
  createInspector(id,title) {
    const panel=node('aside','center-inspector'),header=node('header','center-inspector-header'),identity=node('div'),titleNode=node('h2','',title);titleNode.tabIndex=-1;identity.append(node('span','center-eyebrow','菜谱中心'),titleNode);
    header.append(identity,iconButton('x','关闭详情',()=>this.closeInspector()));const body=node('div','center-inspector-body'),footer=node('footer','center-inspector-footer'),status=node('p','center-inspector-status');status.setAttribute('role','status');status.hidden=true;panel.append(header,body,status,footer);
    const editor={id,panel,header,title:titleNode,eyebrow:identity.firstChild,body,footer,status,state:new CenterEditorState(id),returnFocus:document.activeElement};this.editor=editor;this.layout.append(panel);this.layout.classList.add('has-inspector');this.adaptInspector();
    const handle=node('div','right-pane-resize');handle.setAttribute('role','separator');handle.setAttribute('aria-label','调整详情宽度');handle.setAttribute('aria-orientation','vertical');handle.tabIndex=0;panel.prepend(handle);
    editor.resizer=resizeRightPane({root:this.layout,handle,key:'chefmate:inspector-width',property:'--inspector-width',initial:500,enabled:()=>!this.compact,bounds:()=>panelBounds(this.host.clientWidth,{minimum:360,remaining:310})});
    if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches && panel.animate)this.motion.start(panel.animate([{transform:'translateX(32px)',opacity:0},{transform:'translateX(0)',opacity:1}],{duration:200,easing:'cubic-bezier(.2,.7,.3,1)'}));
    titleNode.focus({preventScroll:true});for(const row of this.content.querySelectorAll('[data-recipe-id]'))row.classList.toggle('is-selected',row.dataset.recipeId===id);return editor;
  }
  adaptInspector() {
    if(!this.editor || this.host.hidden)return;this.compact=this.narrow.matches || this.host.clientWidth<820;this.layout.classList.toggle('compact-inspector',this.compact);this.pane.inert=this.compact;
    for(const background of [document.getElementById('sidebar'),document.querySelector('.topbar')])if(background)background.inert=this.compact;
    this.editor.panel.setAttribute('role',this.compact?'dialog':'region');if(this.compact)this.editor.panel.setAttribute('aria-modal','true');else this.editor.panel.removeAttribute('aria-modal');this.editor.panel.setAttribute('aria-label',this.editor.title.textContent);
  }
  disposeInspector(restoreFocus=true) {
    this.detailGeneration++;this.detailAbort?.abort();this.motion.cancel();const editor=this.editor;if(!editor)return;editor.resizer?.dispose();editor.state.dispose();editor.panel.remove();this.editor=null;if(this.pane)this.pane.inert=false;this.layout?.classList.remove('has-inspector','compact-inspector');
    for(const background of [document.getElementById('sidebar'),document.querySelector('.topbar')])if(background)background.inert=false;
    for(const row of this.content?.querySelectorAll('.is-selected') || [])row.classList.remove('is-selected');
    if(restoreFocus){const target=editor.returnFocus?.isConnected?editor.returnFocus:[...this.content?.querySelectorAll('[data-recipe-id]') || []].find(row=>row.dataset.recipeId===editor.id)?.querySelector('button');target?.focus({preventScroll:true});}
  }
  closeInspector() {return this.leave(()=>this.disposeInspector());}
  inspectorTabs(editor,items,panels,selected=items[0][0]) {
    editor.body.replaceChildren();const navigation=tabs(items,selected,key=>{editor.activeTab=key;editor.onTabChange?.(key);},{label:'详情内容',panels}),bar=node('div','center-inspector-tabs');bar.append(navigation.bar);editor.body.append(bar,...Object.values(panels));editor.tabs=navigation;editor.activeTab=selected;
  }
  status(editor,text,error=false) {if(this.editor!==editor)return;editor.status.hidden=false;editor.status.textContent=text;editor.status.classList.toggle('center-error',error);}
  recipeDetail(editor,recipe,item) {
    editor.title.textContent=recipe.name;editor.eyebrow.textContent=item.origin==='user'?'我的菜谱':'精选菜谱';this.adaptInspector();const steps=node('section','center-tab-panel'),ingredients=node('section','center-tab-panel'),nutrition=node('section','center-tab-panel'),source=node('section','center-tab-panel');
    const ordered=node('ol','center-recipe-steps');for(const step of recipe.steps || [])ordered.append(node('li','',typeof step==='string'?step:step.instruction || step.description));steps.append(ordered);if(!ordered.children.length)steps.append(node('p','center-muted','暂时没有做法记录。'));
    const foods=node('ul','center-ingredients');for(const food of recipe.ingredients || []){const row=node('li');row.append(node('span','',food.canonical_name || food.raw_text),node('span','center-muted',food.raw_amount?`${food.raw_amount}${food.raw_unit || ''}`:food.raw_text || '适量'));foods.append(row);}ingredients.append(foods);
    nutrition.append(node('h3','','原配方每份营养'),metricGrid(recipe.nutrition?.per_serving),node('p','center-muted',recipe.nutrition?.estimated?'含估算，仅供参考。未知项保留空缺。':'按原配方计算。未知项保留空缺。'));
    const record=editor.record;source.append(node('h3','',item.origin==='user'?'你的个人菜谱':'菜谱库原配方'),node('p','center-muted',[Number.isFinite(recipe.servings)?`原配方 ${recipe.servings} 份`:'原份量待确认',Number.isFinite(recipe.total_time_min)?`总耗时 ${recipe.total_time_min} 分钟`:'总耗时待确认'].join(' · ')));
    if(record?.source_text)source.append(node('h3','','来源原文'),node('p','center-source-text',record.source_text));for(const warning of recipe.warnings || [])source.append(node('p','center-warning',warning));
    this.inspectorTabs(editor,[['steps','做法'],['ingredients','食材'],['nutrition','营养'],['source','来源']],{steps,ingredients,nutrition,source});const actions=node('div','center-footer-actions');
    if(item.origin==='user')actions.append(button('移到回收站',()=>this.deleteRecipe(editor.record),'danger quiet'));actions.append(button('就做这道菜',()=>this.chat(`我想做${item.origin==='user'?'我保存的自定义菜谱':'菜谱中心里的'}「${recipe.name}」。请先检索并读取真实菜谱，再帮我安排。`),'primary'));editor.footer.replaceChildren(actions);
  }
  draftEditor(editor,record) {
    const recipe=record.document,controls={},ingredientInputs=[];editor.title.textContent=recipe.name || '核对菜谱';editor.eyebrow.textContent='待核对草稿';editor.record=record;this.adaptInspector();const basic=node('section','center-tab-panel'),method=node('section','center-tab-panel'),source=node('section','center-tab-panel');basic.append(node('p','center-muted','核对名字、用量与做法。保存到“我的菜谱”后，就能让助手安排烹饪。'));
    const add=(parent,key,label,text,type='text',hint='')=>{const input=node(type==='textarea'?'textarea':'input','center-input');if(type!=='textarea')input.type=type;input.value=text??'';controls[key]=input;parent.append(field(label,input,hint));return input;};
    add(basic,'name','菜名',recipe.name).required=true;const servings=add(basic,'servings','原配方份数',recipe.servings,'number','未知可以留空，不需要猜测。');servings.min='.1';servings.step='.1';add(basic,'servings_evidence','份数依据',recipe.servings_evidence);
    const duration=add(basic,'total_time_min','总耗时（分钟）',recipe.total_time_min,'number','填写来源明确给出的总耗时；未知可以留空。');duration.min='0';duration.step='.5';add(basic,'time_evidence','耗时依据',record.time_evidence);
    method.append(node('h3','','食材与用量'));const ingredients=node('div','center-ingredient-editor');
    const addIngredient=food=>{
      const row=node('div','center-ingredient-row'),name=node('input','center-input'),amount=node('input','center-input');name.value=food.canonical_name || '';name.placeholder='食材名称';name.setAttribute('aria-label','食材名称');amount.value=food.raw_amount?`${food.raw_amount}${food.raw_unit || ''}`:'适量';amount.placeholder='用量，如 200克';amount.setAttribute('aria-label','食材用量');const item={name,amount,original:food,originalAmount:amount.value,row};ingredientInputs.push(item);
      row.append(enhanceControl(name,{label:'食材名称'}),enhanceControl(amount,{label:'食材用量'}),iconButton('x','移除这项食材',()=>{row.remove();ingredientInputs.splice(ingredientInputs.indexOf(item),1);editor.state.change();}));ingredients.append(row);
    };
    for(const food of recipe.ingredients)addIngredient(food);method.append(ingredients,button('添加食材',()=>{addIngredient({canonical_name:'',scalable:true,allergens:[]});editor.state.change();ingredientInputs.at(-1).name.focus();},'quiet'));add(method,'steps','做法（每行一步）',recipe.steps.join('\n'),'textarea');controls.steps.rows=10;
    add(source,'source_text','来源原文',record.source_text,'textarea','可修正截图辨认错误，份数、耗时和用量依据需要能在这里找到。');controls.source_text.rows=10;add(source,'warnings','注意事项（每行一条）',(recipe.warnings || []).join('\n'),'textarea');
    this.inspectorTabs(editor,[['basic','基本信息'],['method','食材与做法'],['source','来源与提示']],{basic,method,source},editor.activeTab || 'basic');const form=node('form','center-editor-form');form.append(...editor.body.childNodes);editor.body.append(form);
    form.addEventListener('input',()=>{editor.state.change();this.status(editor,'有未保存的修改');});form.addEventListener('change',()=>editor.state.change());
    const payload=()=>({name:controls.name.value,description:recipe.description || '',source_text:controls.source_text.value,source_kind:record.source_kind,servings:controls.servings.value===''?null:Number(controls.servings.value),servings_evidence:controls.servings_evidence.value,prep_time_min:recipe.prep_time_min,cook_time_min:recipe.cook_time_min,total_time_min:controls.total_time_min.value===''?null:Number(controls.total_time_min.value),time_evidence:controls.time_evidence.value,ingredients:ingredientInputs.map(draftIngredient),steps:controls.steps.value.split('\n').map(step=>step.trim()).filter(Boolean),warnings:controls.warnings.value.split('\n').map(warning=>warning.trim()).filter(Boolean)});
    const persist=async publishNow=>{
      if(editor.state.busy)return;const invalid=Object.entries(controls).find(([,control])=>!control.checkValidity());
      if(invalid){const [key,input]=invalid;editor.tabs.select(['name','servings','servings_evidence','total_time_min','time_evidence'].includes(key)?'basic':key==='steps'?'method':'source');input.reportValidity();return;}
      const token=editor.state.begin();if(token===null)return;const interactive=[...editor.panel.querySelectorAll('input,textarea,select,button')];for(const input of interactive)input.disabled=true;this.status(editor,publishNow?'正在保存菜谱…':'正在保存草稿…');
      try{
        if(editor.state.dirty){record=await this.api(`/api/cookbook/${record.recipe_id}`,{method:'PUT',body:JSON.stringify({revision:record.revision,recipe:payload()})});editor.record=record;editor.state.dirty=false;}
        if(publishNow)record=await this.api(`/api/cookbook/${record.recipe_id}/publish`,{method:'POST',body:JSON.stringify({revision:record.revision})});
        if(!editor.state.finish(token,{saved:true}) || this.editor!==editor)return;editor.record=record;
        if(publishNow){this.disposeInspector();this.source='user';this.offset=0;this.navigation.replaceChildren();this.recipeNavigation();this.message('菜谱已保存，现在可以让小锅搭子带你做。');await this.refresh();}
        else{this.draftEditor(editor,record);this.status(editor,'草稿已保存。');this.refresh();}
      }catch(error){if(editor.state.finish(token))this.status(editor,error.message,true);}finally{if(this.editor===editor)for(const input of interactive)input.disabled=false;}
    };
    const actions=node('div','center-footer-actions');actions.append(button('移到回收站',()=>this.deleteRecipe(editor.record),'danger quiet'),button('保存草稿',()=>persist(false),'quiet'),button('确认保存菜谱',()=>persist(true),'primary'));editor.footer.replaceChildren(actions);form.onsubmit=event=>{event.preventDefault();persist(false);};
  }
  async deleteRecipe(record) {
    if(!record || this.editor?.state.busy)return;const editor=this.editor;
    const approved=await this.confirm(`把「${record.document.name}」移到回收站？`,`${editor?.state.dirty?'未保存的修改会丢失。':''}之后可在菜谱中心恢复，做过的饭和正在进行的烹饪记录会保留。`,'移到回收站','保留菜谱');if(!approved || (editor && this.editor!==editor))return;
    const token=editor?.state.begin();if(editor && token===null)return;
    const interactive=editor?[...editor.panel.querySelectorAll('input,textarea,select,button')]:[];for(const input of interactive)input.disabled=true;
    try{await this.api(`/api/cookbook/${record.recipe_id}?revision=${record.revision}`,{method:'DELETE'});if(editor && (!editor.state.finish(token,{saved:true}) || this.editor!==editor))return;this.disposeInspector();this.message('已移到回收站，可以随时恢复。');await this.refresh();}
    catch(error){editor?.state.finish(token);if(editor)this.status(editor,error.message,true);else this.message(error.message,true);}
    finally{if(this.editor===editor)for(const input of interactive)input.disabled=false;}
  }
  async restoreRecipe(item,control) {
    if(this.restorePending.has(item.recipe_id))return;this.restorePending.add(item.recipe_id);control.disabled=true;const pane=this.pane;
    try{await this.api(`/api/cookbook/${item.recipe_id}/restore`,{method:'POST',body:JSON.stringify({revision:item.revision})});if(!this.isCurrentPane(pane))return;this.message(item.deleted_from==='draft'?'草稿已恢复到“待核对”。':'菜谱已恢复到“我的菜谱”。');await this.refresh();}catch(error){if(this.isCurrentPane(pane))this.message(error.message,true);}finally{this.restorePending.delete(item.recipe_id);control.disabled=false;}
  }
  linkContentTab(bar,key) {this.content.id||=`center-page-panel-${++tabSequence}`;this.content.setAttribute('role','tabpanel');for(const tab of bar.children){tab.setAttribute('aria-controls',this.content.id);if(tab.dataset.key===key)this.content.setAttribute('aria-labelledby',tab.id);}}
  journalNavigation() {
    const navigation=tabs([['overview','营养概览'],['records','饮食记录'],['drafts','待核对'],['production','做饭记录'],['deleted','回收站']],this.journalTab,key=>{this.journalTab=key;this.linkContentTab(navigation.bar,key);this.journalContent();},{label:'食光手记内容'}),toolbar=node('div','center-journal-toolbar'),period=node('select','center-input');this.journalTabs=navigation;period.setAttribute('aria-label','记录时间范围');this.linkContentTab(navigation.bar,this.journalTab);
    for(const days of [7,30,90]){const option=node('option','',`最近 ${days} 天`);option.value=String(days);option.selected=days===this.days;period.append(option);}period.onchange=()=>{this.days=Number(period.value);this.refresh();};toolbar.append(enhanceControl(period,{label:'记录时间范围'}),iconButton('refresh-cw','刷新记录',()=>this.refresh()));this.navigation.append(navigation.bar,toolbar);
  }
  journalContent() {
    renderJournalContent(this);
  }
  setJournalTab(key) {this.journalTabs?.select(key);}
  openFoodLog(record) {
    return this.leave(async()=>{
      this.disposeInspector(false);const epoch=++this.detailGeneration;this.detailAbort=new AbortController();const editor=this.createInspector(record.id,record.document.title);editor.body.append(node('p','center-muted','正在读取记录…'));
      try{const current=await this.api(`/api/food-logs/${record.id}`,{signal:this.detailAbort.signal});if(epoch===this.detailGeneration && this.editor===editor)renderFoodLogEditor(this,editor,current);}catch(error){if(error.name!=='AbortError' && this.editor===editor)this.status(editor,error.message,true);}
    });
  }
  async deleteFoodLog(record) {
    const editor=this.editor;if(editor?.state.busy)return;const approved=await this.confirm(`移除「${record.document.title}」？`,'移入回收站后不计入个人营养统计，之后可以恢复。未保存修改会丢失。','移入回收站','保留记录');if(!approved || this.editor!==editor)return;
    const token=editor.state.begin();if(token===null)return;const controls=[...editor.panel.querySelectorAll('input,textarea,select,button')];for(const item of controls)item.disabled=true;
    try{await this.api(`/api/food-logs/${record.id}?revision=${record.revision}`,{method:'DELETE'});if(editor.state.finish(token,{saved:true}) && this.editor===editor){this.disposeInspector();this.message('饮食记录已移入回收站。');await this.refresh();}}
    catch(error){if(editor.state.finish(token))this.status(editor,error.message,true);}finally{if(this.editor===editor)for(const item of controls)item.disabled=false;}
  }
  async restoreFoodLog(record) {
    if(this.restorePending.has(record.id))return;this.restorePending.add(record.id);const pane=this.pane;
    try{await this.api(`/api/food-logs/${record.id}/restore`,{method:'POST',body:JSON.stringify({revision:record.revision})});if(this.isCurrentPane(pane)){this.message('饮食记录已恢复。');await this.refresh();}}catch(error){if(this.isCurrentPane(pane))this.message(error.message,true);}finally{this.restorePending.delete(record.id);}
  }
  journalRecords(entries,nutrition=false) {
    const list=node('div','center-journal-list');for(const entry of entries){const row=node('article','center-journal-row'),identity=node('div','center-journal-identity');identity.append(node('time','center-eyebrow',entry.date),node('h3','',entry.recipes.map(recipe=>recipe.name).join('、')),node('p','center-muted',`${entry.status==='completed'?'已完成':'已尝试'} · ${entry.diners?`${entry.diners} 人共享`:'人数待确认'} · ${entry.recipes.length} 道菜`));const actions=node('div','center-row-actions');actions.append(button(nutrition?'查看营养':'查看这顿饭',()=>this.openJournal(entry),'quiet'));row.append(identity,actions);if(nutrition)row.append(metricGrid(entry.nutrition?.meal_total));list.append(row);}
    if(!entries.length){const empty=node('div','center-empty');empty.append(node('h2','','认真做过的饭，都会留在这里'),node('p','center-muted','开始烹饪后会记下一次尝试。所有菜做好后，点“完成本次菜肴”，就能留下这顿饭。'));list.append(empty);}this.content.append(list);
  }
  openJournal(entry) {
    return this.leave(()=>{
      this.disposeInspector(false);const editor=this.createInspector(entry.id,'这顿饭');editor.eyebrow.textContent=entry.date;editor.title.textContent=entry.status==='completed'?'好好吃饭的一天':'这次厨房尝试';this.adaptInspector();const dishes=node('section','center-tab-panel'),nutrition=node('section','center-tab-panel'),feedback=node('section','center-tab-panel');
      dishes.append(node('p','center-muted',`${entry.diners?`${entry.diners} 人共享`:'人数待确认'} · ${entry.recipes.length} 道菜`));const list=node('ul','center-meal-recipes');for(const recipe of entry.recipes)list.append(node('li','',recipe.name));dishes.append(list);
      if(entry.status==='completed'){const panels={meal_total:node('div'),per_diner_reference:node('div')};panels.meal_total.append(metricGrid(entry.nutrition?.meal_total));panels.per_diner_reference.append(metricGrid(entry.nutrition?.per_diner_reference));const basis=tabs([['meal_total','整餐制作量'],['per_diner_reference','均分参考']],'meal_total',null,{label:'营养计算口径',panels});nutrition.append(basis.bar,...Object.values(panels),node('p','center-muted',`${entry.nutrition?.estimated?'含估算。':''}${entry.nutrition?.notice || ''}`));}else nutrition.append(node('p','center-muted','这餐还没有确认完成，暂不计入已完成餐食的营养汇总。'));
      const rating=node('select','center-input');for(const [score,label] of [['','暂不评分'],['5','很满意'],['4','挺好吃'],['3','还可以'],['2','待改进'],['1','没成功']]){const option=node('option','',label);option.value=score;option.selected=String(entry.rating || '')===score;rating.append(option);}const notes=node('textarea','center-input');notes.rows=6;notes.maxLength=2000;notes.value=entry.notes || '';notes.placeholder='记点下次想调整的口味、火候…';feedback.append(field('这次做菜的感受',rating),field('留给下次的笔记',notes));
      for(const input of [rating,notes]){input.addEventListener('input',()=>{editor.state.change();this.status(editor,'有未保存的感受');});input.addEventListener('change',()=>editor.state.change());}
      this.inspectorTabs(editor,[['dishes','这顿饭'],['nutrition','营养'],['feedback','做菜感受']],{dishes,nutrition,feedback});
      const save=button('保存感受',async()=>{const token=editor.state.begin();if(token===null)return;save.disabled=rating.disabled=notes.disabled=true;
        try{await this.api(`/api/journal/${entry.id}`,{method:'PATCH',body:JSON.stringify({rating:rating.value?Number(rating.value):null,notes:notes.value})});if(editor.state.finish(token,{saved:true}) && this.editor===editor){entry.rating=rating.value?Number(rating.value):null;entry.notes=notes.value;this.status(editor,'感受已保存。');this.refresh();}}
        catch(error){if(editor.state.finish(token))this.status(editor,error.message,true);}finally{if(this.editor===editor)save.disabled=rating.disabled=notes.disabled=false;}
      },'primary');const actions=node('div','center-footer-actions');actions.append(save);editor.footer.append(actions);editor.footer.hidden=true;editor.onTabChange=key=>{editor.footer.hidden=key!=='feedback';};
    });
  }
}
