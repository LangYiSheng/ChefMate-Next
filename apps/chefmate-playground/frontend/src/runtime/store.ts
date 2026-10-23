import { HttpError, normalizeSession, normalizeToolCall, readEvents, requestJson, safeText } from './http';
import { BrowserAttachments, imageMetadata, prepareImage, releaseImages } from './attachments';
import type { ApiData, AttachmentPersistence, Continuation, ImageDraft, Message, RunState, RuntimeState, SendOptions, Session, StorageLike, StoreOptions, WorkspaceAdapter } from './types';

const DRAFTS_KEY = 'chefmate:react-drafts';
const ACTIVE_KEY = 'chefmate:active-session';
const CONTINUATIONS_KEY = 'chefmate:react-continuations';
const SUBMISSIONS_KEY = 'chefmate:react-submissions';
const blankId = () => `draft:${crypto.randomUUID()}`;
const url = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;
const omit = <T,>(record: Record<string, T>, key: string): Record<string, T> => {
  const next = { ...record }; delete next[key]; return next;
};
const errorText = (error: unknown, fallback = '操作没有完成，请重试。') => safeText(error instanceof Error ? error.message : fallback);

interface ActiveRun extends RunState {
  controller: AbortController;
  lifecycle: number;
  messageIndex: number;
  done: boolean;
  resultReceived: boolean;
  failed: boolean;
  httpRejected: boolean;
  accepted: boolean;
  terminalStatus?: string;
  retryable?: boolean;
}

interface PendingSubmission {
  requestId: string;
  input: string;
  imageIds: string[];
  paired: boolean;
}

/** The service owns jobs; React components only subscribe to immutable snapshots. */
export class ChefStore {
  private state: RuntimeState;
  private listeners = new Set<() => void>();
  private fetcher: typeof fetch;
  private storage: StorageLike | null;
  private attachments: AttachmentPersistence;
  private prepareImage: (file: File) => Promise<ImageDraft>;
  private polls = new Map<string, ReturnType<typeof setTimeout>>();
  private active = new Map<string, ActiveRun>();
  private imageOperations = new Map<string, object>();
  private sessionEpoch = new Map<string, number>();
  private deleted = new Set<string>();
  private touchedImages = new Set<string>();
  private adapter: WorkspaceAdapter | null = null;
  private lifecycle = 0;
  private selectionEpoch = 0;
  private listEpoch = 0;
  private statusEpoch = 0;
  private started = false;
  private startTask: Promise<void> | null = null;
  private hydratedImages = false;
  private persistQueue: Promise<void> = Promise.resolve();
  private continuationScheduled = false;
  private pollInterval: number;
  private submissions: Record<string, PendingSubmission>;
  private onFocus = (): void => {
    if (!this.started || this.state.loading) return;
    void this.loadSessions().catch(() => undefined);
    if (this.state.currentId && !this.active.has(this.state.currentId)) void this.refreshSession().catch(() => undefined);
  };

  constructor(options: StoreOptions = {}) {
    this.fetcher = options.fetch || ((...args) => fetch(...args));
    try { this.storage = options.storage === undefined ? globalThis.localStorage || null : options.storage; } catch { this.storage = null; }
    this.attachments = options.attachments || new BrowserAttachments();
    this.prepareImage = options.prepareImage || prepareImage;
    this.pollInterval = options.pollInterval ?? 2500;
    const saved = this.read<{ blankId?: string; drafts?: Record<string, string> }>(DRAFTS_KEY, {});
    const continuations = this.read<Record<string, Continuation>>(CONTINUATIONS_KEY, {});
    this.submissions = this.read<Record<string, PendingSubmission>>(SUBMISSIONS_KEY, {});
    for (const [id, value] of Object.entries(this.submissions)) {
      if (!value || typeof value.requestId !== 'string' || typeof value.input !== 'string' || !Array.isArray(value.imageIds)) delete this.submissions[id];
    }
    // A reload may happen after acceptance but before its response. Reconcile first.
    for (const [id, value] of Object.entries(continuations)) {
      if (!value || typeof value.message !== 'string' || typeof value.requestId !== 'string') delete continuations[id];
      else continuations[id] = { ...value, stage: value.stage === 'sending' ? 'queued' : value.stage };
    }
    this.state = {
      status: null, sessions: [], currentId: this.readString(ACTIVE_KEY), session: null, cache: {},
      blankId: saved.blankId || blankId(), drafts: saved.drafts || {}, imageDrafts: {}, imageUploads: {}, runs: {}, continuations,
      loading: false, preparing: false, sessionsLoading: false, nextCursor: null, error: null,
    };
  }

  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  getSnapshot = (): RuntimeState => this.state;
  getServerSnapshot = (): RuntimeState => this.state;

  private readString(key: string): string | null { try { return this.storage?.getItem(key) || null; } catch { return null; } }
  private read<T>(key: string, fallback: T): T { try { return JSON.parse(this.storage?.getItem(key) || 'null') ?? fallback; } catch { return fallback; } }
  private write(key: string, value: unknown): void { try { this.storage?.setItem(key, JSON.stringify(value)); } catch { /* In-memory work remains usable. */ } }
  private saveSelection(): void { try { if (this.state.currentId) this.storage?.setItem(ACTIVE_KEY, this.state.currentId); else this.storage?.removeItem(ACTIVE_KEY); } catch { /* Selection still lives in memory. */ } }
  private emit(patch: Partial<RuntimeState> = {}): void {
    const next = { ...this.state, ...patch };
    next.session = next.currentId ? next.cache[next.currentId] || null : null;
    this.state = next;
    for (const listener of this.listeners) listener();
  }
  private request<T = any>(path: string, options: RequestInit = {}): Promise<T> { return requestJson<T>(this.fetcher, path, options); }
  private valid(lifecycle: number): boolean { return this.lifecycle === lifecycle; }
  private report(error: unknown): void { this.emit({ error: errorText(error) }); }
  clearError = (): void => { this.emit({ error: null }); };
  draftKey = (id: string | null = this.state.currentId): string => id || this.state.blankId;
  busy = (id: string | null = this.state.currentId): boolean => Boolean(id && (this.active.has(id) || this.state.cache[id]?.running));
  pendingQuestion = (id: string | null = this.state.currentId): ApiData | null => {
    if (!id) return null;
    // An explicit null from the workspace is authoritative after an answer saves.
    const pending = this.adapter?.pendingQuestion?.(id);
    return pending !== undefined ? pending : this.state.cache[id]?.pending_question || null;
  };
  setWorkspaceAdapter(adapter: WorkspaceAdapter): void { this.adapter = adapter; this.scheduleContinuations(); }

  private saveDrafts(): void {
    this.write(DRAFTS_KEY, { blankId: this.state.blankId, drafts: this.state.drafts });
  }
  private saveImages(): void {
    const images = this.state.imageDrafts;
    this.persistQueue = this.persistQueue.catch(() => undefined).then(() => this.attachments.save(images)).catch(() => {
      if (Object.values(this.state.imageDrafts).some(items => items.length)) this.emit({ error: '图片仍保留在当前页面，浏览器暂时无法保存本地图片草稿。' });
    });
  }
  private saveContinuations(): void { this.write(CONTINUATIONS_KEY, this.state.continuations); }
  private setCache(id: string, session: Session): void {
    if (this.deleted.has(id)) return;
    this.emit({ cache: { ...this.state.cache, [id]: session }, sessions: this.state.sessions.map(item => item.id === id
      ? { ...item, title: session.title, pinned: session.pinned, running: session.running, pending_question: session.pending_question, awaiting_user: session.awaiting_user, conversation_state: session.conversation_state }
      : item) });
  }

  start = (): Promise<void> => {
    if (this.started) return this.startTask || Promise.resolve();
    this.started = true;
    const lifecycle = ++this.lifecycle;
    globalThis.addEventListener?.('focus', this.onFocus);
    this.emit({ loading: true, error: null });
    this.startTask = (async () => {
      if (!this.hydratedImages) {
        try {
          const stored = await this.attachments.load();
          if (!this.valid(lifecycle)) return;
          const images = { ...this.state.imageDrafts };
          for (const [id, items] of Object.entries(stored)) if (!this.touchedImages.has(id)) {
            images[id] = items.map(image => image.blob ? { ...image, previewUrl: URL.createObjectURL(image.blob) } : image);
          }
          this.hydratedImages = true; this.emit({ imageDrafts: images });
        } catch { /* Text-only use is available if local attachment storage is disabled. */ }
      }
      if (!this.valid(lifecycle)) return;
      const recoverIds = new Set([...Object.keys(this.submissions), ...(this.state.currentId ? [this.state.currentId] : [])]);
      await Promise.allSettled([this.loadStatus(), this.loadSessions(), ...[...recoverIds].map(id => this.refreshSession(id))]);
      if (!this.valid(lifecycle)) return;
      this.emit({ loading: false });
      this.scheduleContinuations();
    })();
    return this.startTask;
  };

  /** Detach transport and local clocks; only cancelTurn asks the server to stop. */
  dispose = (): void => {
    this.started = false; this.startTask = null; this.lifecycle++;
    globalThis.removeEventListener?.('focus', this.onFocus);
    this.listEpoch++; this.statusEpoch++; this.selectionEpoch++;
    for (const timer of this.polls.values()) clearTimeout(timer);
    this.polls.clear();
    for (const run of this.active.values()) run.controller.abort();
    this.active.clear(); this.imageOperations.clear();
    this.emit({ runs: {}, imageUploads: {}, preparing: false, loading: false, sessionsLoading: false });
  };

  loadStatus = async (): Promise<void> => {
    const lifecycle = this.lifecycle, epoch = ++this.statusEpoch;
    try {
      const status = await this.request<ApiData>('/api/status');
      if (this.valid(lifecycle) && epoch === this.statusEpoch) { this.emit({ status }); this.scheduleContinuations(); }
    } catch (error) { if (this.valid(lifecycle) && epoch === this.statusEpoch) this.report(error); throw error; }
  };

  loadSessions = async (more = false): Promise<void> => {
    if (more && (this.state.sessionsLoading || !this.state.nextCursor)) return;
    const lifecycle = this.lifecycle, epoch = ++this.listEpoch;
    const target = more ? 20 : Math.max(20, this.state.sessions.length);
    let cursor = more ? this.state.nextCursor : null;
    let items = more ? [...this.state.sessions] : [], received = 0;
    this.emit({ sessionsLoading: true });
    try {
      do {
        const page = await this.request<ApiData>(`/api/sessions?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
        if (!this.valid(lifecycle) || epoch !== this.listEpoch) return;
        const byId = new Map(items.map(item => [item.id, item]));
        for (const item of page.items || []) if (!this.deleted.has(item.id)) byId.set(item.id, normalizeSession(item));
        items = [...byId.values()]; received += (page.items || []).length;
        const next = page.next_cursor || null;
        if (next && next === cursor) throw new Error('会话列表读取失败，请重试。');
        cursor = next;
      } while (!more && cursor && received < target);
      this.emit({ sessions: items, nextCursor: cursor });
      for (const item of items) if (item.running && !this.active.has(item.id)) this.schedulePoll(item.id);
    } catch (error) { if (this.valid(lifecycle) && epoch === this.listEpoch) this.report(error); throw error; }
    finally { if (this.valid(lifecycle) && epoch === this.listEpoch) this.emit({ sessionsLoading: false }); }
  };
  moreSessions = (): Promise<void> => this.loadSessions(true);

  newSession = (_options: { retainDraft?: boolean } = {}): void => {
    if (this.state.preparing) return;
    this.selectionEpoch++;
    this.emit({ currentId: null, blankId: blankId(), error: null });
    this.saveSelection(); this.saveDrafts();
  };

  recoverableDraftKey = (): string | null => {
    const current = this.draftKey();
    return [...new Set([...Object.keys(this.state.drafts), ...Object.keys(this.state.imageDrafts)])].reverse()
      .find(key => key !== current && key.startsWith('draft:') && (this.state.drafts[key]?.trim() || this.state.imageDrafts[key]?.length)) || null;
  };
  resumeDraft = (key = this.recoverableDraftKey()): void => {
    if (!key || this.state.preparing) return;
    this.selectionEpoch++; this.emit({ currentId: null, blankId: key, error: null }); this.saveSelection(); this.saveDrafts();
  };

  openSession = async (id: string): Promise<void> => {
    this.selectionEpoch++;
    this.emit({ currentId: id, error: null }); this.saveSelection();
    if (!this.active.has(id)) await this.refreshSession(id);
  };

  refreshSession = async (id: string | null = this.state.currentId): Promise<void> => {
    if (!id || this.active.has(id) || this.deleted.has(id)) return;
    const lifecycle = this.lifecycle, epoch = (this.sessionEpoch.get(id) || 0) + 1;
    this.sessionEpoch.set(id, epoch);
    try {
      const session = normalizeSession(await this.request(url(id)));
      if (!this.valid(lifecycle) || this.sessionEpoch.get(id) !== epoch || this.active.has(id) || this.deleted.has(id)) return;
      this.setCache(id, session); this.adapter?.setRunning?.(id, Boolean(session.running));
      this.reconcileSubmission(id, session);
      if (!session.running && this.state.runs[id]?.recovering) this.emit({ runs: omit(this.state.runs, id) });
      this.reconcileContinuation(id, session);
      void Promise.resolve(this.adapter?.refresh?.(id)).catch(() => undefined);
      if (session.running) this.schedulePoll(id); else { this.stopPoll(id); this.scheduleContinuations(); }
    } catch (error) {
      if (!this.valid(lifecycle) || this.sessionEpoch.get(id) !== epoch || this.active.has(id)) return;
      if (error instanceof HttpError && error.status === 404) { this.recoverMissing(id); return; }
      if (this.state.currentId === id) this.report(error);
      if (this.state.cache[id]?.running || this.state.sessions.some(item => item.id === id && item.running)) this.schedulePoll(id);
      throw error;
    }
  };

  private stopPoll(id: string): void { const timer = this.polls.get(id); if (timer) clearTimeout(timer); this.polls.delete(id); }
  private schedulePoll(id: string): void {
    if (!this.started || this.active.has(id) || this.deleted.has(id)) return;
    this.stopPoll(id);
    this.polls.set(id, setTimeout(() => {
      this.polls.delete(id);
      void this.refreshSession(id).catch(() => this.schedulePoll(id));
    }, this.pollInterval));
  }

  private recoverMissing(id: string): void {
    this.stopPoll(id);
    this.forgetSubmission(id);
    const key = this.state.currentId === id ? blankId() : `draft:recovered:${id}`;
    const drafts = { ...this.state.drafts }, images = { ...this.state.imageDrafts };
    if (drafts[id]) { drafts[key] = drafts[id]; delete drafts[id]; }
    if (images[id]) {
      images[key] = images[id].filter(image => image.blob).map(image => ({ ...image, id: `local:${crypto.randomUUID()}`, local: true })); delete images[id];
    }
    this.emit({ cache: omit(this.state.cache, id), sessions: this.state.sessions.filter(item => item.id !== id), drafts, imageDrafts: images,
      continuations: omit(this.state.continuations, id), ...(this.state.currentId === id ? { currentId: null, blankId: key } : {}),
      error: '之前的对话已不存在，已打开新对话；未发送的内容已保留。' });
    this.adapter?.remove?.(id); this.saveSelection(); this.saveDrafts(); this.saveImages(); this.saveContinuations();
  }

  private editSession = async (id: string, values: ApiData): Promise<void> => {
    const lifecycle = this.lifecycle;
    try {
      const saved = await this.request<ApiData>(url(id), { method: 'PATCH', body: JSON.stringify(values) });
      if (!this.valid(lifecycle) || this.deleted.has(id)) return;
      const cached = this.state.cache[id];
      this.emit({ sessions: this.state.sessions.map(item => item.id === id ? { ...item, ...saved } : item),
        cache: cached ? { ...this.state.cache, [id]: { ...cached, ...saved } } : this.state.cache });
      await this.loadSessions();
    } catch (error) { if (this.valid(lifecycle)) this.report(error); throw error; }
  };
  renameSession = (id: string, title: string): Promise<void> => this.editSession(id, { title: title.trim() });
  pinSession = (id: string, pinned?: boolean): Promise<void> => this.editSession(id, { pinned: pinned ?? !(this.state.cache[id]?.pinned ?? this.state.sessions.find(item => item.id === id)?.pinned) });
  deleteSession = async (id: string): Promise<void> => {
    if (this.busy(id)) throw new Error('请先停止这段对话。');
    const lifecycle = this.lifecycle;
    try {
      await this.request(url(id), { method: 'DELETE' });
      if (!this.valid(lifecycle)) return;
      this.deleted.add(id); this.sessionEpoch.set(id, (this.sessionEpoch.get(id) || 0) + 1); this.stopPoll(id);
      this.forgetSubmission(id);
      releaseImages(this.state.imageDrafts[id]); this.imageOperations.delete(id); this.touchedImages.add(id);
      this.emit({ cache: omit(this.state.cache, id), sessions: this.state.sessions.filter(item => item.id !== id), drafts: omit(this.state.drafts, id),
        imageDrafts: omit(this.state.imageDrafts, id), imageUploads: omit(this.state.imageUploads, id), continuations: omit(this.state.continuations, id),
        ...(this.state.currentId === id ? { currentId: null, blankId: blankId() } : {}) });
      this.adapter?.remove?.(id); this.saveSelection(); this.saveDrafts(); this.saveImages(); this.saveContinuations();
    } catch (error) { if (this.valid(lifecycle)) this.report(error); throw error; }
  };

  setDraft = (text: string, id: string | null = this.state.currentId): void => {
    const key = this.draftKey(id);
    this.emit({ drafts: text ? { ...this.state.drafts, [key]: text } : omit(this.state.drafts, key) }); this.saveDrafts();
  };

  uploadImages = async (files: FileList | File[], id: string | null = this.state.currentId): Promise<void> => {
    const key = this.draftKey(id), maximum = Math.max(1, Math.min(12, Number(this.state.status?.max_images) || 3));
    if (!files.length || this.state.preparing || this.busy(id) || this.imageOperations.has(key)) return;
    if (!this.state.status?.supports_images) throw new Error('当前模型没有开启图片能力。');
    if ((this.state.imageDrafts[key]?.length || 0) + files.length > maximum) throw new Error(`当前模型每次最多接收 ${maximum} 张图片。`);
    const operation = {}, lifecycle = this.lifecycle;
    this.imageOperations.set(key, operation); this.touchedImages.add(key);
    this.emit({ imageUploads: { ...this.state.imageUploads, [key]: true } });
    try {
      for (const file of Array.from(files)) {
        const image = await this.prepareImage(file);
        if (!this.valid(lifecycle) || this.imageOperations.get(key) !== operation) { releaseImages([image]); break; }
        this.emit({ imageDrafts: { ...this.state.imageDrafts, [key]: [...(this.state.imageDrafts[key] || []), image] } }); this.saveImages();
      }
    } catch (error) { if (this.valid(lifecycle) && this.imageOperations.get(key) === operation) this.report(error); throw error; }
    finally {
      if (this.imageOperations.get(key) === operation) {
        this.imageOperations.delete(key); this.emit({ imageUploads: omit(this.state.imageUploads, key) });
      }
    }
  };

  removeImage = async (imageId: string, id: string | null = this.state.currentId): Promise<void> => {
    if (this.state.preparing || this.busy(id)) return;
    const key = this.draftKey(id), image = this.state.imageDrafts[key]?.find(item => item.id === imageId), lifecycle = this.lifecycle;
    if (!image) return;
    try {
      if (!image.local && id) await this.request(`${url(id)}/images/${encodeURIComponent(imageId)}`, { method: 'DELETE' });
      if (!this.valid(lifecycle)) return;
      releaseImages([image]); this.touchedImages.add(key);
      this.emit({ imageDrafts: { ...this.state.imageDrafts, [key]: (this.state.imageDrafts[key] || []).filter(item => item.id !== imageId) } }); this.saveImages();
    } catch (error) { if (this.valid(lifecycle)) this.report(error); throw error; }
  };

  private moveDrafts(from: string, to: string): void {
    if (from === to) return;
    const drafts = { ...this.state.drafts }, images = { ...this.state.imageDrafts };
    if (from in drafts) { drafts[to] = drafts[from]; delete drafts[from]; }
    if (from in images) { images[to] = images[from]; delete images[from]; }
    this.touchedImages.add(from); this.touchedImages.add(to);
    this.emit({ drafts, imageDrafts: images }); this.saveDrafts(); this.saveImages();
  }

  private updateRun(run: ActiveRun, mutate: (message: Message) => void): void {
    if (!this.valid(run.lifecycle) || this.active.get(run.sessionId) !== run) return;
    const session = this.state.cache[run.sessionId]; if (!session) return;
    const old = session.messages[run.messageIndex]; if (!old) return;
    const message = { ...old, tool_calls: (old.tool_calls || []).map(call => ({ ...call })), actions: [...(old.actions || [])], process: [...(old.process || [])], skills: [...(old.skills || [])] };
    mutate(message);
    const messages = [...session.messages]; messages[run.messageIndex] = message;
    this.emit({ cache: { ...this.state.cache, [session.id]: { ...session, messages } }, runs: { ...this.state.runs, [session.id]: this.publicRun(run) } });
  }
  private publicRun(run: ActiveRun): RunState {
    return { sessionId: run.sessionId, requestId: run.requestId, input: run.input, status: run.status, cancelling: run.cancelling, recovering: run.recovering };
  }
  private applyEvent(run: ActiveRun, event: ApiData): void {
    this.updateRun(run, message => {
      if (event.type === 'model_usage') message.usage = event.usage;
      if (event.type === 'context_usage') message.context_usage = event.context_usage;
      if (event.type === 'status') run.status = safeText(event.message || event.text || '正在执行…');
      if (event.type === 'assistant_delta') message.content += String(event.text ?? event.delta ?? '');
      if (event.type === 'tool_start') {
        if (message.content) { message.process.push(message.content); message.content = ''; }
        message.tool_calls!.push({ ...event, running: true }); run.status = '正在使用厨房工具…';
      }
      if (event.type === 'tool_end') {
        const call = message.tool_calls!.find(item => item.call_id === event.call_id);
        if (call) Object.assign(call, event, { running: false }); else message.tool_calls!.push({ ...event, running: false });
      }
      if (event.type === 'ui_action') { if (event.action) message.actions.push(event.action); void Promise.resolve(this.adapter?.refresh?.(run.sessionId)).catch(() => undefined); }
      if (event.type === 'skill_loaded') message.skills.push(event.skill || event);
      if (event.type === 'result') {
        const result = event.result || event;
        message.content = result.text ?? message.content;
        message.recipes = result.status === 'completed' ? result.recipes || [] : [];
        message.selected_recipe = result.status === 'completed' ? result.selected_recipe : null;
        message.snapshot = result.snapshot; message.usage = result.usage;
        message.context_usage = result.context_usage ?? message.context_usage;
        message.actions = result.actions || message.actions; message.skills = result.skills || message.skills;
        message.process = result.process || message.process;
        message.suggested_replies = result.status === 'completed' ? result.suggested_replies || [] : [];
        if (result.tool_calls?.length) message.tool_calls = result.tool_calls.map(normalizeToolCall);
        message.resultTotal = result.total ?? result.query_result?.total;
        run.terminalStatus = result.status; run.resultReceived = result.status === 'completed'; message.resultComplete = run.resultReceived;
        if (result.status === 'cancelled') { message.cancelled = true; message.retryMessage = run.input; }
        else if (result.status !== 'completed') {
          run.failed = true; run.retryable = result.error?.retryable !== false;
          message.error = safeText(result.error?.message || '这次执行没有完成，请重试。'); message.retryMessage = run.retryable ? run.input : null;
        }
      }
      if (event.type === 'error') {
        message.error = safeText(event.message || event.error?.message || '这次执行没有完成，请重试。');
        run.failed = true; run.retryable = event.retryable !== false; message.retryMessage = run.retryable ? run.input : null;
      }
      if (event.type === 'done') {
        run.done = true; run.terminalStatus = event.status || run.terminalStatus;
        message.cancelled = run.terminalStatus === 'cancelled' || Boolean(event.cancelled);
        if (message.cancelled) message.retryMessage = run.input;
        else if (run.terminalStatus !== 'completed') {
          run.failed = true; message.error ||= '这次执行没有完成，请重试。';
          if (run.retryable !== false) message.retryMessage ??= run.input;
        }
      }
    });
  }

  sendMessage = async (text?: string, options: SendOptions = {}): Promise<{ error?: string; accepted?: boolean }> => {
    const targetId = options.sessionId === undefined ? this.state.currentId : options.sessionId;
    const sourceKey = this.draftKey(targetId), fromComposer = options.fromComposer !== false;
    const input = (text ?? this.state.drafts[sourceKey] ?? '').trim();
    let images = [...(options.message?.retryImages || (fromComposer ? this.state.imageDrafts[sourceKey] : []) || [])];
    const fail = (message: string) => { this.emit({ error: message }); return { error: message }; };
    if (this.pendingQuestion(targetId)) return fail('先回答助手的问题，确认后就会接着处理。');
    if (fromComposer && targetId && this.state.continuations[targetId]) return fail('确认已保存，请先重试继续，或选择“暂不继续”后再发送。');
    if (this.state.loading || this.state.preparing || this.busy(targetId) || this.state.imageUploads[sourceKey]) return { error: '请等待当前操作结束。' };
    if (!input && !images.length) return { error: '请输入消息或上传图片。' };
    if (!this.state.status?.ready) return fail('请先完成模型与数据库连接。');
    if (input.length > 8000) return fail('消息最多 8000 个字符，请缩短后发送。');
    if (images.length && !this.state.status?.supports_images) return fail('当前模型没有开启图片能力。');
    if (images.length > Math.max(1, Math.min(12, Number(this.state.status?.max_images) || 3))) return fail('图片数量超过当前模型限制，请移除多余图片。');
    const lifecycle = this.lifecycle, selection = this.selectionEpoch;
    let id = targetId, snapshot: ApiData | null = null, created = false;
    this.emit({ preparing: true, error: null });
    try {
      if (!id) {
        const session = normalizeSession(await this.request('/api/sessions', { method: 'POST', body: '{}' }));
        if (!this.valid(lifecycle)) return { error: '页面已关闭，请重新打开对话。' };
        id = session.id; created = true; this.setCache(id, session); this.moveDrafts(sourceKey, id);
        if (selection === this.selectionEpoch && this.state.currentId === null && sourceKey === this.state.blankId) {
          this.selectionEpoch++; this.emit({ currentId: id, blankId: blankId() }); this.saveSelection(); this.saveDrafts();
        }
      }
      for (let index = 0; index < images.length; index++) {
        const image = images[index]; if (!image.local) continue;
        if (!image.blob) throw new Error('本地图片已失效，请移除后重新选择。');
        const uploaded = await this.request<ApiData>(`${url(id)}/images?name=${encodeURIComponent(image.name)}`, {
          method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: image.blob,
        });
        if (!this.valid(lifecycle)) return { error: '页面已关闭，请重新打开对话。' };
        images[index] = { ...image, ...uploaded, local: false };
        if (fromComposer && !options.message) { this.emit({ imageDrafts: { ...this.state.imageDrafts, [id]: [...images] } }); this.saveImages(); }
      }
      snapshot = this.adapter ? await this.adapter.prepareTurn(id) : (await this.request<ApiData>(`${url(id)}/workspace`)).ui_snapshot;
      if (!this.valid(lifecycle)) return { error: '页面已关闭，请重新打开对话。' };
      if (snapshot?.pending_question) throw new Error('问题已更新，请先回答当前问题再继续。');
    } catch (error) {
      if (!this.valid(lifecycle)) return { error: '页面已关闭，请重新打开对话。' };
      if (created && id) {
        this.moveDrafts(id, sourceKey);
        const recovered = (this.state.imageDrafts[sourceKey] || []).map(image => image.blob ? { ...image, id: `local:${crypto.randomUUID()}`, local: true } : image);
        this.emit({ cache: omit(this.state.cache, id), imageDrafts: { ...this.state.imageDrafts, [sourceKey]: recovered },
          ...(this.state.currentId === id ? { currentId: null, blankId: sourceKey } : {}) });
        this.saveSelection(); this.saveDrafts(); this.saveImages(); this.adapter?.remove?.(id);
        void this.request(url(id), { method: 'DELETE' }).catch(() => undefined);
      }
      return fail(errorText(error));
    } finally { if (this.valid(lifecycle)) { this.emit({ preparing: false }); this.scheduleContinuations(); } }
    if (!id) return fail('对话尚未创建，请重试。');
    this.stopPoll(id); this.sessionEpoch.set(id, (this.sessionEpoch.get(id) || 0) + 1);
    const session = this.state.cache[id] || normalizeSession({ id, messages: [] });
    const messages = [...session.messages];
    const replaced = options.message;
    if (options.requestId && !replaced && messages.at(-1)?.request_id === options.requestId && messages.at(-1)?.error) {
      const failed = messages.pop();
      if (failed?.retryAsNew && messages.at(-1)?.role === 'user' && messages.at(-1)?.request_id === options.requestId) messages.pop();
    }
    if (replaced && (messages.at(-1) === replaced || messages.at(-1)?.retryMessage === replaced.retryMessage && messages.at(-1)?.error === replaced.error)) {
      messages.pop();
      if (replaced.retryAsNew && messages.at(-1)?.role === 'user' && messages.at(-1)?.content === input) messages.pop();
    }
    const metadata = images.map(imageMetadata);
    const requestId = options.requestId || crypto.randomUUID();
    if (!(options.retry && messages.at(-1)?.role === 'user' && messages.at(-1)?.content === input)) messages.push({ role: 'user', content: input, images: metadata, request_id: requestId });
    messages.push({ role: 'assistant', content: '', tool_calls: [], actions: [], skills: [], process: [], recipes: [], streaming: true, retryImages: metadata, request_id: requestId });
    const run: ActiveRun = { sessionId: id, input, requestId, controller: new AbortController(), lifecycle,
      status: '正在理解需求…', messageIndex: messages.length - 1, done: false, resultReceived: false, failed: false, httpRejected: false, accepted: false };
    this.active.set(id, run);
    this.emit({ cache: { ...this.state.cache, [id]: { ...session, messages, running: true,
      title: !session.title_custom && ['新对话', '新会话', ''].includes(session.title) ? input.slice(0, 26) || '图片对话' : session.title } },
      runs: { ...this.state.runs, [id]: this.publicRun(run) } });
    this.adapter?.setRunning?.(id, true);
    if (fromComposer) {
      this.submissions[id] = { requestId, input, imageIds: metadata.map(image => image.id), paired: Boolean(options.message) };
      this.write(SUBMISSIONS_KEY, this.submissions);
    }
    try {
      const response = await this.fetcher(`${url(id)}/turn`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, signal: run.controller.signal,
        body: JSON.stringify({ message: input, images: metadata.map(image => image.id), retry: Boolean(options.retry), request_id: run.requestId, ui_snapshot: snapshot }),
      });
      if (!this.valid(lifecycle)) return { error: '页面已关闭，请重新打开对话。' };
      if (!response.ok) {
        run.httpRejected = true;
        const payload = await response.json().catch(() => null);
        throw new HttpError(safeText(typeof payload?.detail === 'string' ? payload.detail : payload?.message || `请求失败（${response.status}）。`), response.status, payload);
      }
      run.accepted = true;
      this.acceptContinuation(id, run.requestId);
      if (fromComposer) this.acceptSubmission(id, run.requestId);
      void this.loadSessions().catch(() => undefined);
      await readEvents(response, event => this.applyEvent(run, event));
      if (!run.resultReceived && !run.failed && run.terminalStatus !== 'cancelled') throw new Error('连接在结果返回前中断，正在恢复执行状态。');
    } catch (error) {
      if (this.valid(lifecycle)) this.updateRun(run, message => { message.error = errorText(error, '连接中断，请重试。'); message.retryMessage = input; });
    } finally {
      if (this.valid(lifecycle) && this.active.get(id) === run) await this.finishRun(run);
    }
    return { accepted: run.accepted, ...(!run.accepted ? { error: this.state.cache[id]?.messages.at(-1)?.error || '下一轮尚未开始。' } : {}) };
  };

  private async finishRun(run: ActiveRun): Promise<void> {
    const id = run.sessionId;
    this.updateRun(run, message => {
      message.streaming = false;
      message.tool_calls = (message.tool_calls || []).map(call => ({ ...call, running: false, ...(call.status === 'running' ? { status: 'interrupted' } : {}) }));
    });
    let canonical: Session | undefined, recovery = false;
    try {
      const saved = normalizeSession(await this.request(url(id)));
      if (!this.valid(run.lifecycle) || this.active.get(id) !== run) return;
      this.reconcileSubmission(id, saved);
      if (saved.running || saved.last_run?.request_id === run.requestId || saved.messages.some(message => message.role === 'user' && message.request_id === run.requestId)) canonical = saved;
      else this.updateRun(run, message => { if (message.error) message.retryAsNew = true; });
    } catch {
      if (!this.valid(run.lifecycle) || this.active.get(id) !== run) return;
      recovery = !run.httpRejected && !run.done && !run.resultReceived;
    }
    if (!this.valid(run.lifecycle) || this.active.get(id) !== run) return;
    if (canonical) {
      this.setCache(id, canonical); recovery = Boolean(canonical.running); this.reconcileContinuation(id, canonical);
    } else {
      if (run.httpRejected) this.forgetSubmission(id, run.requestId);
      this.updateRun(run, message => { if (recovery) { message.streaming = true; message.recovering = true; delete message.error; } });
      const cached = this.state.cache[id];
      this.setCache(id, { ...cached, running: recovery, ...(recovery ? { recoveryStatus: '连接中断，正在恢复执行状态…' } : {}) });
    }
    this.active.delete(id);
    this.emit({ runs: omit(this.state.runs, id) });
    this.adapter?.setRunning?.(id, recovery);
    void Promise.resolve(this.adapter?.refresh?.(id)).catch(() => undefined);
    if (recovery) this.schedulePoll(id);
    void this.loadSessions().catch(() => undefined);
    this.scheduleContinuations();
  }

  cancelTurn = async (id: string | null = this.state.currentId): Promise<void> => {
    if (!id || !this.busy(id)) return;
    const run = this.active.get(id), lifecycle = this.lifecycle;
    if (run?.cancelling || this.state.runs[id]?.cancelling) return;
    if (run) { run.cancelling = true; this.emit({ runs: { ...this.state.runs, [id]: this.publicRun(run) } }); }
    else this.emit({ runs: { ...this.state.runs, [id]: { sessionId: id, requestId: '', input: '', status: '正在停止，请稍等…', cancelling: true, recovering: true } } });
    try {
      await this.request(`${url(id)}/cancel`, { method: 'POST', body: '{}' });
      if (this.valid(lifecycle) && !run) await this.refreshSession(id);
    } catch (error) {
      if (this.valid(lifecycle)) {
        if (run) { run.cancelling = false; this.emit({ runs: { ...this.state.runs, [id]: this.publicRun(run) } }); }
        else this.emit({ runs: omit(this.state.runs, id) });
        this.report(error);
      }
      throw error;
    }
  };

  retryMessage = (message: Message, id: string | null = this.state.currentId): Promise<{ error?: string; accepted?: boolean }> =>
    this.sendMessage(message.retryMessage ?? '', { sessionId: id, retry: !message.retryAsNew, message,
      requestId: message.retryAsNew && typeof message.request_id === 'string' ? message.request_id : undefined });

  enqueueContinuation = (id: string, message: string, requestId = `confirm:${crypto.randomUUID()}`): void => {
    const existing = this.state.continuations[id];
    if (existing?.requestId === requestId) return;
    this.emit({ continuations: { ...this.state.continuations, [id]: { message, requestId, stage: 'queued' } } });
    this.saveContinuations(); this.scheduleContinuations();
  };
  retryContinuation = (id: string): void => {
    const entry = this.state.continuations[id]; if (!entry || entry.stage === 'sending') return;
    this.emit({ continuations: { ...this.state.continuations, [id]: { ...entry, stage: 'queued', error: undefined } } }); this.saveContinuations(); this.scheduleContinuations();
  };
  dismissContinuation = (id: string): void => {
    if (this.state.continuations[id]?.stage === 'sending') return;
    this.emit({ continuations: omit(this.state.continuations, id) }); this.saveContinuations();
  };
  private acceptContinuation(id: string, requestId: string): void {
    if (this.state.continuations[id]?.requestId !== requestId) return;
    this.emit({ continuations: omit(this.state.continuations, id) }); this.saveContinuations();
  }
  private forgetSubmission(id: string, requestId?: string): void {
    if (!this.submissions[id] || requestId && this.submissions[id].requestId !== requestId) return;
    this.submissions = omit(this.submissions, id); this.write(SUBMISSIONS_KEY, this.submissions);
  }
  private acceptSubmission(id: string, requestId: string): void {
    const pending = this.submissions[id]; if (!pending || pending.requestId !== requestId) return;
    const retained = this.state.imageDrafts[id] || [];
    const sameText = (this.state.drafts[id] || '').trim() === pending.input;
    const sameImages = retained.length === pending.imageIds.length && retained.every((image, index) => image.id === pending.imageIds[index]);
    if (!pending.paired || sameText && sameImages) {
      if (sameImages) { releaseImages(retained); this.touchedImages.add(id); }
      this.emit({ drafts: sameText ? omit(this.state.drafts, id) : this.state.drafts,
        imageDrafts: sameImages ? omit(this.state.imageDrafts, id) : this.state.imageDrafts }); this.saveDrafts(); this.saveImages();
    }
    this.forgetSubmission(id, requestId);
  }
  private reconcileSubmission(id: string, session: Session): void {
    const pending = this.submissions[id]; if (!pending) return;
    if (session.active_turn?.request_id === pending.requestId || session.last_run?.request_id === pending.requestId
      || session.messages.some(message => message.role === 'user' && message.request_id === pending.requestId)) this.acceptSubmission(id, pending.requestId);
    else if (!session.running) this.forgetSubmission(id, pending.requestId);
  }
  private reconcileContinuation(id: string, session: Session): void {
    const entry = this.state.continuations[id]; if (!entry) return;
    if (session.active_turn?.request_id === entry.requestId || session.last_run?.request_id === entry.requestId
      || session.messages.some(message => message.role === 'user' && message.request_id === entry.requestId)) this.acceptContinuation(id, entry.requestId);
  }
  private scheduleContinuations(): void {
    if (!this.started || this.continuationScheduled) return;
    this.continuationScheduled = true;
    queueMicrotask(() => { this.continuationScheduled = false; void this.continueNext(); });
  }
  private async continueNext(): Promise<void> {
    if (!this.started || this.state.loading || this.state.preparing || !this.state.status?.ready) return;
    for (const [id, entry] of Object.entries(this.state.continuations)) {
      if (entry.stage !== 'queued' || this.busy(id) || this.pendingQuestion(id)) continue;
      const cached = this.state.cache[id];
      // On reload, resolve whether the server already accepted this request first.
      if (!cached) {
        try { await this.refreshSession(id); } catch { return; }
        if (this.state.continuations[id] !== entry || this.busy(id)) continue;
      }
      this.emit({ continuations: { ...this.state.continuations, [id]: { ...entry, stage: 'sending' } } }); this.saveContinuations();
      const result = await this.sendMessage(entry.message, { sessionId: id, fromComposer: false, requestId: entry.requestId });
      if (this.state.continuations[id]?.requestId === entry.requestId) {
        this.emit({ continuations: { ...this.state.continuations, [id]: { ...entry, stage: 'failed', error: result.error || '确认已保存，下一轮尚未开始。' } } }); this.saveContinuations();
      }
      break;
    }
  }
}

export const chefStore = new ChefStore();
