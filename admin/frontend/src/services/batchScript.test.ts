import { webcrypto } from 'node:crypto';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { strToU8, strFromU8 } from 'fflate';
import { batchConfig, tabularFixtureWorkbook } from '../test/scenarioFixture';
import { defaultTemplate, importBatchTemplate, materializeCases, prepareScriptRun, scriptInputEnd } from './batchScript';
import { parseScenarioWorkbook, insertScenarioResults } from './scenarioWorkbook';
import { exportBatchWorkbook, exportScriptRun, importScriptRun } from './batchRecords';
import { refreshBatchEvidence } from './batchRunner';
import { behaviorRun } from '../test/behaviorFixture';
import { exportBehaviorJson, refreshBehaviorEvidence } from './behaviorRecords';

beforeEach(() => vi.stubGlobal('crypto', webcrypto));
afterEach(() => vi.unstubAllGlobals());
function fixture(rows = [['訊息', '訊息', '答案', '備註'], ['第一輪', '補充資料', '性工法', '保留內容']]) {
  const workbook = parseScenarioWorkbook(tabularFixtureWorkbook(rows), 'synthetic.xlsx');
  const sheet = workbook.sheets[0];
  const template = { ...defaultTemplate(), rounds: [{ columns: [{ index: 0, header: '訊息' }, { index: 1, header: '訊息' }], text: '請整理' }, { columns: [], text: '請再說明' }], metadata: { reference: { index: 2, header: '答案' } } };
  return { workbook, sheet, template };
}
describe('declarative batch scripts', () => {
  it('uses ordered multiple columns, fixed text, optional metadata and automatic source IDs', () => {
    const { workbook, sheet, template } = fixture();
    const cases = materializeCases(workbook, sheet, 1, template);
    expect(cases[0]).toMatchObject({ id: '合成測試:2', category: '', expected: '', criticalFailure: '', reference: '性工法', turns: ['第一輪\n補充資料\n請整理', '請再說明'] });
    expect(scriptInputEnd(template)).toBe(1);
    template.rounds.reverse();
    expect(materializeCases(workbook, sheet, 1, template)[0].turns[0]).toBe('請再說明');
  });
  it('does not skip a case when a referenced cell is empty', () => {
    const { workbook, sheet, template } = fixture([['訊息', '訊息', '答案', '備註'], ['第一輪', '', '性工法', '有資料']]);
    expect(() => materializeCases(workbook, sheet, 1, template)).toThrow('合成測試 第 2 列、第 1 輪：第 2 欄為空白');
    template.rounds = [{ columns: [], text: '固定文字' }];
    expect(materializeCases(workbook, sheet, 1, template)[0].turns).toEqual(['固定文字']);
  });
  it('matches duplicate headers by position, rejects changed headers and strips non-template data', () => {
    const { workbook, sheet, template } = fixture();
    const imported = importBatchTemplate(JSON.stringify({ ...template, csrf_token: 'secret', approved: true, cases: ['private'] }));
    expect(imported).toEqual(template);
    expect(materializeCases(workbook, sheet, 1, imported)[0].turns[0]).toContain('補充資料');
    imported.rounds[0].columns[1].header = '不同欄';
    expect(() => materializeCases(workbook, sheet, 1, imported)).toThrow('標題不符');
  });
  it('round-trips selected built-in presets without replacing their scripts with placeholder text', () => {
    const template = { ...defaultTemplate(), mode: 'chat' as const, builtinCaseIds: ['C01'], rounds: [{ columns: [], text: '沿用內建案例' }] };
    expect(importBatchTemplate(JSON.stringify(template)).builtinCaseIds).toEqual(['C01']);
    expect(() => importBatchTemplate(JSON.stringify({ ...template, builtinCaseIds: ['unknown'] }))).toThrow();
  });
  it('checks duplicate IDs, input lengths and per-mode count limits', () => {
    const { workbook, sheet, template } = fixture([['訊息', '訊息', '答案', '備註'], ['重複', '補充', '', ''], ['重複', '補充', '', '']]);
    template.metadata = { ...template.metadata, ...{ id: { index: 0, header: '訊息' } } };
    expect(() => materializeCases(workbook, sheet, 1, template)).toThrow('不可重複');
    sheet.rows.get(2)!.set(0, 'a'.repeat(2001));
    expect(() => materializeCases(workbook, sheet, 1, template)).toThrow('2,000');
    const large = fixture([['訊息', '訊息', '答案', '備註'], ...Array.from({ length: 101 }, () => ['內容', '補充', '', ''])]);
    expect(() => materializeCases(large.workbook, large.sheet, 1, { ...large.template, mode: 'chat' })).toThrow('100');
  });
});

describe('inserted workbook results', () => {
  it('shifts right-side metadata and styles, preserves other ZIP parts and leaves the original untouched', () => {
    const { workbook, sheet } = fixture();
    const original = workbook.entries[sheet.path];
    workbook.entries[sheet.path] = strToU8(strFromU8(original).replace('<sheetData>', '<cols><col min="1" max="4" width="19" style="2"/></cols><sheetData>').replace('r="D2"', 'r="D2" s="3"'));
    const bytes = insertScenarioResults(workbook, sheet, 1, 1, ['回覆', '状态'], [{ row: 2, values: ['=literal <text>', '完成'] }]);
    const output = parseScenarioWorkbook(bytes, 'copy.xlsx');
    expect([...output.sheets[0].rows.get(2)!.values()]).toEqual(['第一輪', '補充資料', '=literal <text>', '完成', '性工法', '保留內容']);
    expect(output.entries[workbook.sheets[1].path]).toEqual(workbook.entries[workbook.sheets[1].path]);
    const doc = new DOMParser().parseFromString(strFromU8(output.entries[sheet.path]), 'application/xml');
    expect(doc.querySelector('c[r="F2"]')?.getAttribute('s')).toBe('3');
    expect(doc.querySelector('c[r="C2"] f')).toBeNull();
    expect(doc.querySelector('col[min="5"]')?.getAttribute('style')).toBe('2');
    expect(sheet.rows.get(2)?.get(3)).toBe('保留內容');
  });
  it.each(['<mergeCells><mergeCell ref="A1:B1"/></mergeCells>', '<tableParts count="1"/>', '<drawing/>', '<conditionalFormatting/>', '<sheetProtection/>'])(
    'blocks unsupported structures before generating results: %s', structure => {
      const { workbook, sheet } = fixture();
      workbook.entries[sheet.path] = strToU8(strFromU8(workbook.entries[sheet.path]).replace('</worksheet>', `${structure}</worksheet>`));
      expect(() => insertScenarioResults(workbook, sheet, 1, 1, ['結果'], [])).toThrow('純值測資');
    });
  it('also blocks formulas on another sheet whose references could be invalidated', () => {
    const { workbook, sheet } = fixture();
    workbook.entries[workbook.sheets[1].path] = strToU8('<worksheet><sheetData><row r="1"><c r="A1"><f>Sheet1!D2</f><v>1</v></c></row></sheetData></worksheet>');
    expect(() => insertScenarioResults(workbook, sheet, 1, 1, ['結果'], [])).toThrow('純值測資');
  });
  it('uses source rows and stable repetition/turn columns even if record completion order differs', async () => {
    const { workbook, sheet, template } = fixture();
    const run = prepareScriptRun(materializeCases(workbook, sheet, 1, template), { ...template, repetitions: 3 }, batchConfig);
    run.records.reverse();
    const output = parseScenarioWorkbook(exportBatchWorkbook(run, workbook, sheet, 1, 1), 'copy.xlsx');
    expect(output.sheets[0].rows.get(1)?.get(2)).toBe('測次 1 第 1 輪 預測');
    expect(output.sheets[0].rows.get(1)?.get(8)).toBe('測次 1 第 2 輪 預測');
    expect(output.sheets[0].rows.get(1)?.get(26)).toBe('測次 3 第 1 輪 預測');
    expect(output.sheets[0].rows.get(2)?.get(38)).toBe('性工法');
    const restored = await importScriptRun(exportScriptRun(await refreshBatchEvidence(run)));
    expect(restored.records).toHaveLength(3);
    expect(restored.cases[0].reference).toBe('性工法');
  });
  it('converts legacy behavior JSON without conferring send approval or losing reviews', async () => {
    const legacy = await refreshBehaviorEvidence(behaviorRun());
    const result = await importScriptRun(exportBehaviorJson(legacy));
    expect(result.format).toBe('batch-run-v1');
    expect(result.mode).toBe('chat');
    expect(result.records).toEqual(legacy.records);
  });
});
