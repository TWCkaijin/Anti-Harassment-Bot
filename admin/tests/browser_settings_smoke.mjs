/** Local Vite + synthetic Flask fixture. No real model or administrative writes. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5174';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-admin-settings-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1050 } });
    page.setDefaultTimeout(15000);
    const errors = [], writes = [];
    let config;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
      if (!url.pathname.includes('/admin/')) return route.continue();
      const headers = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' };
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      if (url.pathname.endsWith('/auth/session')) return route.fulfill({ status: 401, json: { authenticated: false } });
      if (url.pathname.endsWith('/auth/login')) return route.fulfill({ json: { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' } });
      if (url.pathname.endsWith('/config')) {
        if (request.method() !== 'GET') { const update = request.postDataJSON(); writes.push(update); config = { ...config, ...update }; }
        return route.fulfill({ json: config, headers });
      }
      return route.fulfill({ json: url.pathname.endsWith('/rerank-models') ? { data: [] } : { skills: [] }, headers });
    });
    try {
      await page.goto(base);
      config = await page.evaluate(async () => ({ ...(await import('/src/test/behaviorFixture.ts')).behaviorConfig, reasoning_effort: 'high', enable_reasoning_display: false }));
      await page.getByLabel('管理密碼', { exact: true }).fill('synthetic-password');
      await page.getByRole('button', { name: '登入', exact: true }).click();
      await page.getByRole('button', { name: '系統設定', exact: true }).click();
      const toggle = page.getByRole('checkbox', { name: '顯示模型 Thinking 過程' });
      await toggle.scrollIntoViewIfNeeded();
      assert.equal(await toggle.isChecked(), false);
      await toggle.check();
      await page.getByRole('button', { name: '儲存變更', exact: true }).click();
      await page.getByRole('button', { name: '已同步', exact: true }).waitFor();
      assert.equal(writes.at(-1).enable_reasoning_display, true);
      assert.equal(writes.at(-1).reasoning_effort, 'high');
      await toggle.uncheck();
      await page.getByRole('button', { name: '儲存變更', exact: true }).click();
      await page.getByRole('button', { name: '已同步', exact: true }).waitFor();
      assert.equal(writes.at(-1).enable_reasoning_display, false);
      await toggle.scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `${width}-settings.png`), fullPage: true });
      assert.deepEqual(errors, []);
      checks.push({ width, saved_reasoning_toggle: true, layout: true });
    } catch (error) {
      await page.screenshot({ path: path.join(output, `${width}-failure.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally { await page.close(); }
  }
} finally { await browser.close(); }
await writeFile(path.join(output, 'verification.json'), JSON.stringify({ synthetic: true, live_model_calls: 0, checks }, null, 2));
console.log(JSON.stringify({ synthetic: true, checks }));
