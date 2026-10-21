import {node} from './ui-components.js';
const valid=data=>data && Number.isInteger(data.round) && data.round>0 && Number.isFinite(data.chars) && data.chars>=0 && Array.isArray(data.categories);
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
    this.host.hidden=!enabled;if(!enabled)return;const data=currentContextUsage(session),key=JSON.stringify([session?.id,data]);if(this.key===key)return;this.key=key;
    this.total.textContent=data?`≈ ${Number(data.estimated_tokens || 0).toLocaleString()} tokens`:'等待请求';this.body.replaceChildren();
    if(!data){this.body.append(node('p','','下一次模型请求后显示分类占用。'));return;}
    this.body.append(node('p','context-meter-caption',`第 ${data.round} 次请求 · ${Number(data.chars).toLocaleString()} 字符`));
    const bar=node('div','context-meter-bar');bar.setAttribute('aria-hidden','true');const list=node('dl');
    (data.categories || []).forEach((item,index)=>{const segment=node('span',`context-color-${index%7}`);segment.style.flexGrow=String(item.chars || 0);bar.append(segment);const row=node('div');row.append(node('dt','',item.label),node('dd','',`${Number(item.chars).toLocaleString()} 字符`));list.append(row);});
    this.body.append(bar,list,node('p','context-meter-caption','Token 为粗略估算，不含图片和服务端封装。'));if(data.images)this.body.append(node('p','context-meter-caption',`${data.images} 张图片，另行计量`));
  }
}
