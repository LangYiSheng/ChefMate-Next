import test from 'node:test';import assert from 'node:assert/strict';import {intakeMetric} from '../nutrition-dashboard.js';
test('partial nutrition uses a labelled known subtotal without turning unknown into zero',()=>{
  assert.deepEqual(intakeMetric({total:{calories_kcal:null},known_total:{calories_kcal:240}},'calories_kcal'),{value:240,partial:true,coverage:undefined});
  assert.equal(intakeMetric({},'protein_g').value,null);assert.equal(intakeMetric({total:{protein_g:0}},'protein_g').value,0);
});
