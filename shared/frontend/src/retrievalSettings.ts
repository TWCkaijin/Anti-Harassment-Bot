import type { RuntimeConfig } from "./api";

export const RETRIEVAL_DEFAULTS = {
  openrouter_provider_sort: "auto" as const,
  rag_rerank_enabled: false,
  rag_rerank_model: "cohere/rerank-4-fast",
  rag_rerank_candidate_k: 20,
  rag_rerank_timeout_seconds: 1,
  rag_rerank_max_chars_per_doc: 2000,
};
export function retrievalSettings(config: Partial<RuntimeConfig>) {
  return {
    openrouter_provider_sort: config.openrouter_provider_sort ?? RETRIEVAL_DEFAULTS.openrouter_provider_sort,
    rag_rerank_enabled: config.rag_rerank_enabled ?? RETRIEVAL_DEFAULTS.rag_rerank_enabled,
    rag_rerank_model: config.rag_rerank_model ?? RETRIEVAL_DEFAULTS.rag_rerank_model,
    rag_rerank_candidate_k: config.rag_rerank_candidate_k ?? RETRIEVAL_DEFAULTS.rag_rerank_candidate_k,
    rag_rerank_timeout_seconds: config.rag_rerank_timeout_seconds ?? RETRIEVAL_DEFAULTS.rag_rerank_timeout_seconds,
    rag_rerank_max_chars_per_doc: config.rag_rerank_max_chars_per_doc ?? RETRIEVAL_DEFAULTS.rag_rerank_max_chars_per_doc,
  };
}
