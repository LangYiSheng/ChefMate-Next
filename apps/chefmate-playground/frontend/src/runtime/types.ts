/** Public HTTP data is owned and validated by the Python application. */
export type ApiData = Record<string, any>;

export interface ImageDraft extends ApiData {
  id: string;
  name: string;
  mime_type?: string;
  width?: number;
  height?: number;
  byte_size?: number;
  blob?: Blob;
  previewUrl?: string;
  local?: boolean;
}

export interface Message extends ApiData {
  role: 'user' | 'assistant';
  content: string;
  tool_calls?: ApiData[];
  images?: ImageDraft[];
  retryImages?: ImageDraft[];
  retryMessage?: string | null;
  streaming?: boolean;
  recovering?: boolean;
  error?: string;
  cancelled?: boolean;
}

export interface Session extends ApiData {
  id: string;
  title: string;
  messages: Message[];
  running?: boolean;
  pinned?: boolean;
  pending_question?: ApiData | null;
}

export interface RunState {
  sessionId: string;
  requestId: string;
  input: string;
  status: string;
  cancelling?: boolean;
  recovering?: boolean;
}

export interface Continuation {
  message: string;
  requestId: string;
  stage: 'queued' | 'sending' | 'failed';
  error?: string;
}

export interface RuntimeState {
  kitchenDisplayRequest?: { sessionId: string; action_id: string; mode: 'focus' | 'conversation'; view_kind?: string | null } | null;
  status: ApiData | null;
  sessions: Session[];
  currentId: string | null;
  session: Session | null;
  cache: Record<string, Session>;
  blankId: string;
  drafts: Record<string, string>;
  imageDrafts: Record<string, ImageDraft[]>;
  imageUploads: Record<string, boolean>;
  runs: Record<string, RunState>;
  continuations: Record<string, Continuation>;
  loading: boolean;
  preparing: boolean;
  sessionsLoading: boolean;
  sessionsError: string | null;
  sessionLoading: Record<string, boolean>;
  nextCursor: string | null;
  error: string | null;
}

export interface WorkspaceAdapter {
  prepareTurn: (sessionId: string) => Promise<ApiData | null>;
  refresh?: (sessionId: string) => Promise<unknown> | void;
  setRunning?: (sessionId: string, running: boolean) => void;
  pendingQuestion?: (sessionId: string) => ApiData | null | undefined;
  remove?: (sessionId: string) => void;
}

export interface SendOptions {
  sessionId?: string | null;
  retry?: boolean;
  message?: Message;
  fromComposer?: boolean;
  requestId?: string;
}

export interface AttachmentPersistence {
  load: () => Promise<Record<string, ImageDraft[]>>;
  save: (images: Record<string, ImageDraft[]>) => Promise<void>;
}

export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export interface StoreOptions {
  fetch?: typeof fetch;
  storage?: StorageLike | null;
  attachments?: AttachmentPersistence;
  pollInterval?: number;
  prepareImage?: (file: File) => Promise<ImageDraft>;
}
