import test from 'node:test';
import assert from 'node:assert/strict';
import { splitToFit, pageForAnchor, swipeDirection } from '../focus-pager.js';

test('long instructions preserve their full text across measured pages and prefer readable boundaries', () => {
  const text = '先把西红柿切块，留下一些汤汁。\n接着加入鸡蛋，轻轻推动。  保持中小火，避免粘锅。'.repeat(12);
  const pieces = splitToFit(text, (start, end) => end - start <= 70);
  assert.equal(pieces.map(piece => text.slice(piece.start, piece.end)).join(''), text);
  assert.ok(pieces.every(piece => piece.end - piece.start <= 70));
  assert.ok(pieces.length > 1);
  assert.equal(splitToFit('', () => true).length, 0);
  const afterHeading = splitToFit(text, (start, end) => end - start <= (start === 0 ? 30 : 70));
  assert.ok(afterHeading[0].end <= 30);
  assert.equal(afterHeading.map(piece => text.slice(piece.start, piece.end)).join(''), text);
});

test('resizing retains the logical passage and checked-item position rather than its old page number', () => {
  const resized = [
    [{ key:'step', start:0, end:80 }], [{ key:'step', start:80, end:160 }],
    [{ key:'shopping:oil', start:0, end:3 }],
  ];
  assert.equal(pageForAnchor(resized, { key:'step', start:120 }), 1);
  assert.equal(pageForAnchor(resized, { key:'shopping:oil', start:0 }), 2);
  assert.equal(pageForAnchor(resized, { key:'missing', start:0 }), 0);
});

test('only intentional horizontal touch gestures turn a card', () => {
  const start = { x:160, y:120, time:10 };
  assert.equal(swipeDirection(start, { x:70, y:132, time:300 }), 1);
  assert.equal(swipeDirection(start, { x:230, y:125, time:300 }), -1);
  assert.equal(swipeDirection(start, { x:155, y:260, time:300 }), 0);
  assert.equal(swipeDirection(start, { x:130, y:122, time:300 }), 0);
  assert.equal(swipeDirection(start, { x:60, y:120, time:1500 }), 0);
});
