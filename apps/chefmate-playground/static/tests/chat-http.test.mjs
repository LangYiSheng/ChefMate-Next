import test from 'node:test';
import assert from 'node:assert/strict';
import {api, normalizeSession, readEvents, safeText, safeValue} from '../chat-http.js';
import {safeLink} from '../chat-markdown.js';

function streamed(text, size=3) {
  const bytes=new TextEncoder().encode(text);
  return new Response(new ReadableStream({start(controller) {
    for(let offset=0;offset<bytes.length;offset+=size)controller.enqueue(bytes.slice(offset,offset+size));
    controller.close();
  }}));
}

test('SSE decoding retains split UTF-8, multiline data, named events and the final unterminated event',async()=>{
  const response=streamed(': heartbeat\r\nevent: assistant_delta\r\ndata: {"text":\r\ndata: "开饭啦"}\r\n\r\ndata: {"type":"done","status":"completed"}');
  const events=[];
  await readEvents(response,event=>events.push(event));
  assert.deepEqual(events,[{text:'开饭啦',type:'assistant_delta'},{type:'done',status:'completed'}]);
  assert.equal(response.body.locked,false);
});

test('a malformed stream reports a useful error and releases its reader',async()=>{
  const response=streamed('data: this is not JSON\n\n');
  await assert.rejects(readEvents(response,()=>{}),/无法读取的事件/);
  assert.equal(response.body.locked,false);
  await assert.rejects(readEvents({body:null},()=>{}),/可读取的响应/);
});

test('JSON failures keep status and payload for conflict recovery and redact diagnostic credentials',async t=>{
  const previous=globalThis.fetch;t.after(()=>globalThis.fetch=previous);
  let request;
  globalThis.fetch=async(path,options)=>{request={path,...options};return new Response(JSON.stringify({detail:'password=example-secret',revision:7}),{status:409});};
  await assert.rejects(api('/api/example',{method:'PATCH',body:'{}'}),error=>{
    assert.equal(error.status,409);assert.equal(error.data.revision,7);
    assert.equal(error.message,'password=[已隐藏]');return true;
  });
  assert.equal(request.headers.Accept,'application/json');
  assert.equal(request.headers['Content-Type'],'application/json');
  assert.equal(safeText('https://user:example@localhost/path'),'https://[已隐藏]@localhost/path');
  assert.deepEqual(safeValue({api_key:'example',nested:{token:'example',ok:1}}),{api_key:'[已隐藏]',nested:{token:'[已隐藏]',ok:1}});
});

test('persisted running turns remain streaming and interrupted turns retain their original retry images',()=>{
  const running=normalizeSession({id:'chat',last_status:'running',messages:[{role:'system',content:'private'},{role:'user',text:'帮我做饭'}],active_turn:{id:'turn',text:'查找中',tool_calls:[{name:'search_recipes',status:'running'}]}});
  assert.equal(running.running,true);assert.equal(running.messages.length,2);
  assert.equal(running.messages[1].streaming,true);assert.equal(running.messages[1].turn_id,'turn');
  assert.equal(running.messages[1].tool_calls[0].running,true);
  const interrupted=normalizeSession({last_status:'interrupted',messages:[{role:'user',content:'看看食材',images:[{id:'image'}]}]});
  assert.equal(interrupted.messages[1].retryMessage,'看看食材');
  assert.deepEqual(interrupted.messages[1].retryImages,[{id:'image'}]);
  assert.match(interrupted.messages[1].error,/重启中断/);
});

test('model links permit only credential-free HTTP and HTTPS destinations',()=>{
  assert.equal(safeLink('https://example.com/recipe'),'https://example.com/recipe');
  for(const href of ['javascript:alert(1)','data:text/html,hello','file:///tmp/file','https://user:password@example.com/'])assert.equal(safeLink(href),null);
});
