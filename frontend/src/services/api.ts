/**
 * 性騷擾防治智能 AI — 後端 API 呼叫層
 * 所有與後端通訊皆透過此模組，方便統一管理 base URL 與錯誤處理。
 */

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";

// ── 型別定義 ──────────────────────────────────────────────────────────────

export interface MessageItem {
  role: "user" | "assistant";
  content: string;
}

export interface ChatRequest {
  message: string;
  history: MessageItem[];
  use_rag: boolean;
  image_base64?: string;
}

export type RagSourceType = "law" | "judgment" | "remedy" | "unknown";

export interface RagSource {
  label: string;
  type: RagSourceType;
  collection?: string;
  doc_id?: string;
  distance?: number;
}

export interface RagInfo {
  status: boolean;
  sources: Array<RagSource | string>;
}

export interface DebugToolCall {
  name: string;
  arguments: Record<string, string>;
  result_count: number;
}

export interface ChatResponse {
  reply: string;
  session_id: string;
  anonymized: boolean;
  rag_used: RagInfo;
  emotion?: string;
  emotion_color?: string;
  suggested_replies: string[];
  action_buttons: ActionButton[];
  interaction_mode: "answer" | "clarify";
  clarifying_questions: string[];
  debug_tool_calls?: DebugToolCall[];
}

/** Cumulative, display-only preview. Trusted actions arrive in the final response. */
export interface ChatGuidance {
  interaction_mode?: "answer" | "clarify";
  clarifying_questions?: string[];
  suggested_replies?: string[];
}

const CHAT_PROGRESS_PHASES = [
  "anonymizing", "preparing", "waiting_model", "retrieving", "generating", "guidance", "validating",
] as const;

/** Server-observed phase and elapsed time; never an estimated completion percentage. */
export interface ChatProgress {
  phase: typeof CHAT_PROGRESS_PHASES[number];
  elapsed_ms: number;
}

export interface TelActionButton {
  action: "tel";
  phone_number: string;
  label: string;
}

export interface UrlActionButton {
  action: "url";
  url: string;
  label: string;
}

export interface ActionOption {
  label: string;
  value: string;
}

export interface OptionsActionButton {
  action: "options";
  id: string;
  label: string;
  title: string;
  options: ActionOption[];
}

export type ActionButton = TelActionButton | UrlActionButton | OptionsActionButton;

export interface ScenarioSkill {
  id: string;
  name: string;
  enabled: boolean;
  priority: number;
  trigger_keywords: string[];
  instruction: string;
  actions: ActionButton[];
}

export type ScenarioSkillInput = Omit<ScenarioSkill, "id">;

export interface HealthResponse {
  status: string;
  timestamp: string;
  version: string;
  environment: string;
}

export interface RuntimeConfig {
  openrouter_model: string;
  rag_retrieval_top_k: number;
  rag_distance_threshold: number | null;
  enable_anonymization: boolean;
  temperature: number;
  top_p: number;
  max_tokens: number;
  reasoning_effort: "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
  agent_prompt_sections: Record<string, string>;
  rag_collections: {
    law: string;
    judgment: string;
    remedy: string;
  };
  maintenance_message?: string;
  enable_image_upload: boolean;
  development_mode: boolean;
  source: string;
  environment_document_id?: string;
  updated_at?: string;
  updated_by?: string;
}

export type RuntimeConfigUpdate = Partial<
  Pick<
    RuntimeConfig,
    | "openrouter_model"
    | "rag_retrieval_top_k"
    | "rag_distance_threshold"
    | "enable_anonymization"
    | "temperature"
    | "top_p"
    | "max_tokens"
    | "reasoning_effort"
    | "agent_prompt_sections"
    | "rag_collections"
    | "maintenance_message"
    | "enable_image_upload"
    | "development_mode"
  >
>;

// ── API 錯誤類別 ─────────────────────────────────────────────────────────

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;
  readonly retryable: boolean;
  readonly debugMessage?: string;

  constructor(
    status: number,
    message: string,
    detail?: string,
    retryable = false,
    debugMessage?: string
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryable = retryable;
    this.debugMessage = debugMessage;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeValidationIssue(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (!isRecord(value)) return undefined;

  const legacyLocation = Array.isArray(value.loc)
    ? value.loc.filter((part): part is string | number =>
        typeof part === "string" || typeof part === "number"
      ).join(".")
    : "";
  const location = typeof value.field === "string" ? value.field : legacyLocation;
  const message = typeof value.message === "string"
    ? value.message
    : typeof value.msg === "string"
      ? value.msg
      : "";
  const combined = [location, message].filter(Boolean).join(": ");
  return combined || undefined;
}

export function normalizeApiErrorDetail(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    const issues = value.map(normalizeValidationIssue).filter((issue): issue is string => Boolean(issue));
    return issues.length > 0 ? issues.join("; ") : undefined;
  }
  if (!isRecord(value)) return undefined;

  const issue = normalizeValidationIssue(value);
  if (issue) return issue;

  try {
    return JSON.stringify(value);
  } catch {
    return undefined;
  }
}

// ── 共用 fetch 包裝 ──────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!response.ok) await throwResponseError(response);

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function throwResponseError(response: Response): Promise<never> {
  let detail: string | undefined;
  let retryable = false;
  let debugMessage: string | undefined;
  try {
    const errorData: unknown = await response.json();
    if (isRecord(errorData)) {
      const summary = normalizeApiErrorDetail(errorData.detail);
      const validationErrors = normalizeApiErrorDetail(errorData.errors);
      detail = validationErrors
        ? [summary, validationErrors].filter(Boolean).join(": ")
        : summary;
      retryable = errorData.retryable === true;
      debugMessage = typeof errorData.debug_message === "string"
        ? errorData.debug_message
        : undefined;
    }
  } catch {
    // 非 JSON 回應
  }
  throw new ApiError(
    response.status,
    `API 請求失敗 (${response.status})`,
    detail,
    retryable,
    debugMessage
  );
}

function invalidStream(): ApiError {
  return new ApiError(502, "回覆串流中斷", "回覆未完整接收，請重新送出訊息", true);
}

/** Consume complete SSE events, preserving split UTF-8 characters and line endings. */
async function readChatStream(
  response: Response,
  signal?: AbortSignal,
  onDelta?: (text: string) => void,
  onGuidance?: (guidance: ChatGuidance) => void,
  onProgress?: (progress: ChatProgress) => void,
): Promise<ChatResponse> {
  if (!response.body) throw invalidStream();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let result: ChatResponse | undefined;
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });

  function consumeLine(line: string) {
    if (line === "") {
      const eventName = event;
      const eventData = data.join("\n");
      event = "";
      data = [];
      if (!eventData || !["delta", "guidance", "progress", "done", "error"].includes(eventName)) return;
      let payload: unknown;
      try { payload = JSON.parse(eventData); } catch { throw invalidStream(); }
      if (!isRecord(payload)) throw invalidStream();
      if (eventName === "progress") {
        if (!CHAT_PROGRESS_PHASES.some(phase => phase === payload.phase)
          || typeof payload.elapsed_ms !== "number" || !Number.isFinite(payload.elapsed_ms)
          || payload.elapsed_ms < 0) throw invalidStream();
        onProgress?.({ phase: payload.phase as ChatProgress["phase"], elapsed_ms: payload.elapsed_ms });
      } else if (eventName === "delta") {
        if (typeof payload.text !== "string") throw invalidStream();
        if (payload.text) onDelta?.(payload.text);
      } else if (eventName === "guidance") {
        const guidance: ChatGuidance = {};
        if (payload.interaction_mode !== undefined) {
          if (payload.interaction_mode !== "answer" && payload.interaction_mode !== "clarify") throw invalidStream();
          guidance.interaction_mode = payload.interaction_mode;
        }
        for (const key of ["clarifying_questions", "suggested_replies"] as const) {
          const items = payload[key];
          if (items === undefined) continue;
          if (!Array.isArray(items) || !items.every(item => typeof item === "string")) throw invalidStream();
          guidance[key] = items;
        }
        // Never promote model-generated actions or other unvalidated metadata.
        if (Object.keys(guidance).length > 0) onGuidance?.(guidance);
      } else if (eventName === "done") {
        if (typeof payload.reply !== "string" || !Array.isArray(payload.suggested_replies)
          || !Array.isArray(payload.action_buttons) || !Array.isArray(payload.clarifying_questions)
          || !isRecord(payload.rag_used)
          || !["answer", "clarify"].includes(String(payload.interaction_mode))) throw invalidStream();
        result = payload as unknown as ChatResponse;
      } else {
        throw new ApiError(
          typeof payload.status === "number" ? payload.status : 502,
          "回覆產生失敗",
          normalizeApiErrorDetail(payload.detail),
          payload.retryable === true,
          typeof payload.debug_message === "string" ? payload.debug_message : undefined,
        );
      }
      return;
    }
    if (line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }

  try {
    while (!result) {
      signal?.throwIfAborted();
      const { value, done } = await reader.read();
      signal?.throwIfAborted();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      // Hold a trailing CR until the next read so a split CRLF is one newline.
      while (true) {
        const match = /[\r\n]/.exec(buffer);
        if (!match || (buffer[match.index] === "\r" && match.index === buffer.length - 1 && !done)) break;
        const line = buffer.slice(0, match.index);
        const length = buffer.slice(match.index, match.index + 2) === "\r\n" ? 2 : 1;
        buffer = buffer.slice(match.index + length);
        consumeLine(line);
        if (result) return result;
      }
      // A transport close without an explicit terminal event is never success.
      if (done) throw invalidStream();
    }
    return result;
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

// ── 公開 API 函式 ────────────────────────────────────────────────────────

/**
 * 傳送對話訊息給 AI，並取得回覆。
 */
export async function sendChat(
  request: ChatRequest,
  signal?: AbortSignal,
  onDelta?: (text: string) => void,
  onGuidance?: (guidance: ChatGuidance) => void,
  onProgress?: (progress: ChatProgress) => void,
): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/v1/chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ ...request, stream: true }),
    signal,
  });
  if (!response.ok) await throwResponseError(response);
  // Allow the new client to work during a rolling backend deployment.
  if (!response.headers.get("content-type")?.includes("text/event-stream")) {
    return response.json() as Promise<ChatResponse>;
  }
  return readChatStream(response, signal, onDelta, onGuidance, onProgress);
}

/**
 * 健康狀態檢查。
 */
export async function checkHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/v1/health/");
}

export async function getRuntimeConfig(adminToken: string): Promise<RuntimeConfig> {
  return apiFetch<RuntimeConfig>("/v1/admin/config", {
    headers: {
      Authorization: `Bearer ${adminToken}`,
    },
  });
}

export async function updateRuntimeConfig(
  adminToken: string,
  request: RuntimeConfigUpdate
): Promise<RuntimeConfig> {
  return apiFetch<RuntimeConfig>("/v1/admin/config", {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${adminToken}`,
    },
    body: JSON.stringify(request),
  });
}

export async function seedRuntimeConfig(adminToken: string): Promise<RuntimeConfig> {
  return apiFetch<RuntimeConfig>("/v1/admin/config/seed", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${adminToken}`,
    },
  });
}

export async function resetRuntimeConfig(adminToken: string): Promise<RuntimeConfig> {
  return apiFetch<RuntimeConfig>("/v1/admin/config/reset", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${adminToken}`,
    },
  });
}

export async function seedScenarioScripts(adminToken: string): Promise<{ script_ids: string[] }> {
  return apiFetch<{ script_ids: string[] }>("/v1/admin/scenario-scripts/seed", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${adminToken}`,
    },
  });
}

export async function getScenarioSkills(adminToken: string): Promise<{ skills: ScenarioSkill[] }> {
  return apiFetch<{ skills: ScenarioSkill[] }>("/v1/admin/scenario-scripts", {
    headers: { Authorization: `Bearer ${adminToken}` },
  });
}

export async function updateScenarioSkill(
  adminToken: string,
  id: string,
  skill: ScenarioSkillInput
): Promise<ScenarioSkill> {
  return apiFetch<ScenarioSkill>(`/v1/admin/scenario-scripts/${id}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${adminToken}` },
    body: JSON.stringify(skill),
  });
}

export async function deleteScenarioSkill(adminToken: string, id: string): Promise<void> {
  await apiFetch<unknown>(`/v1/admin/scenario-scripts/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${adminToken}` },
  });
}
