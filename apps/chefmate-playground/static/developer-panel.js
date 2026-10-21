import {icon} from './icons.js';
import {enhanceControl} from './form-controls.js';
import {TOOL_NAMES} from './presentation.js';

const node=(tag,cls,text)=>{const item=document.createElement(tag);if(cls)item.className=cls;if(text!=null)item.textContent=String(text);return item;};
const labels={system:'系统提示',user:'用户输入',assistant:'助手输出',tool:'工具回调',idea:'交流',planning:'选菜',prep:'备菜',cooking:'烹饪',completed:'完成'};
const fieldLabels={revision:'任务版本',presentation_revision:'展示版本',current_actions:'可执行操作',allowed_transitions:'可进入阶段',blockers:'阻塞原因',effect:'操作类型',auto_display:'自动展示',parameter_integrity:'参数完整性',stage:'当前阶段',preferences:'用餐条件',components:'界面内容',recent_actions:'最近操作',pending_question:'待回答问题',last_answer:'用户答案',recipe_id:'菜谱 ID',recipe_ids:'菜谱 ID',item_id:'食材项目 ID',expected_revision:'预期版本',status:'状态',message:'说明',items:'项目',actions:'界面更新',ui_snapshot:'任务状态',values:'条件',servings:'用餐人数',ingredient_terms:'想吃的食材',exclude_ingredient_terms:'不吃的食材',max_total_time_min:'时间上限',available_tools:'可用厨具',dietary_notes:'饮食说明',question:'问题',option_ids:'选择',text:'文字',title:'标题'};
const tokenValue=value=>Number.isFinite(value)?value.toLocaleString('zh-CN'):'未报告';
export function traceRequests(capture) {
  return (capture?.debug_trace || []).map((request,index)=>({request,round:request.round || index+1,usage:(capture.usage?.calls || []).find(call=>call.round===(request.round || index+1)) || null}));
}

function details(title,build,{open=false}={}) {
  const item=node('details','dev-disclosure'),summary=node('summary','',title);item.append(summary);
  let built=false;
  const fill=()=>{if(!built&&item.open){built=true;item.append(build());}};
  item.addEventListener('toggle',fill);item.open=open;fill();return item;
}
function paragraph(text){return node('p','dev-text',text);}
function valueView(value) {
  if(value===null)return node('span','dev-null','未提供');
  if(typeof value!=='object')return paragraph(typeof value==='boolean'?(value?'是':'否'):value);
  if(Array.isArray(value)){
    const list=node('div','dev-value-list');
    value.forEach((item,index)=>list.append(typeof item==='object'&&item!==null?details(`第 ${index+1} 项`,()=>valueView(item)):paragraph(item)));
    if(!value.length)list.append(node('span','dev-null','空列表'));return list;
  }
  const list=node('dl','dev-fields');
  for(const [key,item]of Object.entries(value)){
    list.append(node('dt','',fieldLabels[key]||key));
    const copy=node('dd');
    copy.append(item&&typeof item==='object'?details(Array.isArray(item)?`${item.length} 项`:'查看内容',()=>valueView(item)):valueView(item));list.append(copy);
  }
  return list;
}
function resolveSchema(schema,root){
  if(schema?.$ref){const key=schema.$ref.split('/').at(-1);return root.$defs?.[key]||root.definitions?.[key]||schema;}
  return schema||{};
}
function schemaView(schema,root=schema) {
  schema=resolveSchema(schema,root);
  const list=node('div','dev-schema');
  if(schema.anyOf){schema.anyOf.forEach(branch=>list.append(schemaView(branch,root)));return list;}
  const required=new Set(schema.required||[]);
  for(const [key,raw]of Object.entries(schema.properties||{})){
    const field=resolveSchema(raw,root),row=node('section','dev-parameter');
    const title=node('div','dev-parameter-title');title.append(node('code','',key),node('span','dev-type',`${required.has(key)?'必填 · ':''}${field.type|| (field.anyOf?'可选类型':'对象')}`));row.append(title);
    if(field.description)row.append(paragraph(field.description));
    if(field.enum)row.append(node('p','dev-constraint',`可选：${field.enum.join(' · ')}`));
    const constraints=[['minimum','最小'],['maximum','最大'],['minLength','最短字符'],['maxLength','最长字符'],['minItems','最少项'],['maxItems','最多项']].filter(([name])=>field[name]!=null).map(([name,label])=>`${label} ${field[name]}`);
    if(constraints.length)row.append(node('p','dev-constraint',constraints.join(' · ')));
    const nested=field.items||field;
    if(nested.$ref||nested.properties||nested.anyOf)row.append(details('字段结构',()=>schemaView(nested,root)));
    list.append(row);
  }
  if(!list.children.length)list.append(paragraph(schema.type==='null'?'不填写':schema.type||'无参数'));
  return list;
}
function messageView(message,index) {
  const article=node('article',`dev-message dev-role-${message.role}`);
  const heading=node('div','dev-message-heading',`${index+1} · ${labels[message.role]||message.role}${message.name?` · ${message.name}`:''}`);article.append(heading);
  if(message.role==='tool'&&message.result)article.append(valueView(message.result));
  else if(message.user_text!=null)article.append(paragraph(message.user_text));
  else if(message.content)article.append(paragraph(typeof message.content==='string'?message.content:'图片及多模态内容'));
  if(message.ui_context)article.append(details('本次附带的可信任务状态',()=>valueView(message.ui_context)));
  if(message.images?.length)article.append(node('p','dev-constraint',`${message.images.length} 张图片 · 仅展示元数据，图片字节已省略`),details('图片信息',()=>valueView(message.images)));
  if(message.tool_calls?.length)article.append(details(`请求调用 ${message.tool_calls.length} 项工具`,()=>valueView(message.tool_calls)));
  if(message.user_text!=null&&message.content!==message.user_text)article.append(details('查看实际消息全文',()=>paragraph(message.content)));
  return article;
}

export class DeveloperPanel {
  constructor({api,sessionId,session,enabled}) {
    this.api=api;this.sessionId=sessionId;this.session=session;this.enabled=enabled;this.host=document.getElementById('query-content');
    this.mode='tools';this.chosen=null;this.payload=null;this.owner=null;this.sequence=0;this.lastFetch=0;this.timer=null;this.busy=false;
    if(!document.querySelector('link[data-developer-style]')){const style=node('link');style.rel='stylesheet';style.href='/static/developer-panel.css';style.dataset.developerStyle='';document.head.append(style);}
  }
  render({force=false}={}) {
    if(!this.enabled()) {this.sequence++;this.payload=null;return;}
    if(this.host.closest('[hidden]'))return;
    const id=this.sessionId();
    if(id!==this.owner){this.owner=id;this.payload=null;this.chosen=null;this.lastFetch=0;this.busy=false;}
    if(this.payload)this.paint();
    else this.host.replaceChildren(node('p','dev-empty','正在读取智能体调试信息…'));
    clearTimeout(this.timer);
    const due=force?0:Math.max(0,300-(performance.now()-this.lastFetch));
    this.timer=setTimeout(()=>this.fetch(),due);
  }
  async fetch() {
    const sequence=++this.sequence,id=this.owner,chosen=this.chosen;
    this.lastFetch=performance.now();this.busy=true;
    try {
      const path=id?`/api/sessions/${encodeURIComponent(id)}/debug${chosen?`?turn_id=${encodeURIComponent(chosen)}`:''}`:'/api/debug/catalog';
      const payload=await this.api(path,{headers:{'X-ChefMate-Developer':'1'}});
      if(sequence!==this.sequence||id!==this.sessionId()||!this.enabled())return;
      this.payload=payload;this.paint();
    }catch(error){
      if(sequence===this.sequence&&id===this.sessionId()&&this.enabled())this.host.replaceChildren(paragraph(error.message||'调试信息暂时无法读取。'),this.refreshButton());
    }finally{if(sequence===this.sequence)this.busy=false;}
  }
  refreshButton(){const button=node('button','dev-refresh','刷新');button.type='button';button.prepend(icon('refresh-cw'));button.addEventListener('click',()=>this.render({force:true}));return button;}
  paint() {
    const paintKey=JSON.stringify([this.mode,this.owner,this.payload]);
    if(paintKey===this.paintKey&&this.host.querySelector('.developer-panel'))return;
    this.paintKey=paintKey;
    const currentFocus=this.host.contains(document.activeElement)?document.activeElement?.dataset.devKey:null;
    const opened=new Set([...this.host.querySelectorAll('details[open][data-dev-key]')].map(item=>item.dataset.devKey));
    const payload=this.payload,root=node('div','developer-panel');
    const heading=node('header','dev-heading');heading.append(node('div','',`智能体调试 · ${labels[payload.stage]||payload.stage||'交流'}`),this.refreshButton());root.append(heading);
    const nav=node('nav','dev-modes');nav.setAttribute('aria-label','调试内容');
    for(const [id,label,glyph]of [['tools','工具','sliders-horizontal'],['logs','调用','list'],['components','组件','book-open'],['context','上下文','layers']]){
      const button=node('button','',label);button.type='button';button.dataset.devKey=`mode:${id}`;button.setAttribute('aria-pressed',String(this.mode===id));button.prepend(icon(glyph));
      button.addEventListener('click',()=>{this.mode=id;this.paint();});nav.append(button);
    }
    root.append(nav);
    if(this.mode==='tools')this.tools(root,payload);
    else if(this.mode==='logs')this.logs(root,payload);
    else if(this.mode==='components')this.components(root,payload);
    else this.context(root,payload);
    this.host.replaceChildren(root);
    for(const item of this.host.querySelectorAll('details[data-dev-key]'))if(opened.has(item.dataset.devKey))item.open=true;
    if(currentFocus)[...this.host.querySelectorAll('[data-dev-key]')].find(item=>item.dataset.devKey===currentFocus)?.focus({preventScroll:true});
  }
  tools(root,payload){
    const tools=payload.tools||[];
    root.append(node('p','dev-note',`${tools.length} 项已注册工具 · ${tools.filter(tool=>tool.available).length} 项当前可执行。展开查看用途与参数。`));
    if(!tools.length)root.append(paragraph('模型连接配置完成后，这里展示实际注册的工具。'));
    for(const tool of tools){
      const entry=details(`${tool.name}${tool.available?'':' · 当前不可用'}`,()=>{
        const body=node('div','dev-tool-body');body.append(paragraph(tool.description));
        if(tool.allowed_stages?.length)body.append(node('p','dev-constraint',`可用阶段：${tool.allowed_stages.map(stage=>labels[stage]||stage).join('、')}`));
        const effects={read:'读取',presentation:'展示',mixed:'展示与任务更新',write:'修改',wait:'等待回答'};
        body.append(node('p','dev-constraint',`操作类型：${effects[tool.effect]||tool.effect||'读取'}${tool.auto_display?' · 成功后自动展示结果':''}`));
        if(tool.side_effects?.length)body.append(details('实际影响',()=>valueView(tool.side_effects)));
        if(tool.preconditions?.length)body.append(details('前置条件',()=>valueView(tool.preconditions)));
        if(tool.blockers?.length)body.append(details('当前阻塞原因',()=>valueView(tool.blockers),{open:true}));
        body.append(schemaView(tool.parameters||{}));return body;
      });entry.dataset.devKey=`tool:${tool.name}`;root.append(entry);
    }
  }
  turnSelect(root,payload){
    if(!payload.runs?.length)return;
    const label=node('label','dev-turn-label','查看轮次'),select=node('select');select.dataset.devKey='turn-select';select.setAttribute('aria-label','查看调试轮次');
    payload.runs.forEach(run=>{const option=node('option','',`${run.message?.slice(0,28)||'本轮'} · ${run.status==='running'?'执行中':run.status==='waiting_for_user'?'待回答':run.model_calls+'次请求'}`);option.value=run.turn_id;select.append(option);});
    select.value=payload.capture?.turn_id||payload.runs[0].turn_id;
    select.addEventListener('change',()=>{this.chosen=select.value;this.selectedRequest=null;this.render({force:true});});label.append(enhanceControl(select,{label:'查看调试轮次'}));root.append(label);
  }
  logs(root,payload){
    this.turnSelect(root,payload);
    const calls=payload.capture?.tool_calls;
    if(calls?.length){calls.forEach((call,index)=>{
      const entry=details(`${index+1} · ${call.name} · ${call.result?.status||call.status||'执行中'}`,()=>{
        const body=node('div');body.append(node('h4','','输入'),valueView(call.arguments||{}),node('h4','','返回'),valueView(call.result||{}));return body;
      });entry.dataset.devKey=`call:${call.call_id||index}`;root.append(entry);
    });return;}
    if(payload.capture){root.append(paragraph('这轮尚无工具调用。'));return;}
    root.append(node('p','dev-note','历史公开调用记录。旧轮次没有实际请求快照。'));
    for(const log of [...(payload.logs||[])].reverse())for(const call of log.tool_calls||[])root.append(details(`${call.name} · ${call.result?.status||'已结束'}`,()=>valueView({arguments:call.arguments,result:call.result})));
  }
  components(root,payload){
    root.append(node('p','dev-note','系统组件统一绑定指南、工具、界面和依赖，开关同时控制可见性与执行权限。'));
    for(const component of payload.components||[])root.append(details(`${component.name} · ${component.enabled?'已启用':'已关闭'}`,()=>valueView(component)));
    this.turnSelect(root,payload);
    root.append(node('h4','','本轮指南读取'));
    const events=payload.capture?.skill_events||[];
    if(!events.length)root.append(paragraph('本轮没有读取指南正文。'));
    events.forEach(event=>root.append(details(`${event.name} · ${event.status}`,()=>valueView(event))));
  }
  context(root,payload){
    this.turnSelect(root,payload);
    const requests=traceRequests(payload.capture);
    root.append(node('p','dev-note','按实际请求顺序查看输入与工具。用量来自服务端报告；分类占用在左侧概览中估算。'));
    if(!requests.length){root.append(paragraph(payload.capture?.status==='running'?'正在准备首次模型请求。':'这轮没有留存实际请求。旧对话不会按当前配置重建。'));return;}
    const turn=payload.capture.turn_id;
    if(this.requestTurn!==turn){this.requestTurn=turn;this.selectedRequest=null;}
    const selected=requests.find(item=>item.round===this.selectedRequest) || requests.at(-1);this.selectedRequest=selected.round;
    const browser=node('section','dev-trace-browser'),navigation=node('nav','dev-request-list');navigation.setAttribute('aria-label','本轮模型请求');
    for(const item of requests){const button=node('button','dev-request-choice');button.type='button';button.dataset.devKey=`request:${turn}:${item.round}`;button.setAttribute('aria-current',item===selected?'true':'false');
      const copy=node('span','dev-request-choice-copy');copy.append(node('strong','',`请求 ${item.round}`),node('small','',`${item.request.messages?.length || 0} 条消息 · ${(item.request.messages || []).filter(message=>message.role==='tool').length} 项工具结果`));
      const usage=node('span','dev-request-choice-tokens',item.usage?`${tokenValue(item.usage.input_tokens)} → ${tokenValue(item.usage.output_tokens)}`:'用量未报告');button.append(node('i','dev-trace-dot'),copy,usage);
      button.addEventListener('click',()=>{this.selectedRequest=item.round;this.paintKey='';this.paint();});navigation.append(button);}
    const content=node('section','dev-trace-detail'),request=selected.request,usage=selected.usage;
    const title=node('div','dev-trace-title');title.append(node('h3','',`第 ${selected.round} 次模型请求`),node('span','',usage?.model || '模型名称未记录'));content.append(title);
    const metrics=node('dl','dev-request-metrics');for(const [key,label]of [['input_tokens','输入'],['output_tokens','输出'],['total_tokens','总量'],['cached_tokens','缓存读取']]){const cell=node('div');cell.append(node('dt','',label),node('dd','',tokenValue(usage?.[key])));metrics.append(cell);}content.append(metrics);
    if(usage?.total_tokens_source==='input_output_sum')content.append(node('p','dev-constraint','总量由本次完整输入与输出精确相加。'));
    const tab=this.contextTab || 'messages',tabs=node('nav','dev-context-tabs');tabs.setAttribute('aria-label','请求内容');tabs.setAttribute('role','tablist');
    const panel=node('div','dev-context-content');panel.id=`dev-request-panel-${selected.round}`;panel.setAttribute('role','tabpanel');
    const tabButtons=[];for(const [key,label]of [['messages','消息'],['system','系统提示'],['tools','工具定义']]){const button=node('button','',label);button.type='button';button.dataset.devKey=`context-tab:${key}`;button.id=`dev-request-tab-${key}`;button.setAttribute('role','tab');button.setAttribute('aria-selected',String(key===tab));button.setAttribute('aria-controls',panel.id);button.tabIndex=key===tab?0:-1;button.addEventListener('click',()=>{this.contextTab=key;this.paintKey='';this.paint();});tabButtons.push(button);tabs.append(button);}
    tabs.addEventListener('keydown',event=>{const index=tabButtons.indexOf(event.target),delta=event.key==='ArrowLeft'?-1:event.key==='ArrowRight'?1:0;let next;if(delta)next=(index+delta+tabButtons.length)%tabButtons.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabButtons.length-1;else return;event.preventDefault();tabButtons[next].click();this.host.querySelector(`#${tabButtons[next].id}`)?.focus();});panel.setAttribute('aria-labelledby',`dev-request-tab-${tab}`);content.append(tabs,panel);
    if(tab==='system'){panel.append(paragraph(request.system || '未记录系统提示词'));if(request.skills?.length)panel.append(details('本次组件指南目录',()=>valueView(request.skills)));}
    else if(tab==='tools'){for(const item of request.tools || []){const tool=item.function || item;panel.append(details(TOOL_NAMES[tool.name] || tool.name || '工具',()=>{const body=node('div');body.append(node('code','dev-tool-name',tool.name || ''),paragraph(tool.description || ''),schemaView(tool.parameters || {}));return body;}));}if(!request.tools?.length)panel.append(paragraph('本次请求未绑定工具。'));}
    else {if(request.native_images_count)panel.append(node('p','dev-constraint',`${request.native_images_count} 张实际图片 · 只记录元数据`));(request.messages || []).forEach((message,index)=>{if(message.role==='tool'){const entry=details(`${index+1} · ${TOOL_NAMES[message.name] || message.name || '工具结果'} · ${message.result?.status || '已返回'}`,()=>messageView(message,index));entry.dataset.devKey=`message:${turn}:${selected.round}:${index}`;panel.append(entry);}else panel.append(messageView(message,index));});if(request.parameter_integrity)panel.append(details('工具参数完整性',()=>valueView(request.parameter_integrity)));}
    content.append(node('p','dev-constraint','来源：实际模型请求的脱敏快照。未记录图片字节、隐藏推理或供应商完整封装。'));browser.append(navigation,content);root.append(browser);
  }
}
