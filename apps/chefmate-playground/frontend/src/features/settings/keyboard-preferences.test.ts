import {beforeEach, describe, expect, it, vi} from 'vitest';
import {readKeyboardPreferences, saveKeyboardPreferences, shouldSendMessage, subscribeKeyboardPreferences} from './keyboard-preferences';

beforeEach(() => {localStorage.clear();});

describe('saved keyboard preferences', () => {
  it('keeps a stable normalized snapshot and publishes persisted changes', () => {
    const original = readKeyboardPreferences(), notify = vi.fn(), unsubscribe = subscribeKeyboardPreferences(notify);
    expect(readKeyboardPreferences()).toBe(original);
    saveKeyboardPreferences({send_mode: 'mod-enter'});
    const accepted = readKeyboardPreferences();
    expect(accepted).toEqual({send_mode: 'mod-enter'}); expect(accepted).not.toBe(original);
    expect(readKeyboardPreferences()).toBe(accepted); expect(notify).toHaveBeenCalledOnce();
    localStorage.setItem('chefmate:keyboard-preferences', JSON.stringify({send_mode: 'enter'}));
    window.dispatchEvent(new StorageEvent('storage', {key: 'chefmate:keyboard-preferences'}));
    expect(notify).toHaveBeenCalledTimes(2); expect(readKeyboardPreferences()).toEqual({send_mode: 'enter'});
    unsubscribe(); window.dispatchEvent(new StorageEvent('storage', {key: null})); expect(notify).toHaveBeenCalledTimes(2);
  });

  it('uses a safe default for damaged saved preferences and does not announce a failed save', () => {
    localStorage.setItem('chefmate:keyboard-preferences', '{invalid'); expect(readKeyboardPreferences().send_mode).toBe('enter');
    localStorage.setItem('chefmate:keyboard-preferences', JSON.stringify({send_mode: 'unknown'})); expect(readKeyboardPreferences().send_mode).toBe('enter');
    const notify = vi.fn(), unsubscribe = subscribeKeyboardPreferences(notify);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => {throw new Error('storage denied');});
    expect(() => saveKeyboardPreferences({send_mode: 'mod-enter'})).toThrow('storage denied');
    expect(readKeyboardPreferences().send_mode).toBe('enter'); expect(notify).not.toHaveBeenCalled(); unsubscribe();
  });

  it('uses exactly the selected send combination and always preserves multiline and IME input', () => {
    const enter = {send_mode: 'enter'} as const, modified = {send_mode: 'mod-enter'} as const;
    expect(shouldSendMessage({key: 'Enter'}, enter)).toBe(true);
    expect(shouldSendMessage({key: 'Enter', ctrlKey: true}, enter)).toBe(false);
    expect(shouldSendMessage({key: 'Enter'}, modified)).toBe(false);
    expect(shouldSendMessage({key: 'Enter', ctrlKey: true}, modified)).toBe(true);
    expect(shouldSendMessage({key: 'Enter', metaKey: true}, modified)).toBe(true);
    for (const preference of [enter, modified]) {
      for (const event of [{shiftKey: true}, {altKey: true}, {isComposing: true}, {keyCode: 229}, {nativeEvent: {isComposing: true}}, {nativeEvent: {keyCode: 229}}]) {
        expect(shouldSendMessage({key: 'Enter', ctrlKey: true, ...event}, preference)).toBe(false);
      }
      expect(shouldSendMessage({key: 'a', ctrlKey: true}, preference)).toBe(false);
    }
  });
});
