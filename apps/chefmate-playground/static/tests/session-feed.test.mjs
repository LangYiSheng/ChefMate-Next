import test from 'node:test';
import assert from 'node:assert/strict';
import {SessionFeed} from '../session-feed.js';

test('sidebar fetches twenty at a time and deduplicates moved conversations',async()=>{
  const requests=[],pages=[{items:Array.from({length:20},(_,id)=>({id})),next_cursor:'next'},
    {items:[{id:19,title:'updated'},{id:20}],next_cursor:null}];
  const feed=new SessionFeed(async query=>{requests.push(query);return pages.shift();});
  await feed.load();assert.equal(feed.items.length,20);assert.equal(requests.length,1);
  await feed.load({more:true});assert.equal(feed.items.length,21);assert.equal(feed.items[19].title,'updated');
  await feed.load({more:true});assert.equal(requests.length,2);
  assert.deepEqual(requests,[{limit:20,cursor:null},{limit:20,cursor:'next'}]);
});
test('refresh wins a race with an older load-more without losing the loaded range',async()=>{
  let resolveMore;
  const fresh=Array.from({length:20},(_,index)=>({id:`fresh-${index}`}));
  const feed=new SessionFeed(async({cursor})=>cursor?new Promise(resolve=>resolveMore=resolve):{items:fresh,next_cursor:'more'});
  await feed.load();const more=feed.load({more:true});
  const refresh=feed.load();await refresh;resolveMore({items:[{id:'stale'}],next_cursor:null});await more;
  assert.deepEqual(feed.items,fresh);assert.equal(feed.cursor,'more');assert.equal(feed.loading,false);
});
test('failed page preserves current rows and cursor for an explicit retry',async()=>{
  let fail=false;
  const first=Array.from({length:20},(_,id)=>({id}));
  const feed=new SessionFeed(async({cursor})=>{if(fail)throw new Error('offline');return {items:cursor?[{id:'second'}]:first,next_cursor:cursor?null:'second'};});
  await feed.load();fail=true;await assert.rejects(feed.load({more:true}),/offline/);
  assert.equal(feed.items.length,20);assert.equal(feed.cursor,'second');assert.equal(feed.loading,false);
  fail=false;await feed.load({more:true});assert.equal(feed.items.length,21);assert.equal(feed.items.at(-1).id,'second');assert.equal(feed.error,null);
});
