/**
 * 性騷擾防治智能 AI — 後端 API 呼叫層
 * 所有與後端通訊皆透過此模組，方便統一管理 base URL 與錯誤處理。
 */

import { isClarification, isFactUpdate, isSummaryUpdate, type SummaryUpdate, type CaseContext, type Clarification, type ClarificationAnswer, type FactUpdate } from "./caseFacts";
import { isAnalysisEntry, sanitizeAnalysis, type AnalysisEntry } from "./analysis";
import { isReasoningEntry, type ReasoningEntry } from "./reasoning";
import type { ClientSettings, PipelineSettings } from "./pipeline";
import { reviewedRequestBody } from "./clientPrivacy";
export type { AnalysisEntry } from "./analysis";
export type { ReasoningEntry } from "./reasoning";
export type { ClientSettings, PipelineSettings } from "./pipeline";
export type { CaseContext, LegacyCaseContext, SummaryCaseContext, SummaryUpdate, Clarification, ClarificationAnswer, FactUpdate, FactKey, CaseFact } from "./caseFacts";

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
  contract_version?: 2 | 3 | 4;
  case_context?: CaseContext;
  clarification_answer?: ClarificationAnswer;
  regenerate_from_summary?: boolean;
}

export type RagSourceType = "law" | "judgment" | "remedy" | "unknown";

export interface RagSource {
  label: string;
  type: RagSourceType;
  collection?: string | null;
  doc_id?: string | null;
  distance?: number | null;
  source_url?: string | null;
  article?: string | null;
  version?: string | null;
  law_name?: string | null;
  article_number?: string | null;
  checked_at?: string | null;
  effective_date?: string | null;
  promulgation_date?: string | null;
  promulgated_date?: string | null;
  promulgated_date_scope?: string | null;
  effective_date_scope?: string | null;
  effective_note?: string | null;
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
  contract_version?: 2 | 3 | 4;
  context_revision?: number;
  summary_update?: SummaryUpdate | null;
  facts_revision?: number;
  fact_updates?: FactUpdate[];
  clarification?: Clarification | null;
  answer_sections?: Array<{ kind: "direction" | "basis" | "next_steps"; text: string; source_ids: string[] }>;
  execution?: { route: string; model_calls: number };
  analysis?: AnalysisEntry[];
}

/** Cumulative, display-only preview. Trusted actions arrive in the final response. */
export interface ChatGuidance {
  interaction_mode?: "answer" | "clarify";
  clarifying_questions?: string[];
  suggested_replies?: string[];
}

export const CHAT_PROGRESS_PHASES = [
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
  capabilities?: { chat_contract_versions: number[] };
  client_settings?: ClientSettings;
}

export interface RuntimeConfig {
  pipeline?: PipelineSettings;
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
  enable_client_privacy_review?: boolean;
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
    | "enable_client_privacy_review"
    | "development_mode"
  >
> & { pipeline?: Partial<PipelineSettings> };

export interface AdminChatTestResult { response: ChatResponse; diagnostics: Record<string, unknown> }

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

function requestGuard(timeoutMs: number, detail: string, parent?: AbortSignal) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const abort = () => controller.abort(parent?.reason);
  const timeOut = (message: string) => controller.abort(
    new ApiError(504, "連線逾時", message, false),
  );
  const reset = (milliseconds: number, message: string) => {
    clearTimeout(timer);
    if (!controller.signal.aborted) timer = setTimeout(() => timeOut(message), milliseconds);
  };
  if (parent?.aborted) abort();
  else parent?.addEventListener("abort", abort, { once: true });
  reset(timeoutMs, detail);
  return {
    signal: controller.signal,
    reset,
    timeOut,
    dispose() {
      clearTimeout(timer);
      parent?.removeEventListener("abort", abort);
    },
  };
}

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

export function assertResponseContract(request: ChatRequest, response: ChatResponse): void {
  if ((response.contract_version ?? 1) !== (request.contract_version ?? 1)) {
    throw new ApiError(502, "回覆協定版本不符", "服務回覆與本次要求的版本不符，請重新連線後再試。", false);
  }
}

export function parseChatResponse(payload: unknown, expectedContractVersion?: number): ChatResponse {
  if (expectedContractVersion !== undefined && isRecord(payload) && (payload.contract_version ?? 1) !== expectedContractVersion) {
    throw new ApiError(502, "回覆協定版本不符", "服務回覆與本次要求的版本不符，請重新連線後再試。", false);
  }
  if (!isRecord(payload) || typeof payload.reply !== "string" || !Array.isArray(payload.suggested_replies)
    || !Array.isArray(payload.action_buttons) || !Array.isArray(payload.clarifying_questions)
    || !isRecord(payload.rag_used) || typeof payload.rag_used.status !== "boolean"
    || !Array.isArray(payload.rag_used.sources) || payload.rag_used.sources.length > 100 || !payload.rag_used.sources.every(isRagSource)
    || !["answer", "clarify"].includes(String(payload.interaction_mode))) throw invalidStream();
  if (payload.contract_version !== undefined && payload.contract_version !== 2 && payload.contract_version !== 3 && payload.contract_version !== 4) throw invalidStream();
  if (payload.contract_version === 2 || payload.contract_version === 3) {
    if (!Number.isSafeInteger(payload.facts_revision) || Number(payload.facts_revision) < 0
      || !Array.isArray(payload.fact_updates) || payload.fact_updates.length > 13 || !payload.fact_updates.every(isFactUpdate)
      || (payload.clarification !== null && !isClarification(payload.clarification, payload.contract_version))
      || !Array.isArray(payload.answer_sections) || payload.answer_sections.length > 6
      || !payload.answer_sections.every(section => isRecord(section) && ["direction", "basis", "next_steps"].includes(String(section.kind))
        && typeof section.text === "string" && Array.isArray(section.source_ids) && section.source_ids.every(id => typeof id === "string"))
      || !isRecord(payload.execution) || typeof payload.execution.route !== "string" || !Number.isSafeInteger(payload.execution.model_calls)
      || Number(payload.execution.model_calls) < 0) throw invalidStream();
    if (payload.contract_version === 2 && payload.fact_updates.some(update => Array.isArray(update.value))) throw invalidStream();
  }
  if (payload.contract_version === 4) {
    if (!Number.isSafeInteger(payload.context_revision) || Number(payload.context_revision) < 0
      || (payload.summary_update !== null && !isSummaryUpdate(payload.summary_update))
      || (payload.clarification !== null && !isClarification(payload.clarification, 4))
      || !isRecord(payload.execution) || typeof payload.execution.route !== "string" || !Number.isSafeInteger(payload.execution.model_calls)
      || Number(payload.execution.model_calls) < 0) throw invalidStream();
  }
  if ((payload.contract_version === 3 || payload.contract_version === 4) && sanitizeAnalysis(payload.analysis) === undefined) throw invalidStream();
  if (payload.analysis !== undefined && sanitizeAnalysis(payload.analysis) === undefined) throw invalidStream();
  return { ...payload, ...(payload.interaction_mode === "answer" && payload.clarification !== undefined ? { clarification: null } : {}) } as unknown as ChatResponse;
}

function isRagSource(value: unknown): value is RagSource | string {
  if (typeof value === "string") return value.trim().length > 0 && value.length <= 4000;
  if (!isRecord(value) || typeof value.label !== "string" || !value.label.trim() || value.label.length > 4000) return false;
  if (Object.keys(value).some(key => !["label", "type", "collection", "doc_id", "distance", "source_url", "article", "version", "law_name", "article_number", "checked_at", "effective_date", "promulgation_date", "promulgated_date", "promulgated_date_scope", "effective_date_scope", "effective_note"].includes(key))) return false;
  if (value.type !== undefined && !["law", "judgment", "remedy", "unknown"].includes(String(value.type))) return false;
  if (["collection", "doc_id", "source_url", "article", "version", "law_name", "article_number", "checked_at", "effective_date", "promulgation_date", "promulgated_date", "promulgated_date_scope", "effective_date_scope", "effective_note"].some(key => value[key] != null && (typeof value[key] !== "string" || value[key].length > 4000))) return false;
  return value.distance == null || (typeof value.distance === "number" && Number.isFinite(value.distance));
}

/** Consume complete SSE events, preserving split UTF-8 characters and line endings. */
async function readChatStream(
  response: Response,
  signal?: AbortSignal,
  onDelta?: (text: string) => void,
  onGuidance?: (guidance: ChatGuidance) => void,
  onProgress?: (progress: ChatProgress) => void,
  onActivity?: () => void,
  onAnalysis?: (analysis: AnalysisEntry) => void,
  onReasoning?: (reasoning: ReasoningEntry) => void,
  expectedContractVersion?: number,
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
      if (!eventData || !["delta", "guidance", "progress", "analysis", "reasoning", "done", "error"].includes(eventName)) return;
      let payload: unknown;
      try { payload = JSON.parse(eventData); } catch { throw invalidStream(); }
      if (!isRecord(payload)) throw invalidStream();
      if (eventName === "reasoning") {
        if (!isReasoningEntry(payload)) throw invalidStream();
        onReasoning?.(payload);
      } else if (eventName === "analysis") {
        if (!isAnalysisEntry(payload)) throw invalidStream();
        onAnalysis?.(payload);
      } else if (eventName === "progress") {
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
        result = parseChatResponse(payload, expectedContractVersion);
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
      if (value?.byteLength) onActivity?.();
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
  onAnalysis?: (analysis: AnalysisEntry) => void,
  onReasoning?: (reasoning: ReasoningEntry) => void,
): Promise<ChatResponse> {
  const reviewedBody = reviewedRequestBody(request);
  const guard = requestGuard(30_000, "後端尚未開始回應，請確認服務已啟動後再試。", signal);
  // Match the Functions 180-second limit with a small allowance for transport.
  // Heartbeats prove the connection is alive, but must not keep a turn pending forever.
  const deadline = setTimeout(() => guard.timeOut("回覆等待逾時，請稍後再試。"), 190_000);
  try {
    const response = await fetch(`${API_BASE_URL}/v1/chat/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: `${reviewedBody.slice(0, -1)},"stream":true}`,
      signal: guard.signal,
    });
    const receivedActivity = () => guard.reset(45_000, "回覆連線已中斷，請稍後再試。");
    receivedActivity();
    if (!response.ok) await throwResponseError(response);
    const result = !response.headers.get("content-type")?.includes("text/event-stream")
      ? parseChatResponse(await response.json(), request.contract_version ?? 1)
      : await readChatStream(response, guard.signal, onDelta, onGuidance, onProgress, receivedActivity, onAnalysis, onReasoning, request.contract_version ?? 1);
    assertResponseContract(request, result);
    return result;
  } catch (error) {
    // Some transports reject with AbortError instead of the supplied reason.
    // Keep timeouts distinct from the user's Stop action and never auto-resend.
    if (guard.signal.aborted) throw guard.signal.reason;
    throw error;
  } finally {
    clearTimeout(deadline);
    guard.dispose();
  }
}

/**
 * 健康狀態檢查。
 */
export async function checkHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const guard = requestGuard(10_000, "後端連線逾時，請稍後重新連線。", signal);
  try {
    return await apiFetch<HealthResponse>("/v1/health/", { signal: guard.signal, cache: "no-store" });
  } catch (error) {
    if (guard.signal.aborted) throw guard.signal.reason;
    throw error;
  } finally {
    guard.dispose();
  }
}

export async function getRuntimeConfig(adminToken: string): Promise<RuntimeConfig> {
  return apiFetch<RuntimeConfig>("/v1/admin/config", {
    headers: {
      Authorization: `Bearer ${adminToken}`,
    },
  });
}

export async function runAdminChatTest(adminToken: string, request: ChatRequest, overrides: RuntimeConfigUpdate, signal?: AbortSignal): Promise<AdminChatTestResult> {
  const reviewedBody = reviewedRequestBody(request);
  const guard = requestGuard(190_000, "診斷測試逾時，請檢查服務後再試。", signal);
  try {
    const result = await apiFetch<AdminChatTestResult>("/v1/admin/chat-test", {
      method: "POST", headers: { Authorization: `Bearer ${adminToken}` }, signal: guard.signal,
      body: `{"request":${reviewedBody},"overrides":${JSON.stringify(overrides)}}`,
    });
    if (!isRecord(result.diagnostics)) throw new ApiError(502, "診斷回應格式不正確");
    return { response: parseChatResponse(result.response, request.contract_version ?? 1), diagnostics: result.diagnostics };
  } catch (error) { if (guard.signal.aborted) throw guard.signal.reason; throw error; }
  finally { guard.dispose(); }
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
