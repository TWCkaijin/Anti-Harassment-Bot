/** Current v4 UI regression against tests.browser_fixture_server and local Vite.
 * Uses synthetic input only. No private workbook, credential, model or Firestore.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5173';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-browser-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [], storageKey = 'harass_bot_conversations';
try {
  for (const mobile of [false, true]) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile });
    await context.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    const errors = [], requests = [], outbound = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      outbound.push(request.url() + (request.postData() || ''));
      if (request.method() === 'POST' && request.url().includes('/chat/')) requests.push(request.postDataJSON());
    });
    const session = () => page.evaluate(key => JSON.parse(localStorage.getItem(key) || '[]').find(s => s.messages.length), storageKey);
    const completed = count => page.waitForFunction(({ key, count }) => {
      const item = JSON.parse(localStorage.getItem(key) || '[]').find(s => s.messages.length);
      return item?.messages.filter(m => m.role === 'assistant' && !m.isStreaming && !m.isError && !m.isCancelled).length >= count;
    }, { key: storageKey, count });
    try {
      await page.goto(base); await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
      await page.locator('main textarea').fill('我在公司遇到不舒服的訊息，我想知道如何申訴。synthetic@example.test');
      await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
      await completed(1);
      assert.equal(await page.getByRole('dialog', { name: '確認即將送出的內容', exact: true }).count(), 0);
      assert.equal(requests.length, 1); assert.equal(requests[0].contract_version, 4);
      assert(requests[0].message.includes('[電子郵件]'));
      assert.equal((await session()).caseFacts.schema_version, 3);
      await page.locator('[data-summary-trigger]').click();
      await page.getByRole('button', { name: '修改摘要', exact: true }).click();
      await page.getByRole('textbox', { name: '已知情境', exact: true }).fill('對方是顧客，這是手動修正摘要。');
      await page.getByRole('button', { name: '儲存摘要', exact: true }).click();
      assert.equal(requests.length, 1);
      const revision = (await session()).caseFacts.revision;
      await page.locator('[data-summary-trigger]').click();
      await page.getByRole('button', { name: '修改摘要', exact: true }).click();
      await page.getByRole('textbox', { name: '已知情境', exact: true }).fill('對方是顧客，我想依更正的情況重新整理。');
      await page.getByRole('button', { name: '儲存並重新回答', exact: true }).click();
      await completed(2);
      assert.equal(requests.length, 2);
      assert.equal(requests[1].regenerate_from_summary, true);
      assert(requests[1].case_context.revision > revision);
      assert(requests[1].case_context.summary.includes('顧客'));
      await page.locator('main textarea').fill('補充訊息：請聯絡 followup@example.test');
      await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
      await completed(3);
      assert.equal(requests.length, 3);
      assert(!outbound.join('\n').match(/synthetic@example\.test|followup@example\.test/));
      const saved = await session();
      assert(!JSON.stringify(saved).match(/synthetic@example\.test|followup@example\.test/));
      await page.reload(); await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
      if (mobile) await page.getByRole('button', { name: '開啟側邊欄', exact: true }).click();
      await page.locator('aside p.truncate').first().click();
      await page.locator('[data-summary-trigger]').click();
      await page.getByText('對方是顧客，我想依更正的情況重新整理。', { exact: true }).waitFor();
      await page.getByRole('dialog', { name: '目前摘要', exact: true }).getByRole('button', { name: '關閉', exact: true }).click();
      assert.equal((await session()).caseFacts.summary, saved.caseFacts.summary);
      await page.getByRole('button', { name: '開啟側邊欄', exact: true }).click();
      await page.locator('aside').getByRole('button', { name: /設定/ }).click();
      const event = page.waitForEvent('download');
      await page.getByRole('button', { name: /匯出為 JSON/ }).click();
      const download = await event;
      const exported = await readFile(await download.path(), 'utf8');
      assert(exported.includes('顧客'));
      assert(!exported.match(/synthetic@example\.test|followup@example\.test/));
      await page.screenshot({ path: path.join(output, `${mobile ? 'mobile' : 'desktop'}-automatic.png`), fullPage: true });
      assert.deepEqual(errors, []);
      checks.push({ viewport: mobile ? 'mobile' : 'desktop', status: 'passed', chat_requests: requests.length, checks: ['v4', 'first-and-subsequent-auto-send', 'masked-network-and-storage', 'summary-save-without-call', 'summary-regeneration', 'reload', 'export', 'no-page-errors'] });
    } catch (error) {
      await page.screenshot({ path: path.join(output, `${mobile ? 'mobile' : 'desktop'}-failure.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally { await context.close(); }
  }
  console.log(JSON.stringify({ status: 'passed', checks }));
} finally {
  await writeFile(path.join(output, 'result.json'), JSON.stringify({ synthetic: true, live_model_calls: 0, checks }, null, 2));
  await browser.close();
}
