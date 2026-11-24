import { importBehaviorRun, MAX_BEHAVIOR_JSON_BYTES, sanitizeBehaviorData } from './behaviorRecords';
import { asBehaviorRun, fromBehaviorRun, importBatchTemplate, type ScriptRun } from './batchScript';
import { refreshBatchEvidence } from './batchRunner';
import { BATCH_STATUS_LABELS, type BatchRow } from './applicableLawBatch';
import { BEHAVIOR_STATUS } from './behaviorTests';
import { parseReferenceAnswer, evaluateLawRow, scoreLawBatch, formatBatchRate, LAW_MATCH_RULE } from './lawBatchScoring';
import { insertScenarioResults, type ScenarioWorkbook, type ScenarioSheet } from './scenarioWorkbook';

export async function importScriptRun(raw: string): Promise<ScriptRun> {
  if (new TextEncoder().encode(raw).length > MAX_BEHAVIOR_JSON_BYTES) throw new Error('執行紀錄不可超過 20 MB。');
  const value = JSON.parse(raw);
  if (value.format === 'behavior-tests-v1') return fromBehaviorRun(await importBehaviorRun(raw));
  if (value.format !== 'batch-run-v1' || !['chat', 'law'].includes(value.mode)) throw new Error('執行紀錄格式不正確。');
  const template = importBatchTemplate(JSON.stringify(value.template));
  if (template.mode !== value.mode || template.repetitions !== value.repetitions) throw new Error('紀錄與腳本設定不符。');
  const { mode, template: _template, ...body } = value;
  void _template;
  const run = await importBehaviorRun(JSON.stringify({ ...body, format: 'behavior-tests-v1' }), mode);
  return refreshBatchEvidence({ ...run, format: 'batch-run-v1', mode, template });
}
export function exportScriptRun(run: ScriptRun): string {
  const raw = JSON.stringify(sanitizeBehaviorData(run), null, 2);
  if (new TextEncoder().encode(raw).length > MAX_BEHAVIOR_JSON_BYTES) throw new Error('執行紀錄超過 20 MB，請縮小測試批次。');
  return raw;
}
export function lawRows(run: ScriptRun, roundNumber?: number): Omit<BatchRow, 'draft'>[] {
  const cases = new Map(run.cases.map(test => [test.id, test]));
  return run.records.map(record => {
    const test = cases.get(record.caseId)!;
    const number = roundNumber ?? test.turns.length;
    const turn = record.turns.filter(turn => turn.number === number).at(-1);
    const earlierFailure = !turn && ['error', 'interrupted'].includes(record.status);
    return { row: test.source.row, reference: parseReferenceAnswer(test.reference),
      status: turn?.prediction?.decision_status ?? (earlierFailure || turn?.error ? 'error' : turn?.status === 'running' ? 'running' : 'pending'),
      laws: turn?.prediction?.predicted_laws ?? [], elapsedMs: turn?.elapsedMs ?? null, error: turn?.error, reason: turn?.prediction?.decision_reason };
  });
}
export function resultHeaders(run: Pick<ScriptRun, 'mode' | 'repetitions' | 'cases'>): string[] {
  const rounds = Math.max(...run.cases.map(test => test.turns.length));
  return Array.from({ length: run.repetitions }, (_, r) => Array.from({ length: rounds }, (_, t) =>
    [run.mode === 'chat' ? '回覆' : '預測', '狀態', '耗時（秒）', '錯誤／原因', ...(run.mode === 'law' ? ['比對結果', '命中法規'] : [])]
      .map(label => `測次 ${r + 1} 第 ${t + 1} 輪 ${label}`)).flat()).flat();
}
export function exportBatchWorkbook(run: ScriptRun, workbook: ScenarioWorkbook, sheet: ScenarioSheet, headerRow: number, afterColumn: number): Uint8Array {
  const rounds = Math.max(...run.cases.map(test => test.turns.length));
  const recordMap = new Map(run.records.map(record => [`${record.caseId}:${record.repetition}`, record]));
  const scores = Array.from({ length: rounds }, (_, i) => new Map(lawRows(run, i + 1).map((row, j) => [`${run.records[j].caseId}:${run.records[j].repetition}`, evaluateLawRow(row)])));
  const rows = run.cases.map(test => ({ row: test.source.row, values: Array.from({ length: run.repetitions }, (_, i) => {
    const key = `${test.id}:${i + 1}`, record = recordMap.get(key);
    return Array.from({ length: rounds }, (_, t) => {
      const turn = record?.turns.filter(turn => turn.number === t + 1).at(-1);
      const evaluation = run.mode === 'law' ? scores[t].get(key) : null;
      return [turn?.result?.response.reply ?? turn?.prediction?.predicted_laws.join('、') ?? '',
        (turn?.prediction ? BATCH_STATUS_LABELS[turn.prediction.decision_status] : turn ? BEHAVIOR_STATUS[turn.status] : undefined) ?? (t >= test.turns.length ? '無此輪次' : record?.status === 'error' || record?.status === 'interrupted' ? '前輪失敗，未執行' : '待執行'),
        turn?.elapsedMs == null ? null : Math.round(turn.elapsedMs / 10) / 100,
        turn?.error ?? turn?.prediction?.decision_reason ?? '', ...(evaluation ? [evaluation.result, evaluation.matches.join('、')] : [])];
    }).flat();
  }).flat() }));
  const summary: (string | number | null)[][] = [['執行 ID', run.runId], ['模式', run.mode], ['模型', run.overrides.openrouter_model ?? ''], ['契約指紋', run.contractSha256], ['並行案例', run.template.parallelism]];
  if (run.mode === 'law') {
    const stats = scoreLawBatch(lawRows(run));
    summary.push(['統計範圍', '每個案例測次的最後一輪'], ['比對規則', LAW_MATCH_RULE], ...stats.metrics.map(metric => [metric.label, formatBatchRate(metric), metric.numerator, metric.denominator]));
  }
  for (const record of run.records) summary.push([record.caseId, record.repetition, record.status, record.evidenceSha256, record.review ? JSON.stringify(record.review) : '未覆核']);
  const settings = JSON.stringify({ template: run.template, overrides: run.overrides });
  for (let i = 0; i < settings.length; i += 30000) summary.push([`設定 ${i / 30000 + 1}`, settings.slice(i, i + 30000)]);
  return insertScenarioResults(workbook, sheet, headerRow, afterColumn, resultHeaders(run), rows, summary);
}
export { asBehaviorRun };
