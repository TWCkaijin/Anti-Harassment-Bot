import { afterEach, expect, it, vi } from 'vitest';
import { latestModelRevision, MODEL_VERSION_ENDPOINT } from './modelVersion';
import { isModelAssetUrl, modelUrl, MODEL_URL } from './config';
afterEach(() => vi.unstubAllGlobals());
it('resolves a fresh immutable revision without credentials or user content', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ sha: 'a'.repeat(40) }) });
  vi.stubGlobal('fetch', fetcher);
  const signal = new AbortController().signal;
  expect(await latestModelRevision(signal)).toBe('a'.repeat(40));
  expect(fetcher).toHaveBeenCalledExactlyOnceWith(MODEL_VERSION_ENDPOINT, { signal, cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer' });
});
it('rejects failed checks and mutable or injected revisions', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: false }); vi.stubGlobal('fetch', fetcher);
  await expect(latestModelRevision(new AbortController().signal)).rejects.toMatchObject({ code: 'model_version_check_failed' });
  fetcher.mockResolvedValue({ ok: true, json: async () => ({ sha: 'main/../../other' }) });
  await expect(latestModelRevision(new AbortController().signal)).rejects.toMatchObject({ code: 'invalid_model_revision' });
});
it('limits downloads to the selected revision and existing artifact filenames', () => {
  const revision = 'a'.repeat(40), url = modelUrl(revision);
  expect(isModelAssetUrl(url + 'params_shard_0.bin', revision)).toBe(true);
  expect(isModelAssetUrl(MODEL_URL + 'params_shard_0.bin', revision)).toBe(false);
  expect(isModelAssetUrl(url + 'params_shard_0.bin?text=private', revision)).toBe(false);
  expect(isModelAssetUrl(url + 'other.js', revision)).toBe(false);
  expect(() => modelUrl('main')).toThrow();
});
