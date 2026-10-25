import { afterEach, expect, it, vi } from 'vitest';
import { MODEL_ID, modelUrl } from './config';
const mocks = vi.hoisted(() => ({ remove: vi.fn().mockResolvedValue(undefined), reload: vi.fn() }));
vi.mock('@mlc-ai/web-llm', () => ({ deleteModelAllInfoInCache: mocks.remove, MLCEngine: class { reload = mocks.reload; unload = vi.fn(); } }));
afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); vi.clearAllMocks(); });
it('deletes only target model cache before redownloading and reports the actual revision', async () => {
  const revision = 'a'.repeat(40);
  const fetcher = vi.fn().mockResolvedValue(new Response('artifact'));
  const worker = { fetch: fetcher, postMessage: vi.fn(), onmessage: null as null | ((e: { data: unknown }) => void) };
  vi.stubGlobal('self', worker);
  vi.stubGlobal('navigator', { gpu: { requestAdapter: async () => ({ info: {} }) } });
  mocks.reload.mockImplementation(async () => { await worker.fetch(modelUrl(revision) + 'tokenizer.json'); });
  await import('./runtime.worker');
  worker.onmessage!({ data: { id: 1, operation: 'load', revision, forceDownload: true } });
  await vi.waitFor(() => expect(worker.postMessage).toHaveBeenCalledWith(expect.objectContaining({ kind: 'loaded', metrics: expect.objectContaining({ modelRevision: revision }) })));
  expect(mocks.remove).toHaveBeenCalledExactlyOnceWith(MODEL_ID, expect.objectContaining({ cacheBackend: 'indexeddb', model_list: [expect.objectContaining({ model: modelUrl(revision) })] }));
  expect(mocks.remove.mock.invocationCallOrder[0]).toBeLessThan(mocks.reload.mock.invocationCallOrder[0]);
  expect(fetcher).toHaveBeenCalledWith(modelUrl(revision) + 'tokenizer.json', expect.objectContaining({ cache: 'reload', credentials: 'omit' }));
  worker.onmessage!({ data: { id: 2, operation: 'load', revision } });
  await vi.waitFor(() => expect(worker.postMessage).toHaveBeenCalledWith(expect.objectContaining({ id: 2, kind: 'loaded' })));
  expect(mocks.remove).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls.at(-1)?.[1]).not.toHaveProperty('cache');
});

it('does not load stale cached weights when targeted cache removal fails', async () => {
  const worker = { fetch: vi.fn(), postMessage: vi.fn(), onmessage: null as null | ((e: { data: unknown }) => void) };
  vi.stubGlobal('self', worker);
  vi.stubGlobal('navigator', { gpu: { requestAdapter: async () => ({ info: {} }) } });
  mocks.remove.mockRejectedValueOnce(new Error('cache unavailable'));
  await import('./runtime.worker');
  worker.onmessage!({ data: { id: 1, operation: 'load', revision: 'a'.repeat(40), forceDownload: true } });
  await vi.waitFor(() => expect(worker.postMessage).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error', code: 'model_load_failed' })));
  expect(mocks.reload).not.toHaveBeenCalled();
});
