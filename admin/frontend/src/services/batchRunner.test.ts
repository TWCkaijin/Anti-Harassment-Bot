import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, runAdminChatTest, runAdminApplicableLawTest, type ChatRequest } from './api';
import { runScriptBatch } from './batchRunner';
import { defaultTemplate, prepareScriptRun, type Parallelism, type ScriptRun } from './batchScript';
import { behaviorConfig, behaviorResult } from '../test/behaviorFixture';
import { reviewedRequestBody } from './clientPrivacy';
import { exportScriptRun, importScriptRun } from './batchRecords';

vi.mock('./api', async () => ({ ...await vi.importActual('./api'), runAdminChatTest: vi.fn(), runAdminApplicableLawTest: vi.fn() }));
const lawResult = { output_contract: 'applicable_laws_v1' as const, prediction: { predicted_laws: ['性工法' as const], decision_status: 'predicted' as const, decision_reason: null }, diagnostics: {} };
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
  vi.mocked(runAdminChatTest).mockReset().mockImplementation(async (_token, request) => behaviorResult(request));
  vi.mocked(runAdminApplicableLawTest).mockReset().mockResolvedValue(lawResult);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function makeRun(parallelism: Parallelism = 1, count = 2, mode: 'chat' | 'law' = 'chat') {
  const cases = Array.from({ length: count }, (_, i) => ({ id: `case-${i}`, category: '', expected: 'gold-never-send', criticalFailure: 'rubric-never-send', originalPrompt: `case-${i} first`, turns: [`case-${i} first`, `case-${i} second`], confirmed: true, source: { file: 'synthetic.xlsx', sheet: '測試', row: i + 2 } }));
  return prepareScriptRun(cases, { ...defaultTemplate(), mode, parallelism, rounds: [{ columns: [], text: 'first' }, { columns: [], text: 'second' }] }, behaviorConfig);
}
const options = (run = makeRun()) => ({ run, token: 'not-persisted-token', signal: new AbortController().signal, shouldPause: () => false, onUpdate: vi.fn(), delayMs: 0 });
const tick = (ms = 1) => new Promise(resolve => setTimeout(resolve, ms));

describe('shared batch scheduler', () => {
  it.each([1, 2, 4, 8] as const)('caps active cases at %s, preserves turn order and isolates histories despite out-of-order completions', async parallelism => {
    let active = 0, maximum = 0;
    const activeCases = new Set<string>(), finished: string[] = [];
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => {
      const id = request.message.split(' ')[0];
      expect(activeCases.has(id)).toBe(false);
      activeCases.add(id); active++; maximum = Math.max(maximum, active);
      if (request.message.endsWith('first')) expect(request.history).toEqual([]);
      else {
        expect(finished).toContain(`${id} first`);
        expect(request.history.map(item => item.content)).toEqual([`${id} first`, `${id} reply`]);
      }
      await tick((8 - Number(id.split('-')[1])) * 4);
      active--; activeCases.delete(id); finished.push(request.message);
      return behaviorResult(request, `${id} reply`);
    });
    const snapshots: ScriptRun[] = [];
    const result = await runScriptBatch({ ...options(makeRun(parallelism, 8)), onUpdate: run => snapshots.push(run) });
    expect(maximum).toBe(parallelism);
    expect(runAdminChatTest).toHaveBeenCalledTimes(16);
    expect(result.run.records.map(record => record.caseId)).toEqual(Array.from({ length: 8 }, (_, i) => `case-${i}`));
    expect(result.run.records.every(record => record.status === 'completed' && /^[a-f0-9]{64}$/.test(record.evidenceSha256))).toBe(true);
    expect(snapshots[0].records.filter(record => record.status === 'running')).toHaveLength(1);
  });
  it('carries accepted summaries, reapplies custom masking to replies and never sends rubrics', async () => {
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => behaviorResult(request, '回覆 PrivateUnit contact@example.com', { summary_update: { base_revision: request.case_context!.revision, summary: 'PrivateUnit 更正為主管', evidence: [request.message] } }));
    const result = await runScriptBatch({ ...options(makeRun(2, 2)), hiddenTerms: ['PrivateUnit'] });
    for (const call of vi.mocked(runAdminChatTest).mock.calls) {
      expect(reviewedRequestBody(call[1])).not.toMatch(/PrivateUnit|example\.com|gold-never-send|rubric-never-send/);
      if (call[1].message.endsWith('second')) expect(call[1].case_context?.revision).toBe(1);
    }
    expect(JSON.stringify(result.run)).not.toContain('not-persisted-token');
    const imported = await importScriptRun(exportScriptRun(result.run));
    expect(imported.records.map(record => record.evidenceSha256)).toEqual(result.run.records.map(record => record.evidenceSha256));
  });
  it('paces dispatch globally rather than pausing every worker independently', async () => {
    const times: number[] = [];
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => { times.push(performance.now()); return behaviorResult(request); });
    await runScriptBatch({ ...options(makeRun(8, 4)), delayMs: 15 });
    expect(times).toHaveLength(8);
    expect(times.at(-1)! - times[0]).toBeGreaterThanOrEqual(85);
  });
  it('pauses after in-flight turns and resumes without repeating them', async () => {
    let paused = false;
    const resolvers: (() => void)[] = [];
    vi.mocked(runAdminChatTest).mockImplementation((_token, request) => new Promise(resolve => resolvers.push(() => resolve(behaviorResult(request)))));
    const pending = runScriptBatch({ ...options(makeRun(2, 3)), shouldPause: () => paused });
    await vi.waitFor(() => expect(resolvers).toHaveLength(2));
    paused = true; resolvers.forEach(resolve => resolve());
    const result = await pending;
    expect(result.run.records.map(record => record.status)).toEqual(['paused', 'paused', 'pending']);
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => behaviorResult(request));
    const resumed = await runScriptBatch(options(result.run));
    expect(runAdminChatTest).toHaveBeenCalledTimes(6);
    expect(resumed.run.records.every(record => record.status === 'completed')).toBe(true);
  });
  it('cancels all in-flight work and requires explicit retry for those turns', async () => {
    const abort = new AbortController();
    vi.mocked(runAdminChatTest).mockImplementation((_token, _request, _config, signal) => new Promise((_resolve, reject) => signal!.addEventListener('abort', () => reject(new Error('abort')))));
    const pending = runScriptBatch({ ...options(makeRun(2, 3)), signal: abort.signal });
    await vi.waitFor(() => expect(runAdminChatTest).toHaveBeenCalledTimes(2));
    abort.abort();
    const result = await pending;
    expect(result.run.records.map(record => record.status)).toEqual(['interrupted', 'interrupted', 'pending']);
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => behaviorResult(request));
    const resumed = await runScriptBatch(options(result.run));
    expect(runAdminChatTest).toHaveBeenCalledTimes(4);
    const retried = await runScriptBatch({ ...options(resumed.run), retryFailed: true });
    expect(runAdminChatTest).toHaveBeenCalledTimes(8);
    expect(retried.run.records[0].turns.map(turn => [turn.number, turn.attempt])).toEqual([[1, 1], [1, 2], [2, 1]]);
  });
  it.each([0, 401, 403, 409, 429])('stops new dispatches on status %s and keeps other completed work', async status => {
    vi.mocked(runAdminChatTest).mockRejectedValueOnce(status ? new ApiError(status, 'private-provider-body') : new Error('network'));
    const result = await runScriptBatch(options(makeRun(8, 8)));
    expect(result.stoppedReason).toBeTruthy();
    expect(runAdminChatTest).toHaveBeenCalledTimes(1);
    expect(result.run.records.slice(1).every(record => record.status === 'pending')).toBe(true);
    expect(JSON.stringify(result.run)).not.toContain('private-provider-body');
  });
  it('blocks later turns only in the failed case and retries exactly that failed turn', async () => {
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => {
      if (request.message === 'case-0 second') throw new ApiError(500, 'private');
      return behaviorResult(request);
    });
    const result = await runScriptBatch(options(makeRun(2, 2)));
    expect(result.run.records.map(record => record.status)).toEqual(['error', 'completed']);
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => behaviorResult(request));
    const retried = await runScriptBatch({ ...options(result.run), retryFailed: true });
    expect(runAdminChatTest).toHaveBeenCalledTimes(5);
    expect(retried.run.records[0].turns.map(turn => [turn.number, turn.attempt, turn.status])).toEqual([[1, 1, 'completed'], [2, 1, 'error'], [2, 2, 'completed']]);
  });
  it('carries law predictions as real assistant history and round-trips law evidence', async () => {
    const seen: ChatRequest[] = [];
    vi.mocked(runAdminApplicableLawTest).mockImplementation(async (_token, request) => { seen.push(request); return lawResult; });
    const result = await runScriptBatch(options(makeRun(2, 2, 'law')));
    expect(runAdminChatTest).not.toHaveBeenCalled();
    expect(seen.filter(request => request.history.length).every(request => request.history[1].content === JSON.stringify(lawResult.prediction))).toBe(true);
    const imported = await importScriptRun(exportScriptRun(result.run));
    expect(imported.records).toEqual(result.run.records);
    const altered = JSON.parse(exportScriptRun(result.run));
    altered.records[0].turns[0].prediction.predicted_laws = ['invented'];
    await expect(importScriptRun(JSON.stringify(altered))).rejects.toThrow();
  });
});
