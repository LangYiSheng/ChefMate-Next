import {icon} from './icons.js';

const node=(tag,cls,text)=>{const item=document.createElement(tag);if(cls)item.className=cls;if(text!=null)item.textContent=String(text);return item;};
const labels={system:'系统提示',user:'用户输入',assistant:'助手输出',tool:'工具回调',idea:'交流',planning:'选菜',prep:'备菜',cooking:'烹饪',completed:'完成'};
const fieldLabels={revision:'任务版本',stage:'当前阶段',preferences:'用餐条件',components:'界面内容',recent_actions:'最近操作',pending_question:'待回答问题',last_answer:'用户答案',recipe_id:'菜谱 ID',recipe_ids:'菜谱 ID',item_id:'食材项目 ID',expected_revision:'预期版本',status:'状态',message:'说明',items:'项目',actions:'界面更新',ui_snapshot:'任务状态',values:'条件',servings:'用餐人数',ingredient_terms:'想吃的食材',exclude_ingredient_terms:'不吃的食材',max_total_time_min:'时间上限',available_tools:'可用厨具',dietary_notes:'饮食说明',question:'问题',option_ids:'选择',text:'文字',title:'标题'};

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
    for(const [id,label,glyph]of [['tools','工具','sliders-horizontal'],['logs','调用','list'],['skills','技能','book-open'],['context','上下文','layers']]){
      const button=node('button','',label);button.type='button';button.dataset.devKey=`mode:${id}`;button.setAttribute('aria-pressed',String(this.mode===id));button.prepend(icon(glyph));
      button.addEventListener('click',()=>{this.mode=id;this.paint();});nav.append(button);
    }
    root.append(nav);
    if(this.mode==='tools')this.tools(root,payload);
    else if(this.mode==='logs')this.logs(root,payload);
    else if(this.mode==='skills')this.skills(root,payload);
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
      const entry=details(`${tool.name}${tool.available?'':' · 阶段受限'}`,()=>{
        const body=node('div','dev-tool-body');body.append(paragraph(tool.description));
        if(tool.allowed_stages?.length)body.append(node('p','dev-constraint',`可用阶段：${tool.allowed_stages.map(stage=>labels[stage]||stage).join('、')}`));
        body.append(schemaView(tool.parameters||{}));return body;
      });entry.dataset.devKey=`tool:${tool.name}`;root.append(entry);
    }
  }
  turnSelect(root,payload){
    if(!payload.runs?.length)return;
    const label=node('label','dev-turn-label','查看轮次'),select=node('select');select.dataset.devKey='turn-select';select.setAttribute('aria-label','查看调试轮次');
    payload.runs.forEach(run=>{const option=node('option','',`${run.message?.slice(0,28)||'本轮'} · ${run.status==='running'?'执行中':run.status==='waiting_for_user'?'待回答':run.model_calls+'次请求'}`);option.value=run.turn_id;select.append(option);});
    select.value=payload.capture?.turn_id||payload.runs[0].turn_id;
    select.addEventListener('change',()=>{this.chosen=select.value;this.render({force:true});});label.append(select);root.append(label);
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
  skills(root,payload){
    root.append(node('p','dev-note','已启用技能只在系统提示中提供名称与简介，需要时才读取正文；技能指引不会增加工具权限。'));
    for(const skill of payload.skills||[])root.append(details(skill.name,()=>valueView(skill)));
    this.turnSelect(root,payload);
    root.append(node('h4','','本轮技能使用'));
    const events=payload.capture?.skill_events||[];
    if(!events.length)root.append(paragraph('本轮没有读取技能正文的记录。'));
    events.forEach(event=>root.append(details(`${event.name} · ${event.status}`,()=>valueView(event))));
  }
  context(root,payload){
    this.turnSelect(root,payload);
    const trace=payload.capture?.debug_trace||[];
    root.append(node('p','dev-note','实际模型请求的脱敏记录，包含系统提示、消息顺序、工具定义及任务状态。图片仅保留元数据，不展示内部推理。最近保留20轮。'));
    if(!trace.length){root.append(paragraph(payload.capture?.status==='running'?'正在准备首次模型请求。':'这轮没有留存实际模型请求。旧对话不会用当前配置重建并冒充原始上下文。'));return;}
    trace.forEach((request,index)=>{
      const entry=details(`第 ${request.round||index+1} 次模型请求 · ${request.messages?.length||0} 条消息`,()=>{
        const body=node('div','dev-request');
        body.append(details('系统提示词',()=>paragraph(request.system||'')));
        if(request.skills?.length)body.append(details('本次系统提示中的技能目录',()=>valueView(request.skills)));
        if(request.native_images_count)body.append(node('p','dev-constraint',`实际发送 ${request.native_images_count} 张图片 · 不记录图片字节`));
        (request.messages||[]).forEach((message,messageIndex)=>body.append(messageView(message,messageIndex)));
        body.append(details(`本次绑定的 ${request.tools?.length||0} 项工具`,()=>valueView((request.tools||[]).map(tool=>({name:tool.name||tool.function?.name,description:tool.description||tool.function?.description})))));
        return body;
      },{open:index===trace.length-1});entry.dataset.devKey=`request:${payload.capture.turn_id}:${request.round||index}`;root.append(entry);
    });
  }
}
