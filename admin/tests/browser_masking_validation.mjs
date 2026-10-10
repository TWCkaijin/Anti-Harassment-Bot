/** Real local WebGPU benchmark, using only the committed synthetic fixture set.
 * Start Vite on 127.0.0.1:5197. No chat/OpenRouter/Firestore requests are permitted.
 * MODEL assets are public downloads; report/profile are written outside the repo.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5197';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/rag-browser-masking-validation';
await mkdir(output, { recursive: true });
const sourceHashes = {};
for (const filename of await readdir('admin/frontend/src/masking-lab')) {
  if (filename.endsWith('.test.ts')) continue;
  sourceHashes[filename] = createHash('sha256').update(await readFile(path.join('admin/frontend/src/masking-lab', filename))).digest('hex');
}
const context = await chromium.launchPersistentContext(path.join(output, 'chrome-profile'), {
  channel: process.env.BROWSER_CHANNEL || 'chrome', headless: process.env.BROWSER_HEADLESS === 'true',
  viewport: { width: 1440, height: 1050 }, serviceWorkers: 'block',
});
const requests = [], violations = [], pageErrors = [];
let state = 'started';
const allowedRemote = ['huggingface.co', 'raw.githubusercontent.com', 'cdn-lfs.huggingface.co', 'cdn-lfs-us-1.hf.co', 'cas-bridge.xethub.hf.co', 'us.aws.cdn.hf.co'];
await context.route('**/*', async route => {
  const req = route.request(), url = new URL(req.url());
  if (url.pathname.endsWith('/auth/session')) return route.fulfill({ json: { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' } });
  // Do not log signed CDN URL query strings or bodies, even though fixtures are synthetic.
  const record = { host: url.hostname, path: url.pathname, method: req.method() };
  requests.push(record);
  const allowed = req.method() === 'GET' && !req.postData()
    && (['127.0.0.1', 'localhost'].includes(url.hostname) && !url.pathname.startsWith('/api/') || allowedRemote.includes(url.hostname));
  if (!allowed) { violations.push(record); return route.abort(); }
  return route.continue();
});
const page = context.pages()[0] || await context.newPage();
page.on('pageerror', error => pageErrors.push(error.message.slice(0, 1000)));
page.on('console', message => {
  if (message.type() === 'error') console.log('browser_error', message.text().slice(0, 500));
});
async function save(name = 'report.json') {
  const report = await page.evaluate(() => window.maskingValidationReport ?? null).catch(() => null);
  await writeFile(path.join(output, name), JSON.stringify({ state, sourceHashes, report, requests, violations, pageErrors }, null, 2));
  return report;
}
async function waitWhileBusy(limitMs) {
  const start = Date.now();
  let last = '';
  while (await page.getByRole('button', { name: '停止', exact: true }).isEnabled()) {
    if (Date.now() - start > limitMs) throw new Error('browser_operation_timeout');
    const status = await page.getByRole('status').innerText();
    if (status !== last) { console.log(status.slice(0, 240)); last = status; await save('progress.json'); }
    await page.waitForTimeout(1000);
  }
  if (await page.getByRole('alert').count()) throw new Error(await page.getByRole('alert').innerText());
}
try {
  await page.goto(`${base}/masking-validation.html`);
  await page.getByRole('heading', { name: '瀏覽器自動遮罩驗證' }).waitFor();
  const capabilities = await page.evaluate(async () => {
    const gpu = navigator.gpu;
    const adapter = gpu ? await gpu.requestAdapter() : null;
    return { userAgent: navigator.userAgent, webgpu: !!gpu, adapter: !!adapter,
      adapterInfo: adapter ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture, device: adapter.info.device, description: adapter.info.description } : null,
      features: adapter ? [...adapter.features] : [],
      memory: navigator.deviceMemory ?? null, hardwareConcurrency: navigator.hardwareConcurrency,
    };
  });
  capabilities.host = { platform: os.platform(), architecture: os.arch(), release: os.release(), cpu: os.cpus()[0]?.model, memoryBytes: os.totalmem() };
  await writeFile(path.join(output, 'capabilities.json'), JSON.stringify(capabilities, null, 2));
  console.log('capabilities', JSON.stringify(capabilities));
  assert(capabilities.adapter, 'No WebGPU adapter available in this browser');
  state = 'cold_load';
  await page.getByRole('button', { name: '載入本機模型', exact: true }).click();
  await waitWhileBusy(660_000);
  await save('cold-load.json');
  state = 'cached_load';
  await page.getByRole('button', { name: '重新載入並量測快取' }).click();
  await waitWhileBusy(660_000);
  await save('cached-load.json');
  for (const [split, label, expected] of [['development', '執行開發集 80 則', 80], ['holdout', '執行保留驗證集 40 則', 40]]) {
    state = split;
    await page.getByRole('button', { name: label }).click();
    await waitWhileBusy(3_000_000);
    const report = await save(`${split}.json`);
    assert.equal(report.scores[split].cases, expected, `${split} did not finish`);
  }
  state = 'completed';
  const report = await save();
  console.log('scores', JSON.stringify(report.scores));
  console.log('gatePassed', report.gatePassed);
  assert.equal(violations.length, 0, 'Unexpected outbound network request');
  assert.equal(pageErrors.length, 0, 'Browser page errors');
} catch (error) {
  state = `failed:${error.message}`;
  await save();
  console.error(state);
  process.exitCode = 1;
} finally {
  await page.screenshot({ path: path.join(output, 'validation.png'), fullPage: false }).catch(() => {});
  await context.close();
}
