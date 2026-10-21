import test from 'node:test';import assert from 'node:assert/strict';
import {currentContextUsage} from '../context-meter.js';
test('running conversation never shows the previous turn as its current context',()=>{
  const old={round:4,chars:100,categories:[]};assert.equal(currentContextUsage({running:true,messages:[{role:'assistant',context_usage:old}]}),null);
  const live={round:1,chars:50,categories:[]};assert.equal(currentContextUsage({running:true,active_turn:{context_usage:old},messages:[{role:'assistant',streaming:true,context_usage:live}]}),live);
});
test('completed and failed conversations use their persisted actual request',()=>{
  const saved={round:2,chars:100,categories:[]};assert.equal(currentContextUsage({last_status:'error',last_run:{result:{context_usage:saved}}}),saved);assert.equal(currentContextUsage(null),null);
});
test('an empty current capture never appears as zero or reuses the previous turn',()=>{
  const old={round:2,chars:100,categories:[]},current={round:1,chars:70,categories:[]};
  assert.equal(currentContextUsage({last_run:{id:'old',result:{context_usage:old}},messages:[{role:'assistant',context_usage:{}}]}),null);
  assert.equal(currentContextUsage({last_run:{id:'old',result:{context_usage:old}},messages:[{role:'assistant',context_usage:current}]}),current);
  assert.equal(currentContextUsage({last_run:{id:'old',result:{context_usage:old}},messages:[{role:'assistant',turn_id:'new'}]}),null);
});
