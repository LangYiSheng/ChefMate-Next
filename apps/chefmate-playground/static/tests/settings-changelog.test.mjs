import test from 'node:test';
import assert from 'node:assert/strict';
import {renderChangelog} from '../settings-changelog.js';
import {SettingsInsights} from '../settings-insights.js';

class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.copy='';this.isConnected=true;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[];this.copy='';this.append(...items);}
  set textContent(value){this.children=[];this.copy=String(value);}
  get textContent(){return this.copy+this.children.map(child=>child.textContent).join('');}
  set innerHTML(_value){throw new Error('Release text must not be interpreted as HTML');}
}
const descendants=root=>[root,...root.children.flatMap(descendants)];
const release=(version='0.2.0',title='把一顿饭安排好')=>({version,date:'2026-10-22',title,summary:'这次有这些进步。',sections:[{title:'更顺手',items:['保存之后继续对话。']}]});
function install(t){const previous=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};t.after(()=>globalThis.document=previous);return new Element('section');}
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};}

test('release history identifies the installed version and preserves dates and grouped content',t=>{
  const host=install(t);
  renderChangelog(host,{current_version:'0.2.0',releases:[release(),release('0.1.0','第一次见面')]});
  const articles=host.children;
  assert.equal(articles.length,2);assert.match(articles[0].textContent,/0\.2\.0当前版本/);
  assert.doesNotMatch(articles[1].textContent,/当前版本/);
  assert.equal(descendants(host).find(item=>item.tagName==='TIME').dateTime,'2026-10-22');
  assert.match(articles[0].textContent,/更顺手保存之后继续对话/);
});

test('release copy stays plain text and an empty history has an explicit state',t=>{
  const host=install(t),copy='<img src=x onerror=alert(1)>';
  renderChangelog(host,{current_version:'0.2.0',releases:[release('0.2.0',copy)]});
  assert.match(host.textContent,/<img src=x onerror=alert\(1\)>/);
  assert.equal(descendants(host).some(item=>['IMG','SCRIPT','INPUT','BUTTON'].includes(item.tagName)),false);
  const empty=new Element('section');renderChangelog(empty,{releases:[]});assert.match(empty.textContent,/还没有发布更新记录/);
});

test('read-only changelog refresh rejects stale responses and disposal prevents late rendering',async t=>{
  const host=install(t),calls=[];
  const insights=new SettingsInsights({host,api:(path,options)=>{const response=deferred();calls.push({path,options,response});return response.promise;}});
  const first=insights.show('changelog'),second=insights.show('changelog');
  assert.equal(calls[0].path,'/api/changelog');assert.equal(calls[0].options.signal.aborted,true);
  assert.equal(calls[1].options.method,undefined);
  calls[1].response.resolve({current_version:'0.2.0',releases:[release('0.2.0','当前内容')]});await second;
  calls[0].response.resolve({current_version:'0.1.0',releases:[release('0.1.0','过期内容')]});await first;
  assert.match(host.textContent,/当前内容/);assert.doesNotMatch(host.textContent,/过期内容/);
  const third=insights.show('changelog');insights.dispose();host.replaceChildren(new Element('div'));const before=host.textContent;
  calls[2].response.resolve({releases:[release('0.2.0','离开后的内容')]});await third;
  assert.equal(host.textContent,before);assert.equal(calls[2].options.signal.aborted,true);
});
