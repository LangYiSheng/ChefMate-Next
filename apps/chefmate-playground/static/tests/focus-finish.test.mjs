import test from 'node:test';
import assert from 'node:assert/strict';
import {CookingFocus} from '../focus.js';

function harness(extra={}) {
  const events=[],entry={snapshot:{stage:'prep',components:[{kind:'task_progress',data:{prep_total:5,prep_ready:5}}]},...extra};
  const focus={active:{sessionId:'own-session',kind:'prep_checklist'},finish:{disabled:false},activity:()=>({running:false}),
    workspace:{entry:id=>{assert.equal(id,'own-session');return entry;},onConfirm:(...args)=>events.push(args)},
    close(){events.push('closed');this.active=null;}};
  return {focus,entry,events,finish:()=>CookingFocus.prototype.finishCurrent.call(focus)};
}
test('ready button returns to chat and queues one explicit cooking request for its own session',()=>{
  const h=harness();h.finish();h.finish();
  assert.deepEqual(h.events,['closed',['own-session','食材已经全部备齐了，开始做菜吧。']]);
});
test('the ready button rechecks actual readiness and refuses stale or unsaved preparation',()=>{
  const notReady=harness();notReady.entry.snapshot.components[0].data.prep_ready=4;notReady.finish();assert.deepEqual(notReady.events,[]);
  for(const key of ['loading','saving','confirming','presentationPending','failedRequest','conflict','autoSaveError']) {
    const h=harness({[key]:true});h.finish();assert.deepEqual(h.events,[],key);
  }
  const running=harness();running.focus.activity=()=>({running:true});running.finish();assert.deepEqual(running.events,[]);
});
