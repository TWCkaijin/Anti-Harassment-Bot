import { webcrypto } from 'node:crypto';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { batchConfig, fixtureWorkbook, tabularFixtureWorkbook } from '../test/scenarioFixture';
import { behaviorConfig, behaviorResult } from '../test/behaviorFixture';
import { getRuntimeConfig, runAdminApplicableLawTest, runAdminChatTest } from '../services/api';
import { reviewedRequestBody } from '../services/clientPrivacy';
import { downloadLocalFile, parseScenarioWorkbook } from '../services/scenarioWorkbook';
import AdminBatchPrediction from './AdminBatchPrediction';

vi.mock('../services/api', async () => ({ ...await vi.importActual('../services/api'), getRuntimeConfig: vi.fn(), runAdminApplicableLawTest: vi.fn(), runAdminChatTest: vi.fn() }));
vi.mock('../services/scenarioWorkbook', async () => ({ ...await vi.importActual('../services/scenarioWorkbook'), downloadLocalFile: vi.fn() }));
vi.mock('../services/batchRunner', async importOriginal => {
  const actual = await importOriginal<typeof import('../services/batchRunner')>();
  return { ...actual, runScriptBatch: (options: Parameters<typeof actual.runScriptBatch>[0]) => actual.runScriptBatch({ ...options, delayMs: 0 }) };
});
const lawResult = { output_contract: 'applicable_laws_v1' as const, prediction: { predicted_laws: ['性工法' as const], decision_status: 'predicted' as const, decision_reason: null }, diagnostics: {} };
async function upload(bytes = fixtureWorkbook()) {
  const file = new File([new Uint8Array(bytes)], 'synthetic.xlsx');
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array(bytes).buffer });
  fireEvent.change(screen.getByLabelText('Excel 檔案'), { target: { files: [file] } });
  await screen.findByLabelText('工作表');
}
async function prepare() {
  fireEvent.click(screen.getByRole('button', { name: '準備預測' }));
  await screen.findByRole('region', { name: '送出預覽' });
}
async function start() {
  fireEvent.click(screen.getByRole('checkbox', { name: /已確認本次設定/ }));
  fireEvent.click(screen.getByRole('button', { name: '確認送出並開始預測' }));
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
  vi.mocked(getRuntimeConfig).mockReset().mockResolvedValue(behaviorConfig);
  vi.mocked(runAdminApplicableLawTest).mockReset().mockResolvedValue(lawResult);
  vi.mocked(runAdminChatTest).mockReset().mockImplementation(async (_token, request) => behaviorResult(request));
  vi.mocked(downloadLocalFile).mockClear();
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('unified batch workflow', () => {
  it('previews masked messages, locks mappings and requires explicit confirmation before paid requests', async () => {
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload();
    expect(screen.getByRole('button', { name: '移除第 1 輪欄位 C' })).toBeEnabled();
    await prepare();
    expect(screen.getByText('第 1 輪：合成聯絡人 [電子郵件]')).toBeInTheDocument();
    expect(runAdminApplicableLawTest).not.toHaveBeenCalled();
    expect(screen.queryByText('gold-never-send')).not.toBeInTheDocument();
    expect(screen.getByLabelText('工作表')).toBeDisabled();
    expect(screen.getByLabelText('並行案例')).toBeDisabled();
    expect(screen.getByRole('button', { name: '確認送出並開始預測' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '下載送出預覽' }));
    expect(downloadLocalFile).toHaveBeenCalledWith(expect.not.stringContaining('test@example.com'), 'batch-preview.json', 'application/json');
    fireEvent.click(screen.getByRole('button', { name: '下載結果 Excel' }));
    const [bytes] = vi.mocked(downloadLocalFile).mock.calls.at(-1)!;
    expect(parseScenarioWorkbook(bytes as Uint8Array, 'copy.xlsx').sheets[0].rows.get(2)?.get(4)).toBe('待執行');
  });
  it('selects another sheet and sends only its input to the authenticated job API', async () => {
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload();
    fireEvent.change(screen.getByLabelText('工作表'), { target: { value: '1' } });
    await prepare(); await start();
    await screen.findByText(/完成 1 \/ 1 · 失敗 0 · 待執行 0/);
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1);
    const args = vi.mocked(runAdminApplicableLawTest).mock.calls[0];
    expect(args[0]).toBe('test');
    expect(args[1]).toMatchObject({ message: '另一工作表情境', history: [], contract_version: 4 });
    expect(reviewedRequestBody(args[1])).toContain('另一工作表情境');
    expect(args[4]).toBe(batchConfig.applicable_laws_contract_sha256);
  });
  it('edits multiple columns and fixed rounds, then runs isolated multi-round chat cases', async () => {
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('測試模式'), { target: { value: 'chat' } });
    await upload(tabularFixtureWorkbook([['情境', '補充', '參考答案'], ['alpha', 'extra', 'gold'], ['beta', 'more', 'gold']]));
    fireEvent.change(screen.getByLabelText('第 1 輪加入來源欄位'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('第 1 輪固定文字'), { target: { value: '請整理' } });
    fireEvent.click(screen.getByRole('button', { name: '新增一輪' }));
    fireEvent.change(screen.getByLabelText('第 2 輪固定文字'), { target: { value: '請補充' } });
    fireEvent.change(screen.getByLabelText('並行案例'), { target: { value: '2' } });
    await prepare(); await start();
    await screen.findByText(/完成 2 \/ 2 · 失敗 0 · 待執行 0/);
    expect(runAdminChatTest).toHaveBeenCalledTimes(4);
    const requests = vi.mocked(runAdminChatTest).mock.calls.map(call => call[1]);
    expect(requests.filter(request => request.history.length === 0).map(request => request.message)).toEqual(['alpha\nextra\n請整理', 'beta\nmore\n請整理']);
    expect(requests.filter(request => request.history.length === 2)).toHaveLength(2);
    expect(JSON.stringify(requests)).not.toContain('gold');
    expect(runAdminApplicableLawTest).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '下載結果 Excel' }));
    const copy = parseScenarioWorkbook(vi.mocked(downloadLocalFile).mock.calls.at(-1)![0] as Uint8Array, 'copy.xlsx');
    expect(copy.sheets[0].rows.get(2)?.get(2)).toContain('合成回覆');
    expect(copy.sheets[0].rows.get(2)?.get(10)).toBe('gold');
  });
  it('blocks blank round cells and unsupported workbook structures before model calls', async () => {
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload(tabularFixtureWorkbook([['情境', '補充'], ['合成案例', '']]));
    fireEvent.click(screen.getByRole('button', { name: '新增一輪' }));
    fireEvent.change(screen.getByLabelText('第 2 輪固定文字'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('第 2 輪加入來源欄位'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: '準備預測' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('第 2 列、第 2 輪');
    expect(getRuntimeConfig).not.toHaveBeenCalled();
    expect(runAdminApplicableLawTest).not.toHaveBeenCalled();
  });
  it('pauses after active requests and exports complete plus pending rows', async () => {
    let resolve!: (result: Awaited<ReturnType<typeof runAdminApplicableLawTest>>) => void;
    vi.mocked(runAdminApplicableLawTest).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload(); await prepare(); await start();
    await waitFor(() => expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '完成本輪後暫停' }));
    resolve({ output_contract: 'applicable_laws_v1', prediction: { predicted_laws: [], decision_status: 'no_target_law', decision_reason: '合成原因' }, diagnostics: {} });
    await screen.findByRole('button', { name: '確認送出並繼續未完成預測' });
    expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '下載結果 Excel' }));
    const copy = parseScenarioWorkbook(vi.mocked(downloadLocalFile).mock.calls.at(-1)![0] as Uint8Array, 'copy.xlsx');
    expect(copy.sheets[0].rows.get(2)?.get(4)).toBe('不適用三法');
    expect(copy.sheets[0].rows.get(2)?.get(6)).toBe('合成原因');
    expect(copy.sheets[0].rows.get(5)?.get(4)).toBe('待執行');
  });
  it('aborts active work on unmount', async () => {
    vi.mocked(runAdminApplicableLawTest).mockImplementationOnce((_token, _request, _config, signal) => new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('aborted')))));
    const view = render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload(); await prepare(); await start();
    await waitFor(() => expect(runAdminApplicableLawTest).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(runAdminApplicableLawTest).mock.calls[0][3];
    view.unmount(); expect(signal?.aborted).toBe(true);
  });
  it('scores references locally and keeps exported numerator and denominator consistent', async () => {
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    await upload(tabularFixtureWorkbook([['情境', '參考答案', '備註'], ['合成測試', '性平法、性工法', '原備註']]));
    expect(screen.getByLabelText('參考答案欄位')).toHaveValue('1');
    await prepare(); await start();
    await screen.findByText(/完成 1 \/ 1 · 失敗 0 · 待執行 0/);
    expect(JSON.stringify(vi.mocked(runAdminApplicableLawTest).mock.calls[0][1])).not.toContain('性平法');
    fireEvent.click(screen.getByRole('button', { name: '下載結果 Excel' }));
    const copy = parseScenarioWorkbook(vi.mocked(downloadLocalFile).mock.calls.at(-1)![0] as Uint8Array, 'copy.xlsx');
    expect(copy.sheets[0].rows.get(2)?.get(5)).toBe('正確');
    expect(copy.sheets[0].rows.get(2)?.get(7)).toBe('性平法、性工法');
    expect(copy.sheets[0].rows.get(2)?.get(8)).toBe('原備註');
    const total = [...copy.sheets.find(sheet => sheet.name === '預測統計')!.rows.values()].find(cells => cells.get(0) === '總正確率')!;
    expect([total.get(1), total.get(2), total.get(3)]).toEqual(['100.00%', '1', '1']);
  });
  it('exports and restores a selected built-in template while retaining its complete conversation', async () => {
    const view = render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('測試模式'), { target: { value: 'chat' } });
    fireEvent.click(screen.getByText('內建行為案例'));
    fireEvent.click(screen.getByRole('button', { name: '載入 Context' }));
    fireEvent.click(screen.getByRole('button', { name: '下載腳本範本' }));
    const raw = vi.mocked(downloadLocalFile).mock.calls[0][0] as string;
    view.unmount();
    render(<AdminBatchPrediction csrfToken="test" onHasDataChange={vi.fn()} />);
    const file = new File([raw], 'preset.json'); Object.defineProperty(file, 'text', { value: async () => raw });
    fireEvent.change(screen.getByLabelText('匯入腳本範本 JSON'), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole('button', { name: '準備預測' })).toBeEnabled());
    await prepare();
    const preview = screen.getByRole('region', { name: '送出預覽' });
    expect(within(preview).getByText(/第 3 輪：請依更正後/)).toBeInTheDocument();
    expect(runAdminChatTest).not.toHaveBeenCalled();
  });
});
