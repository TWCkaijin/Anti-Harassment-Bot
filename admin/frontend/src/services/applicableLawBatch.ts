import type { ReferenceAnswer } from "./lawBatchScoring";
import type { ApplicableLawPrediction, ChatRequest } from "./api";
import type { PrivacyDraft } from "@rag/shared/clientPrivacy";

export type BatchStatus = "pending" | "running" | "error" | ApplicableLawPrediction["decision_status"];
export interface BatchRow {
  row: number;
  reference?: ReferenceAnswer;
  draft: PrivacyDraft;
  request?: ChatRequest;
  status: BatchStatus;
  laws: string[];
  elapsedMs: number | null;
  error?: string;
  reason?: string | null;
}
export const BATCH_STATUS_LABELS: Record<BatchStatus, string> = {
  pending: "待預測", running: "預測中", predicted: "已預測", insufficient_facts: "資訊不足", no_target_law: "不適用三法", error: "失敗",
};
