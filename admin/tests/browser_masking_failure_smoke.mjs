/** Real-browser fault injection: the worker sees an unavailable WebGPU API. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5198';
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/rag-masking-failure-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1360, height: 1050 } });
const blocked = [], errors = [];
let injected = false;
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url());
  if (['localhost', '127.0.0.1'].includes(url.hostname) && url.pathname.endsWith('/admin/auth/session')) {
    return route.fulfill({ json: { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' } });
  }
  if (!['localhost', '127.0.0.1'].includes(url.hostname) || request.method() !== 'GET' || url.pathname.startsWith('/api/')) {
    blocked.push({ host: url.hostname, path: url.pathname, method: request.method() });
    return route.abort();
  }
  if (/\/assets\/runtime\.worker-[^/]+\.js$/.test(url.pathname)) {
    const response = await route.fetch();
    injected = true;
    return route.fulfill({ response, body: 'Object.defineProperty(self.navigator, "gpu", { value: undefined, configurable: true });\n' + await response.text() });
  }
  return route.continue();
});
try {
  await page.goto(`${base}/masking-validation.html`);
  await page.getByRole('button', { name: '載入本機模型', exact: true }).click();
  await page.getByRole('alert').waitFor({ timeout: 30_000 });
  const errorText = await page.getByRole('alert').innerText();
  assert(injected, 'Worker fault injection must have run');
  assert.match(errorText, /webgpu_unavailable|gpu_adapter_unavailable/);
  assert.equal(await page.getByRole('button', { name: '執行開發集 80 則' }).isEnabled(), false);
  assert.equal(await page.getByRole('button', { name: '執行保留驗證集 40 則' }).isEnabled(), false);
  assert.equal(blocked.length, 0, 'Failed initialization must not attempt any remote or API request');
  assert.equal(errors.length, 0);
  await page.screenshot({ path: path.join(output, 'unsupported-webgpu.png'), fullPage: true });
  await writeFile(path.join(output, 'failure-smoke.json'), JSON.stringify({ passed: true, method: 'real-browser worker script fault injection: navigator.gpu undefined', errorText, blocked, errors }, null, 2));
  console.log(JSON.stringify({ passed: true, errorText, networkRequestsOutsideStaticPage: blocked.length }));
} finally { await browser.close(); }
