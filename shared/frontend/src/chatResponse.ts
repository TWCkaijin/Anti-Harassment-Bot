/** Validation of chat and diagnostic responses before anything reaches the UI. */
import { isClarification, isSummaryUpdate } from "./caseFacts";
import { sanitizeAnalysis } from "./analysis";
import { ApiError, invalidStream, isRecord } from "./apiErrors";
import {
  APPLICABLE_LAW_NAMES,
  CHAT_CONTRACT_VERSION,
  type AdminApplicableLawTestResult,
  type ApplicableLawName,
  type ApplicableLawPrediction,
  type ChatRequest,
  type ChatResponse,
  type RagInfo,
  type RagSource,
} from "./apiTypes";

export function assertResponseContract(request: ChatRequest, response: ChatResponse): void {
  if (response.contract_version !== (request.contract_version ?? CHAT_CONTRACT_VERSION)) {
    throw new ApiError(502, "回覆協定版本不符", "服務回覆與本次要求的版本不符，請重新連線後再試。", false);
  }
}

const RAG_SOURCE_STRING_FIELDS = [
  "collection",
  "doc_id",
  "source_url",
  "article",
  "version",
  "law_name",
  "article_number",
  "checked_at",
  "effective_date",
  "promulgation_date",
  "promulgated_date",
  "promulgated_date_scope",
  "effective_date_scope",
  "effective_note",
] as const;

const RAG_SOURCE_ALLOWED_KEYS = new Set<string>([
  "label",
  "type",
  "distance",
  ...RAG_SOURCE_STRING_FIELDS,
]);

const RAG_SOURCE_TYPES = new Set<string>(["law", "judgment", "remedy", "unknown"]);
const RETRIEVAL_STATUSES = new Set<string>(["ok", "empty", "failed", "disabled", "not_requested"]);
const RETRIEVAL_DATA_TYPES = new Set<string>(["law", "judgment", "remedy"]);

function isNonNegativeSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isValidExecutionSummary(value: unknown): value is { route: string; model_calls: number } {
  return (
    isRecord(value) &&
    typeof value.route === "string" &&
    isNonNegativeSafeInteger(value.model_calls)
  );
}

export function parseChatResponse(payload: unknown): ChatResponse {
  if (
    !isRecord(payload) ||
    typeof payload.reply !== "string" ||
    !Array.isArray(payload.suggested_replies) ||
    !Array.isArray(payload.action_buttons) ||
    !Array.isArray(payload.clarifying_questions) ||
    !isRecord(payload.rag_used) ||
    typeof payload.rag_used.status !== "boolean" ||
    !Array.isArray(payload.rag_used.sources) ||
    payload.rag_used.sources.length > 100 ||
    !payload.rag_used.sources.every(isRagSource) ||
    (payload.rag_used.retrieval !== undefined && !isRetrievalReport(payload.rag_used.retrieval)) ||
    (payload.interaction_mode !== "answer" && payload.interaction_mode !== "clarify")
  ) {
    throw invalidStream();
  }

  if (payload.contract_version !== CHAT_CONTRACT_VERSION) {
    throw new ApiError(
      502,
      "回覆協定版本不符",
      "服務回覆與本次要求的版本不符，請重新連線後再試。",
      false,
    );
  }

  const validV4 =
    isNonNegativeSafeInteger(payload.context_revision) &&
    (payload.summary_update === null || isSummaryUpdate(payload.summary_update)) &&
    (payload.clarification === null || isClarification(payload.clarification, 4)) &&
    isValidExecutionSummary(payload.execution) &&
    sanitizeAnalysis(payload.analysis) !== undefined;
  if (!validV4) throw invalidStream();

  return {
    ...payload,
    ...(payload.interaction_mode === "answer" && payload.clarification !== undefined
      ? { clarification: null }
      : {}),
  } as unknown as ChatResponse;
}

export function isRetrievalReport(value: unknown): value is NonNullable<RagInfo["retrieval"]> {
  if (!isRecord(value) || !RETRIEVAL_STATUSES.has(String(value.status))) {
    return false;
  }
  if (
    !Array.isArray(value.data_types) ||
    value.data_types.length > 3 ||
    !value.data_types.every((kind) => RETRIEVAL_DATA_TYPES.has(String(kind))) ||
    new Set(value.data_types).size !== value.data_types.length
  ) {
    return false;
  }
  return (
    Array.isArray(value.sources) &&
    value.sources.length <= 100 &&
    value.sources.every(isRagSource)
  );
}

function isRagSource(value: unknown): value is RagSource | string {
  if (typeof value === "string") {
    return value.trim().length > 0 && value.length <= 4000;
  }
  if (
    !isRecord(value) ||
    typeof value.label !== "string" ||
    !value.label.trim() ||
    value.label.length > 4000
  ) {
    return false;
  }
  if (Object.keys(value).some((key) => !RAG_SOURCE_ALLOWED_KEYS.has(key))) {
    return false;
  }
  if (value.type !== undefined && !RAG_SOURCE_TYPES.has(String(value.type))) {
    return false;
  }
  const hasInvalidStringField = RAG_SOURCE_STRING_FIELDS.some((key) => {
    const fieldValue = value[key];
    return fieldValue != null && (typeof fieldValue !== "string" || fieldValue.length > 4000);
  });
  if (hasInvalidStringField) {
    return false;
  }
  return (
    value.distance == null ||
    (typeof value.distance === "number" && Number.isFinite(value.distance))
  );
}

/** This diagnostic contract is independent of normal chat replies and never repairs model output. */
export function parseAdminApplicableLawTestResult(payload: unknown): AdminApplicableLawTestResult {
  const invalid = () => new ApiError(502, "適用法規預測格式不正確");
  if (!isRecord(payload) || Object.keys(payload).some(key => !["output_contract", "prediction", "diagnostics"].includes(key))
    || payload.output_contract !== "applicable_laws_v1" || !isRecord(payload.diagnostics)
    || !isRecord(payload.prediction)) throw invalid();
  const prediction = payload.prediction;
  const laws = prediction.predicted_laws;
  const status = prediction.decision_status;
  const reason = prediction.decision_reason;
  if (Object.keys(prediction).some(key => !["predicted_laws", "decision_status", "decision_reason"].includes(key))
    || !Array.isArray(laws) || laws.length > 3
    || !laws.every(law => APPLICABLE_LAW_NAMES.some(name => name === law))
    || new Set(laws).size !== laws.length
    || typeof status !== "string" || !["predicted", "insufficient_facts", "no_target_law"].includes(status)
    || (status === "predicted" ? laws.length === 0 : laws.length !== 0)
    || (status === "predicted" ? reason !== null : typeof reason !== "string" || !reason.trim() || [...reason].length > 300)) throw invalid();
  return {
    output_contract: "applicable_laws_v1",
    prediction: { predicted_laws: [...laws] as ApplicableLawName[], decision_status: status as ApplicableLawPrediction["decision_status"], decision_reason: reason as string | null },
    diagnostics: payload.diagnostics,
  };
}
