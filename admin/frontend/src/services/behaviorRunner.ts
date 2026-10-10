import { createChatRequest } from "../hooks/conversationHistory";
import { ApiError, runAdminChatTest, type ChatRequest } from "./api";
import { emptySummaryContext } from "./caseFacts";
import { preparePrivateRequest } from "./privateRequest";
import { acceptedBehaviorContext, checkBehaviorTurn, refreshBehaviorEvidence, sanitizeBehaviorResult } from "./behaviorRecords";
import type { BehaviorRecord, BehaviorRun, BehaviorTurn } from "./behaviorTests";

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const done = () => { clearTimeout(timer); signal.removeEventListener("abort", done); resolve(); };
    const timer = setTimeout(done, ms); signal.addEventListener("abort", done, { once: true });
    if (signal.aborted) done();
  });
}
function failureMessage(error: unknown, aborted: boolean): { message: string; stop: boolean } {
  const status = error instanceof ApiError ? error.status : 0;
  const message = aborted ? "請求已中斷，可能已呼叫模型。請確認後手動重試。"
    : status === 409 ? "對話測試契約已變更，請下載紀錄並重新準備。"
    : status === 401 || status === 403 ? "管理員驗證失效，請重新登入。"
    : status === 429 ? "請求過於頻繁，請稍後手動重試。"
    : status === 504 ? "等待完整回覆逾時，可能已呼叫模型。"
    : status === 502 ? "模型或回覆格式未通過驗證，本輪沒有可覆核的完整回覆。"
    : status ? `請求失敗（HTTP ${status}），可手動重試。` : "連線失敗，請檢查網路後手動重試。";
  return { message, stop: aborted || [0, 401, 403, 409, 429].includes(status) };
}

/** Every turn gets its own full privacy review; stored JSON cannot confer send approval. */
export async function runBehaviorTests(options: {
  run: BehaviorRun; token: string; signal: AbortSignal; shouldPause: () => boolean;
  onUpdate: (run: BehaviorRun) => void; onActivity?: (text: string) => void;
  retryFailed?: boolean; delayMs?: number;
}): Promise<{ run: BehaviorRun; stoppedReason: string | null }> {
  const { token, signal, shouldPause, onUpdate } = options;
  let run = structuredClone(options.run), sent = false;
  const publish = async (index: number, change: Partial<BehaviorRecord>) => {
    run = { ...run, records: run.records.map((record, i) => i === index ? { ...record, ...change, evidenceSha256: "" } : record) };
    run = await refreshBehaviorEvidence(run); onUpdate(run);
  };
  const targets = run.records.map((record, index) => ({ record, index })).filter(({ record }) => options.retryFailed
    ? record.status === "error" || record.status === "interrupted" : record.status === "pending" || record.status === "paused");
  for (const { index } of targets) {
    const test = run.cases.find(item => item.id === run.records[index].caseId)!;
    while (true) {
      const record = run.records[index];
      const completed = record.turns.filter(turn => turn.status === "completed");
      const number = completed.length + 1;
      if (number > test.turns.length || signal.aborted || shouldPause()) break;
      if (sent) await delay(options.delayMs ?? 2100, signal);
      if (signal.aborted || shouldPause()) break;
      const context = structuredClone(completed.at(-1)?.contextAfter ?? emptySummaryContext());
      const history = completed.flatMap(turn => [
        { role: "user" as const, content: turn.request.message },
        { role: "assistant" as const, content: turn.result!.response.reply },
      ]);
      const request: ChatRequest = { ...createChatRequest(history, test.turns[number - 1], undefined, 4), contract_version: 4, case_context: context, use_rag: run.overrides.pipeline?.enable_rag !== false };
      await publish(index, { status: "running" });
      options.onActivity?.(`${test.id} · 第 ${record.repetition} 次 · 第 ${number} 輪：準備本機遮罩`);
      let approved: ChatRequest | null;
      try {
        approved = await preparePrivateRequest(request, signal);
      } catch {
        await publish(index, { status: "paused" });
        return { run, stoppedReason: "無法完成本機遮罩，請檢查訊息、歷史及摘要長度。" };
      }
      if (!approved || signal.aborted || shouldPause()) {
        await publish(index, { status: "paused" });
        return { run, stoppedReason: !approved && !signal.aborted ? "本機處理已取消，未送出本輪請求。" : null };
      }
      const start = performance.now();
      const turn: BehaviorTurn = { number, attempt: record.turns.filter(turn => turn.number === number).length + 1, status: "running", startedAt: new Date().toISOString(), elapsedMs: null, request: structuredClone(approved), issues: [] };
      await publish(index, { turns: [...run.records[index].turns, turn] });
      options.onActivity?.(`${test.id} · 第 ${record.repetition} 次 · 第 ${number} 輪：等待完整回覆`);
      sent = true;
      try {
        const received = await runAdminChatTest(token, approved, run.overrides, signal, run.contractSha256);
        if (signal.aborted) throw new Error("interrupted");
        if (received.diagnostics.chat_test_contract_sha256 !== run.contractSha256) throw new ApiError(409, "Contract mismatch");
        const result = sanitizeBehaviorResult(received);
        const finished: BehaviorTurn = { ...turn, status: "completed", elapsedMs: performance.now() - start, result, contextAfter: acceptedBehaviorContext(approved, result), issues: checkBehaviorTurn(test, { ...turn, result }) };
        await publish(index, { status: number === test.turns.length ? "completed" : "paused", turns: [...run.records[index].turns.slice(0, -1), finished] });
      } catch (error) {
        const failure = failureMessage(error, signal.aborted);
        const status = signal.aborted ? "interrupted" : "error";
        await publish(index, { status, turns: [...run.records[index].turns.slice(0, -1), { ...turn, status, elapsedMs: performance.now() - start, error: failure.message }] });
        if (failure.stop) return { run, stoppedReason: failure.message };
        break; // A failed turn blocks later turns in this case, never other independent cases.
      }
    }
    if (signal.aborted || shouldPause()) break;
  }
  return { run, stoppedReason: null };
}
