import { useEffect, useState, useSyncExternalStore } from 'react';
import { workspaceStore, type Entry } from './store';

export function useWorkspace(sessionId: string | null, running?: boolean): Entry | null {
  if (sessionId) workspaceStore.entry(sessionId);
  const state = useSyncExternalStore(workspaceStore.subscribe, workspaceStore.getSnapshot);
  useEffect(() => { if (sessionId) void workspaceStore.load(sessionId).catch(() => {}); }, [sessionId]);
  useEffect(() => { if (sessionId && running !== undefined) workspaceStore.setRunning(sessionId, running); }, [sessionId, running]);
  return sessionId ? state.entries[sessionId] || null : null;
}
export function useViewportHeight() {
  const read = () => window.visualViewport?.height || window.innerHeight;
  const [height, setHeight] = useState(read);
  useEffect(() => {
    const resize = () => setHeight(read());
    window.addEventListener('resize', resize); window.visualViewport?.addEventListener('resize', resize);
    return () => { window.removeEventListener('resize', resize); window.visualViewport?.removeEventListener('resize', resize); };
  }, []);
  return height;
}
