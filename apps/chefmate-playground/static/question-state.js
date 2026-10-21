import {makeAction} from './ui-protocol.js';

export function answerPayload(question, draft) {
  const allowed=new Set((question.options || []).map(option=>option.id));
  const optionIds=[...new Set(draft.optionIds || [])].filter(id=>allowed.has(id));
  const text=question.allow_custom ? String(draft.text || '').trim() : '';
  if(!question.multiple && optionIds.length>1)throw new Error('请选择一个答案。');
  if(!optionIds.length && !text)throw new Error('选一个答案，或者写下你的想法。');
  return {question_id:question.id,option_ids:optionIds,text};
}
export function answerMessage(question, payload) {
  const labels=(question.options || []).filter(option=>payload.option_ids.includes(option.id)).map(option=>option.label);
  return `关于“${question.question}”，我的回答是：${[...labels,payload.text].filter(Boolean).join('；')}。请按这个答案继续。`;
}
export function answerRequestId(questionId) { return `answer:${questionId}`; }
export function matchesAnswer(answer,payload) {
  return Boolean(answer && payload && answer.question_id===payload.question_id
    && JSON.stringify([...(answer.option_ids || [])].sort())===JSON.stringify([...(payload.option_ids || [])].sort())
    && String(answer.text || '').trim()===String(payload.text || '').trim());
}

// One answer transaction owns both its idempotent action and its next user turn.
export class QuestionState {
  constructor({read=()=>null,write=()=>{},uuid=()=>crypto.randomUUID()}={}) {
    this.read=read;this.write=write;this.uuid=uuid;this.items=new Map();
  }
  get(id) {
    if(!id)return null;
    if(!this.items.has(id)) {
      let saved;try{saved=this.read(id);}catch{/* Memory remains available. */}
      if(saved?.stage==='saving')saved.stage='uncertain';
      this.items.set(id,saved && typeof saved==='object'?saved:null);
    }
    return this.items.get(id);
  }
  set(id,value) {this.items.set(id,value);try{this.write(id,value);}catch{/* Memory remains available. */}return value;}
  draft(id,question) {
    const current=this.get(id);
    if(current?.questionId===question.id)return current;
    return this.set(id,{questionId:question.id,optionIds:[],text:'',stage:'draft',error:''});
  }
  prepare(id,question,revision) {
    const entry=this.draft(id,question),payload=answerPayload(question,entry);
    if(entry.request && JSON.stringify(entry.request.payload)===JSON.stringify(payload) && entry.stage==='uncertain')return entry;
    const request=makeAction('answer_question',payload,revision,null,this.uuid());
    return this.set(id,{...entry,payload,request,message:answerMessage(question,payload),stage:'saving',error:''});
  }
  saved(id) {const entry=this.get(id);if(entry)this.set(id,{...entry,stage:'saved',error:''});}
  fail(id,error,{uncertain=false}={}) {const entry=this.get(id);if(entry)this.set(id,{...entry,stage:uncertain?'uncertain':'draft',error});}
  accepted(id) {return this.set(id,null);}
  failedContinuation(id,questionId,error) {
    const entry=this.get(id);
    if(entry?.questionId===questionId)this.set(id,{...entry,stage:'continue_failed',error});
  }
  recovered(id,session) {
    const entry=this.get(id);if(!entry || !['saved','sending','continue_failed'].includes(entry.stage))return false;
    const requestId=answerRequestId(entry.questionId);
    if(session?.active_turn?.request_id===requestId || session?.last_run?.request_id===requestId
      || session?.messages?.some(message=>message.role==='user' && message.request_id===requestId)) {this.accepted(id);return true;}
    return false;
  }
}
