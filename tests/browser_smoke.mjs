/** Synthetic local browser acceptance. Start tests.browser_fixture_server and Vite first.
 * PLAYWRIGHT_MODULE may point to an installed Playwright module; no dependency is downloaded.
 * Artifacts go to BROWSER_ARTIFACTS (default /tmp/harass-browser-smoke).
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5173';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Use a local synthetic endpoint');
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-browser-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
const storageKey = 'harass_bot_conversations';
try {
  for (const mobile of [false, true]) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      // Existing UI fonts are static GETs; model, analytics and other external calls stay blocked.
      return ['127.0.0.1', 'localhost'].includes(url.hostname) || ['blob:', 'data:'].includes(url.protocol) || (route.request().method() === 'GET' && ['fonts.googleapis.com', 'fonts.gstatic.com'].includes(url.hostname)) ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(12_000);
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/chat/')) requests.push(request.postDataJSON()); });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
    await page.locator('textarea').fill('我在公司被騷擾，我想知道如何申訴');
    await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
    await page.getByRole('radio', { name: '主管', exact: true }).check();
    await page.getByRole('button', { name: '送出回覆', exact: true }).click();
    await page.getByRole('radio', { name: '受僱者', exact: true }).check();
    // Keyboard selection/submission uses the same question-specific contract.
    await page.getByRole('button', { name: '送出回覆', exact: true }).focus();
    await page.keyboard.press('Enter');
    await page.getByText('這是本機合成測試。我會依你更正後的情況整理資訊。', { exact: true }).waitFor();
    await page.waitForFunction(key => JSON.parse(localStorage.getItem(key) || '[]').some(s => s.caseFacts?.facts.subject_role?.value === '受僱者'), storageKey);
    const sessions = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), storageKey);
    const session = sessions.find(s => s.messages.length);
    assert.equal(session.caseFacts.facts.other_role.value, '主管');
    assert.equal(requests.length, 3);
    assert.equal(requests[1].clarification_answer.fact_key, 'other_role');
    assert.equal(requests[2].clarification_answer.fact_key, 'subject_role');
    assert(requests.every(r => r.contract_version === 2));
    await page.getByText('目前了解的情況', { exact: true }).click();
    await page.getByRole('button', { name: '修改摘要', exact: true }).click();
    await page.getByRole('textbox', { name: '對方的角色 內容', exact: true }).fill('同事');
    await page.getByRole('button', { name: '儲存摘要', exact: true }).click();
    await page.waitForFunction(key => JSON.parse(localStorage.getItem(key)).some(s => s.caseFacts?.facts.other_role?.value === '同事'), storageKey);
    assert.equal(requests.length, 3, 'Save must not invoke a model');
    await page.getByRole('button', { name: '修改摘要', exact: true }).click();
    await page.getByRole('textbox', { name: '對方的角色 內容', exact: true }).fill('顧客');
    await page.getByRole('button', { name: '儲存並重新回答', exact: true }).click();
    await page.waitForFunction(key => JSON.parse(localStorage.getItem(key)).some(s => s.messages.filter(m => m.role === 'assistant').length === 4), storageKey);
    assert.equal(requests.length, 4);
    assert.equal(requests.at(-1).message, requests[0].message, 'Reanswer must preserve the original request after typed follow-ups');
    assert.equal(requests.at(-1).case_context.facts.other_role.value, '顧客');
    assert(requests.at(-1).case_context.revision > requests[2].case_context.revision);
    await page.screenshot({ path: path.join(output, mobile ? 'mobile-summary.png' : 'desktop-summary.png'), fullPage: true });
    // Reopen the existing conversation after refresh rather than infer facts anew.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
    if (mobile) await page.getByRole('button', { name: '開啟側邊欄', exact: true }).click();
    await page.locator('aside p.truncate').first().click();
    await page.getByText('目前了解的情況', { exact: true }).click();
    await page.getByText('顧客', { exact: true }).waitFor();
    if (mobile) await page.getByRole('button', { name: '開啟側邊欄', exact: true }).click();
    await page.locator('aside').getByRole('button', { name: /設定/ }).click();
    assert.equal(await page.getByRole('checkbox', { name: '顯示情緒標籤', exact: true }).isChecked(), false);
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /匯出為 JSON/ }).click();
    const download = await downloadPromise;
    const exported = JSON.parse(await readFile(await download.path(), 'utf8'));
    const exportedText = JSON.stringify(exported);
    assert(exportedText.includes('caseFacts') && exportedText.includes('顧客'));
    await page.getByRole('button', { name: /清除所有對話紀錄/ }).click();
    await page.getByRole('button', { name: '確認', exact: true }).click();
    await page.waitForFunction(key => !JSON.parse(localStorage.getItem(key) || '[]').some(s => s.messages.length || Object.keys(s.caseFacts?.facts || {}).length), storageKey);
    if (mobile) {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
      await page.locator('textarea').fill('我被騷擾，我想了解申訴流程');
      await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
      await page.getByRole('radio', { name: '不確定', exact: true }).check();
      await page.getByRole('button', { name: '送出回覆', exact: true }).click();
      await page.getByRole('radio', { name: '暫不提供', exact: true }).check();
      await page.getByRole('button', { name: '送出回覆', exact: true }).click();
      await page.getByText('這是本機合成測試。我會依你更正後的情況整理資訊。', { exact: true }).waitFor();
      const declined = await page.evaluate(key => JSON.parse(localStorage.getItem(key)).find(s => s.messages.length).caseFacts.facts, storageKey);
      assert.equal(declined.other_role.status, 'unknown');
      assert.equal(declined.subject_role.status, 'declined');
      assert.equal(await page.getByRole('radio').count(), 0, 'Unknown/declined facts must not be asked again');
      let release, reached;
      const gate = new Promise(resolve => { release = resolve; });
      const intercepted = new Promise(resolve => { reached = resolve; });
      await page.route('**/api/v1/chat/', async route => {
        const response = await route.fetch();
        reached();
        await gate;
        try { await route.fulfill({ response }); } catch { /* Browser may cancel the intercepted request. */ }
      });
      await page.locator('textarea').fill('請說明一般法律資訊');
      await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
      await intercepted;
      await page.getByRole('button', { name: '停止回覆', exact: true }).click();
      release();
      await page.getByRole('button', { name: '傳送訊息', exact: true }).waitFor();
      await page.waitForFunction(key => JSON.parse(localStorage.getItem(key)).some(s => s.messages.some(m => m.isCancelled)), storageKey);
      const stopped = await page.evaluate(key => JSON.parse(localStorage.getItem(key)).find(s => s.messages.length), storageKey);
      assert.equal(stopped.messages.filter(m => m.role === 'assistant' && !m.isCancelled && !m.isError).length, 3);
    }
    assert.deepEqual(errors, []);
    checks.push({ viewport: mobile ? 'mobile' : 'desktop', status: 'passed', chat_requests: requests.length, checks: ['single-question', 'keyboard', 'typed-field-binding', 'save-without-request', 'save-and-reanswer', 'revision', 'reload', 'local-facts', 'export', 'clear', 'emotion-default-off', 'no-page-errors', ...(mobile ? ['unknown', 'declined', 'stop', 'late-response'] : [])] });
    await context.close();
  }
  await writeFile(path.join(output, 'result.json'), JSON.stringify({ synthetic: true, live_model_calls: 0, checks }, null, 2));
  console.log(JSON.stringify({ status: 'passed', artifacts: output, checks }, null, 2));
} finally { await browser.close(); }
