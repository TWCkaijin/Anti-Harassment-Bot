import { parseReferenceAnswer, type ReferenceAnswer } from "./lawBatchScoring";
import { ApiError, runAdminApplicableLawTest, type ApplicableLawPrediction, type ChatRequest, type RuntimeConfig, type RuntimeConfigUpdate } from "./api";
import { reproducibleOverrides } from "./adminDiagnostics";
import { approvePrivacyDraft, createPrivacyDraft, type PrivacyDraft } from "./clientPrivacy";
import type { ScenarioRow } from "./scenarioWorkbook";

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
export interface BatchSnapshot { overrides: RuntimeConfigUpdate; contractSha256: string; model: string }
export const BATCH_STATUS_LABELS: Record<BatchStatus, string> = {
  pending: "待預測", running: "預測中", predicted: "已預測", insufficient_facts: "資訊不足", no_target_law: "不適用三法", error: "失敗",
};

export function prepareLawBatch(rows: ScenarioRow[], config: RuntimeConfig, hiddenTerms: string[]): { rows: BatchRow[]; snapshot: BatchSnapshot } {
  const overrides = reproducibleOverrides(config);
  if (!overrides || !/^[a-f0-9]{64}$/.test(config.applicable_laws_contract_sha256 ?? "")) throw new Error("後端未提供完整預測設定，請確認後端已更新並重新登入。");
  if (!rows.length) throw new Error("所選欄位沒有可預測的情境。");
  const tooLong = rows.filter(row => [...row.text].length > 2000);
  if (tooLong.length) throw new Error(`第 ${tooLong.slice(0, 8).map(row => row.row).join("、")} 列情境超過 2,000 字，請縮短後重新選檔。`);
  const prepared = rows.map(({ row, text, reference }): BatchRow => {
    const draft = createPrivacyDraft({ message: text, history: [], use_rag: true, contract_version: 4 }, hiddenTerms);
    // Custom replacements can lengthen text; never truncate a scenario silently.
    if ([...draft.request.message].length > 2000) throw new Error(`第 ${row} 列遮罩後超過 2,000 字，請縮短情境。`);
    return { row, reference: parseReferenceAnswer(reference), draft, status: "pending", laws: [], elapsedMs: null };
  });
  return { rows: prepared, snapshot: { overrides, contractSha256: config.applicable_laws_contract_sha256!, model: config.openrouter_model } };
}

/** Called only by the explicit batch-start action, after the full preview is available. */
export function approveLawBatch(rows: BatchRow[]): BatchRow[] {
  return rows.map(row => ({ ...row, request: row.request ?? approvePrivacyDraft(row.draft) }));
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

export async function runLawBatch(options: {
  rows: BatchRow[]; snapshot: BatchSnapshot; token: string; signal: AbortSignal;
  shouldPause: () => boolean; onUpdate: (rows: BatchRow[]) => void;
  retryFailed?: boolean; delayMs?: number;
}): Promise<{ rows: BatchRow[]; stoppedReason: string | null }> {
  const { snapshot, token, signal, shouldPause, onUpdate } = options;
  let rows = [...options.rows];
  const targets = rows.map((row, index) => ({ row, index })).filter(({ row }) => row.status === (options.retryFailed ? "error" : "pending"));
  let stoppedReason: string | null = null;
  const update = (index: number, changes: Partial<BatchRow>) => {
    rows = rows.map((row, i) => i === index ? { ...row, ...changes } : row);
    onUpdate(rows);
  };
  for (const [position, { row, index }] of targets.entries()) {
    if (signal.aborted || shouldPause()) break;
    if (!row.request) throw new Error("請先確認整批送出內容。");
    update(index, { status: "running", error: undefined, reason: null, laws: [], elapsedMs: null });
    const start = performance.now();
    try {
      const result = await runAdminApplicableLawTest(token, row.request, snapshot.overrides, signal, snapshot.contractSha256);
      if (signal.aborted) throw new Error("interrupted");
      update(index, { status: result.prediction.decision_status, laws: result.prediction.predicted_laws, reason: result.prediction.decision_reason, elapsedMs: performance.now() - start });
    } catch (error) {
      // Keep provider bodies and diagnostics out of batch results and exports.
      const status = error instanceof ApiError ? error.status : 0;
      const message = signal.aborted ? "請求已中斷，可能已呼叫模型。"
        : status === 409 ? "預測契約已變更，請下載結果並重新準備。"
        : status === 401 || status === 403 ? "管理員驗證失效，請重新登入。"
        : status === 429 ? "請求過於頻繁，請稍後重試。"
        : status ? `請求失敗（HTTP ${status}），可手動重試。` : "連線失敗，請檢查網路後重試。";
      update(index, { status: "error", error: message, elapsedMs: performance.now() - start });
      if (signal.aborted || [0, 401, 403, 409, 429].includes(status)) { stoppedReason = message; break; }
    }
    if (position < targets.length - 1 && !signal.aborted && !shouldPause()) await pause(options.delayMs ?? 2100, signal);
  }
  return { rows, stoppedReason };
}
