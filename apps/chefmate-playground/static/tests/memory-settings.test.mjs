import test from 'node:test';
import assert from 'node:assert/strict';

// Exercise the real memory editor handlers with a deliberately small DOM.
// The response is deferred so edits cannot race the committed revision.
class Element extends EventTarget {
  constructor(tag) {
    super(); this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.attributes = {}; this.className = ''; this.value = ''; this.disabled = false;
    this.validity = {valid:true};
  }
  setAttribute(name, value) {this.attributes[name] = String(value);}
  getAttribute(name) {return this.attributes[name];}
  get classList() {return {add:name => {this.className += ` ${name}`;}};}
  append(...items) {
    for (const item of items) {
      if (item.parentNode) item.parentNode.children.splice(item.parentNode.children.indexOf(item), 1);
      item.parentNode = this; this.children.push(item);
    }
  }
  replaceChildren(...items) {for (const child of this.children) child.parentNode = null; this.children = []; this.copy = ''; this.append(...items);}
  set textContent(value) {this.copy = String(value); this.children = [];}
  get textContent() {return (this.copy || '') + this.children.map(child => child.textContent).join('');}
  focus() {}
}
const descendants = root => [root, ...root.children.flatMap(descendants)];
const byId = (root, id) => descendants(root).find(item => item.id === id);
const response = (data, ok = true) => ({ok, json:async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));
let sequence = 0;

for (const succeeds of [true, false]) test(`memory save locks edits until ${succeeds ? 'the committed response arrives' : 'a failure restores the same draft'}`, async t => {
  const previous = {document:globalThis.document, window:globalThis.window, fetch:globalThis.fetch};
  t.after(() => Object.assign(globalThis, previous));
  globalThis.document = {createElement:tag => new Element(tag)};
  globalThis.window = new EventTarget();
  const initial = {revision:1, user_notes:'少盐', enabled:true, summary:'喜欢清淡'};
  let settle, mutations = 0;
  globalThis.fetch = async (_, options = {}) => {
    if (!options.method) return response(initial);
    mutations++;
    return await new Promise(resolve => {settle = resolve;});
  };
  const busy = [], dirty = [];
  window.addEventListener('chefmate:memory-busy', event => busy.push(event.detail.busy));
  window.addEventListener('chefmate:memory-dirty', event => dirty.push(event.detail.dirty));
  await import(`../memory-settings.js?test=${sequence++}`);
  const host = new Element('section');
  window.dispatchEvent(new CustomEvent('chefmate:memory-settings', {detail:{container:host}}));
  await flush();
  const notes = byId(host, 'personal-memory-notes'), toggle = byId(host, 'personal-memory-enabled');
  notes.value = '少盐，不吃香菜'; notes.dispatchEvent(new Event('input'));
  const save = descendants(host).find(item => item.tagName === 'BUTTON' && item.textContent === '保存记忆');
  const operation = save.onclick();
  assert.equal(notes.disabled, true); assert.equal(toggle.disabled, true); assert.equal(save.disabled, true);
  assert.deepEqual(busy, [true]);
  await save.onclick();
  assert.equal(mutations, 1, 'a repeated gesture cannot submit the pending revision twice');
  settle(succeeds ? response({...initial, revision:2, user_notes:notes.value}) : response({detail:'连接中断'}, false));
  await operation;
  assert.equal(byId(host, 'personal-memory-notes').value, '少盐，不吃香菜');
  assert.equal(byId(host, 'personal-memory-notes').disabled, false);
  assert.equal(byId(host, 'personal-memory-enabled').disabled, false);
  assert.deepEqual(busy, [true, false]);
  assert.deepEqual(dirty, succeeds ? [true, false] : [true]);
  assert.match(host.textContent, succeeds ? /记忆已保存/ : /连接中断/);
});
