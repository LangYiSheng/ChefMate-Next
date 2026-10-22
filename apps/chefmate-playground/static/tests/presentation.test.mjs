import test from 'node:test';
import assert from 'node:assert/strict';
import {pageWindow,viewChoices,activityRows,latestActivity,conversationLabel,snapshotStatus,messageViewsForStage} from '../presentation.js';

test('cooking and completed chats prioritize steps over obsolete preparation receipts',()=>{
  const views=['preference_form','recipe_choices','prep_checklist','cooking_guide','meal_plan'].map(kind=>({kind}));
  assert.equal(messageViewsForStage(views,'prep').length,5);
  for(const stage of ['cooking','completed'])assert.deepEqual(messageViewsForStage(views,stage).map(view=>view.kind),['cooking_guide','meal_plan']);
});

test('session labels follow model-owned selection and active dish state',()=>{
  assert.equal(conversationLabel({stage:'idea'}),'闲聊');
  assert.equal(conversationLabel(snapshotStatus({stage:'idea',components:[{kind:'preference_form'}]})),'正在选菜');
  assert.equal(conversationLabel({stage:'planning',recipe_names:['意面']}),'已选菜 - 意面');
  assert.equal(conversationLabel({stage:'cooking',recipe_names:['意面','荷包蛋'],active_recipe_name:'荷包蛋'}),'烹饪 - 荷包蛋 · 另1道');
});
test('paging preserves all choices and bounds stale pages after the source shrinks',()=>{
  const items=Array.from({length:11},(_,id)=>({id}));
  assert.deepEqual([0,1,2].flatMap(page=>pageWindow(items,page,4).items),items);
  assert.equal(pageWindow(items.slice(0,2),9,4).index,0);
  assert.deepEqual(pageWindow([],8).items,[]);
  assert.equal(items.length,11);
});
test('workspace navigation focuses the phase and hides obsolete choices and stage controls',()=>{
  const views=['preference_form','task_progress','recipe_choices','meal_plan','prep_checklist','cooking_guide'].map(kind=>({kind}));
  assert.deepEqual(viewChoices(views,'cooking').map(v=>v.kind),['cooking_guide','preference_form','meal_plan','prep_checklist']);
  assert.equal(viewChoices(views,'completed')[0].kind,'meal_plan');
});
test('compact activity reports actual running tools and handles failed and stopped runs',()=>{
  const message={streaming:true,tool_calls:[{name:'search_recipes',call_id:'a',running:true},{name:'get_recipe',call_id:'b',running:true}]};
  assert.match(latestActivity(message),/筛选菜谱.*另有 1/);
  message.tool_calls[0]={name:'search_recipes',result:{status:'snapshot_changed'}};
  assert.equal(activityRows(message)[0].state,'failed');
  assert.equal(latestActivity({cancelled:true,tool_calls:message.tool_calls}),'本轮已停止');
});
test('activity uses public process text and does not invent or extract hidden reasoning',()=>{
  const rows=activityRows({process:['我会按已填写条件查找。'],reasoning:'private',reasoning_content:'private',tool_calls:[]});
  assert.deepEqual(rows.map(r=>r.text),['我会按已填写条件查找。']);
  assert.equal(latestActivity({streaming:true,content:'这里是正在流式输出的完整回答。'}),'正在回复…');
  assert.deepEqual(activityRows({streaming:true,content:'回答正文不复制为加载提示。'}),[]);
  assert.equal(latestActivity({streaming:true,tool_calls:[{name:'show_shopping_list',running:false}]}),'整理备菜清单');
});
