import test from 'node:test';
import assert from 'node:assert/strict';
import {stepControl} from '../form-controls.js';

// These contract tests cover delegation and events. Real min/max/step and
// keyboard behavior are exercised against native inputs in browser acceptance.
function control(properties = {}) {
  const input = new EventTarget();
  Object.assign(input, {value:'2', disabled:false, readOnly:false}, properties);
  input.calls = [];
  input.stepUp = () => {input.calls.push('up'); input.value = '3';};
  input.stepDown = () => {input.calls.push('down'); input.value = '1';};
  return input;
}

test('number buttons delegate stepping to the native control and notify draft listeners in order', () => {
  const input = control(), events = [];
  input.addEventListener('input', event => events.push([event.type, event.bubbles, input.value]));
  input.addEventListener('change', event => events.push([event.type, event.bubbles, input.value]));
  assert.equal(stepControl(input, 'up'), true);
  assert.deepEqual(input.calls, ['up']);
  assert.deepEqual(events, [['input', true, '3'], ['change', true, '3']]);
  assert.equal(stepControl(input, 'down'), true);
  assert.deepEqual(input.calls, ['up', 'down']);
});

test('disabled, read-only and unsupported actions never step or emit changes', () => {
  for (const properties of [{disabled:true}, {readOnly:true}, {}]) {
    const input = control(properties), events = [];
    input.addEventListener('input', event => events.push(event.type));
    assert.equal(stepControl(input, Object.keys(properties).length ? 'up' : 'left'), false);
    assert.deepEqual(input.calls, []);
    assert.deepEqual(events, []);
    assert.equal(input.value, '2');
  }
});

test('native boundary refusal and invalid step configuration never produce false draft edits', () => {
  const input = control(), events = [];
  input.addEventListener('input', event => events.push(event.type));
  input.stepUp = () => {};
  assert.equal(stepControl(input, 'up'), false);
  input.stepUp = () => {throw new DOMException('Unsupported step');};
  assert.equal(stepControl(input, 'up'), false);
  assert.equal(input.value, '2');
  assert.deepEqual(events, []);
});
