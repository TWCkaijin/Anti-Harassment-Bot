import type { RuntimeConfigUpdate } from "./api";
import { isRecord } from "./caseFacts";
import { DEFAULT_PIPELINE } from "./pipeline";

/** Freeze the complete writable configuration; partial diagnostics cannot support a fair comparison. */
export function reproducibleOverrides(value: unknown): RuntimeConfigUpdate | null {
  if (!isRecord(value) || !isRecord(value.pipeline)) return null;
  const pipeline = value.pipeline;
  const collections = value.rag_collections;
  for (const [key, defaultValue] of Object.entries(DEFAULT_PIPELINE)) {
    if (typeof value.pipeline[key] !== typeof defaultValue) return null;
  }
  if (!["repair", "annotate"].includes(String(value.pipeline.content_policy))) return null;
  if (value.enable_client_privacy_review !== undefined && typeof value.enable_client_privacy_review !== "boolean") return null;
  if (typeof value.openrouter_model !== "string" || !value.openrouter_model.trim()
    || !["none", "minimal", "low", "medium", "high", "xhigh", "max"].includes(String(value.reasoning_effort))
    || !["enable_anonymization", "enable_image_upload", "development_mode"].every(key => typeof value[key] === "boolean")
    || !["rag_retrieval_top_k", "temperature", "top_p", "max_tokens"].every(key => typeof value[key] === "number" && Number.isFinite(value[key]))
    || !(value.rag_distance_threshold === null || typeof value.rag_distance_threshold === "number" && Number.isFinite(value.rag_distance_threshold))
    || !(value.maintenance_message == null || typeof value.maintenance_message === "string")
    || !isRecord(value.agent_prompt_sections) || !Object.values(value.agent_prompt_sections).every(item => typeof item === "string")
    || !isRecord(collections) || !["law", "judgment", "remedy"].every(key => typeof collections[key] === "string")) return null;
  const keys = ["openrouter_model", "rag_retrieval_top_k", "rag_distance_threshold", "enable_anonymization", "temperature", "top_p", "max_tokens", "reasoning_effort", "agent_prompt_sections", "rag_collections", "enable_image_upload", "development_mode"];
  const result: Record<string, unknown> = Object.fromEntries(keys.map(key => [key, structuredClone(value[key])]));
  result.maintenance_message = value.maintenance_message ?? "";
  if (typeof value.enable_client_privacy_review === "boolean") result.enable_client_privacy_review = value.enable_client_privacy_review;
  result.pipeline = Object.fromEntries(Object.keys(DEFAULT_PIPELINE).map(key => [key, pipeline[key]]));
  return result as RuntimeConfigUpdate;
}
