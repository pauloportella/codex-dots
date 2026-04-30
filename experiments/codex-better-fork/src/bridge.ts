import { invoke } from "@tauri-apps/api/core";

const HTTP_BRIDGE_ORIGIN = "http://127.0.0.1:1421";

export type ReasoningEffort = "none" | "minimal" | "low" | "medium" | "high" | "xhigh";
export type ServiceTier = "fast" | "flex";

export type ThreadSession = {
  id: string;
  title: string;
  repo: string;
  cwd: string;
  updatedLabel: string;
  statusLabel: string;
};

export type ThreadTurn = {
  id: string;
  appTurnId: string;
  appTurnIndex: number;
  appTurnCount: number;
  index: number;
  author: string;
  body: string;
  status?: "ready" | "running" | "blocked";
};

export type ForkState = {
  sessionId: string;
  selectedTurnId: string | null;
  model: string | null;
  reasoning: ReasoningEffort | null;
  serviceTier: ServiceTier | null;
  handoff: string;
};

export type ForkData = {
  sessions: ThreadSession[];
  turns: ThreadTurn[];
  forkState: ForkState | null;
  forkThreadUrl: string | null;
};

export type DeeplinkResult = {
  url: string;
  opened: boolean;
};

export type ForkTransactionRequest = {
  sourceSessionId: string;
  baseTurnId: string;
  baseTurnIndex: number;
  turnCount: number;
  name?: string | null;
  model?: string | null;
  reasoningEffort?: ReasoningEffort | null;
  serviceTier?: ServiceTier | null;
  handoff?: string | null;
};

export type ForkTransaction = {
  id: string;
  sourceSessionId: string;
  baseTurnId: string;
  status: "succeeded" | "failed";
  targetSessionId?: string | null;
  targetThreadUrl?: string | null;
  rollbackTurns: number;
};

export type HandoffPreviewRequest = {
  sourceSessionId: string;
  baseTurnId: string;
  baseTurnIndex: number;
  turnCount: number;
  model?: string | null;
  reasoningEffort?: ReasoningEffort | null;
  serviceTier?: ServiceTier | null;
};

export type HandoffPreview = {
  sourceSessionId: string;
  baseTurnId: string;
  baseTurnIndex: number;
  previewThreadId: string;
  text: string;
};

type NativeSessionSummary = {
  id: string;
  title?: string | null;
  cwd?: string | null;
  latestActivityMs?: number | null;
  status: "idle" | "running" | "unknown";
};

type NativeThreadTurn = {
  id: string;
  appTurnId: string;
  appTurnIndex: number;
  appTurnCount: number;
  role: string;
  summary?: string | null;
};

type NativeThreadDetails = {
  sessionId: string;
  title?: string | null;
  model: string;
  modelProvider: string;
  serviceTier?: ServiceTier | null;
  reasoningEffort?: ReasoningEffort | null;
  turns: NativeThreadTurn[];
};

type TauriInternals = {
  invoke?: typeof invoke;
};

declare global {
  interface Window {
    __TAURI_INTERNALS__?: TauriInternals;
  }
}

type BridgeTransport = {
  listSessions(): Promise<NativeSessionSummary[]>;
  readThreadDetails(sessionId: string): Promise<NativeThreadDetails>;
  startForkTransaction(request: ForkTransactionRequest): Promise<ForkTransaction>;
  generateHandoffPreview(request: HandoffPreviewRequest): Promise<HandoffPreview>;
  openCodexDeeplink(sessionId: string): Promise<DeeplinkResult>;
};

export async function loadForkData(): Promise<ForkData> {
  const transport = getBridgeTransport();
  const sessions = await loadSessions(transport);
  const firstSession = sessions[0];

  if (!firstSession) {
    return {
      sessions: [],
      turns: [],
      forkThreadUrl: null,
      forkState: null,
    };
  }

  const details = await loadSessionDetails(transport, firstSession.id);

  return {
    sessions,
    turns: details.turns,
    forkThreadUrl: `codex://threads/${firstSession.id}`,
    forkState: createForkState(firstSession.id, details),
  };
}

export async function loadSessions(transport = getBridgeTransport()): Promise<ThreadSession[]> {
  const nativeSessions = await transport.listSessions();
  return nativeSessions.map((session) => mapNativeSession(session));
}

export async function loadThreadTurns(sessionId: string): Promise<ThreadTurn[]> {
  return loadSessionDetails(getBridgeTransport(), sessionId).then((details) => details.turns);
}

export async function loadThreadDetails(sessionId: string): Promise<{
  turns: ThreadTurn[];
  forkState: ForkState;
}> {
  const details = await loadSessionDetails(getBridgeTransport(), sessionId);
  return {
    turns: details.turns,
    forkState: createForkState(sessionId, details),
  };
}

export async function startForkTransaction(
  request: ForkTransactionRequest,
): Promise<ForkTransaction> {
  return getBridgeTransport().startForkTransaction(request);
}

export async function generateHandoffPreview(
  request: HandoffPreviewRequest,
): Promise<HandoffPreview> {
  return getBridgeTransport().generateHandoffPreview(request);
}

export async function openCodexDeeplink(sessionId: string): Promise<DeeplinkResult> {
  return getBridgeTransport().openCodexDeeplink(sessionId);
}

function getBridgeTransport(): BridgeTransport {
  return hasTauriInvoke() ? nativeInvokeTransport : httpBridgeTransport;
}

function hasTauriInvoke(): boolean {
  return typeof window !== "undefined" && typeof window.__TAURI_INTERNALS__?.invoke === "function";
}

function mapNativeSession(session: NativeSessionSummary): ThreadSession {
  const pathParts = session.cwd?.split("/").filter(Boolean) ?? [];
  const repo = pathParts[pathParts.length - 1] ?? slugFromTitle(session.title) ?? "codex";

  return {
    id: session.id,
    title: session.title ?? repo,
    repo,
    cwd: session.cwd ?? "",
    updatedLabel: formatRelativeTime(session.latestActivityMs),
    statusLabel: formatStatus(session.status),
  };
}

function createForkState(sessionId: string, details: LoadedThreadDetails): ForkState {
  return {
    sessionId,
    selectedTurnId: details.turns.find((turn) => turn.author === "User")?.id ?? null,
    model: details.model,
    reasoning: details.reasoning,
    serviceTier: details.serviceTier,
    handoff: "",
  };
}

type LoadedThreadDetails = {
  turns: ThreadTurn[];
  model: string | null;
  reasoning: ReasoningEffort | null;
  serviceTier: ServiceTier | null;
};

async function loadSessionDetails(
  transport: BridgeTransport,
  sessionId: string,
): Promise<LoadedThreadDetails> {
  const details = await transport.readThreadDetails(sessionId);
  return {
    turns: details.turns.map((turn, index) => mapNativeTurn(turn, index)),
    model: details.model || null,
    reasoning: details.reasoningEffort ?? null,
    serviceTier: details.serviceTier ?? null,
  };
}

function mapNativeTurn(turn: NativeThreadTurn, index: number): ThreadTurn {
  return {
    id: turn.id,
    appTurnId: turn.appTurnId,
    appTurnIndex: turn.appTurnIndex,
    appTurnCount: turn.appTurnCount,
    index: index + 1,
    author: turn.role,
    body: turn.summary ?? "No turn summary returned by app-server.",
  };
}

function slugFromTitle(title: string | null | undefined): string | null {
  if (!title) {
    return null;
  }

  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");

  return slug || null;
}

function formatRelativeTime(timestamp: number | null | undefined): string {
  if (!timestamp) {
    return "no update time";
  }

  const elapsedMs = Math.max(0, Date.now() - timestamp);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;

  if (elapsedMs < minute) {
    return "now";
  }

  if (elapsedMs < hour) {
    return `${Math.floor(elapsedMs / minute)}m`;
  }

  if (elapsedMs < day) {
    return `${Math.floor(elapsedMs / hour)}h`;
  }

  return `${Math.floor(elapsedMs / day)}d`;
}

function formatStatus(status: NativeSessionSummary["status"]): string {
  if (status === "idle" || status === "running") {
    return status;
  }

  return "status not returned";
}

const nativeInvokeTransport: BridgeTransport = {
  listSessions() {
    return invoke<NativeSessionSummary[]>("list_sessions");
  },
  readThreadDetails(sessionId) {
    return invoke<NativeThreadDetails>("read_thread_details", { sessionId });
  },
  startForkTransaction(request) {
    return invoke<ForkTransaction>("start_fork_transaction", { request });
  },
  generateHandoffPreview(request) {
    return invoke<HandoffPreview>("generate_handoff_preview", { request });
  },
  openCodexDeeplink(sessionId) {
    return invoke<DeeplinkResult>("open_codex_deeplink", { request: { sessionId } });
  },
};

const httpBridgeTransport: BridgeTransport = {
  listSessions() {
    return fetchJson<NativeSessionSummary[]>("/api/sessions");
  },
  readThreadDetails(sessionId) {
    const url = new URL("/api/thread", HTTP_BRIDGE_ORIGIN);
    url.searchParams.set("sessionId", sessionId);
    return fetchJson<NativeThreadDetails>(url);
  },
  startForkTransaction(request) {
    return postJson<ForkTransaction>("/api/fork", request);
  },
  generateHandoffPreview(request) {
    return postJson<HandoffPreview>("/api/handoff", request);
  },
  openCodexDeeplink(sessionId) {
    return postJson<DeeplinkResult>("/api/open", { sessionId });
  },
};

async function fetchJson<T>(pathOrUrl: string | URL, init?: RequestInit): Promise<T> {
  const url = pathOrUrl instanceof URL ? pathOrUrl : new URL(pathOrUrl, HTTP_BRIDGE_ORIGIN);
  let response: Response;
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");

  try {
    response = await fetch(url, {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch (error) {
    throw new Error(
      `HTTP bridge unavailable at ${HTTP_BRIDGE_ORIGIN}; expected Codex app-server bridge endpoints. ${formatCause(error)}`,
    );
  }

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(
      `HTTP bridge request failed: ${response.status} ${response.statusText} from ${url.toString()}${body ? ` - ${body}` : ""}`,
    );
  }

  return response.json() as Promise<T>;
}

async function postJson<T>(pathOrUrl: string | URL, body: unknown): Promise<T> {
  return fetchJson<T>(pathOrUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

function formatCause(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
