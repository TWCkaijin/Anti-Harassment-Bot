/** Verifies the real CPU model worker fails closed when its weights are unavailable. */
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
const context = await browser.newContext({ viewport: { width: 1360, height: 1050 }, serviceWorkers: 'block' });
const page = await context.newPage();
const blocked = [], errors = [];
let injected = false;
page.on('pageerror', error => errors.push(error.message));
await context.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url());
  if (url.hostname === 'fonts.googleapis.com') return route.fulfill({ contentType: 'text/css', body: '' });
  if (url.origin !== new URL(base).origin || request.method() !== 'GET' || request.postData()) {
    blocked.push({ host: url.hostname, path: url.pathname, method: request.method() });
    return route.abort();
  }
  if (url.pathname.endsWith('/admin/auth/session')) {
    return route.fulfill({ json: { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' } });
  }
  if (url.pathname.endsWith('/config')) return route.fulfill({ json: { openrouter_model: 'synthetic/not-used', agent_prompt_sections: {}, rag_collections: {}, environment_document_id: 'synthetic', source: 'local' } });
  if (url.pathname.endsWith('/scenario-scripts')) return route.fulfill({ json: { skills: [] } });
  if (url.pathname.startsWith('/api/')) {
    blocked.push({ host: url.hostname, path: url.pathname, method: request.method() });
    return route.abort();
  }
  if (url.pathname.startsWith('/masking-tiny/') && url.pathname.endsWith('/model.onnx')) {
    injected = true;
    return route.fulfill({ status: 404 });
  }
  return route.continue();
});
try {
  await page.goto(`${base}/masking-validation.html`);
  await page.getByRole('button', { name: '載入 Tiny NER', exact: true }).click();
  await page.getByRole('alert').waitFor({ timeout: 30_000 });
  const errorText = await page.getByRole('alert').innerText();
  assert(injected, 'The model asset fault injection must have run');
  assert.match(errorText, /tiny_assets_missing/);
  assert.equal(await page.getByRole('button', { name: '執行開發集 80 則' }).isEnabled(), false);
  assert.equal(await page.getByRole('button', { name: '執行保留驗證集 40 則' }).isEnabled(), false);
  assert.deepEqual(blocked, [], 'Failed initialization must not attempt any remote or API request');
  assert.equal(errors.length, 0);
  await page.screenshot({ path: path.join(output, 'missing-tiny-assets.png'), fullPage: true });
  await writeFile(path.join(output, 'failure-smoke.json'), JSON.stringify({ passed: true, method: 'forced 404 on the local Tiny NER model asset', errorText, blocked, errors }, null, 2));
  console.log(JSON.stringify({ passed: true, errorText, networkRequestsOutsideStaticPage: blocked.length }));
} finally { await browser.close(); }
