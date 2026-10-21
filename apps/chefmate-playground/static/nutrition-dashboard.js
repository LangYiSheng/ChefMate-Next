import {node,estimateMarker} from './ui-components.js';
const macros=[['calories_kcal','热量','kcal'],['protein_g','蛋白质','g'],['fat_g','脂肪','g'],['carbohydrate_g','碳水','g']];
const mealLabels={breakfast:'早餐',lunch:'午餐',dinner:'晚餐',snack:'加餐',unspecified:'一餐'};
const number=value=>Number.isFinite(value)?Math.round(value*10)/10:null;
function button(text,action,style='quiet'){const el=node('button',`center-button ${style}`,text);el.type='button';el.onclick=action;return el;}
export function intakeMetric(summary,key) {
  const full=number(summary?.total?.[key]),known=number(summary?.known_total?.[key]);
  return {value:full??known,partial:full===null && known!==null,coverage:summary?.coverage?.[key]};
}
export function nutritionHero(summary,{title='今天的营养',className=''}={}) {
  const section=node('section',`intake-hero ${className}`),header=node('div','intake-hero-heading'),count=node('span','center-muted',`已记录 ${summary?.record_count || 0} 餐`);if(summary?.estimated)count.append(estimateMarker(' · 含估算'));header.append(node('h2','',title),count);section.append(header);
  const metrics=node('dl','intake-metrics');
  for(const [key,label,unit] of macros){const item=intakeMetric(summary,key),cell=node('div',key==='calories_kcal'?'intake-energy':'');cell.append(node('dt','',label));const amount=node('dd','',item.value===null?'—':String(item.value));if(item.value!==null)amount.append(node('small','',unit));cell.append(amount);
    const coverage=node('span','intake-coverage',item.partial?'已知部分':item.value===null?summary?.record_count?'待补充营养':'记录一餐后显示':'已记录');if(item.value!==null && !item.partial && summary.estimated)coverage.replaceChildren(estimateMarker('含估算'));cell.append(coverage);metrics.append(cell);}
  section.append(metrics,node('p','center-footnote','只统计已确认的个人饮食。未记录不代表没有吃，未知营养不会填成零。'));return section;
}
export function foodLogRows(centers,records,{deleted=false}={}) {
  const list=node('div','food-log-list');
  for(const record of records){const row=node('article','food-log-row'),copy=node('div'),title=record.document.title;copy.append(node('time','center-eyebrow',`${record.date} · ${mealLabels[record.document.meal_type] || '一餐'}`),node('h3','',title),node('p','center-muted',record.document.items.map(item=>item.name).join('、')));
    const actions=node('div','center-row-actions');
    if(deleted)actions.append(button('恢复记录',()=>centers.restoreFoodLog(record)));
    else{const energy=intakeMetric(record.nutrition,'calories_kcal');const metric=node('strong','food-log-energy',energy.value===null?'热量待补充':`${energy.value} kcal${energy.partial?' · 部分':''}`),basis=node('span','food-log-basis',record.status==='draft'?'待核对 · 未计入统计':'已确认');if(record.status!=='draft' && record.nutrition.estimated)basis.append(estimateMarker(' · 含估算'));copy.append(metric,basis);actions.append(button(record.status==='draft'?'核对记录':'查看记录',()=>centers.openFoodLog(record)));}
    row.append(copy,actions);list.append(row);
  }
  if(!records.length){const empty=node('div','center-empty');empty.append(node('h2','',deleted?'没有已移除的记录':'从记录今天的一餐开始'),node('p','center-muted',deleted?'移除的个人饮食记录可以在这里恢复。':'拍张饭菜照片，或告诉小锅搭子吃了什么。核对后就能看到这一份的营养。'));list.append(empty);}return list;
}
export function renderJournalContent(centers) {
  const data=centers.journalData;if(!data)return;const host=centers.content,summary=data.summary,intake=summary.intake || {},today=intake.today || summary.today?.intake || {};host.replaceChildren();
  const appendRecords=(page,options={})=>{host.append(foodLogRows(centers,page?.items || [],options));if(page?.total>page?.items?.length)host.append(node('p','center-footnote',`显示最近 ${page.items.length} 条，共 ${page.total} 条；营养汇总包含所选时间内的全部已确认记录。`));};
  if(centers.journalTab==='drafts'){host.append(node('p','center-muted','核对自己吃的份量与估算依据，确认后才计入个人营养。'));appendRecords(data.drafts);return;}
  if(centers.journalTab==='deleted'){appendRecords(data.deleted_food_logs,{deleted:true});return;}
  if(centers.journalTab==='records'){host.append(nutritionHero(intake,{title:'这段时间的个人饮食'}));appendRecords(data.food_logs);return;}
  if(centers.journalTab==='production'){
    host.append(node('h2','center-section-title','做过的饭'),node('p','center-muted','这里记录整餐制作量，和个人实际吃了多少分开保存。'));const stats=node('dl','center-stats');for(const [label,count] of [['完成餐次',summary.completed_meals],['完成菜品',summary.completed_dishes],['尝试菜谱',summary.unique_recipes]]){const item=node('div');item.append(node('dt','',label),node('dd','',String(count)));stats.append(item);}host.append(stats);centers.journalRecords(data.entries);return;
  }
  host.append(nutritionHero(today));
  if((data.drafts?.total || 0)>0){const pending=node('div','intake-pending');pending.append(node('span','',`${data.drafts.total} 份饮食草稿等你核对`),button('去核对',()=>centers.setJournalTab('drafts')));host.append(pending);}
  const trendHeader=node('div','intake-section-heading');trendHeader.append(node('h2','center-section-title','个人饮食趋势'),node('span','center-muted','每日已知热量 · kcal'));host.append(trendHeader);
  const days=(intake.days || []).slice(-14),max=Math.max(1,...days.map(day=>intakeMetric(day,'calories_kcal').value || 0)),chart=node('div','intake-chart');chart.setAttribute('role','img');chart.setAttribute('aria-label',days.map(day=>`${day.date} ${intakeMetric(day,'calories_kcal').value??'未知'} kcal`).join('；'));
  for(const day of days){const column=node('div','intake-chart-column'),metric=intakeMetric(day,'calories_kcal'),bar=node('div','intake-chart-bar');bar.style.height=`${metric.value===null?0:Math.max(3,metric.value/max*100)}px`;column.append(node('span','',metric.value===null?'—':String(metric.value)),bar,node('time','',day.date.slice(5)));chart.append(column);}host.append(chart);
  const recent=node('div','intake-section-heading');recent.append(node('h2','center-section-title','最近吃了什么'),button('全部记录',()=>centers.setJournalTab('records')));host.append(recent,foodLogRows(centers,(data.food_logs?.items || []).slice(0,5)));
  const cooking=node('div','intake-production-link');cooking.append(node('span','center-muted',`这段时间完成 ${summary.completed_meals} 餐，做过 ${summary.completed_dishes} 道菜`),button('查看做饭记录',()=>centers.setJournalTab('production')));host.append(cooking);
}
