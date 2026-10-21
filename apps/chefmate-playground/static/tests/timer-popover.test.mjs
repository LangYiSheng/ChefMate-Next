import test from 'node:test';
import assert from 'node:assert/strict';
import {visibleTimers} from '../timer-popover.js';

test('header timer chooses an expired reminder, then active deadlines, then paused timers',()=>{
  const now=Date.parse('2026-10-21T10:00:00Z');
  const items=[{id:'paused',status:'paused',remaining_seconds:2},
    {id:'slow',status:'running',deadline_at:'2026-10-21T10:02:00Z'},
    {id:'fast',status:'running',deadline_at:'2026-10-21T10:01:00Z'},
    {id:'due',status:'finished'},{id:'cancelled',status:'cancelled'}];
  assert.deepEqual(visibleTimers(items,new Set(),now).map(timer=>timer.id),['due','fast','slow','paused']);
  assert.deepEqual(visibleTimers(items,new Set(['due']),now).map(timer=>timer.id),['fast','slow','paused']);
  assert.deepEqual(visibleTimers([{id:'locally-due',status:'running',deadline_at:'2026-10-21T09:59:59Z'}],new Set(['locally-due']),now),[]);
});

test('one step has one visible timer and acknowledging history cannot hide an active timer',()=>{
  const items=[{id:'old',status:'finished',recipe_id:'r',step_id:'s'},
    {id:'live',status:'paused',remaining_seconds:12,recipe_id:'r',step_id:'s'},
    {id:'later-history',status:'finished',recipe_id:'r',step_id:'s'},
    {id:'manual',status:'paused',remaining_seconds:20}];
  assert.deepEqual(visibleTimers(items,new Set(['live']),Date.now()).map(timer=>timer.id),['live','manual']);
});
