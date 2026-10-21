import test from 'node:test';
import assert from 'node:assert/strict';
import {CenterEditorState,draftIngredient} from '../center-state.js';

test('a failed save retains edits; a successful save clears only its own editor',()=>{
  const editor=new CenterEditorState('draft-1');editor.change();const token=editor.begin();
  assert.equal(editor.begin(),null);assert.equal(editor.finish(token),true);assert.equal(editor.dirty,true);
  const next=editor.begin();assert.equal(editor.finish(token,{saved:true}),false);
  assert.equal(editor.busy,true);assert.equal(editor.finish(next,{saved:true}),true);assert.equal(editor.dirty,false);
});
test('disposed editors ignore late save responses',()=>{
  const editor=new CenterEditorState('draft-1');editor.change();const token=editor.begin();editor.dispose();
  assert.equal(editor.finish(token,{saved:true}),false);assert.equal(editor.dirty,true);
});
test('editing ingredient identity removes stale nutrition mapping and allergen evidence',()=>{
  const ingredient={name:{value:'豆腐'},amount:{value:'200克'},original:{canonical_name:'鸡蛋',grams:100,amount_evidence:'2个鸡蛋',allergens:['egg'],usda_query:'egg',scalable:true},originalAmount:'2个'};
  assert.deepEqual(draftIngredient(ingredient),{name:'豆腐',amount:'200克',grams:null,amount_evidence:'',allergens:[],usda_query:'',scalable:true});
  ingredient.name.value='鸡蛋';ingredient.amount.value='2个';
  assert.equal(draftIngredient(ingredient).grams,100);
  ingredient.amount.value='3个';assert.equal(draftIngredient(ingredient).grams,null);
});
