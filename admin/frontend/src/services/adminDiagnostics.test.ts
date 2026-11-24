import { describe, expect, it } from "vitest";
import { DEFAULT_PIPELINE } from "@rag/shared/pipeline";
import { reproducibleOverrides } from "./adminDiagnostics";

const snapshot = {
  pipeline: DEFAULT_PIPELINE,
  openrouter_model: "synthetic/main",
  rag_retrieval_top_k: 3,
  rag_distance_threshold: null,
  enable_anonymization: true,
  temperature: 0.2,
  top_p: 1,
  max_tokens: 1200,
  reasoning_effort: "none",
  agent_prompt_sections: {},
  rag_collections: { law: "laws", judgment: "judgments", remedy: "remedies" },
  enable_image_upload: false,
  development_mode: false,
  gate_enabled: true,
  gate_model: "synthetic/gate",
  gate_prompt: "審查合成測試輸入。",
  gate_post_prompt: "審查合成測試回覆。",
  gate_timeout_seconds: 10,
};

describe("reproducibleOverrides Gate policy", () => {
  it("keeps the selected Gate settings in a diagnostic snapshot", () => {
    expect(reproducibleOverrides(snapshot)).toEqual(expect.objectContaining({
      gate_enabled: true,
      gate_model: "synthetic/gate",
      gate_prompt: "審查合成測試輸入。",
      gate_post_prompt: "審查合成測試回覆。",
      gate_timeout_seconds: 10,
    }));
  });

  it("rejects an enabled Gate snapshot without its policy", () => {
    expect(reproducibleOverrides({ ...snapshot, gate_prompt: "" })).toBeNull();
    expect(reproducibleOverrides({ ...snapshot, gate_post_prompt: "" })).toBeNull();
    const oldSnapshot: Record<string, unknown> = { ...snapshot };
    delete oldSnapshot.gate_post_prompt;
    expect(reproducibleOverrides(oldSnapshot)).toBeNull();
  });
});
