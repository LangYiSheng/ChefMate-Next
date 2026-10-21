import {node} from './ui-components.js';
import {icon} from './icons.js';
import {FocusPager} from './focus-pager.js';

export class QuestionComposer {
  constructor(container,{state,onAnswer,onRetry}) {
    this.container=container;this.state=state;this.onAnswer=onAnswer;this.onRetry=onRetry;this.signature='';
  }
  render(id,question,{busy=false,ready=true,continuation=null}={}) {
    const visible=Boolean(question && !busy);
    this.container.hidden=!visible && !continuation;
    if(!visible && !continuation){this.signature='';this.pager?.destroy();this.pager=null;this.container.replaceChildren();return;}
    const entry=question ? this.state.draft(id,question) : continuation;
    const signature=JSON.stringify([id,question,entry.stage,entry.error,busy,ready]);
    if(signature===this.signature)return;
    const anchor=this.pager?.anchor();this.pager?.destroy();this.pager=null;
    this.signature=signature;this.container.replaceChildren();
    if(!question) {
      const line=node('p','question-saved',entry.stage==='continue_failed'?'答案已保存，暂时没能开始下一轮。':'答案已保存，正在接着处理…');
      this.container.append(line);
      if(entry.stage==='continue_failed') {
        const retry=node('button','question-confirm','重试继续');retry.type='button';retry.disabled=!ready || busy;
        retry.addEventListener('click',()=>this.onRetry(id));this.container.append(retry);
      }
      return;
    }
    const form=node('form','question-form');form.setAttribute('aria-label','回答这个问题');
    const heading=node('div','question-heading');heading.append(icon('circle-help'),node('span','',question.title || '想先确认一下'));
    const title=node('h3','question-title',question.question);title.id='question-title';
    const body=node('div','question-body'),pages=node('nav','question-pages focus-pagination');
    pages.setAttribute('aria-label','问题与选项分页');
    const units=[{node:title,key:'question'}];
    const locked=!ready || busy || ['saving','saved','sending','uncertain'].includes(entry.stage);
    const update=()=>this.state.set(id,entry);
    const optionInputs=[];
    for(const option of question.options || []) {
      const label=node('label','question-option'),input=node('input');input.type=question.multiple?'checkbox':'radio';input.name=`answer-${question.id}`;
      input.value=option.id;input.checked=entry.optionIds?.includes(option.id);input.disabled=locked;
      input.addEventListener('change',()=>{
        if(question.multiple)entry.optionIds=input.checked?[...new Set([...entry.optionIds,option.id])]:entry.optionIds.filter(value=>value!==option.id);
        else {
          entry.optionIds=[option.id];
          // Other cards are detached, so native radio grouping cannot update them.
          for(const control of optionInputs)control.checked=control.value===option.id;
        }
        entry.error='';update();
      });input.dataset.controlKey=`question:${question.id}:${option.id}`;
      optionInputs.push(input);
      label.append(input,node('span','',option.label));units.push({node:label,key:`option:${option.id}`});
    }
    form.append(heading,body,pages);
    if(question.allow_custom) {
      const label=node('label','question-custom'),copy=node('span','','或者写下你的答案'),text=node('textarea');
      text.rows=2;text.maxLength=2000;text.placeholder='补充你自己的想法…';text.value=entry.text || '';text.disabled=locked;
      text.dataset.controlKey=`question:${question.id}:custom`;
      text.addEventListener('input',()=>{entry.text=text.value;entry.error='';update();});label.append(copy,text);units.push({node:label,key:'custom'});
    }
    if(entry.error){const error=node('p','question-error',entry.error);error.setAttribute('role','alert');form.append(error);}
    const footer=node('div','question-footer'),hint=node('span','',question.multiple?'可多选，也可以补充说明':'选择或填写后，助手会接着处理');
    const submit=node('button','question-confirm',entry.stage==='saving'?'正在保存…':entry.stage==='uncertain'?'重试提交':'确认并继续');submit.type='submit';submit.disabled=!ready || busy || ['saving','saved','sending'].includes(entry.stage);submit.prepend(icon('arrow-up'));
    footer.append(hint,submit);form.append(footer);
    form.addEventListener('submit',event=>{event.preventDefault();this.onAnswer(id,question);});this.container.append(form);
    this.pager=new FocusPager(body,pages,{label:question.multiple?'问题 · 可多选':'问题'});
    this.pager.setContent(units);if(anchor)this.pager.pendingAnchor=anchor;this.pager.setEnabled(true);
  }
}
