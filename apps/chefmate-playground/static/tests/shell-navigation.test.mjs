import test from 'node:test';
import assert from 'node:assert/strict';
import { ShellNavigation } from '../shell.js';

function harness({ page = 'chat', allow = true } = {}) {
  const checks = [], changes = [], actions = [];
  let closeRequests = 0;
  const navigation = new ShellNavigation({
    page,
    beforeChange: target => { checks.push(target); return state.allow; },
    applyPage: (target, previous) => changes.push({ target, previous }),
    requestSettingsClose: () => { closeRequests++; },
  });
  const state = {
    navigation, checks, changes, actions, allow,
    get closeRequests() { return closeRequests; },
    // This is the sidebar capture contract: only continue the target's actual
    // handler when accepted, and bypass a second guard during a saved replay.
    click(target, action) {
      if (!navigation.replaying && !navigation.request(target, () => state.click(target, action))) return false;
      actions.push(action);
      return true;
    },
  };
  return state;
}

test('setPage reports rejection without changing the page or applying it', () => {
  const state = harness({ page: 'recipes', allow: false });
  assert.equal(state.navigation.setPage('journal'), false);
  assert.equal(state.navigation.page, 'recipes');
  assert.deepEqual(state.checks, ['journal']);
  assert.deepEqual(state.changes, []);
  state.allow = true;
  assert.equal(state.navigation.setPage('journal'), true);
  assert.equal(state.navigation.page, 'journal');
  assert.deepEqual(state.changes, [{ target: 'journal', previous: 'recipes' }]);
});

for (const action of ['session', 'new-chat', 'brand', 'primary-page']) {
  test(`blocked ${action} does not run its handler and resumes once after confirmation`, () => {
    const state = harness({ page: 'recipes', allow: false });
    const target = action === 'primary-page' ? 'journal' : 'chat';
    assert.equal(state.click(target, action), false);
    assert.deepEqual(state.actions, []);
    assert.equal(state.navigation.page, 'recipes');
    state.allow = true;
    assert.equal(state.navigation.setPage(target), true);
    assert.deepEqual(state.actions, [action]);
    assert.deepEqual(state.changes, [{ target, previous: 'recipes' }]);
    assert.deepEqual(state.checks, [target, target]);
    assert.equal(state.navigation.replaying, false);
    assert.equal(state.navigation.setPage(target), true);
    assert.deepEqual(state.actions, [action]);
    assert.equal(state.changes.length, 1);
  });
}

test('cancelling a center departure clears its concrete click before later navigation', () => {
  const state = harness({ page: 'recipes', allow: false });
  state.click('chat', 'old-session');
  state.navigation.cancelNavigation();
  state.allow = true;
  assert.equal(state.navigation.setPage('chat'), true);
  assert.deepEqual(state.actions, []);
});

test('a fresh gesture replaces a blocked target even when both lead to chat', () => {
  const state = harness({ page: 'recipes', allow: false });
  state.click('chat', 'old-session');
  state.click('chat', 'new-chat');
  state.allow = true;
  state.navigation.setPage('chat');
  assert.deepEqual(state.actions, ['new-chat']);
});

test('an unrelated programmatic destination cannot replay an old blocked click', () => {
  const state = harness({ page: 'recipes', allow: false });
  state.click('chat', 'old-session');
  state.allow = true;
  state.navigation.setPage('journal');
  state.navigation.setPage('chat');
  assert.deepEqual(state.actions, []);
  assert.deepEqual(state.changes, [
    { target: 'journal', previous: 'recipes' },
    { target: 'chat', previous: 'journal' },
  ]);
});

test('settings acceptance guards and applies only the final target and replays it once', () => {
  const state = harness({ page: 'settings' });
  assert.equal(state.click('chat', 'old-session'), false);
  assert.equal(state.click('journal', 'primary-page'), false);
  assert.equal(state.closeRequests, 1);
  assert.deepEqual(state.actions, []);
  assert.deepEqual(state.checks, []);
  assert.deepEqual(state.changes, []);
  assert.equal(state.navigation.acceptSettingsClose(), true);
  assert.deepEqual(state.checks, ['journal']);
  assert.deepEqual(state.changes, [{ target: 'journal', previous: 'settings' }]);
  assert.deepEqual(state.actions, ['primary-page']);
  assert.equal(state.navigation.acceptSettingsClose(), false);
  assert.deepEqual(state.actions, ['primary-page']);
});

test('settings cancellation keeps the page and discards the queued app handler', () => {
  const state = harness({ page: 'settings' });
  state.click('chat', 'old-session');
  state.navigation.cancelSettingsClose();
  assert.equal(state.navigation.page, 'settings');
  assert.deepEqual(state.changes, []);
  assert.deepEqual(state.actions, []);
  state.click('chat', 'new-chat');
  assert.equal(state.closeRequests, 2);
  state.navigation.acceptSettingsClose();
  assert.deepEqual(state.actions, ['new-chat']);
});

test('closing settings from its own button returns to chat without replaying a toggle', () => {
  const state = harness({ page: 'settings' });
  assert.equal(state.navigation.acceptSettingsClose(), true);
  assert.equal(state.navigation.page, 'chat');
  assert.deepEqual(state.changes, [{ target: 'chat', previous: 'settings' }]);
  assert.deepEqual(state.actions, []);
});

test('programmatic departures from settings use its existing confirmation flow', () => {
  const state = harness({ page: 'settings' });
  assert.equal(state.navigation.setPage('recipes'), false);
  assert.equal(state.closeRequests, 1);
  assert.equal(state.navigation.page, 'settings');
  assert.deepEqual(state.checks, []);
  assert.equal(state.navigation.acceptSettingsClose(), true);
  assert.deepEqual(state.changes, [{ target: 'recipes', previous: 'settings' }]);
});

test('opening settings from a dirty center waits before creating or focusing its frame', () => {
  const state = harness({ page: 'recipes', allow: false });
  assert.equal(state.navigation.request('settings'), false);
  assert.deepEqual(state.changes, []);
  assert.equal(state.closeRequests, 0);
  state.allow = true;
  assert.equal(state.navigation.setPage('settings'), true);
  assert.deepEqual(state.changes, [{ target: 'settings', previous: 'recipes' }]);
  assert.deepEqual(state.actions, []);
});

test('a page can still block navigation after settings accepts, without a second settings prompt', () => {
  const state = harness({ page: 'settings', allow: false });
  state.click('recipes', 'primary-page');
  assert.equal(state.navigation.acceptSettingsClose(), false);
  assert.equal(state.navigation.page, 'settings');
  assert.deepEqual(state.actions, []);
  state.allow = true;
  assert.equal(state.navigation.setPage('recipes'), true);
  assert.equal(state.closeRequests, 1);
  assert.deepEqual(state.actions, ['primary-page']);
});

test('cancelling a blocked departure also discards a prior settings-close acceptance', () => {
  const state = harness({ page: 'settings', allow: false });
  state.click('recipes', 'primary-page');
  state.navigation.acceptSettingsClose();
  state.navigation.cancelNavigation();
  state.allow = true;
  assert.equal(state.navigation.setPage('recipes'), false);
  assert.equal(state.closeRequests, 2);
  state.navigation.acceptSettingsClose();
  assert.deepEqual(state.actions, []);
});

test('same-page navigation does not discard an editor or duplicate page-change effects', () => {
  const state = harness({ page: 'recipes', allow: false });
  assert.equal(state.click('recipes', 'primary-page'), true);
  assert.equal(state.navigation.setPage('recipes'), true);
  assert.deepEqual(state.checks, []);
  assert.deepEqual(state.changes, []);
  assert.deepEqual(state.actions, ['primary-page']);
});

test('a replay failure clears its bypass flag and cannot run a second time', () => {
  const state = harness({ page: 'recipes', allow: false });
  let attempted = 0;
  state.navigation.request('chat', () => { attempted++; throw new Error('target failed'); });
  state.allow = true;
  assert.throws(() => state.navigation.setPage('chat'), /target failed/);
  assert.equal(attempted, 1);
  assert.equal(state.navigation.replaying, false);
  assert.equal(state.navigation.setPage('chat'), true);
  assert.equal(attempted, 1);
});
