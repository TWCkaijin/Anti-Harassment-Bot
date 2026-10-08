/** Local-only UI regression. Start Vite on :5174 with VITE_API_BASE_URL=http://127.0.0.1:5055/api.
 * Uses synthetic Excel and intercepted API responses; never calls a model or Firestore.
 * PLAYWRIGHT_MODULE can point to an existing Playwright install.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5174';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-batch-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
try {
  for (const viewport of [{ width: 1440, height: 1100 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    let config, calls = 0;
    const requests = [];
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
      if (!url.pathname.startsWith('/api/')) return route.continue();
      if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' } });
      let payload;
      if (url.pathname.endsWith('/config')) payload = config;
      else if (url.pathname.endsWith('/rerank-models')) payload = { data: [{ id: 'cohere/rerank-4-fast', name: 'Fast' }] };
      else if (url.pathname.includes('/skills') || url.pathname.includes('/scenario-scripts')) payload = { skills: [] };
      else if (url.pathname.endsWith('/chat-test')) {
        calls++; const request = route.request().postDataJSON(); requests.push(request);
        assert.equal(request.request.history.length, 0);
        assert.equal(request.request.contract_version, 4);
        assert.equal(request.expected_applicable_laws_contract_sha256, 'a'.repeat(64));
        assert(!JSON.stringify(request).includes('gold-never-send'));
        assert(!JSON.stringify(request).includes('test@example.com'));
        await new Promise(resolve => setTimeout(resolve, 500));
        payload = { output_contract: 'applicable_laws_v1', prediction: { predicted_laws: calls === 1 ? ['性工法', '性騷法'] : [], decision_status: calls === 1 ? 'predicted' : 'insufficient_facts', decision_reason: calls === 1 ? null : '未提供當事人身分與關係，無法判斷主要適用法規。' }, diagnostics: {} };
      } else payload = { status: 'ok', version: 'synthetic', environment: 'test', capabilities: { chat_contract_versions: [4] } };
      return route.fulfill({ json: payload, headers: { 'Access-Control-Allow-Origin': '*' } });
    });
    await page.goto(base);
    await page.getByRole('main').waitFor();
    const fixture = await page.evaluate(async () => {
      const module = await import('/src/test/scenarioFixture.ts');
      const { unzipSync, zipSync, strFromU8, strToU8 } = await import('/node_modules/.vite/deps/fflate.js');
      const entries = unzipSync(module.fixtureWorkbook());
      entries['xl/worksheets/sheet1.xml'] = strToU8(strFromU8(entries['xl/worksheets/sheet1.xml']).replace('法規標籤', '適用法規').replace('gold-never-send', '性平法、性工法'));
      return { config: module.batchConfig, bytes: Array.from(zipSync(entries)) };
    });
    config = fixture.config;
    const openSidebar = page.getByRole('button', { name: '開啟側邊欄', exact: true });
    if (viewport.width < 768) await openSidebar.click();
    await page.getByRole('button', { name: '緊急協助', exact: true }).click();
    await page.getByRole('button', { name: '管理', exact: true }).click();
    await page.getByPlaceholder('輸入 ADMIN_API_KEY').fill('synthetic-admin-token');
    await page.getByRole('button', { name: '驗證並進入' }).click();
    await page.getByRole('group', { name: '驗證及測試', exact: true }).waitFor();
    assert.deepEqual(await page.getByRole('navigation').getByRole('button').allTextContents(), ['系統設定', 'Prompt 設定', 'Skills 設定', '本次診斷測試', '批次預測', '對話行為測試']);
    await page.getByRole('button', { name: '批次預測', exact: true }).click();
    await page.getByLabel('Excel 檔案', { exact: true }).setInputFiles({ name: 'synthetic.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from(fixture.bytes) });
    await page.getByRole('button', { name: '準備預測', exact: true }).click();
    await page.getByText('合成聯絡人 [電子郵件]', { exact: true }).waitFor();
    assert.equal(calls, 0);
    await page.screenshot({ path: path.join(output, `${viewport.width}-preview.png`), fullPage: true });
    await page.getByRole('button', { name: '確認送出並開始預測', exact: true }).click();
    await page.getByRole('button', { name: '完成本筆後暫停', exact: true }).click();
    await page.getByRole('button', { name: '繼續未完成預測', exact: true }).waitFor();
    assert.equal(calls, 1);
    await page.getByRole('button', { name: '系統設定', exact: true }).click();
    await page.getByLabel('Provider 排序').selectOption('latency');
    await page.getByLabel('重排等待上限（秒）').fill('2');
    await page.getByRole('region', { name: '模型設定', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, `${viewport.width}-settings.png`), fullPage: true });
    await page.getByRole('checkbox', { name: '後端 PII 遮罩（第二層）' }).scrollIntoViewIfNeeded();
    await page.getByText('進階遮罩範圍（全部開啟）', { exact: true }).click();
    await page.getByRole('checkbox', { name: '後端 PII 遮罩（第二層）' }).uncheck();
    assert(await page.getByLabel('遮罩檢索查詢', { exact: true }).isDisabled());
    await page.getByRole('checkbox', { name: '後端 PII 遮罩（第二層）' }).check();
    assert(await page.getByLabel('遮罩檢索查詢', { exact: true }).isChecked());
    await page.screenshot({ path: path.join(output, `${viewport.width}-workflow.png`), fullPage: true });
    await page.getByRole('button', { name: '批次預測', exact: true }).click();
    await page.getByRole('button', { name: '繼續未完成預測', exact: true }).click();
    await page.getByText('完成 2 / 2 · 失敗 0 · 待預測 0', { exact: true }).waitFor();
    assert.equal(calls, 2);
    await page.getByText('命中：性工法', { exact: true }).waitFor();
    await page.getByText('無法預測原因：未提供當事人身分與關係，無法判斷主要適用法規。', { exact: true }).waitFor();
    assert(!JSON.stringify(requests).includes('性平法、性工法'));
    await page.getByRole('region', { name: '批次預測統計' }).waitFor();
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: '下載結果 Excel', exact: true }).click();
    const download = await downloadEvent;
    assert.equal(download.suggestedFilename(), 'synthetic.predicted.xlsx');
    await download.saveAs(path.join(output, `${viewport.width}-result.xlsx`));
    await page.getByText('無法預測原因：未提供當事人身分與關係，無法判斷主要適用法規。', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, `${viewport.width}-complete.png`), fullPage: true });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Page must not overflow horizontally');
    assert.deepEqual(pageErrors, []);
    checks.push({ viewport, calls, masked: true, pause_resume: true, tab_preservation: true, grouped_settings: true, abstention_reason: true, download: true });
    await page.close();
  }
} finally { await browser.close(); }
await writeFile(path.join(output, 'verification.json'), JSON.stringify({ synthetic: true, model_calls: 0, checks }, null, 2));
console.log(JSON.stringify({ synthetic: true, model_calls: 0, checks }));
