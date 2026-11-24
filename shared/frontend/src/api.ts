/**
 * 性騷擾防治智能 AI — 後端 API 呼叫層
 * 所有與後端通訊皆透過此模組，方便統一管理 base URL 與錯誤處理。
 * Types, errors and response validation live in apiTypes.ts, apiErrors.ts and
 * chatResponse.ts; this module re-exports their public parts.
 */

import { isAnalysisEntry, type AnalysisEntry } from "./analysis";
import { isReasoningEntry, type ReasoningEntry } from "./reasoning";
import { reviewedRequestBody } from "./clientPrivacy";
import { ApiError, invalidStream, isRecord, normalizeApiErrorDetail, throwResponseError } from "./apiErrors";
import { assertResponseContract, parseChatResponse } from "./chatResponse";
import { CHAT_PROGRESS_PHASES, type ChatGuidance, type ChatProgress, type ChatRequest, type ChatResponse, type ChatRetraction, type HealthResponse } from "./apiTypes";

export * from "./apiTypes";
export { ApiError, normalizeApiErrorDetail } from "./apiErrors";
export { assertResponseContract, isRetrievalReport, parseAdminApplicableLawTestResult, parseChatResponse } from "./chatResponse";
export type { AnalysisEntry } from "./analysis";
export type { ReasoningEntry } from "./reasoning";
export type { ClientSettings, PipelineSettings } from "./pipeline";
export type { CaseContext, LegacyCaseContext, SummaryCaseContext, SummaryUpdate, Clarification, ClarificationAnswer, FactUpdate, FactKey, CaseFact } from "./caseFacts";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "/api";

// ── 共用 fetch 包裝 ──────────────────────────────────────────────────────

export function requestGuard(timeoutMs: number, detail: string, parent?: AbortSignal) {
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
  onRetract?: (retraction: ChatRetraction) => void,
): Promise<ChatResponse> {
  if (!response.body) throw invalidStream();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let result: ChatResponse | undefined;
  let turnId: string | undefined;
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });

  function consumeLine(line: string) {
    if (line === "") {
      const eventName = event;
      const eventData = data.join("\n");
      event = "";
      data = [];
      if (!eventData || !["delta", "guidance", "progress", "analysis", "reasoning", "retract", "done", "error"].includes(eventName)) return;
      let payload: unknown;
      try { payload = JSON.parse(eventData); } catch { throw invalidStream(); }
      if (!isRecord(payload)) throw invalidStream();
      if (eventName === "retract") {
        if (typeof payload.turn_id !== "string" || !/^[0-9a-f-]{32,36}$/.test(payload.turn_id)
          || (payload.code !== "gate_denied" && payload.code !== "review_unavailable")
          || (turnId && turnId !== payload.turn_id)) throw invalidStream();
        turnId = payload.turn_id;
        onRetract?.({ turn_id: payload.turn_id, code: payload.code });
      } else if (eventName === "reasoning") {
        if (!isReasoningEntry(payload)) throw invalidStream();
        onReasoning?.(payload);
      } else if (eventName === "analysis") {
        if (!isAnalysisEntry(payload)) throw invalidStream();
        onAnalysis?.(payload);
      } else if (eventName === "progress") {
        if (!CHAT_PROGRESS_PHASES.some(phase => phase === payload.phase)
          || typeof payload.elapsed_ms !== "number" || !Number.isFinite(payload.elapsed_ms)
          || payload.elapsed_ms < 0) throw invalidStream();
        if (payload.turn_id !== undefined) {
          if (typeof payload.turn_id !== "string" || !/^[0-9a-f-]{32,36}$/.test(payload.turn_id)
            || (turnId && turnId !== payload.turn_id)) throw invalidStream();
          turnId = payload.turn_id;
        }
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
        if (payload.turn_id !== undefined && (typeof payload.turn_id !== "string" || (turnId && turnId !== payload.turn_id))) throw invalidStream();
        result = parseChatResponse(payload);
      } else {
        throw new ApiError(
          typeof payload.status === "number" ? payload.status : 502,
          "回覆產生失敗",
          normalizeApiErrorDetail(payload.detail),
          payload.retryable === true,
          typeof payload.debug_message === "string" ? payload.debug_message : undefined,
          typeof payload.code === "string" ? payload.code : undefined,
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
  onRetract?: (retraction: ChatRetraction) => void,
  appCheckToken?: string,
): Promise<ChatResponse> {
  const reviewedBody = reviewedRequestBody(request);
  const guard = requestGuard(30_000, "後端尚未開始回應，請確認服務已啟動後再試。", signal);
  // Match the Functions 180-second limit with a small allowance for transport.
  // Heartbeats prove the connection is alive, but must not keep a turn pending forever.
  const deadline = setTimeout(() => guard.timeOut("回覆等待逾時，請稍後再試。"), 190_000);
  try {
    const response = await fetch(`${API_BASE_URL}/v1/chat/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(appCheckToken ? { "X-Firebase-AppCheck": appCheckToken } : {}) },
      body: `${reviewedBody.slice(0, -1)},"stream":true}`,
      signal: guard.signal,
    });
    const receivedActivity = () => guard.reset(45_000, "回覆連線已中斷，請稍後再試。");
    receivedActivity();
    if (!response.ok) await throwResponseError(response);
    const result = !response.headers.get("content-type")?.includes("text/event-stream")
      ? parseChatResponse(await response.json())
      : await readChatStream(response, guard.signal, onDelta, onGuidance, onProgress, receivedActivity, onAnalysis, onReasoning, onRetract);
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
