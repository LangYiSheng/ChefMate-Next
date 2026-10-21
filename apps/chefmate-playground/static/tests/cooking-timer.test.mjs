import test from 'node:test';
import assert from 'node:assert/strict';
import {durationText,timerPresentation,timerForStep,manualTimerSeconds,timerDraftParts} from '../cooking-timer.js';

test('cooking duration labels express seconds and mixed units without fractional minutes',()=>{
  assert.equal(durationText(30),'30 秒');
  assert.equal(durationText(90),'1 分钟 30 秒');
  assert.equal(durationText(3605),'1 小时 5 秒');
  assert.equal(durationText(null),'时长未知');
});

test('running, paused and expired timers share an exact stable countdown model',()=>{
  const now=Date.parse('2026-10-21T12:00:00Z');
  const timer={status:'running',seconds:60,deadline_at:'2026-10-21T12:00:30Z'};
  assert.deepEqual(timerPresentation(timer,now),{remaining:30,clock:'0:30',status:'正在计时',state:'running',expired:false,progress:.5});
  assert.equal(timerPresentation(timer,now+30000).expired,true);
  assert.equal(timerPresentation({...timer,status:'paused',remaining_seconds:17},now+50000).clock,'0:17');
  assert.equal(timerPresentation({...timer,status:'finished'},now).status,'时间到');
  assert.equal(timerPresentation({...timer,status:'cancelled'},now).expired,false);
  assert.equal(timerPresentation({status:'running',seconds:60},now).clock,'--:--');
});

test('step timers stay tied to the actual recipe and prefer a live timer over old terminal records',()=>{
  const timers=[{id:'old',recipe_id:'r1',step_id:'s1',status:'finished'},
    {id:'live',recipe_id:'r1',step_id:'s1',status:'paused'},
    {id:'other-recipe',recipe_id:'r2',step_id:'s1',status:'running'}];
  assert.equal(timerForStep(timers,'r1','s1').id,'live');
  assert.equal(timerForStep(timers,'r1','missing'),null);
  assert.equal(timerForStep(timers.slice(0,1),'r1','s1').id,'old');
});

test('manual timers accept whole seconds and preserve a one-second draft across redraws',()=>{
  assert.equal(manualTimerSeconds('',30),30);
  assert.equal(manualTimerSeconds(1,5),65);
  assert.equal(manualTimerSeconds(1440,0),86400);
  for(const pair of [[0,0],[1440,1],[-1,30],[1,60],[1,.5],['','']]) assert.equal(manualTimerSeconds(...pair),null);
  assert.deepEqual(timerDraftParts(1/60),{minutes:0,seconds:1});
  assert.deepEqual(timerDraftParts(1.5),{minutes:1,seconds:30});
});
