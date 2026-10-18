import type { RuntimeConfig, RuntimeConfigUpdate } from "./api";
import { retrievalSettings } from "./retrievalSettings";
import { DEFAULT_PIPELINE } from "./pipeline";

export const PROMPT_SECTION_FIELDS = [
  ["core_mission", "你的核心使命", 5],
  ["communication_principles", "溝通原則", 5],
  ["important_resources", "重要通報資源", 4],
  ["limitations", "限制說明", 4],
  ["language", "語言", 3],
  ["output_format", "強制輸出格式", 7],
  ["analysis_rules", "分析規則", 10],
  ["retrieval_instructions", "檢索指令", 6],
] as const;


export function configToUpdate(config: RuntimeConfig): RuntimeConfigUpdate & { revision?: string } {
  return {
    revision: config.revision,
    pipeline: { ...DEFAULT_PIPELINE, ...config.pipeline },
    ...retrievalSettings(config),
    openrouter_model: config.openrouter_model,
    rag_retrieval_top_k: config.rag_retrieval_top_k,
    rag_distance_threshold: config.rag_distance_threshold,
    enable_anonymization: config.enable_anonymization,
    temperature: config.temperature,
    top_p: config.top_p,
    max_tokens: config.max_tokens,
    reasoning_effort: config.reasoning_effort,
    agent_prompt_sections: config.agent_prompt_sections,
    rag_collections: config.rag_collections,
    maintenance_message: config.maintenance_message ?? "",
    enable_image_upload: config.enable_image_upload,
    development_mode: config.development_mode,
    enable_reasoning_display: config.enable_reasoning_display ?? false,
  };
}


export function temporaryOverrides(config: RuntimeConfig): RuntimeConfigUpdate {
  const result = configToUpdate(config);
  delete result.revision;
  return result;
}
