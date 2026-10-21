import {node} from './ui-components.js';
import {enhanceControl} from './form-controls.js';

export const tokenText=value=>Number.isFinite(value)?Math.round(value).toLocaleString('zh-CN'):'未报告';
function metric(label,value,caption='') {
  const item=node('div','insight-metric');item.append(node('dt','',label),node('dd','',tokenText(value)));
  if(caption)item.append(node('small','',caption));return item;
}
function row(label,value) {const item=node('div','status-row');item.append(node('span','',label),node('span','',value));return item;}
export class SettingsInsights {
  constructor({host,api}) {this.host=host;this.api=api;this.sequence=0;this.abort=null;this.days='30';}
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
    for(const [key,label] of [['7','最近 7 天'],['30','最近 30 天'],['all','全部记录']]){const option=node('option','',label);option.value=key;option.selected=key===this.days;period.append(option);}
    period.onchange=()=>{this.days=period.value;this.show('usage');};toolbar.append(enhanceControl(period,{label:'Token统计时间范围'}));this.host.append(toolbar);
    const summary=data.summary || {},totals=summary.known_totals || summary;
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
    this.host.append(node('h2','','营养数据'),node('p','settings-note','菜谱制作量与个人饮食分别记录。照片识别、份量和营养估算会标明依据，核对后再保存；未知值不会填成零。'));
  }
}
