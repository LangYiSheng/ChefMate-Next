// Long-term memory has its own revision and controls, separate from model settings.
import {node} from './ui-components.js';

window.addEventListener('chefmate:memory-settings', async event => {
  const host=event.detail.container;
  let record, draft='', enabled=true;
  const status=node('p','setting-help','正在读取记忆…');status.setAttribute('role','status');host.append(status);
  async function request(url,options={}) {
    const response=await fetch(url,{...options,headers:{'Content-Type':'application/json'}});
    const data=await response.json();if(!response.ok)throw new Error(data.detail || '暂时无法保存，请重试。');return data;
  }
  function paint() {
    host.replaceChildren();
    const label=node('label','setting-toggle');const toggle=node('input');toggle.type='checkbox';toggle.checked=enabled;
    label.append(toggle,document.createTextNode('在对话中使用长期记忆'));
    const help=node('p','setting-help','你的明确偏好优先于助手总结。关闭后不读取或维护记忆；聊天记录仍按原设置保存。历史仅在需要时检索相关片段。');
    const title=node('label','field-label','你希望记住的事');title.htmlFor='personal-memory-notes';
    const input=node('textarea','setting-input');input.id='personal-memory-notes';input.rows=7;input.maxLength=6000;
    input.placeholder='例如：不吃香菜；喜欢清淡口味；平时两个人吃饭；家里没有烤箱。';input.value=draft;
    input.addEventListener('input',()=>{draft=input.value;window.dispatchEvent(new CustomEvent('chefmate:memory-dirty',{detail:{dirty:true}}));});
    toggle.addEventListener('change',()=>{enabled=toggle.checked;window.dispatchEvent(new CustomEvent('chefmate:memory-dirty',{detail:{dirty:true}}));});
    const subtitle=node('h3','','助手整理的记忆');
    const summary=node('p','memory-summary',record.summary || '还没有总结。聊过几次后，助手会把你明确表达的长期习惯整理在这里。');
    const actions=node('div','settings-actions');const save=node('button','primary','保存记忆');save.type='button';
    const reload=node('button','secondary','读取最新');reload.type='button';const clear=node('button','secondary','清空所有记忆');clear.type='button';
    const confirm=node('div','memory-clear-confirm');confirm.hidden=true;
    const yes=node('button','secondary','确认清空');yes.type='button';const no=node('button','secondary','保留记忆');no.type='button';
    confirm.append(node('p','','会清空你写的偏好和助手总结，历史对话和饮食记录仍保留。'),yes,no);
    clear.onclick=()=>{confirm.hidden=false;yes.focus();};no.onclick=()=>{confirm.hidden=true;clear.focus();};
    async function run(operation) {
      save.disabled=reload.disabled=clear.disabled=yes.disabled=true;
      try {record=await operation();draft=record.user_notes;enabled=record.enabled;window.dispatchEvent(new CustomEvent('chefmate:memory-dirty',{detail:{dirty:false}}));paint();status.textContent='已保存。';}
      catch(error){status.textContent=error.message;}
      finally {save.disabled=reload.disabled=clear.disabled=yes.disabled=false;}
    }
    save.onclick=()=>run(()=>request('/api/memory',{method:'PATCH',body:JSON.stringify({revision:record.revision,user_notes:draft,enabled})}));
    reload.onclick=()=>{if(draft!==record.user_notes || enabled!==record.enabled){status.textContent='先保存当前修改，再读取最新记忆；版本冲突时可复制文字后重新打开。';return;}run(()=>request('/api/memory'));};
    yes.onclick=()=>run(()=>request(`/api/memory?revision=${record.revision}`,{method:'DELETE'}));
    actions.append(save,reload,clear);host.append(label,help,title,input,subtitle,summary,actions,confirm,status);
    status.textContent='记忆保存在本机，可随时修改。厨房技能中关闭“长期记忆”也会停用相关工具。';
  }
  try {record=await request('/api/memory');draft=record.user_notes;enabled=record.enabled;paint();}
  catch(error){status.textContent=error.message;}
});
