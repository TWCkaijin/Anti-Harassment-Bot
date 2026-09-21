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

  if (!response.ok) {
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

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// ── 公開 API 函式 ────────────────────────────────────────────────────────

/**
 * 傳送對話訊息給 AI，並取得回覆。
 */
export async function sendChat(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  return apiFetch<ChatResponse>("/v1/chat/", {
    method: "POST",
    body: JSON.stringify(request),
    signal,
  });
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
