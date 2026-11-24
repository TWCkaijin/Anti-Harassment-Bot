/** Local Vite + synthetic Flask fixture. No real model or administrative writes. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5199';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-retrieval-settings-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1050 } });
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
      if (url.pathname.includes('/admin/')) return route.abort();
      return route.continue();
    });
    try {
      await page.goto(base);
      await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
      await page.locator('main textarea').fill('合成情境：我想了解一般處理方向。');
      await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
      await page.getByText('已檢索法律知識庫', { exact: true }).waitFor();
      assert(await page.getByText('已檢索救濟管道', { exact: true }).isVisible());
      assert.equal(await page.getByText('本次引用來源', { exact: true }).count(), 0);
      await page.getByText('已檢索法律知識庫', { exact: true }).click();
      await page.getByText('已檢索救濟管道', { exact: true }).click();
      assert(await page.getByText('合成資料', { exact: true }).last().isVisible());
      assert(await page.getByText('合成救濟管道', { exact: true }).last().isVisible());
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('harass_bot_conversations')).flatMap(s => s.messages).find(m => m.role === 'assistant'));
      assert.deepEqual(stored.ragUsed.retrieval.data_types, ['law', 'remedy']);
      assert.equal(stored.ragUsed.sources.length, 0);
      assert.equal(stored.reasoning, undefined);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: path.join(output, `${width}-retrieval.png`), fullPage: true });

      assert.deepEqual(errors, []);
      checks.push({ width, retrieved_uncited_sources: true, layout: true });
    } catch (error) {
      await page.screenshot({ path: path.join(output, `${width}-failure.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally { await page.close(); }
  }
} finally { await browser.close(); }
await writeFile(path.join(output, 'verification.json'), JSON.stringify({ synthetic: true, live_model_calls: 0, checks }, null, 2));
console.log(JSON.stringify({ synthetic: true, checks }));
