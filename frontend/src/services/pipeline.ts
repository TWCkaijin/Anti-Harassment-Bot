export interface PipelineSettings {
  mask_message: boolean;
  mask_case_context: boolean;
  mask_retrieval_query: boolean;
  trim_history: boolean;
  history_max_messages: number;
  history_max_chars: number;
  extract_facts: boolean;
  auto_clarify: boolean;
  model_selection_mode: boolean;
  enable_rag: boolean;
  enable_skills: boolean;
  enable_analysis: boolean;
  content_policy: "repair" | "annotate";
}

export const DEFAULT_PIPELINE: PipelineSettings = {
  mask_message: true, mask_case_context: true, mask_retrieval_query: true,
  trim_history: true, history_max_messages: 40, history_max_chars: 12_000,
  extract_facts: true, auto_clarify: true, model_selection_mode: true,
  enable_rag: true, enable_skills: true, enable_analysis: true, content_policy: "repair",
};

export interface ClientSettings {
  contract_version: 3 | 4;
  pipeline: Pick<PipelineSettings, "trim_history" | "history_max_messages" | "history_max_chars" | "model_selection_mode" | "enable_analysis">;
  enable_image_upload: boolean;
  enable_client_privacy_review?: boolean;
}
