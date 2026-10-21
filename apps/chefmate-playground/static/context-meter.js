import {node,estimateMarker} from './ui-components.js';
const valid=data=>data && Number.isInteger(data.round) && data.round>0 && Number.isFinite(data.chars) && data.chars>=0 && Array.isArray(data.categories);
const categoryKeys=['system','conversation','tools','tool_schemas','ui_state','memory','guidance'];
export function contextBreakdown(data) {
  const rows=(data?.categories || []).filter(item=>Number.isFinite(item.chars)&&item.chars>=0).map(item=>({...item,color:Math.max(0,categoryKeys.indexOf(item.key)),tokens:Number.isFinite(item.estimated_tokens)?item.estimated_tokens:null}));
  const byTokens=rows.every(item=>item.tokens!==null),total=rows.reduce((sum,item)=>sum+(byTokens?item.tokens:item.chars),0);
  return rows.map(item=>({...item,percent:total?((byTokens?item.tokens:item.chars)/total*100):0})).sort((left,right)=>right.percent-left.percent);
}
export function currentContextUsage(session) {
  if(!session)return null;
  const latest=[...(session.messages || [])].reverse().find(message=>message.role==='assistant');
  if(session.running || latest?.streaming){const current=latest?.streaming && valid(latest.context_usage)?latest.context_usage:session.active_turn?.context_usage;return valid(current)?current:null;}
  if(valid(latest?.context_usage))return latest.context_usage;
  if(latest?.context_usage!=null)return null;
  const saved=session.last_run?.result?.context_usage;
  return valid(saved) && (!latest || latest.turn_id===session.last_run?.id)?saved:null;
}
export class ContextMeter {
  constructor(sidebar) {
    this.host=node('details','context-meter');this.host.hidden=true;const summary=node('summary');this.heading=node('span','','本次上下文');this.total=node('strong');summary.append(this.heading,this.total);
    this.body=node('div','context-meter-body');this.host.append(summary,this.body);sidebar.insertBefore(this.host,sidebar.querySelector('.sidebar-footer'));this.key='';
  }
  render(session,enabled) {
    this.host.hidden=enabled!==true;if(enabled!==true)return;const data=currentContextUsage(session),key=JSON.stringify([session?.id,data]);if(this.key===key)return;this.key=key;
    this.total.replaceChildren();
    if(data)this.total.append(estimateMarker('≈ '),node('span','',`${Number(data.estimated_tokens || 0).toLocaleString()} tokens`));else this.total.textContent='等待请求';
    this.body.replaceChildren();
    if(!data){this.body.append(node('p','','下一次模型请求后显示分类占用。'));return;}
    this.body.append(node('p','context-meter-caption',`第 ${data.round} 次请求 · ${Number(data.chars).toLocaleString()} 字符`));
    const bar=node('div','context-meter-bar');bar.setAttribute('aria-hidden','true');const list=node('dl');
    contextBreakdown(data).forEach(item=>{
      const segment=node('span',`context-color-${item.color}`);segment.style.flexGrow=String(item.percent);segment.hidden=item.percent===0;bar.append(segment);
      const row=node('div'),label=node('dt'),dot=node('i',`context-meter-dot context-color-${item.color}`);dot.setAttribute('aria-hidden','true');label.append(dot,node('span','',item.label));
      const amount=node('dd'),quantity=node('span','context-meter-quantity');if(item.tokens===null)quantity.textContent='—';else quantity.append(estimateMarker('≈ '),node('span','',item.tokens.toLocaleString()));quantity.title=`${item.chars.toLocaleString()} 字符`;amount.append(quantity,node('span','context-meter-percent',`${Math.round(item.percent)}%`));row.append(label,amount);list.append(row);
    });
    const inspect=node('button','context-meter-inspect','查看请求轨迹');inspect.type='button';inspect.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('chefmate:inspect-context')));
    const caption=node('p','context-meter-caption');caption.append(estimateMarker('分类数值为估算 Token。'),'不含图片与服务端封装。');
    this.body.append(bar,list,caption,inspect);if(data.images)this.body.append(node('p','context-meter-caption',`${data.images} 张图片，另行计量`));
  }
}
