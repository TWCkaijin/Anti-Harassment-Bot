import { BASIC_CHECKS } from "./selfCheck";
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ValidationPage from './ValidationPage';
import { BrowserMaskingRuntime } from './runtime';
import { evaluateSplit, type CaseResult } from './benchmark';

const mock = vi.hoisted(() => ({ load: vi.fn(), detect: vi.fn(), dispose: vi.fn() }));
vi.mock('./runtime', () => ({ BrowserMaskingRuntime: vi.fn(class { load = mock.load; detect = mock.detect; dispose = mock.dispose; }) }));
vi.mock('./benchmark', async () => ({ ...await vi.importActual('./benchmark'), evaluateSplit: vi.fn() }));
const result: CaseResult = { id: 'synthetic-1', split: 'development', category: 'synthetic', characters: 10, source: '合成原文', expected: [], detected: [], masked: '合成原文', missing: [], unexpected: [], elapsedMs: 1, error: null };
beforeEach(() => {
  vi.mocked(BrowserMaskingRuntime).mockClear(); mock.dispose.mockReset(); mock.detect.mockReset().mockImplementation(async (source: string) => ({ entities: BASIC_CHECKS.find(item => item.source === source)?.expected ?? [] }));
  mock.load.mockReset().mockResolvedValue({ elapsedMs: 5, fetchedBytes: 0 });
  vi.mocked(evaluateSplit).mockReset();
});
afterEach(() => vi.restoreAllMocks());

it('does not call an all-empty model ready and exposes the actual basic-check outputs', async () => {
  mock.detect.mockResolvedValue({ entities: [] });
  render(<ValidationPage />);
  fireEvent.click(screen.getByRole('button', { name: '載入本機模型' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('basic_extraction_failed');
  expect(screen.getByRole('button', { name: '執行開發集 80 則' })).toBeDisabled();
  expect(screen.getByLabelText('基本辨識檢查')).toHaveTextContent('命中 0 / 3');
  expect(window.maskingValidationReport?.basicCheck?.passed).toBe(false);
  expect(window.maskingValidationReport?.scores.all.cases).toBe(0);
});

it('allows imperfect extraction once a real source span is found without inflating benchmark scores', async () => {
  mock.detect.mockImplementation(async (source: string) => ({ entities: source === BASIC_CHECKS[0].source ? [{ text: '許映辰', type: 'person' }] : [] }));
  render(<ValidationPage />);
  fireEvent.click(screen.getByRole('button', { name: '載入本機模型' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '執行開發集 80 則' })).toBeEnabled());
  expect(screen.getByLabelText('基本辨識檢查')).toHaveTextContent('命中 1 / 3');
  expect(screen.getByLabelText('基本辨識檢查')).toHaveTextContent('[人物1]');
  expect(window.maskingValidationReport?.scores.all.cases).toBe(0);
  expect(window.maskingValidationReport?.gatePassed).toBe(false);
});

it('does not enable evaluation or keep probing after a basic check throws', async () => {
  mock.detect.mockRejectedValueOnce(new Error('model failure'));
  render(<ValidationPage />);
  fireEvent.click(screen.getByRole('button', { name: '載入本機模型' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('model_execution_failed');
  expect(screen.getByRole('button', { name: '執行開發集 80 則' })).toBeDisabled();
  expect(mock.detect).toHaveBeenCalledOnce();
});

it('stops basic checks without starting the next model request after cancellation', async () => {
  let complete!: (value: { entities: typeof BASIC_CHECKS[0]['expected'] }) => void;
  mock.detect.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  render(<ValidationPage />);
  fireEvent.click(screen.getByRole('button', { name: '載入本機模型' }));
  await waitFor(() => expect(mock.detect).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: '停止' }));
  await act(async () => complete({ entities: BASIC_CHECKS[0].expected }));
  expect(mock.detect).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: '執行開發集 80 則' })).toBeDisabled();
  expect(window.maskingValidationReport?.basicCheck).toBeNull();
});

it('loads only on explicit action, stops on session lock and retains the report for reentry', async () => {
  let complete!: () => void; let signal!: AbortSignal;
  vi.mocked(evaluateSplit).mockImplementation(async (_runtime, _split, abort, onCase) => {
    signal = abort; onCase(result); await new Promise<void>(resolve => { complete = resolve; }); return [result];
  });
  const view = render(<ValidationPage active />);
  expect(BrowserMaskingRuntime).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '載入本機模型' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '執行開發集 80 則' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '執行開發集 80 則' }));
  await screen.findByText(/synthetic-1 ·/);
  view.rerender(<ValidationPage active={false} />);
  expect(signal.aborted).toBe(true); expect(mock.dispose).toHaveBeenCalled();
  await act(async () => complete());
  view.rerender(<ValidationPage active />);
  expect(screen.getByRole('button', { name: '執行開發集 80 則' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '重新載入並量測快取' })).toBeEnabled();
  expect(window.maskingValidationReport?.cases).toHaveLength(1);
  expect(BrowserMaskingRuntime).toHaveBeenCalledTimes(1);
  view.unmount(); expect(window.maskingValidationReport).toBeUndefined();
});
it('invalidates a loading model on session lock and ignores late completions', async () => {
  let loaded!: (value: { elapsedMs: number; fetchedBytes: number }) => void;
  mock.load.mockImplementationOnce(() => new Promise(resolve => { loaded = resolve; }));
  const view = render(<ValidationPage active />);
  fireEvent.click(screen.getByRole('button', { name: '載入本機模型' }));
  view.rerender(<ValidationPage active={false} />);
  await act(async () => loaded({ elapsedMs: 1, fetchedBytes: 0 }));
  view.rerender(<ValidationPage active />);
  expect(mock.detect).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '載入本機模型' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '執行開發集 80 則' })).toBeDisabled();
});
