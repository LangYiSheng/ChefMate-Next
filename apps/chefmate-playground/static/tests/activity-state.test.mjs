import test from 'node:test';
import assert from 'node:assert/strict';
import {ActivityTimeline,activityFrame} from '../activity-state.js';

test('SSE re-renders continue the same activity clock; new public steps restart it',()=>{
  const clock=new ActivityTimeline(),message={};
  assert.equal(clock.start(message,'识别食材',100),100);
  assert.equal(clock.start(message,'识别食材',300),100);
  assert.equal(clock.start(message,'筛选菜谱',400),400);
  assert.equal(clock.start('run:1','读取菜谱',500),500);
  assert.equal(clock.start('run:1','读取菜谱',800),500);
});

test('activity typing uses a readable pace, preserves Unicode and respects reduced motion',()=>{
  assert.equal(activityFrame('🍲筛选菜谱',0).text,'🍲');
  assert.deepEqual(activityFrame('正在理解你的需求…',144),{text:'正在',done:false});
  assert.deepEqual(activityFrame('正在理解你的需求…',700),{text:'正在理解你的需求…',done:true});
  assert.equal(activityFrame('这是一条需要读完才能理解的较长工具执行进度提示文字',700).done,false);
  assert.equal(activityFrame('这是一条需要读完才能理解的较长工具执行进度提示文字',1800).done,true);
  assert.deepEqual(activityFrame('读取菜谱',0,true),{text:'读取菜谱',done:true});
  assert.deepEqual(activityFrame('',0),{text:'',done:true});
});
