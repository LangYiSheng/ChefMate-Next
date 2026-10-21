import test from 'node:test';
import assert from 'node:assert/strict';
import {navigationIndex,navigationEdges,revealNavigationItem} from '../settings-navigation.js';

test('phone settings keyboard navigation wraps and supports home/end without consuming unrelated keys',()=>{
  assert.equal(navigationIndex('ArrowRight',7,8),0);
  assert.equal(navigationIndex('ArrowLeft',0,8),7);
  assert.equal(navigationIndex('Home',5,8),0);
  assert.equal(navigationIndex('End',2,8),7);
  assert.equal(navigationIndex('Tab',2,8),null);
  assert.equal(navigationIndex('ArrowRight',0,0),null);
});

test('gentle overflow cues disappear at the actual navigation edges',()=>{
  assert.deepEqual(navigationEdges(0,320,800),{left:false,right:true});
  assert.deepEqual(navigationEdges(200,320,800),{left:true,right:true});
  assert.deepEqual(navigationEdges(480,320,800),{left:true,right:false});
  assert.deepEqual(navigationEdges(0,800,320),{left:false,right:false});
});

test('revealing selected settings changes only horizontal offset and stays within bounds',()=>{
  const base={scrollLeft:200,viewportLeft:0,viewportWidth:320,maximum:600};
  assert.equal(revealNavigationItem({...base,itemLeft:120,itemRight:210}),200);
  assert.equal(revealNavigationItem({...base,itemLeft:300,itemRight:390}),282);
  assert.equal(revealNavigationItem({...base,itemLeft:-80,itemRight:20}),108);
  assert.equal(revealNavigationItem({...base,scrollLeft:0,itemLeft:-80,itemRight:20}),0);
  assert.equal(revealNavigationItem({...base,scrollLeft:590,itemLeft:310,itemRight:400}),600);
});
