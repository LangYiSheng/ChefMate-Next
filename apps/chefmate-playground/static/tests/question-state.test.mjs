import test from 'node:test';
import assert from 'node:assert/strict';
import {QuestionState,answerPayload,answerMessage,answerRequestId,matchesAnswer} from '../question-state.js';
import {messageViews,makeAction,canChangeStage} from '../ui-protocol.js';
import {WorkspaceController} from '../workspace.js';

const question={id:'question-one',title:'确认口味',question:'这顿饭想怎么调味？',options:[{id:'light',label:'清淡一点'},{id:'spicy',label:'微辣'}],allow_custom:true,multiple:false};

test('answers use only real option IDs and preserve the editable custom answer',()=>{
  const payload=answerPayload(question,{optionIds:['invented','light','light'],text:'  少放盐  '});
  assert.deepEqual(payload,{question_id:question.id,option_ids:['light'],text:'少放盐'});
  assert.match(answerMessage(question,payload),/清淡一点；少放盐/);
  assert.throws(()=>answerPayload(question,{optionIds:['light','spicy']}),/一个答案/);
  assert.throws(()=>answerPayload(question,{optionIds:[],text:''}),/选一个答案/);
  assert.equal(answerPayload({...question,allow_custom:false},{optionIds:['light'],text:'ignored'}).text,'');
});

test('uncertain answer retries keep the original action and saved continuations have a stable request ID',()=>{
  let next=0;const state=new QuestionState({uuid:()=>`action-${++next}`});
  state.draft('first',question).optionIds=['light'];
  const first=state.prepare('first',question,4);
  state.fail('first','连接中断',{uncertain:true});
  assert.deepEqual(state.prepare('first',question,9).request,first.request);
  state.fail('first','任务更新，请复核');
  assert.equal(state.prepare('first',question,9).request.base_revision,9);
  assert.equal(next,2);
  state.saved('first');
  assert.equal(state.get('first').stage,'saved');
  assert.equal(answerRequestId(question.id),'answer:question-one');
  assert.equal(state.recovered('first',{active_turn:{request_id:answerRequestId(question.id)}}),true);
  assert.equal(state.get('first'),null);
});

test('reload keeps answers in their owning conversation and uncertain saves reconcile only matching receipts',()=>{
  const saved={questionId:question.id,stage:'saving',payload:{question_id:question.id,option_ids:['light'],text:'少盐'}};
  const storage=new Map([['first',saved]]);
  const state=new QuestionState({read:id=>structuredClone(storage.get(id)),write:(id,value)=>storage.set(id,value)});
  assert.equal(state.get('first').stage,'uncertain');
  assert.equal(state.get('second'),null);
  assert.equal(matchesAnswer({question_id:question.id,option_ids:['light'],text:'少盐'},saved.payload),true);
  assert.equal(matchesAnswer({question_id:question.id,option_ids:['spicy'],text:'少盐'},saved.payload),false);
  state.saved('first');
  assert.equal(storage.get('first').stage,'saved');
  assert.equal(state.recovered('first',{last_run:{request_id:'another turn'}}),false);
  assert.equal(state.recovered('first',{last_run:{request_id:'another turn'},messages:[
    {role:'user',request_id:answerRequestId(question.id),content:'已接受的答案'},
    {role:'user',request_id:'another turn',content:'较新的消息'},
  ]}),true);
  assert.equal(state.get('first'),null);
});

test('questions become visible only after their model turn finishes',t=>{
  const controller=new WorkspaceController({api:async()=>null,notify(){},onChange(){},openPanel(){}});
  t.after(()=>clearInterval(controller.timer));
  controller.receive('first',{workspace:{revision:1,stage:'prep',pending_question:null},views:[]});
  controller.setRunning('first',true);
  controller.receive('first',{workspace:{revision:2,stage:'prep',pending_question:question},views:[]});
  assert.equal(controller.snapshot('first').pending_question,null);
  controller.setRunning('first',false);
  assert.equal(controller.snapshot('first').pending_question.id,question.id);
  assert.equal(controller.snapshot('second'),undefined);
});

test('an earlier answer completion never marks a newly asked question as saved or failed',()=>{
  const state=new QuestionState();state.draft('session',question);state.saved('session');
  const next={...question,id:'question-two',question:'用什么厨具？'};
  state.draft('session',next);
  state.failedContinuation('session',question.id,'旧轮次晚到');
  assert.equal(state.get('session').questionId,next.id);assert.equal(state.get('session').stage,'draft');
});

test('only the preparation workflow is offered, while old purchase cards remain readable',()=>{
  assert.equal(canChangeStage({stage:'planning',recipe_count:1},'shopping'),false);
  assert.equal(canChangeStage({stage:'planning',recipe_count:1},'prep'),true);
  assert.throws(()=>makeAction('check_shopping_item',{},1,null,'id'));
  assert.equal(makeAction('answer_question',{question_id:'q',option_ids:[],text:'有了'},1,null,'id').type,'answer_question');
  const [view]=messageViews({actions:[{kind:'show_component',component:{id:'old',kind:'shopping_list',revision:1,data:{items:[{id:'salt',checked:true}]}}}]});
  assert.equal(view.kind,'prep_checklist');assert.equal(view.title,'备菜清单');assert.equal(view.data.items[0].status,'ready');
});
