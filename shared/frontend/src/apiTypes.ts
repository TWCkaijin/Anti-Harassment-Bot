/** Request, response and configuration shapes shared by the client and admin apps. */
import type { SummaryUpdate, CaseContext, Clarification, ClarificationAnswer } from "./caseFacts";
import type { AnalysisEntry } from "./analysis";
import type { ClientSettings, PipelineSettings } from "./pipeline";

// ── 型別定義 ──────────────────────────────────────────────────────────────

export interface MessageItem {
  role: "user" | "assistant";
  content: string;
}

/** The only chat contract the backend accepts. */
export const CHAT_CONTRACT_VERSION = 4;

export interface ChatRequest {
  message: string;
  history: MessageItem[];
  use_rag: boolean;
  image_base64?: string;
  /** Optional: the backend assumes the current contract when it is omitted. */
  contract_version?: typeof CHAT_CONTRACT_VERSION;
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
  /** Actual retrieval, independent of the subset cited by the final answer. */
  retrieval?: {
    status: "ok" | "empty" | "failed" | "disabled" | "not_requested";
    data_types: Array<"law" | "judgment" | "remedy">;
    sources: Array<RagSource | string>;
  };
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
  contract_version?: typeof CHAT_CONTRACT_VERSION;
  context_revision?: number;
  summary_update?: SummaryUpdate | null;
  clarification?: Clarification | null;
  /** Only present in conversations saved under the retired v2/v3 contract. */
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

export interface ChatRetraction {
  turn_id: string;
  code: "gate_denied" | "review_unavailable";
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
  revision?: string;
  gate_enabled?: boolean;
  gate_model?: string;
  gate_prompt?: string;
  gate_post_prompt?: string;
  gate_timeout_seconds?: number;
  gate_jev_threshold?: number;
  openrouter_provider_sort?: "auto" | "latency" | "throughput" | "price";
  rag_rerank_enabled?: boolean;
  rag_rerank_model?: string;
  rag_rerank_candidate_k?: number;
  rag_rerank_timeout_seconds?: number;
  rag_rerank_max_chars_per_doc?: number;

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
  /** Legacy configuration only; local preparation always runs automatically. */
  enable_client_privacy_review?: boolean;
  development_mode: boolean;
  enable_reasoning_display?: boolean;
  source: string;
  environment_document_id?: string;
  updated_at?: string;
  updated_by?: string;
  applicable_laws_contract_sha256?: string;
  chat_test_contract_sha256?: string;
}

export type RuntimeConfigUpdate = Partial<
  Pick<
    RuntimeConfig,
    | "gate_enabled"
    | "gate_model"
    | "gate_prompt"
    | "gate_post_prompt"
    | "gate_timeout_seconds"
    | "gate_jev_threshold"
    | "openrouter_provider_sort"
    | "rag_rerank_enabled"
    | "rag_rerank_model"
    | "rag_rerank_candidate_k"
    | "rag_rerank_timeout_seconds"
    | "rag_rerank_max_chars_per_doc"
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
    | "enable_reasoning_display"
  >
> & { pipeline?: Partial<PipelineSettings> };

export interface AdminChatTestResult { response: ChatResponse; diagnostics: Record<string, unknown> }

export const APPLICABLE_LAW_NAMES = ["性騷法", "性工法", "性平法"] as const;
export type ApplicableLawName = typeof APPLICABLE_LAW_NAMES[number];
export interface ApplicableLawPrediction {
  predicted_laws: ApplicableLawName[];
  decision_status: "predicted" | "insufficient_facts" | "no_target_law";
  decision_reason: string | null;
}
export interface AdminApplicableLawTestResult {
  output_contract: "applicable_laws_v1";
  prediction: ApplicableLawPrediction;
  diagnostics: Record<string, unknown>;
}
