import {icon} from './icons.js';
import {node} from './ui-components.js';

export class SessionMenu {
  constructor({onAction,isBusy=()=>false,returnFocus=()=>{}}){
    Object.assign(this,{onAction,isBusy,returnFocus});
    this.menu=node('div','session-menu');this.menu.popover='auto';this.menu.id='session-menu';this.menu.setAttribute('role','menu');this.menu.setAttribute('aria-label','对话操作');
    this.dialog=node('dialog','session-dialog');this.dialog.setAttribute('aria-labelledby','session-dialog-title');
    document.body.append(this.menu,this.dialog);
    this.menu.addEventListener('toggle',event=>{this.trigger?.setAttribute('aria-expanded',String(event.newState==='open'));});
    this.menu.addEventListener('keydown',event=>{
      const buttons=[...this.menu.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
        event.preventDefault();buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length]?.focus();
      }
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();this.menu.hidePopover();this.trigger?.focus();}
      if(event.key==='Tab')this.menu.hidePopover();
    });
    this.dialog.addEventListener('close',()=>this.returnFocus(this.subject?.id));
    window.addEventListener('resize',()=>{if(this.menu.matches(':popover-open'))this.menu.hidePopover();});
  }
  open(session,trigger){
    if(this.menu.matches(':popover-open')){const same=this.trigger===trigger;this.menu.hidePopover();if(same)return;}
    this.subject=session;this.trigger=trigger;this.menu.replaceChildren();
    const options=[['rename','重命名','copy'],['pin',session.pinned?'取消置顶':'置顶','star'],['delete','删除','trash-2']];
    for(const [action,label,glyph]of options){
      const button=node('button',action==='delete'?'session-menu-danger':'');button.type='button';button.setAttribute('role','menuitem');
      button.append(icon(glyph),node('span','',label));button.disabled=action==='delete' && this.isBusy(session.id);
      button.onclick=()=>{this.menu.hidePopover();if(action==='pin')void this.pin(session);else this.confirm(action,session);};this.menu.append(button);
    }
    this.menu.showPopover();
    const rect=trigger.getBoundingClientRect(),width=this.menu.offsetWidth,height=this.menu.offsetHeight;
    this.menu.style.left=`${Math.max(8,Math.min(innerWidth-width-8,rect.right-width))}px`;
    this.menu.style.top=`${Math.max(8,Math.min(innerHeight-height-8,rect.bottom+5))}px`;
    this.menu.querySelector('button')?.focus();
  }
  async pin(session){
    try{await this.onAction('pin',session);this.returnFocus(session.id);}
    catch(error){this.confirm('error',session,error.message);}
  }
  confirm(action,session,errorText=''){
    this.subject=session;this.dialog.replaceChildren();
    const title=node('h2','',action==='rename'?'重命名对话':action==='delete'?'删除这段对话？':'暂时无法修改');title.id='session-dialog-title';
    const form=node('form'),description=node('p','session-dialog-description',action==='delete'?'这段对话和聊天图片将被删除，无法撤销。个人菜谱、食光手记和长期记忆会保留。':action==='rename'?'给这段对话起个方便查找的名字。':errorText);
    const error=node('p','session-dialog-error');error.setAttribute('role','alert');error.hidden=true;
    const input=node('input','ui-input');input.maxLength=80;input.required=true;input.value=session.title || '新对话';input.setAttribute('aria-label','对话名称');
    const actions=node('div','session-dialog-actions'),cancel=node('button','secondary-button','取消'),submit=node('button',action==='delete'?'danger-button':'primary-button',action==='rename'?'保存名称':action==='delete'?'删除对话':'关闭');
    cancel.type='button';submit.type='submit';cancel.onclick=()=>this.dialog.close();actions.append(cancel,submit);
    form.append(description);if(action==='rename')form.append(input);form.append(error,actions);this.dialog.append(title,form);
    let saving=false;
    this.dialog.oncancel=event=>{event.stopPropagation();if(saving)event.preventDefault();};
    form.onsubmit=async event=>{
      event.preventDefault();if(saving)return;
      if(action==='error'){this.dialog.close();return;}
      const title=input.value.trim();if(action==='rename' && !title){input.setCustomValidity('请输入对话名称。');input.reportValidity();return;}
      saving=true;submit.disabled=true;cancel.disabled=true;input.disabled=true;error.hidden=true;
      try{await this.onAction(action,session,title);this.dialog.close();}
      catch(reason){error.textContent=reason.message;error.hidden=false;}
      finally{saving=false;submit.disabled=false;cancel.disabled=false;input.disabled=false;}
    };
    input.oninput=()=>input.setCustomValidity('');this.dialog.showModal();
    if(action==='rename'){input.focus();input.select();}else cancel.focus();
  }
}
