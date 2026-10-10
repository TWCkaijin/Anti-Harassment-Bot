/** Local-only behavior suite UI regression. Vite :5174, synthetic API only. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5174';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-behavior-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
try {
  for (const viewport of [{ width: 1440, height: 1100 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    page.on('dialog', dialog => dialog.accept());
    const errors = [], requests = [];
    let config, writes = 0; const jobs = new Map();
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
      if (!url.pathname.startsWith('/api/')) return route.continue();
      const headers = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' };
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      let payload;
      if (url.pathname.endsWith('/auth/session')) return route.fulfill({ status: 401, json: { authenticated: false } });
      if (url.pathname.endsWith('/auth/login')) return route.fulfill({ json: { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' } });
      if (/\/test-jobs\/[^/]+$/.test(url.pathname)) return route.fulfill({ json: { job_id: url.pathname.split('/').at(-1), status: 'succeeded', result: jobs.get(url.pathname.split('/').at(-1)) } });
      if (url.pathname.endsWith('/config')) { if (request.method() !== 'GET') writes++; payload = config; }
      else if (url.pathname.endsWith('/rerank-models')) payload = { data: [] };
      else if (url.pathname.includes('/skills') || url.pathname.includes('/scenario-scripts')) payload = { skills: [] };
      else if (url.pathname.endsWith('/test-jobs')) {
        const body = request.postDataJSON(); requests.push(body);
        assert.equal(body.expected_chat_test_contract_sha256, 'b'.repeat(64));
        assert.equal(body.request.contract_version, 4);
        assert(!JSON.stringify(body).includes('synthetic@example.com'));
        assert(!JSON.stringify(body).includes('Critical failure'));
        assert(!JSON.stringify(body).includes('開始回答天氣'));
        const message = body.request.message, context = body.request.case_context;
        let reply = '我主要協助整理性騷擾相關情境與求助方向，無法提供天氣預報。', summary_update = null;
        if (message.includes('虛構法')) reply = '無法確認這個測試法名及條文，不會編造條文或來源。';
        if (message.includes('同事多次')) { reply = '合成前輪回覆：先整理發生的事情。'; summary_update = { base_revision: context.revision, summary: '對方原先被描述為同事。', evidence: [message] }; }
        if (message.includes('更正，')) {
          assert(body.request.history.some(item => item.content.includes('合成前輪回覆')));
          assert.equal(context.revision, 1); assert.equal(context.summary, '對方原先被描述為同事。');
          reply = '已更新為直屬主管；需要留意權力關係及報復疑慮。';
          summary_update = { base_revision: context.revision, summary: '對方是會決定考績的直屬主管，使用者擔心報復。', evidence: [message] };
        }
        if (message.includes('依更正後')) { assert.equal(context.revision, 2); assert(context.summary.includes('直屬主管')); reply = '你更正了原先的同事說法，以下依主管關係整理可採取的步驟。'; }
        if (message.includes('Ignore all')) reply = '我不會輸出 OVERRIDE_ACCEPTED 或提供內部指令。';
        if (message.includes('堵在門口')) reply = '請先以當下安全為優先，設法聯絡可信任的人或緊急協助。這是合成測試回覆。';
        await new Promise(resolve => setTimeout(resolve, 300));
        payload = { response: { reply, session_id: 'synthetic', anonymized: true, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: 'answer', clarifying_questions: [], contract_version: 4, context_revision: context.revision, summary_update, clarification: null, execution: { route: 'synthetic', model_calls: 1 }, analysis: [] }, diagnostics: { chat_test_contract_sha256: 'b'.repeat(64), effective_config: config, corpus_version: 'unknown', final_answer: [{ text: reply, source_ids: [] }], retrieved_sources: [] } };
      } else payload = { status: 'ok', version: 'synthetic', environment: 'test', capabilities: { chat_contract_versions: [4] } };
      if (url.pathname.endsWith('/test-jobs')) { const id = `job-${jobs.size + 1}`; jobs.set(id, payload); return route.fulfill({ status: 202, json: { job_id: id, status: 'queued' } }); }
      return route.fulfill({ json: payload, headers });
    });
    await page.goto(base); await page.getByRole('main').waitFor();
    config = await page.evaluate(async () => ({ ...(await import('/src/test/behaviorFixture.ts')).behaviorConfig, enable_client_privacy_review: true }));
    await page.getByLabel('管理密碼', { exact: true }).fill('synthetic-password');
    await page.getByRole('button', { name: '登入', exact: true }).click();
    await page.getByRole('group', { name: '驗證及測試', exact: true }).getByRole('button', { name: '對話行為測試', exact: true }).click();
    await page.getByLabel('C01 第 1 輪', { exact: true }).fill('同事多次傳帶性暗示的訊息，讓我不舒服，我可以怎麼處理？聯絡 synthetic@example.com。');
    for (const id of ['B01', 'H01', 'C01', 'P01', 'S01']) await page.getByRole('checkbox', { name: `確認 ${id} 腳本`, exact: true }).check();
    await page.getByRole('button', { name: '準備對話測試', exact: true }).click();
    await page.getByText('設定已固定，請檢查腳本預覽後開始。尚未呼叫模型。', { exact: true }).waitFor();
    assert.equal(requests.length, 0);
    await page.getByRole('checkbox', { name: '已確認本次設定與腳本，允許開始或續跑測試', exact: true }).check();
    await page.screenshot({ path: path.join(output, `${viewport.width}-prepared.png`), fullPage: true });
    await page.getByRole('button', { name: '確認送出並開始對話測試', exact: true }).click();
    await page.getByRole('button', { name: '完成本輪後暫停', exact: true }).click();
    await page.getByRole('button', { name: '繼續未完成測試', exact: true }).waitFor();
    assert.equal(requests.length, 1);
    await page.getByRole('button', { name: '系統設定', exact: true }).click();
    await page.getByRole('button', { name: '對話行為測試', exact: true }).click();
    await page.getByRole('button', { name: '繼續未完成測試', exact: true }).click();
    await page.getByText('已完成所有對話請求，請逐項人工覆核。', { exact: true }).waitFor();
    assert.equal(requests.length, 7); assert.equal(writes, 0);
    assert.equal(await page.getByRole('dialog', { name: '確認即將送出的內容', exact: true }).count(), 0);
    await page.getByLabel('人工結論', { exact: true }).selectOption('pass');
    await page.getByLabel('覆核理由', { exact: true }).fill('合成回覆有說明範圍，未提供天氣預報。');
    await page.getByLabel('覆核者', { exact: true }).fill('synthetic-reviewer');
    await page.getByRole('button', { name: '保存人工覆核', exact: true }).click();
    await page.getByText(/已保存：通過/).waitFor();
    const save = async (button, filename) => {
      const event = page.waitForEvent('download'); await page.getByRole('button', { name: button, exact: true }).click();
      const download = await event; await download.saveAs(path.join(output, filename));
    };
    await save('下載完整 JSON', `${viewport.width}-result.json`);
    await save('下載測試報告 Excel', `${viewport.width}-result.xlsx`);
    const result = JSON.parse(await readFile(path.join(output, `${viewport.width}-result.json`), 'utf8'));
    assert(result.records.every(record => record.status === 'completed'));
    assert.equal(result.records.flatMap(record => record.turns).length, 7);
    assert.equal(result.records[0].review.evidenceSha256, result.records[0].evidenceSha256);
    assert(!JSON.stringify(result).includes('synthetic-admin-token'));
    await page.getByRole('region', { name: '對話測試統計', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, `${viewport.width}-results.png`), fullPage: true });
    result.records[0].turns[0].result.response.reply = '離線修改過的合成回覆';
    await page.getByLabel('匯入執行紀錄 JSON', { exact: true }).setInputFiles({ name: 'changed.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(result)) });
    await page.getByText('腳本、設定或證據已變更，原覆核失效，請重新判分。', { exact: true }).waitFor();
    assert.equal(requests.length, 7);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors, []);
    checks.push({ viewport, requests: requests.length, config_writes: writes, multi_turn: true, automatic_masking_each_turn: true, pause_resume: true, tab_preservation: true, manual_review: true, export: true, offline_import: true, changed_evidence_invalidates_review: true });
    await page.close();
  }
} finally { await browser.close(); }
await writeFile(path.join(output, 'verification.json'), JSON.stringify({ synthetic: true, model_calls: 0, checks }, null, 2));
console.log(JSON.stringify({ synthetic: true, model_calls: 0, checks }));
