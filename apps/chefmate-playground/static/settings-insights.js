import {node} from './ui-components.js';
import {enhanceControl} from './form-controls.js';

export const tokenText=value=>Number.isFinite(value)?Math.round(value).toLocaleString('zh-CN'):'未报告';
const dayMilliseconds=86400000;
const dateNumber=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)?Date.parse(`${value}T00:00:00Z`):NaN;
export function usageCalendar(data) {
  const end=dateNumber(data.period?.to);if(!Number.isFinite(end))return {days:[],offset:0,columns:0,clipped:false};
  const given=dateNumber(data.period?.from),requested=Number.isFinite(given)?given:end-364*dayMilliseconds,start=Math.max(requested,end-365*dayMilliseconds),byDate=new Map((data.daily || []).map(day=>[day.date,day]));
  const days=[];
  for(let time=start;time<=end;time+=dayMilliseconds){const date=new Date(time).toISOString().slice(0,10),day=byDate.get(date),calls=Number.isFinite(day?.model_calls)?day.model_calls:0,total=day?.known_totals?.total_tokens??day?.total_tokens;
    const report=day?.coverage?.total_tokens,known=report?.known??report?.reported??(Number.isFinite(total)?calls:0),status=calls===0?'empty':!Number.isFinite(total)?'unknown':known<calls?'partial':'complete';
    days.push({date,time,calls,value:Number.isFinite(total)?total:null,status});}
  const maximum=Math.max(1,...days.map(day=>day.value || 0));for(const day of days)day.level=day.value>0?Math.min(4,Math.max(1,Math.ceil(Math.sqrt(day.value/maximum)*4))):0;
  const offset=(new Date(start).getUTCDay()+6)%7;
  return {days,offset,columns:Math.ceil((offset+days.length)/7),clipped:start>requested};
}
export function usageDayLabel(day) {
  if(day.status==='empty')return `${day.date} · 没有模型请求`;
  if(day.status==='unknown')return `${day.date} · ${day.calls} 次请求，Token未报告`;
  return `${day.date} · ${tokenText(day.value)} Token${day.status==='partial'?'（已知小计）':''} · ${day.calls} 次请求`;
}
function renderUsageCalendar(data) {
  const calendar=usageCalendar(data),section=node('section','usage-calendar'),heading=node('div','usage-calendar-heading');heading.append(node('h2','','每日用量'),node('span','',calendar.days.length?`${calendar.days[0].date} — ${calendar.days.at(-1).date}`:'暂无统计日期'));section.append(heading);
  if(!calendar.days.length){section.append(node('p','settings-note','开始一次对话后，这里会按天显示模型用量。'));return section;}
  const scroller=node('div','usage-calendar-scroll'),plot=node('div','usage-calendar-plot');plot.style.setProperty('--heatmap-columns',calendar.columns);
  const months=node('div','usage-calendar-months'),labels=node('div','usage-calendar-weekdays');labels.setAttribute('aria-hidden','true');for(const [label,position]of [['一',1],['三',3],['五',5]]){const item=node('span','',label);item.style.gridRow=String(position);labels.append(item);}
  let previousMonth='';const monthMarks=new Map();calendar.days.forEach((day,index)=>{const month=day.date.slice(0,7);if(month!==previousMonth){const column=Math.floor((calendar.offset+index)/7)+1;let mark=monthMarks.get(column);if(!mark){mark=node('span');mark.style.gridColumn=String(column);months.append(mark);monthMarks.set(column,mark);}mark.textContent=`${Number(day.date.slice(5,7))}月`;previousMonth=month;}});
  let previousMark=null;for(const [column,mark] of monthMarks){if(previousMark && column-previousMark.column<3)previousMark.mark.remove();previousMark={column,mark};}
  const grid=node('div','usage-calendar-grid');grid.setAttribute('role','group');grid.setAttribute('aria-label','每日Token用量，方向键切换日期');for(let index=0;index<calendar.offset;index++){const blank=node('span','usage-calendar-spacer');blank.setAttribute('aria-hidden','true');grid.append(blank);}
  const description=node('p','usage-calendar-day',usageDayLabel(calendar.days.at(-1)));description.setAttribute('role','status');description.setAttribute('aria-live','polite');const buttons=[];
  const show=index=>{description.textContent=usageDayLabel(calendar.days[index]);buttons.forEach((button,position)=>{button.tabIndex=position===index?0:-1;button.setAttribute('aria-pressed',String(position===index));});};
  calendar.days.forEach((day,index)=>{const cell=node('button',`usage-calendar-cell level-${day.level} is-${day.status}`);cell.type='button';cell.setAttribute('aria-label',usageDayLabel(day));cell.title=usageDayLabel(day);cell.tabIndex=index===calendar.days.length-1?0:-1;cell.setAttribute('aria-pressed',String(index===calendar.days.length-1));
    cell.addEventListener('click',()=>show(index));cell.addEventListener('focus',()=>show(index));cell.addEventListener('pointerenter',()=>{description.textContent=usageDayLabel(day);});
    cell.addEventListener('keydown',event=>{const delta={ArrowLeft:-7,ArrowRight:7,ArrowUp:-1,ArrowDown:1}[event.key];let next;if(delta)next=index+delta;else if(event.key==='Home')next=0;else if(event.key==='End')next=calendar.days.length-1;else return;event.preventDefault();next=Math.min(calendar.days.length-1,Math.max(0,next));show(next);buttons[next].focus({preventScroll:true});buttons[next].scrollIntoView?.({block:'nearest',inline:'nearest'});});buttons.push(cell);grid.append(cell);});
  plot.append(months,labels,grid);scroller.append(plot);section.append(scroller,description);queueMicrotask(()=>{if(scroller.isConnected)scroller.scrollLeft=scroller.scrollWidth;});const legend=node('div','usage-calendar-legend');legend.append(node('span','','少'));for(let index=0;index<5;index++){const swatch=node('i',`usage-calendar-cell level-${index}`);swatch.setAttribute('aria-hidden','true');legend.append(swatch);}legend.append(node('span','','多'),node('i','usage-calendar-cell is-unknown'),node('span','','未报告'),node('i','usage-calendar-cell level-2 is-partial'),node('span','','部分报告'));section.append(legend);
  if(calendar.clipped)section.append(node('p','settings-note','热力图显示最近366天，汇总仍包含所选范围的全部记录。'));return section;
}
function metric(label,value,caption='') {
  const item=node('div','insight-metric');item.append(node('dt','',label),node('dd','',tokenText(value)));
  if(caption)item.append(node('small','',caption));return item;
}
function row(label,value) {const item=node('div','status-row');item.append(node('span','',label),node('span','',value));return item;}
export class SettingsInsights {
  constructor({host,api}) {this.host=host;this.api=api;this.sequence=0;this.abort=null;this.days='365';}
  dispose() {this.sequence++;this.abort?.abort();}
  async show(section) {
    const epoch=++this.sequence;this.abort?.abort();this.abort=new AbortController();this.host.replaceChildren(node('p','settings-note','正在读取…'));
    try{
      const data=await this.api(section==='usage'?`/api/usage?days=${this.days}`:'/api/about',{signal:this.abort.signal});
      if(epoch!==this.sequence || !this.host.isConnected)return;
      this.host.replaceChildren();if(section==='usage')this.usage(data);else this.about(data);
    }catch(error){if(error.name!=='AbortError' && epoch===this.sequence)this.host.replaceChildren(node('p','settings-error',error.message));}
  }
  usage(data) {
    const toolbar=node('div','insight-toolbar'),period=node('select');period.setAttribute('aria-label','Token统计时间范围');
    for(const [key,label] of [['7','最近 7 天'],['30','最近 30 天'],['365','最近一年'],['all','全部记录']]){const option=node('option','',label);option.value=key;option.selected=key===this.days;period.append(option);}
    period.onchange=()=>{this.days=period.value;this.show('usage');};toolbar.append(enhanceControl(period,{label:'Token统计时间范围'}));this.host.append(toolbar);
    this.host.append(renderUsageCalendar(data));const summary=data.summary || {},totals=summary.known_totals || summary;
    const caption=key=>{const report=summary.coverage?.[key],known=report?.known??report?.reported,parts=[];if(report && known<report.calls)parts.push(`已知小计 · ${known}/${report.calls} 次请求`);if(key==='total_tokens' && summary.total_derived_calls)parts.push(`${summary.total_derived_calls} 次按输入+输出计算`);if(key==='cached_tokens')parts.push('包含在输入中');return parts.join(' · ');};
    const metrics=node('dl','insight-metrics');metrics.append(metric('输入 Token',totals.input_tokens,caption('input_tokens')),metric('输出 Token',totals.output_tokens,caption('output_tokens')),metric('缓存读取',totals.cached_tokens,caption('cached_tokens')),metric('总 Token',totals.total_tokens,caption('total_tokens')));this.host.append(metrics);
    const coverage=summary.coverage?.input_tokens;
    this.host.append(node('p','settings-note',`${summary.model_calls ?? 0} 次模型请求${coverage?` · ${coverage.reported ?? 0} 次报告输入用量`:''}。同次输入与输出完整时相加得到总量；其余缺失项保留未知。`));
    const today=data.today?.known_totals || data.today?.summary?.known_totals || data.today || {};
    const lifetime=data.all_time?.known_totals || data.all_time?.summary?.known_totals || data.all_time || {};
    this.host.append(node('h2','','使用概况'),row('今日已知 Token',tokenText(today.total_tokens)),row('累计已知 Token',tokenText(lifetime.total_tokens)));
    this.host.append(node('h2','insight-section-title','按模型统计'));
    for(const model of data.models || []){const title=node('h3','insight-model-name',model.model && model.model!=='unknown'?model.model:'历史模型未记录'),usage=model.known_totals || model.summary?.known_totals || model;this.host.append(title,row('输入 / 输出',`${tokenText(usage.input_tokens)} / ${tokenText(usage.output_tokens)}`),row('缓存读取',tokenText(usage.cached_tokens)));}
    if(!data.models?.length)this.host.append(node('p','settings-note','还没有模型请求记录。开始一次对话后，这里会显示服务端报告的用量。'));
    this.host.append(node('p','settings-note','统计保存在本机。删除聊天会保留用量账本；历史中未记录的模型身份不会推断。'));
  }
  about(data) {
    const intro=node('div','about-intro'),image=node('img');image.src='/static/brand/friendly-pot.svg';image.alt='';intro.append(image,node('h2','',data.name || 'ChefMate'),node('p','',data.subtitle || '您的个性化烹饪助手'));this.host.append(intro);
    this.host.append(row('版本',data.version || '未提供'),row('运行方式',data.runtime || '本地工作台'),row('基础菜谱',`${data.recipe_count ?? '—'} 道`),row('系统组件',`${data.component_count ?? '—'} 个`));
    this.host.append(node('h2','insight-section-title','数据与隐私'),node('p','settings-note','聊天、个人菜谱、饮食记录和长期记忆保存在本机。调用模型时，会发送本次对话需要的内容与图片。API Key 不回显。'));
    this.host.append(node('h2','','营养数据'),node('p','settings-note','制作量与个人摄入分别记录。单人餐完成后按配方自动登记，多人餐先确认本人份额；照片记餐需核对后保存，未知营养留空。'));
  }
}
