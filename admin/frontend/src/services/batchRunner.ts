import { createChatRequest } from '../hooks/conversationHistory';
import { ApiError, runAdminApplicableLawTest, runAdminChatTest, type ChatRequest } from './api';
import { emptySummaryContext } from './caseFacts';
import { preparePrivateRequest } from './privateRequest';
import { createPrivacyDraft } from './clientPrivacy';
import { acceptedBehaviorContext, behaviorDigest, checkBehaviorTurn, sanitizeBehaviorData, sanitizeBehaviorResult } from './behaviorRecords';
import type { BatchRecord, BatchTurn, ScriptRun } from './batchScript';

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, ms); signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}
export async function refreshBatchRecord(run: ScriptRun, record: BatchRecord): Promise<BatchRecord> {
  return { ...record, evidenceSha256: await behaviorDigest({ scriptVersion: run.scriptVersion,
    case: run.cases.find(test => test.id === record.caseId), repetition: record.repetition, overrides: run.overrides,
    contractSha256: run.contractSha256, environment: run.environment, status: record.status, turns: record.turns }) };
}
export async function refreshBatchEvidence(run: ScriptRun): Promise<ScriptRun> {
  return { ...run, records: await Promise.all(run.records.map(record => refreshBatchRecord(run, record))) };
}
/** One queue owns dispatch pacing; workers own separate case histories and update only their record. */
export async function runScriptBatch(options: {
  run: ScriptRun; token: string; signal: AbortSignal; shouldPause: () => boolean;
  onUpdate: (run: ScriptRun) => void; hiddenTerms?: string[]; retryFailed?: boolean; delayMs?: number;
}): Promise<{ run: ScriptRun; stoppedReason: string | null }> {
  const { token, signal, shouldPause } = options;
  let run = structuredClone(options.run), stoppedReason: string | null = null;
  let gate = Promise.resolve(), nextDispatch = 0, cursor = 0;
  const stopped = () => signal.aborted || shouldPause() || !!stoppedReason;
  const reserve = async () => {
    const previous = gate;
    let release!: () => void;
    gate = new Promise<void>(resolve => { release = resolve; });
    await previous;
    try {
      if (stopped()) return false;
      await delay(Math.max(0, nextDispatch - performance.now()), signal);
      if (stopped()) return false;
      nextDispatch = performance.now() + (options.delayMs ?? 2100);
      return true;
    } finally { release(); }
  };
  const publish = (index: number, changes: Partial<BatchRecord>) => {
    run = { ...run, records: run.records.map((record, i) => i === index ? { ...record, ...changes, evidenceSha256: '' } : record) };
    options.onUpdate(run);
  };
  const seal = async (index: number) => {
    const original = run.records[index], updated = await refreshBatchRecord(run, original);
    if (run.records[index] === original) {
      run = { ...run, records: run.records.map((record, i) => i === index ? updated : record) };
      options.onUpdate(run);
    }
  };
  const targets = run.records.map((record, index) => ({ record, index })).filter(({ record }) =>
    (options.retryFailed ? ['error', 'interrupted'] : ['pending', 'paused']).includes(record.status));
  const execute = async (index: number) => {
    const test = run.cases.find(test => test.id === run.records[index].caseId)!;
    while (!stopped()) {
      const record = run.records[index], completed = record.turns.filter(turn => turn.status === 'completed');
      const number = completed.length + 1;
      if (number > test.turns.length) break;
      const history = completed.flatMap(turn => [
        { role: 'user' as const, content: turn.request.message },
        { role: 'assistant' as const, content: turn.result?.response.reply ?? JSON.stringify(turn.prediction) },
      ]);
      const request: ChatRequest = { ...createChatRequest(history, test.turns[number - 1], undefined, 4), contract_version: 4,
        case_context: structuredClone(completed.at(-1)?.contextAfter ?? emptySummaryContext()), use_rag: run.overrides.pipeline?.enable_rag !== false };
      let approved: ChatRequest | null;
      try { approved = await preparePrivateRequest(createPrivacyDraft(request, options.hiddenTerms ?? []).request, signal); }
      catch { stoppedReason = '無法完成本機遮罩，請檢查訊息、歷史及摘要長度。'; break; }
      if (!approved) { stoppedReason = '本機處理已取消，未送出本輪請求。'; break; }
      if (!await reserve()) break;
      const start = performance.now();
      const turn: BatchTurn = { number, attempt: record.turns.filter(turn => turn.number === number).length + 1,
        status: 'running', startedAt: new Date().toISOString(), elapsedMs: null, request: structuredClone(approved), issues: [] };
      publish(index, { status: 'running', turns: [...record.turns, turn] });
      try {
        let finished: BatchTurn;
        if (run.mode === 'chat') {
          const received = await runAdminChatTest(token, approved, run.overrides, signal, run.contractSha256);
          if (received.diagnostics.chat_test_contract_sha256 !== run.contractSha256) throw new ApiError(409, 'Contract mismatch');
          const result = sanitizeBehaviorResult(received);
          finished = { ...turn, status: 'completed', result, contextAfter: acceptedBehaviorContext(approved, result), issues: checkBehaviorTurn(test, { ...turn, result }) };
        } else {
          const received = await runAdminApplicableLawTest(token, approved, run.overrides, signal, run.contractSha256);
          finished = { ...turn, status: 'completed', prediction: received.prediction, lawDiagnostics: sanitizeBehaviorData(received.diagnostics) as Record<string, unknown> };
        }
        if (signal.aborted) throw new Error('interrupted');
        finished.elapsedMs = performance.now() - start;
        publish(index, { status: number === test.turns.length ? 'completed' : 'paused', turns: [...run.records[index].turns.slice(0, -1), finished] });
      } catch (error) {
        const status = error instanceof ApiError ? error.status : 0;
        const message = signal.aborted ? '請求已中斷，可能已呼叫模型。請確認後手動重試。'
          : status === 409 ? '測試契約已變更，請下載紀錄並重新準備。'
          : status === 401 || status === 403 ? '管理員驗證失效，請重新登入。'
          : status === 429 ? '請求過於頻繁，已停止派送；請稍後手動重試。'
          : status ? `請求失敗（HTTP ${status}），可能已呼叫模型，請確認後手動重試。` : '連線失敗，可能已呼叫模型，請確認後手動重試。';
        const turnStatus = signal.aborted ? 'interrupted' : 'error';
        publish(index, { status: turnStatus, turns: [...run.records[index].turns.slice(0, -1), { ...turn, status: turnStatus, elapsedMs: performance.now() - start, error: message }] });
        if (signal.aborted || [0, 401, 403, 409, 429].includes(status)) stoppedReason = message;
        break;
      }
    }
    await seal(index);
  };
  await Promise.all(Array.from({ length: run.template.parallelism }, async () => {
    while (!stopped() && cursor < targets.length) {
      const target = targets[cursor++];
      await execute(target.index);
    }
  }));
  return { run, stoppedReason };
}
