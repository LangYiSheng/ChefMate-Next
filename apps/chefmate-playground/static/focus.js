import {renderView,node} from './ui-components.js';
import {conversationLabel,snapshotStatus} from './presentation.js';

export class CookingFocus {
  constructor(workspace,{session,onChange}) {
    this.workspace=workspace;this.session=session;this.onChange=onChange;
    this.root=document.getElementById('kitchen-focus');this.content=document.getElementById('focus-content');
    this.reply=document.getElementById('focus-reply');this.title=document.getElementById('focus-title');
    this.prompts=document.getElementById('focus-prompts');this.composer=document.querySelector('.composer-area');
    this.home=this.composer.parentElement;this.active=null;this.talking=false;
    document.getElementById('exit-focus').addEventListener('click',()=>this.close());
    this.root.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();this.close();}
      if(event.key==='Tab') {
        const controls=[...this.root.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],summary')].filter(control=>control.getClientRects().length);
        const first=controls[0],last=controls.at(-1);
        if(event.shiftKey && (document.activeElement===first || !this.root.contains(document.activeElement))){event.preventDefault();last?.focus();}
        else if(!event.shiftKey && (document.activeElement===last || !this.root.contains(document.activeElement))){event.preventDefault();first?.focus();}
      }
    });
    this.talk=node('button','focus-talk-toggle','问问助手');this.talk.type='button';
    this.talk.addEventListener('click',()=>{
      this.talking=!this.talking;this.updateTalking();
      if(this.talking)document.getElementById('composer-input').focus();
    });
    this.root.append(this.talk);
  }
  open(view,sessionId) {
    if(this.active?.sessionId!==sessionId)this.close();
    this.active={kind:view.kind,id:view.id,sessionId,startIndex:this.session()?.messages?.length || 0};
    this.signature=null;this.talking=false;this.root.hidden=false;this.root.append(this.composer);
    document.getElementById('app').inert=true;this.updateTalking();this.refresh();
    document.getElementById('exit-focus').focus();this.onChange?.();
  }
  updateTalking() {
    this.composer.hidden=!this.talking;this.prompts.hidden=!this.talking;
    this.talk.textContent=this.talking?'收起交流，专心做菜':'问问助手';
    this.prompts.replaceChildren();
    for(const text of ['这一步需要注意什么？','我缺少一种食材，帮我调整','当前做法可以再简单一点吗？']) {
      const button=node('button','composer-choice',text);button.type='button';
      button.addEventListener('click',()=>{
        const input=document.getElementById('composer-input');input.value=text;
        input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();
      });this.prompts.append(button);
    }
  }
  refresh() {
    if(!this.active)return;
    const active=this.active,entry=this.workspace.entry(active.sessionId);
    if(entry.snapshot?.stage==='cooking' && ['prep_checklist','shopping_list'].includes(active.kind))active.kind='cooking_guide';
    const view=entry.views.find(item=>item.kind===active.kind);
    if(!view){this.close();return;}
    active.id=view.id;
    this.title.textContent=view.kind==='recipe_detail'?`做法 - ${view.data.recipe.name}`:conversationLabel(snapshotStatus(entry.snapshot));
    const signature=JSON.stringify([view,entry.saving,entry.viewState[view.id]]);
    if(signature!==this.signature) {
      const scroll=this.content.scrollTop,key=this.content.contains(document.activeElement)?document.activeElement?.dataset.controlKey:null;
      const ctx={...this.workspace.context(view,active.sessionId),focused:true};
      ctx.onViewState=()=>{this.workspace.onInlineChange?.();this.refresh();};
      this.content.replaceChildren(renderView(view,ctx));this.content.scrollTop=scroll;this.signature=signature;
      if(key)[...this.content.querySelectorAll('[data-control-key]')].find(control=>control.dataset.controlKey===key)?.focus({preventScroll:true});
    }
    const messages=this.session()?.messages || [],last=messages.at(-1);
    const current=messages.length>active.startIndex && last?.role==='assistant';
    this.reply.hidden=!current;
    this.reply.textContent=current?(last.streaming && !last.resultComplete?'助手正在回应…':last.content || ''):'';
  }
  close() {
    if(!this.active)return;
    this.active=null;this.root.hidden=true;this.composer.hidden=false;this.home.append(this.composer);
    document.getElementById('app').inert=false;this.content.replaceChildren();this.reply.hidden=true;
    this.onChange?.();document.getElementById('composer-input').focus();
  }
}
