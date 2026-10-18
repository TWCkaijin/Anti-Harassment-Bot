import { createPrivacyDraft } from './clientPrivacy';
import { reproducibleOverrides } from './adminDiagnostics';
import type { RuntimeConfig, ApplicableLawPrediction } from './api';
import { builtinBehaviorCases } from './behaviorTests';
import type { BehaviorCase, BehaviorRecord, BehaviorRun, BehaviorTurn } from './behaviorTests';
import type { ScenarioSheet, ScenarioWorkbook } from './scenarioWorkbook';

export type BatchMode = 'chat' | 'law';
export type Parallelism = 1 | 2 | 4 | 8;
export interface ColumnBinding { index: number; header: string }
export interface ScriptRound { columns: ColumnBinding[]; text: string }
export type MetadataKey = 'id' | 'category' | 'reference' | 'expected' | 'criticalFailure';
export interface BatchTemplate {
  builtinCaseIds?: string[];
  format: 'batch-template-v1'; mode: BatchMode; rounds: ScriptRound[];
  metadata: Partial<Record<MetadataKey, ColumnBinding>>; parallelism: Parallelism; repetitions: 1 | 3;
}
export interface ScriptCase extends BehaviorCase { reference?: string }
export interface BatchTurn extends BehaviorTurn { prediction?: ApplicableLawPrediction }
export interface BatchRecord extends Omit<BehaviorRecord, 'turns'> { turns: BatchTurn[] }
export interface ScriptRun extends Omit<BehaviorRun, 'format' | 'records' | 'cases'> {
  format: 'batch-run-v1'; mode: BatchMode; template: BatchTemplate; cases: ScriptCase[]; records: BatchRecord[];
}
export const defaultTemplate = (): BatchTemplate => ({ format: 'batch-template-v1', mode: 'law', rounds: [{ columns: [], text: '' }], metadata: {}, parallelism: 1, repetitions: 1 });
export const bindColumn = (sheet: ScenarioSheet, headerRow: number, index: number): ColumnBinding => ({ index, header: sheet.rows.get(headerRow)?.get(index)?.trim() ?? '' });

/** Column indices disambiguate duplicate headings; headings guard against stale templates. */
export function validateTemplate(template: BatchTemplate, sheet?: ScenarioSheet, headerRow = 1): void {
  if (!template || template.format !== 'batch-template-v1' || !['chat', 'law'].includes(template.mode)
    || ![1, 2, 4, 8].includes(template.parallelism) || ![1, 3].includes(template.repetitions)
    || !Array.isArray(template.rounds) || !template.rounds.length || template.rounds.length > 10
    || !template.metadata || typeof template.metadata !== 'object' || Array.isArray(template.metadata)) throw new Error('腳本範本格式不正確。');
  if (template.builtinCaseIds !== undefined && (template.mode !== 'chat' || !Array.isArray(template.builtinCaseIds)
    || !template.builtinCaseIds.length || template.builtinCaseIds.length > 5 || new Set(template.builtinCaseIds).size !== template.builtinCaseIds.length
    || template.builtinCaseIds.some(id => !builtinBehaviorCases().some(test => test.id === id)))) throw new Error('內建案例選項不正確。');
  const check = (binding: ColumnBinding) => {
    if (!binding || !Number.isSafeInteger(binding.index) || binding.index < 0 || binding.index >= 16384 || typeof binding.header !== 'string' || binding.header.length > 32767) throw new Error('範本欄位格式不正確。');
    if (sheet && (binding.index > sheet.maxColumn || (sheet.rows.get(headerRow)?.get(binding.index)?.trim() ?? '') !== binding.header)) throw new Error(`範本的第 ${binding.index + 1} 欄標題不符，請重新選擇欄位。`);
  };
  for (const round of template.rounds) {
    if (!round || !Array.isArray(round.columns) || round.columns.length > 100 || typeof round.text !== 'string' || round.text.length > 2000 || (!round.columns.length && !round.text.trim())) throw new Error('每輪至少選擇一個欄位或填入固定文字，最多 2,000 字。');
    if (new Set(round.columns.map(item => item.index)).size !== round.columns.length) throw new Error('同一輪不可重複引用相同欄位。');
    round.columns.forEach(check);
  }
  for (const [key, value] of Object.entries(template.metadata)) {
    if (!['id', 'category', 'reference', 'expected', 'criticalFailure'].includes(key)) throw new Error('範本含不支援的欄位。');
    check(value);
  }
}
export function importBatchTemplate(raw: string): BatchTemplate {
  if (raw.length > 1024 * 1024) throw new Error('範本不可超過 1 MB。');
  const value = JSON.parse(raw) as BatchTemplate;
  validateTemplate(value);
  // Only the declarative configuration crosses this boundary. No credentials or send approvals.
  const copy = (binding: ColumnBinding): ColumnBinding => ({ index: binding.index, header: binding.header });
  return { ...(value.builtinCaseIds ? { builtinCaseIds: [...value.builtinCaseIds] } : {}), format: 'batch-template-v1', mode: value.mode, parallelism: value.parallelism, repetitions: value.repetitions,
    rounds: value.rounds.map(round => ({ text: round.text, columns: round.columns.map(copy) })),
    metadata: Object.fromEntries(Object.entries(value.metadata).map(([key, binding]) => [key, copy(binding)])) };
}
export function scriptInputEnd(template: BatchTemplate): number {
  return Math.max(-1, ...template.rounds.flatMap(round => round.columns.map(column => column.index)));
}
export function materializeCases(workbook: ScenarioWorkbook, sheet: ScenarioSheet, headerRow: number, template: BatchTemplate, hiddenTerms: string[] = []): ScriptCase[] {
  validateTemplate(template, sheet, headerRow);
  if (!Number.isSafeInteger(headerRow) || headerRow < 1 || headerRow > sheet.maxRow) throw new Error('請指定有效的標題列。');
  const rows = [...sheet.rows].filter(([row, cells]) => row > headerRow && [...cells.values()].some(value => value.trim())).sort(([a], [b]) => a - b);
  const maximum = template.mode === 'chat' ? 100 : 5000;
  if (!rows.length || rows.length > maximum) throw new Error(`此模式每批需有 1–${maximum.toLocaleString()} 個案例。`);
  const cases = rows.map(([row, cells]): ScriptCase => {
    const metadata = (key: MetadataKey) => template.metadata[key] ? cells.get(template.metadata[key]!.index)?.trim() ?? '' : '';
    const turns = template.rounds.map((round, index) => {
      const values = round.columns.map(binding => {
        const value = cells.get(binding.index)?.trim();
        if (!value) throw new Error(`${sheet.name} 第 ${row} 列、第 ${index + 1} 輪：第 ${binding.index + 1} 欄為空白。`);
        return value;
      });
      const message = [...values, round.text.trim()].filter(Boolean).join('\n');
      if (message.length > 2000) throw new Error(`${sheet.name} 第 ${row} 列、第 ${index + 1} 輪超過 2,000 字。`);
      const masked = createPrivacyDraft({ message, history: [], use_rag: true, contract_version: 4 }, hiddenTerms).request.message;
      if (masked.length > 2000) throw new Error(`${sheet.name} 第 ${row} 列、第 ${index + 1} 輪遮罩後超過 2,000 字。`);
      return masked;
    });
    const id = metadata('id') || `${sheet.name}:${row}`;
    if (id.length > 80 || metadata('category').length > 100 || [metadata('expected'), metadata('criticalFailure')].some(text => text.length > 4000)) throw new Error(`第 ${row} 列中繼欄位超過長度限制。`);
    return { id, category: metadata('category'), expected: metadata('expected'), criticalFailure: metadata('criticalFailure'),
      reference: template.metadata.reference ? metadata('reference') : undefined, originalPrompt: turns[0], turns, confirmed: true,
      source: { file: workbook.fileName, sheet: sheet.name, row } };
  });
  if (new Set(cases.map(test => test.id)).size !== cases.length) throw new Error('案例 ID 不可重複。');
  return cases;
}
export function prepareScriptRun(cases: ScriptCase[], template: BatchTemplate, config: RuntimeConfig): ScriptRun {
  validateTemplate(template);
  if (!cases.length || cases.length > (template.mode === 'chat' ? 100 : 5000)) throw new Error('案例數超過此模式限制。');
  const overrides = reproducibleOverrides(config);
  const contractSha256 = template.mode === 'chat' ? config.chat_test_contract_sha256 : config.applicable_laws_contract_sha256;
  if (!overrides || !/^[a-f0-9]{64}$/.test(contractSha256 ?? '')) throw new Error('後端未提供完整設定與契約，請重新登入。');
  return { format: 'batch-run-v1', mode: template.mode, template: structuredClone(template), cases: structuredClone(cases),
    runId: crypto.randomUUID(), createdAt: new Date().toISOString(), scriptVersion: '1', repetitions: template.repetitions,
    overrides, contractSha256: contractSha256!, environment: config.environment_document_id ?? 'local fallback', corpusVersion: 'unknown',
    records: cases.flatMap(test => Array.from({ length: template.repetitions }, (_, index) => ({ caseId: test.id, repetition: index + 1, status: 'pending', turns: [], evidenceSha256: '' }))) };
}
export function asBehaviorRun(run: ScriptRun): BehaviorRun {
  return { format: 'behavior-tests-v1', runId: run.runId, createdAt: run.createdAt, scriptVersion: '1', cases: run.cases,
    repetitions: run.repetitions, overrides: run.overrides, contractSha256: run.contractSha256, environment: run.environment, corpusVersion: 'unknown', records: run.records };
}
export function fromBehaviorRun(run: BehaviorRun): ScriptRun {
  return { ...run, format: 'batch-run-v1', mode: 'chat', template: { ...defaultTemplate(), mode: 'chat', repetitions: run.repetitions, rounds: [{ columns: [], text: '沿用匯入紀錄的逐案腳本' }] } };
}
