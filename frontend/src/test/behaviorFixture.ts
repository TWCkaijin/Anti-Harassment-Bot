import type { AdminChatTestResult, ChatRequest, RuntimeConfig } from "../services/api";
import { batchConfig } from "./scenarioFixture";
import { builtinBehaviorCases, prepareBehaviorRun } from "../services/behaviorTests";

export const behaviorConfig: RuntimeConfig = { ...batchConfig, chat_test_contract_sha256: "b".repeat(64), enable_client_privacy_review: false };
export const readyBehaviorCases = () => builtinBehaviorCases().map(test => ({ ...test, confirmed: true }));
export const behaviorRun = (ids = ["B01"], repetitions: 1 | 3 = 1) => prepareBehaviorRun(readyBehaviorCases().filter(test => ids.includes(test.id)), repetitions, behaviorConfig);
export function behaviorResult(request?: ChatRequest, reply = "合成回覆：我可以協助整理相關情境。", extra: Partial<AdminChatTestResult["response"]> = {}): AdminChatTestResult {
  return { response: { reply, session_id: "synthetic", anonymized: true, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [], contract_version: 4, context_revision: request?.case_context?.revision ?? 0, summary_update: null, clarification: null, execution: { route: "synthetic", model_calls: 1 }, analysis: [], ...extra }, diagnostics: { chat_test_contract_sha256: behaviorConfig.chat_test_contract_sha256, effective_config: behaviorConfig, corpus_version: "unknown", retrieved_sources: [], final_answer: [{ text: reply, source_ids: [] }] } };
}
