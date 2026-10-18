/** Local synthetic API only: verifies cache reuse and shared chat interactions. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5184';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-admin-diagnostic-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
const signedToken = Buffer.from(JSON.stringify({ question_id: 'open.work', selection_mode: 'single', max_selections: 1, contract_version: 4, context_revision: 1, context_scope: 'personal', allowed_values: ['主管', '同事'] })).toString('base64url') + '.' + 'a'.repeat(64);
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1050 } });
    page.setDefaultTimeout(15000);
    // A synthetic worker tests navigation lifetime without downloading a model.
    await page.addInitScript(() => {
      const NativeWorker = window.Worker;
      window.__maskingWorkerStats = { created: 0, terminated: 0, detections: 0 };
      window.Worker = class extends EventTarget {
        constructor(url, options) {
          super();
          if (!String(url).includes('runtime.worker')) return new NativeWorker(url, options);
          window.__maskingWorkerStats.created++;
          this.stopped = false;
        }
        postMessage(command) {
          setTimeout(() => {
            if (this.stopped) return;
            const data = command.operation === 'load'
              ? { id: command.id, kind: 'loaded', metrics: { elapsedMs: 5, fetchedBytes: 0, fetches: 0, failedFetches: 0, adapter: {} } }
              : { id: command.id, kind: 'detected', detection: { entities: command.text === '我叫許映辰，電話是0910-246-802。' ? [{ text: '許映辰', type: 'person' }, { text: '0910-246-802', type: 'contact' }] : command.text === '我的電子郵件是masking.check@example.com。' ? [{ text: 'masking.check@example.com', type: 'contact' }] : [], elapsedMs: 1, chunks: 1 } };
            if (command.operation === 'extract') window.__maskingWorkerStats.detections++;
            this.onmessage?.({ data });
          }, 60);
        }
        terminate() { this.stopped = true; window.__maskingWorkerStats.terminated++; }
      };
    });
    let config, loggedIn = false, configReads = 0, settingsWrites = 0;
    let allowFirstReply = false, firstReplyDelivered = false, releaseLogin;
    const loginGate = new Promise(resolve => { releaseLogin = resolve; });
    const jobs = [], errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
      if (!url.pathname.includes('/admin/')) return route.continue();
      const session = { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' };
      if (url.pathname.endsWith('/auth/session')) return route.fulfill({ status: loggedIn ? 200 : 401, json: loggedIn ? session : { authenticated: false } });
      if (url.pathname.endsWith('/auth/login')) { await loginGate; loggedIn = true; return route.fulfill({ json: session }); }
      if (url.pathname.endsWith('/config')) {
        if (request.method() === 'GET') configReads++; else settingsWrites++;
        return route.fulfill({ json: config });
      }
      if (url.pathname.endsWith('/test-jobs')) {
        jobs.push(request.postDataJSON());
        return route.fulfill({ json: { job_id: String(jobs.length), status: 'queued' } });
      }
      if (/\/test-jobs\/\d+$/.test(url.pathname)) {
        const id = Number(url.pathname.split('/').at(-1)), body = jobs[id - 1];
        if (id === 1 && !allowFirstReply) return route.fulfill({ json: { job_id: '1', status: 'running' } });
        if (id === 1) firstReplyDelivered = true;
        const extra = id === 1 ? { interaction_mode: 'clarify', summary_update: { base_revision: 0, summary: '合成工作情境', evidence: [body.request.message] }, clarification: { question_id: 'open.work', context_revision: 1, question: '對方與您是什麼關係？', reason: '了解處理管道', options: [{ label: '主管', value: '主管' }, { label: '同事', value: '同事' }], selection_mode: 'single', max_selections: 1, context_scope: 'personal', validation_token: signedToken } } : {};
        const response = { reply: `合成診斷回覆 ${id}`, session_id: 'synthetic', anonymized: true, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: 'answer', clarifying_questions: [], contract_version: 4, context_revision: body.request.case_context?.revision ?? 0, summary_update: null, clarification: null, execution: { route: 'synthetic', model_calls: 1 }, analysis: [], emotion: '擔心', emotion_color: 'orange', ...extra };
        return route.fulfill({ json: { job_id: String(id), status: 'completed', result: { response, diagnostics: { effective_config: { ...config, ...body.overrides } } } } });
      }
      return route.fulfill({ json: url.pathname.endsWith('/rerank-models') ? { data: [] } : { skills: [] } });
    });
    try {
      await page.goto(base);
      config = await page.evaluate(async () => ({ ...(await import('/src/test/behaviorFixture.ts')).behaviorConfig, enable_image_upload: true }));
      await page.getByLabel('管理密碼', { exact: true }).fill('synthetic-password');
      await page.getByRole('button', { name: '登入', exact: true }).click();
      const pendingLogin = page.getByRole('button', { name: '正在登入…', exact: true });
      await pendingLogin.waitFor();
      assert(await page.getByLabel('管理密碼', { exact: true }).isDisabled());
      assert(await pendingLogin.isDisabled());
      assert.equal(await pendingLogin.getAttribute('aria-busy'), 'true');
      assert.equal(await pendingLogin.locator('[aria-hidden]').evaluate(node => getComputedStyle(node).animationName), 'spin');
      await page.screenshot({ path: path.join(output, `${width}-login-pending.png`), fullPage: true });
      releaseLogin();
      await page.getByRole('button', { name: '本次診斷測試', exact: true }).click();
      await page.getByRole('textbox', { name: '測試訊息' }).waitFor();
      assert.equal(configReads, 1);
      await page.reload();
      await page.getByRole('button', { name: '本次診斷測試', exact: true }).click();
      await page.getByRole('textbox', { name: '測試訊息' }).waitFor();
      assert.equal(configReads, 1, 'authenticated reload reuses tab cache');
      await page.getByRole('button', { name: '重新載入設定', exact: true }).click();
      await page.waitForFunction(() => !document.body.textContent.includes('載入環境設定…'));
      assert.equal(configReads, 2, 'explicit refresh fetches current state');
      if (width < 1280) await page.getByRole('button', { name: '開啟暫時設定面板' }).click();
      await page.getByLabel('本次模型', { exact: true }).fill('synthetic/temporary-model');
      await page.screenshot({ path: path.join(output, `${width}-settings.png`), fullPage: true, animations: 'disabled' });
      if (width < 1280) await page.getByRole('button', { name: '關閉暫時設定面板', exact: true }).click();
      const send = async text => { await page.getByRole('textbox', { name: '測試訊息' }).fill(text); await page.getByRole('button', { name: '傳送訊息', exact: true }).click(); };
      const timeline = page.getByLabel('診斷對話訊息', { exact: true });
      await send('合成情境：工作時遇到令人困擾的言語。');
      await page.getByRole('button', { name: '停止回覆', exact: true }).waitFor();
      await page.getByRole('button', { name: '系統設定', exact: true }).click();
      allowFirstReply = true;
      await page.waitForResponse(response => /\/test-jobs\/1$/.test(new URL(response.url()).pathname) && firstReplyDelivered);
      await page.getByRole('button', { name: '本次診斷測試', exact: true }).click();
      await timeline.getByText('合成診斷回覆 1', { exact: true }).waitFor();
      assert.equal(jobs[0].overrides.openrouter_model, 'synthetic/temporary-model');
      await page.screenshot({ path: path.join(output, `${width}-choices.png`), fullPage: true, animations: 'disabled' });
      await page.getByRole('radio', { name: '主管', exact: true }).click();
      await page.getByRole('button', { name: '送出回覆', exact: true }).click();
      await timeline.getByText('合成診斷回覆 2', { exact: true }).waitFor();
      assert.equal(jobs[1].request.clarification_answer.value, '主管');
      assert.equal(jobs[1].request.case_context.revision, 1);
      assert(jobs[1].request.history.length > 0);
      await page.getByRole('button', { name: '顯示及更改摘要', exact: true }).click();
      await page.getByRole('button', { name: '修改摘要', exact: true }).click();
      await page.getByLabel('已知情境', { exact: true }).fill('更正：對方是主管。');
      await page.screenshot({ path: path.join(output, `${width}-summary.png`), fullPage: true, animations: 'disabled' });
      await page.getByRole('button', { name: '儲存並重新回答', exact: true }).click();
      await timeline.getByText('合成診斷回覆 3', { exact: true }).waitFor();
      assert.equal(jobs[2].request.regenerate_from_summary, true);
      assert.equal(jobs[2].request.case_context.summary, '更正：對方是主管。');
      assert.deepEqual(jobs[2].request.history, []);
      await page.getByLabel('選擇圖片', { exact: true }).setInputFiles({ name: 'synthetic.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64') });
      await page.getByAltText('已選擇的圖片', { exact: true }).waitFor();
      await send('請看合成圖片');
      await timeline.getByText('合成診斷回覆 4', { exact: true }).waitFor();
      assert.match(jobs[3].request.image_base64, /^data:image\/png;base64,/);
      assert(await timeline.getByText('擔心', { exact: true }).count() > 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
      const composer = await page.getByRole('textbox', { name: '測試訊息' }).boundingBox();
      assert(composer.y >= 0 && composer.y + composer.height <= page.viewportSize().height, 'composer stays visible');
      await timeline.evaluate(element => element.scrollTo({ top: element.scrollHeight, behavior: 'instant' }));
      await page.screenshot({ path: path.join(output, `${width}-chat.png`), fullPage: true, animations: 'disabled' });
      await page.getByRole('button', { name: '本機遮罩驗證', exact: true }).click();
      await page.getByRole('button', { name: '載入本機模型', exact: true }).click();
      await page.getByRole('button', { name: '執行開發集 80 則', exact: true }).click();
      await page.waitForFunction(() => window.maskingValidationReport?.cases.length >= 1);
      await page.getByRole('button', { name: '本次診斷測試', exact: true }).click();
      await page.waitForFunction(() => window.maskingValidationReport?.cases.length === 80);
      assert.deepEqual(await page.evaluate(() => window.__maskingWorkerStats), { created: 1, terminated: 0, detections: 83 });
      await page.getByRole('button', { name: '本機遮罩驗證', exact: true }).click();
      await page.getByText('本輪完成 80 則；請查看指標與失敗案例', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, `${width}-background-complete.png`), fullPage: false });
      await page.getByRole('button', { name: '執行保留驗證集 40 則', exact: true }).click();
      await page.getByRole('button', { name: '停止', exact: true }).click();
      assert.equal(await page.evaluate(() => window.__maskingWorkerStats.terminated), 1);
      assert.equal(settingsWrites, 0);
      assert.deepEqual(errors, []);
      checks.push({ width, cached_reload: true, explicit_refresh: true, multi_turn: true, choices: true, summary_edit: true, image: true, emotion: true, temporary_only: true, login_locked_with_spinner: true, diagnostic_background: true, masking_background_synthetic_worker: true, masking_manual_stop: true });
    } catch (error) {
      await page.screenshot({ path: path.join(output, `${width}-failure.png`), fullPage: true, animations: 'disabled' }).catch(() => {});
      throw error;
    } finally { await page.close(); }
  }
} finally { await browser.close(); }
await writeFile(path.join(output, 'verification.json'), JSON.stringify({ synthetic: true, live_model_calls: 0, checks }, null, 2));
console.log(JSON.stringify({ synthetic: true, checks }));
