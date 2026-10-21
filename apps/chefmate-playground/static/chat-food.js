import {node} from './ui-components.js';
import {intakeMetric} from './nutrition-dashboard.js';
const foodTools=new Set(['create_food_log_draft','revise_food_log_draft','get_food_log','confirm_food_log']);
const macros=[['calories_kcal','热量','kcal'],['protein_g','蛋白质','g'],['fat_g','脂肪','g'],['carbohydrate_g','碳水','g']];
const meals={breakfast:'早餐',lunch:'午餐',dinner:'晚餐',snack:'加餐',unspecified:'饮食记录'};
const value=number=>Number.isFinite(number)?String(Math.round(number*10)/10):'—';
function action(label,run,disabled=false){const button=node('button','chat-food-action',label);button.type='button';button.disabled=disabled;button.onclick=run;return button;}
export function foodPresentations(calls=[]) {
  const records=new Map();let search=null;
  for(const call of calls){if(call.result?.status!=='ok')continue;if(foodTools.has(call.name)){const record=call.result.record || call.result.draft;if(record?.id)records.set(record.id,record);}if(call.name==='search_food_logs')search=call.result;}
  return {records:[...records.values()],search};
}
function metrics(summary) {
  const grid=node('dl','chat-food-metrics');
  for(const [key,label,unit] of macros){const item=intakeMetric(summary,key),cell=node('div');cell.append(node('dt','',label));const amount=node('dd','',value(item.value));if(item.value!==null)amount.append(node('small','',unit));cell.append(amount);if(item.partial)cell.append(node('small','chat-food-unknown','已知部分'));grid.append(cell);}return grid;
}

export class ChatFood {
  constructor({api,sessionId,ask,notify,onChange,canAct}){Object.assign(this,{api,sessionId,ask,notify,onChange,canAct});this.cache=new Map();this.pending=new Map();this.saving=new Set();}
  remember(record){const old=this.cache.get(record.id);if(!old || old.record.revision<=record.revision)this.cache.set(record.id,{record,at:Date.now()});if(this.cache.size>100)this.cache.delete(this.cache.keys().next().value);return this.cache.get(record.id).record;}
  async read(id){if(!this.pending.has(id)){const request=this.api(`/api/food-logs/${encodeURIComponent(id)}`).then(record=>this.remember(record)).finally(()=>this.pending.delete(id));this.pending.set(id,request);}return this.pending.get(id);}
  render(message,{sessionId,canAct=true,recordOwners=null,index=0}={}) {
    const presentation=foodPresentations(message.tool_calls),root=node('div','chat-food-content');
    for(const receipt of presentation.records){if(recordOwners && recordOwners.get(receipt.id)!==index)continue;const host=node('section','chat-food-block');host.setAttribute('aria-label','饮食记录');root.append(host);
      if(receipt.document)this.remember(receipt);const cached=this.cache.get(receipt.id);const fresh=cached && cached.record.revision>=receipt.revision;
      this.record(host,fresh?cached.record:receipt,{sessionId,canAct,loading:!fresh});
      if(!fresh || Date.now()-cached.at>30000)this.read(receipt.id).then(record=>{if(host.isConnected && this.sessionId()===sessionId)this.record(host,record,{sessionId,canAct});}).catch(error=>{if(host.isConnected && this.sessionId()===sessionId){host.append(node('p','chat-food-note',error.message || '这份记录暂时无法读取。'));}});
    }
    if(presentation.search)root.append(this.summary(presentation.search,{sessionId,canAct}));return root.children.length?root:null;
  }
  record(host,record,{sessionId,canAct,loading=false,notice=''}={}) {
    host.replaceChildren();host.dataset.viewKind='food_log';const document=record.document,heading=node('header','chat-food-heading'),identity=node('div');identity.append(node('p','chat-food-kicker',`${record.date || ''} · ${meals[document?.meal_type] || '饮食记录'}`),node('h3','',document?.title || record.title || '这顿饭'));heading.append(identity,node('span',`chat-food-state ${record.status==='saved'?'saved':''}`,record.status==='saved'?'已记录':'待确认'));host.append(heading);
    if(document){const foods=node('ul','chat-food-items');for(const item of document.items){const row=node('li'),copy=node('span');copy.append(node('strong','',item.name),node('small','',item.portion || '份量待补充'));row.append(copy,node('span','chat-food-item-energy',Number.isFinite(item.nutrition?.calories_kcal)?`${value(item.nutrition.calories_kcal)} kcal`:'营养待补充'));foods.append(row);}host.append(foods);}
    else host.append(node('p','chat-food-note',loading?'正在读取食物与份量…':`${record.food_count || 0} 项食物`));
    host.append(metrics(record.nutrition));host.append(node('p','chat-food-note',record.status==='saved'?(record.nutrition?.estimated?'营养含估算；已计入个人饮食。':'已计入个人饮食。'):'尚未计入营养统计。可以直接告诉我哪里要改。'));
    if(document){const details=node('details','chat-food-source');details.append(node('summary','','来源与估算依据'));for(const item of document.items){if(!item.evidence && !item.assumptions?.length)continue;details.append(node('h4','',item.name));if(item.evidence)details.append(node('p','',item.evidence));for(const assumption of item.assumptions || [])details.append(node('p','',assumption));}if(document.notes)details.append(node('p','',document.notes));host.append(details);}
    const actions=node('div','chat-food-actions'),locked=!canAct || this.saving.has(record.id) || loading;
    if(record.status==='draft')actions.append(action(this.saving.has(record.id)?'正在确认…':'确认记录',()=>this.publish(host,record,{sessionId,canAct}),locked),action('修改这顿饭',()=>this.ask(`我想调整${record.date}的“${document?.title || record.title || '这顿饭'}”：`,'edit'),locked));
    actions.append(action('查看最近营养',()=>this.ask('请查看我最近7天已确认饮食的营养统计。','send'),!canAct));host.append(actions);if(notice)host.append(node('p','chat-food-note',notice));
  }
  async publish(host,record,context){
    if(this.saving.has(record.id) || this.sessionId()!==context.sessionId || !context.canAct || this.canAct?.()===false)return;
    const identity=record.id;let notice='';this.saving.add(identity);this.record(host,record,context);
    try{record=await this.api(`/api/food-logs/${encodeURIComponent(identity)}/publish`,{method:'POST',body:JSON.stringify({revision:record.revision})});this.remember(record);notice='这顿饭记好了。';}
    catch(error){this.cache.delete(identity);notice=error.message || '确认失败，请重试。';try{record=await this.read(identity);if(record.status==='saved')notice='这顿饭已经记录好了。';}catch{}}
    finally{this.saving.delete(identity);if(this.sessionId()===context.sessionId){if(host.isConnected)this.record(host,record,{...context,notice});else this.onChange?.(context.sessionId);}}
  }
  summary(result,{sessionId,canAct}){
    const summary=result.summary || {},host=node('section','chat-food-block chat-food-summary');host.dataset.viewKind='nutrition_summary';host.setAttribute('aria-label','个人营养统计');const period=summary.period || {},heading=node('header','chat-food-heading'),identity=node('div');identity.append(node('p','chat-food-kicker',[period.from,period.to].filter(Boolean).join(' — ') || '已确认的个人饮食'),node('h3','','最近的营养'));heading.append(identity,node('span','chat-food-state',`${summary.record_count || 0} 餐`));host.append(heading,metrics(summary));
    host.append(node('p','chat-food-note',`${summary.estimated?'含估算。':''}未知营养未填零，未记录不代表没有吃。`));
    const rows=node('div','chat-food-search');for(const item of result.items || []){const row=action(`${item.date} · ${item.title}`,()=>this.ask(`请查看${item.date}的“${item.title}”这份饮食记录的详情。`,'send'),!canAct);row.classList.add('chat-food-record-link');rows.append(row);}host.append(rows);return host;
  }
}
