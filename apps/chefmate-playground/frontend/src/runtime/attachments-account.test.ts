import {afterEach, describe, expect, it, vi} from 'vitest';
import {BrowserAttachments} from './attachments';

afterEach(() => {localStorage.clear(); vi.unstubAllGlobals();});
describe('Account-owned image drafts', () => {
  it('pins each draft persistence instance to its account even when another tab changes the browser account', async () => {
    const opened: string[] = [];
    vi.stubGlobal('indexedDB', {open: (name: string) => {
      opened.push(name);
      const request = {error: new Error('blocked-test-database'), onerror: null as (() => void) | null};
      queueMicrotask(() => request.onerror?.()); return request;
    }});
    localStorage.setItem('chefmate:browser-account', 'account-a');
    const firstAccount = new BrowserAttachments();
    localStorage.setItem('chefmate:browser-account', 'account-b');
    const secondAccount = new BrowserAttachments();
    await expect(firstAccount.save({})).rejects.toThrow('blocked-test-database');
    await expect(secondAccount.load()).rejects.toThrow('blocked-test-database');
    expect(opened).toEqual(['chefmate-react-drafts:account-a', 'chefmate-react-drafts:account-b']);
  });
  it('keeps an explicit signed-out draft store separate from every signed-in account', async () => {
    const opened: string[] = [];
    vi.stubGlobal('indexedDB', {open: (name: string) => {
      opened.push(name); const request = {error: new Error('blocked'), onerror: null as (() => void) | null}; queueMicrotask(() => request.onerror?.()); return request;
    }});
    localStorage.setItem('chefmate:browser-account', 'account-b');
    await expect(new BrowserAttachments(null).load()).rejects.toThrow('blocked');
    await expect(new BrowserAttachments('account-c').load()).rejects.toThrow('blocked');
    expect(opened).toEqual(['chefmate-react-drafts', 'chefmate-react-drafts:account-c']);
  });
});
