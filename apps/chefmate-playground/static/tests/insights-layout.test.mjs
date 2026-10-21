import test from 'node:test';
import assert from 'node:assert/strict';
import {usageCalendar,usageDayLabel} from '../settings-insights.js';
import {contextBreakdown} from '../context-meter.js';
import {traceRequests} from '../developer-panel.js';

test('daily heatmap distinguishes no requests, unknown usage and partially observed zero',()=>{
  const calendar=usageCalendar({period:{from:'2026-10-18',to:'2026-10-21'},daily:[
    {date:'2026-10-19',model_calls:2,known_totals:{total_tokens:null}},
    {date:'2026-10-20',model_calls:2,known_totals:{total_tokens:0},coverage:{total_tokens:{known:1,calls:2}}},
    {date:'2026-10-21',model_calls:1,known_totals:{total_tokens:100},coverage:{total_tokens:{reported:1,known:1,calls:1}}},
  ]});
  assert.deepEqual(calendar.days.map(day=>day.status),['empty','unknown','partial','complete']);
  assert.equal(calendar.days[1].value,null);assert.equal(calendar.days[2].value,0);
  assert.match(usageDayLabel(calendar.days[1]),/Token未报告/);assert.match(usageDayLabel(calendar.days[2]),/0 Token（已知小计）/);
  assert.match(usageDayLabel(calendar.days[0]),/没有模型请求/);assert.equal(calendar.days.at(-1).level,4);
});

test('calendar uses server date boundaries, fills actual calendar gaps and caps a long all-time plot',()=>{
  const short=usageCalendar({period:{from:'2024-02-28',to:'2024-03-01'},daily:[]});
  assert.deepEqual(short.days.map(day=>day.date),['2024-02-28','2024-02-29','2024-03-01']);assert.equal(short.offset,2);
  const long=usageCalendar({period:{from:'2020-01-01',to:'2026-10-21'},daily:[]});
  assert.equal(long.days.length,366);assert.equal(long.clipped,true);assert.equal(long.days.at(-1).date,'2026-10-21');
  assert.equal(usageCalendar({period:{to:'not-a-date'}}).days.length,0);
});

test('context distribution ranks actual category estimates without treating character share as token share',()=>{
  const rows=contextBreakdown({categories:[{key:'system',label:'系统',chars:400,estimated_tokens:100},{key:'tools',label:'工具',chars:300,estimated_tokens:300}]});
  assert.equal(rows[0].key,'tools');assert.equal(rows[0].percent,75);assert.equal(rows[0].tokens,300);assert.equal(rows[1].percent,25);
  const legacy=contextBreakdown({categories:[{key:'system',chars:100},{key:'tools',chars:300}]});
  assert.equal(legacy[0].tokens,null);assert.equal(legacy[0].percent,75);
  assert.equal(contextBreakdown({categories:[{key:'system',chars:0,estimated_tokens:0}]})[0].percent,0);
});

test('trace request usage is matched by recorded round rather than array position',()=>{
  const requests=traceRequests({debug_trace:[{round:1},{round:2},{round:3}],usage:{calls:[{round:1,input_tokens:12},{round:3,input_tokens:37}]}});
  assert.equal(requests[0].usage.input_tokens,12);assert.equal(requests[1].usage,null);assert.equal(requests[2].usage.input_tokens,37);
  assert.deepEqual(traceRequests(null),[]);
});
