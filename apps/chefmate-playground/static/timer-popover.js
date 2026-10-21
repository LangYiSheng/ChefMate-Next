import {node, timerForm} from './ui-components.js';
import {icon} from './icons.js';
import {timerPresentation} from './cooking-timer.js';
import {pageWindow} from './presentation.js';
import {TimerSound} from './timer-sound.js';

export function visibleTimers(timers, acknowledged, now) {
  const chosen=new Map();
  for(const timer of timers) {
    const key=timer.step_id ? `${timer.recipe_id}:${timer.step_id}` : timer.id;
    const previous=chosen.get(key);
    if(!previous || !['running','paused'].includes(previous.status) || ['running','paused'].includes(timer.status))chosen.set(key,timer);
  }
  const priority=timer=>timerPresentation(timer,now).expired ? 0 : timer.status==='running'?1:2;
  return [...chosen.values()].filter(timer=>timerPresentation(timer,now).expired ? !acknowledged.has(timer.id)
    : ['running','paused'].includes(timer.status))
    .sort((a,b)=>priority(a)-priority(b) || (timerPresentation(a,now).remaining??Infinity)-(timerPresentation(b,now).remaining??Infinity));
}

export class TimerPopover {
  constructor(workspace) {
    this.sound = new TimerSound();
    this.workspace=workspace;this.mode='list';this.page=0;this.sessionId=null;this.timers=[];
    this.triggers=[...document.querySelectorAll('[data-timer-trigger]')];
    this.panel=node('section','timer-popover');this.panel.id='kitchen-timer-popover';this.panel.hidden=true;
    this.panel.setAttribute('role','dialog');this.panel.setAttribute('aria-label','厨房计时');
    document.body.append(this.panel);
    for(const trigger of this.triggers)trigger.addEventListener('click',()=>this.panel.hidden?this.open(trigger):this.close());
    this.panel.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();this.close();}
    });
    document.addEventListener('keydown',event=>{
      if(event.key!=='Escape' || this.panel.hidden || document.querySelector('dialog[open]'))return;
      const restore=this.panel.contains(document.activeElement);
      event.preventDefault();event.stopImmediatePropagation();this.close(restore);
    },true);
    document.addEventListener('pointerdown',event=>{
      if(!this.panel.hidden && !this.panel.contains(event.target) && !this.triggers.some(trigger=>trigger.contains(event.target)))this.close(false);
    });
    window.addEventListener('resize',()=>{if(!this.panel.hidden){this.signature=null;this.render();}});
    window.visualViewport?.addEventListener('resize',()=>{if(!this.panel.hidden){this.signature=null;this.render();}});
  }
  button(label,glyph,run,{disabled=false,primary=false}={}) {
    const button=node('button',primary?'timer-popup-primary':'timer-popup-button');button.type='button';button.disabled=disabled;
    button.setAttribute('aria-label',label);button.title=label;
    if(glyph)button.append(icon(glyph));
    if(!glyph || primary)button.append(node('span','',label));
    button.addEventListener('click',()=>Promise.resolve(run()).catch(()=>{}));return button;
  }
  trigger() {
    const focus=document.getElementById('kitchen-focus');
    return this.triggers.find(trigger=>focus && !focus.hidden ? focus.contains(trigger) : !focus?.contains(trigger));
  }
  update(timers,entry,sessionId) {
    if(this.sessionId!==sessionId && !this.panel.hidden)this.close(false);
    this.sessionId=sessionId;this.entry=entry;
    this.allTimers=timers;
    this.timers=visibleTimers(timers,entry?.timerAcknowledged || new Set(),entry?.timerNow() ?? Date.now());
    const first=this.timers[0],display=first && timerPresentation(first,entry.timerNow());
    for(const trigger of this.triggers) {
      trigger.hidden=!sessionId || !(entry?.snapshot?.stage==='cooking' || this.timers.length);
      trigger.dataset.state=display?.state || 'empty';
      const identity=JSON.stringify([first?.id,display?.state,this.timers.length]);
      if(trigger.dataset.identity!==identity) {
        trigger.dataset.identity=identity;
        trigger.replaceChildren(icon('timer'),node('span','timer-trigger-clock',display?.clock || '计时'));
        if(this.timers.length>1)trigger.append(node('span','timer-trigger-count',String(this.timers.length)));
      }
      const clock=trigger.querySelector('.timer-trigger-clock');if(clock)clock.textContent=display?.clock || '计时';
      trigger.setAttribute('aria-label',first?`厨房计时：${first.label}，${display.clock}，${display.status}`:'打开厨房计时');
    }
    if(!this.panel.hidden)this.render();
  }
  open(trigger=this.trigger(),{focus=true,timerId=null}={}) {
    if(!this.entry || !trigger || trigger.hidden)return;
    const focusRoot=document.getElementById('kitchen-focus');
    (focusRoot && !focusRoot.hidden ? focusRoot : document.body).append(this.panel);
    this.anchor=trigger;this.mode='list';this.page=0;this.highlight=timerId;
    if(timerId){const size=this.pageSize(),index=this.timers.findIndex(timer=>timer.id===timerId);if(index>=0)this.page=Math.floor(index/size);}
    this.panel.hidden=false;this.signature=null;
    this.triggers.forEach(control=>control.setAttribute('aria-expanded',String(control===trigger)));
    this.render();
    if(focus)this.panel.querySelector('button:not(:disabled),input:not(:disabled)')?.focus({preventScroll:true});
  }
  close(restore=true) {
    if(this.panel.hidden)return;
    this.panel.hidden=true;this.triggers.forEach(control=>control.setAttribute('aria-expanded','false'));
    if(restore && this.anchor?.isConnected && this.anchor.getClientRects().length)this.anchor.focus({preventScroll:true});
  }
  remind(timer,sessionId) {
    if(this.workspace.entries.get(sessionId)?.timerAcknowledged.has(timer.id))return;
    this.sound.play();
    if(sessionId!==this.sessionId)return;
    // An automatic reminder is persistent, but never takes keyboard focus or
    // replaces a timer name the user is currently entering.
    if(this.panel.hidden)this.open(this.trigger(),{focus:false,timerId:timer.id});
  }
  started(payload,sessionId) {
    if(sessionId!==this.sessionId)return;
    const timer=[...(this.allTimers || [])].reverse().find(timer=>['running','paused'].includes(timer.status)
      && (payload.step_id ? timer.step_id===payload.step_id && timer.recipe_id===payload.recipe_id
        : timer.label===payload.label && timer.seconds===payload.seconds));
    if(timer)this.open(this.trigger(),{focus:false,timerId:timer.id});
  }
  timerView() {
    return this.entry?.views.find(view=>view.kind==='timers') || {
      kind:'timers',id:undefined,revision:this.entry?.snapshot?.revision,editable:true,data:{timers:[]},
    };
  }
  pageSize() {const height=window.visualViewport?.height || innerHeight;return height<520?1:height<740?2:3;}
  position() {
    const rect=this.anchor?.getBoundingClientRect();if(!rect)return;
    const viewport=window.visualViewport;
    const width=viewport?.width || innerWidth,height=viewport?.height || innerHeight;
    const offsetTop=viewport?.offsetTop || 0,offsetLeft=viewport?.offsetLeft || 0;
    this.panel.style.width=`${Math.min(350,width-24)}px`;
    this.panel.style.left=`${Math.max(offsetLeft+12,Math.min(rect.right-this.panel.offsetWidth,offsetLeft+width-this.panel.offsetWidth-12))}px`;
    this.panel.style.top=`${Math.max(offsetTop+12,Math.min(rect.bottom+8,offsetTop+height-this.panel.offsetHeight-12))}px`;
  }
  render() {
    if(this.panel.hidden || !this.entry)return;
    const entry=this.entry,now=entry.timerNow(),id=this.sessionId;
    const policy=entry.snapshot?.cooking_settings || {timer_limit:3,clear_timer_on_step_change:false};
    const active=(this.allTimers || []).filter(timer=>['running','paused'].includes(timer.status) && !timerPresentation(timer,now).expired).length;
    const full=active>=policy.timer_limit;
    const size=this.pageSize();
    const windowed=pageWindow(this.timers,this.page,size);this.page=windowed.index;
    const signature=JSON.stringify([id,this.mode,this.page,size,this.timers.map(timer=>[timer,timerPresentation(timer,now).state]),
      entry.saving,entry.error,entry.snapshot?.stage,entry.snapshot?.cooking_settings,entry.snapshot?.timer_capacity]);
    if(signature===this.signature){this.position();return;}
    this.signature=signature;
    const focused=this.panel.contains(document.activeElement)?document.activeElement?.dataset.controlKey:null;
    if(focused)this.pendingFocus=focused;
    const title=node('div','timer-popup-heading');
    title.append(node('strong','',this.mode==='new'?'新建计时':'厨房计时'));
    if(this.mode==='new')title.append(this.button('返回计时列表','arrow-left',()=>{this.mode='list';this.signature=null;this.render();}));
    else title.append(this.button('添加计时器','plus',()=>{this.mode='new';this.signature=null;this.render();this.panel.querySelector('input')?.focus();},{disabled:entry.snapshot?.stage!=='cooking' || entry.saving || full}));
    title.append(this.button('关闭计时浮层','x',()=>this.close()));this.panel.replaceChildren(title);
    if(entry.error)this.panel.append(node('p','timer-popup-error',entry.error));
    if(this.mode==='new') {
      const view=this.timerView(),ctx=this.workspace.context(view,id),original=ctx.onStartTimer;
      ctx.onStartTimer=async(origin,payload)=>{await original(origin,payload);if(this.sessionId===id){this.mode='list';this.signature=null;this.render();}};
      this.panel.append(timerForm(view,ctx));
    } else {
      const list=node('div','timer-popup-list');
      for(const timer of windowed.items)list.append(this.row(timer,now));
      if(!windowed.total)list.append(node('p','timer-popup-empty','还没有进行中的计时。'));
      this.panel.append(list);
      if(windowed.pages>1){
        const navigation=node('div','timer-popup-pages');
        navigation.append(this.button('上一组计时器','chevron-left',()=>{this.page--;this.signature=null;this.render();},{disabled:windowed.index===0}),
          node('span','',`${windowed.index+1} / ${windowed.pages}`),this.button('下一组计时器','chevron-right',()=>{this.page++;this.signature=null;this.render();},{disabled:windowed.index===windowed.pages-1}));
        this.panel.append(navigation);
      }
    }
    this.panel.append(node('p','timer-popup-policy',`${full?`已有 ${active} / ${policy.timer_limit} 个计时，请先结束已有计时`:
      policy.timer_limit===1?'单计时器':'最多 3 个同时计时'} · ${policy.clear_timer_on_step_change?'换步骤自动清除':'换步骤保留计时'}`));
    this.position();
    const restoreKey=focused || (!entry.saving && document.activeElement===document.body ? this.pendingFocus : null);
    const target=restoreKey && [...this.panel.querySelectorAll('[data-control-key]')].find(control=>control.dataset.controlKey===restoreKey && !control.disabled);
    if(target){target.focus({preventScroll:true});this.pendingFocus=null;}
    else if(restoreKey && !entry.saving && document.activeElement===document.body){
      this.panel.querySelector('button:not(:disabled)')?.focus({preventScroll:true});this.pendingFocus=null;
    }
  }
  row(timer,now) {
    const display=timerPresentation(timer,now),row=node('article','timer-popup-row');row.dataset.state=display.state;
    if(timer.id===this.highlight)row.classList.add('highlighted');
    const copy=node('div','timer-popup-copy');copy.append(node('strong','',timer.label));
    const readout=node('div','timer-popup-readout'),clock=node('time','timer-popup-clock',display.clock);
    clock.dataset.timerId=timer.id;clock.setAttribute('aria-label',`剩余 ${display.remaining ?? '未知'} 秒`);
    readout.append(clock,node('span','',display.status));copy.append(readout);row.append(copy);
    const actions=node('div','timer-popup-actions'),view=this.timerView(),id=this.sessionId;
    const run=(type,payload)=>this.workspace.dispatch(type,payload,view,{sessionId:id});
    const locked=this.entry.saving || this.entry.snapshot?.stage!=='cooking';
    if(display.expired) {
      actions.append(this.button(`确认${timer.label}时间到`,'check',()=>this.workspace.acknowledgeTimer(id,timer.id)),
        this.button(`重新计时${timer.label}`,'refresh-cw',async()=>{
          await run('start_timer',{seconds:timer.seconds,label:timer.label,
            ...(timer.recipe_id?{recipe_id:timer.recipe_id}:{}),...(timer.step_id?{step_id:timer.step_id}:{})});
          this.workspace.acknowledgeTimer(id,timer.id);
        },{disabled:locked}));
    } else {
      actions.append(this.button(`${timer.status==='paused'?'继续':'暂停'}${timer.label}`,timer.status==='paused'?'play':'pause',
        ()=>run(timer.status==='paused'?'resume_timer':'pause_timer',{timer_id:timer.id}),{disabled:locked}),
      this.button(`结束${timer.label}`,'square',()=>run('cancel_timer',{timer_id:timer.id}),{disabled:this.entry.saving}));
    }
    for(const [index,control] of [...actions.children].entries())control.dataset.controlKey=`timer:${timer.id}:${index}`;
    row.append(actions);return row;
  }
}
