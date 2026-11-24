/** Browser-only synthetic contract exercise; all nonlocal requests are blocked. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5174';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/rag-admin-corpus-smoke';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1050 } });
    page.setDefaultTimeout(15000);
    const errors = [], writes = [], blocked = [];
    let config, authenticated = false, imported = false, deleted = false, draft = null;
    const jobs = new Map();
    const record = { id: 'synthetic-chunk-1', kind: 'law', content: '這是用於本機瀏覽器驗證的完整合成資料內容。', metadata: { synthetic: true }, embedding: { dimensions: 3 }, source_id: 'synthetic-source' };
    const source = { source_id: 'synthetic-source', title: '合成測試資料', filename: 'synthetic.csv', kind: 'law', status: 'active', revision: 1, chunk_count: 1, original_available: true, chunks: [record] };
    const preview = { import_id: 'synthetic-import', status: 'preview', parse_revision: 2, plan_hash: 'synthetic-plan', filename: source.filename, title: source.title, kind: 'law', chunk_count: 1, chunks: [{ content: record.content, metadata: record.metadata }], warnings: [], duplicate_source_id: null, expires_at: '2099-01-01T00:00:00Z', limits: {} };
    const session = { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' };
    page.on('pageerror', error => { errors.push(error.message); console.error(error.message); });
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) { blocked.push(url.hostname); return route.abort(); }
      if (!url.pathname.startsWith('/api/')) return route.continue();
      const endpoint = url.pathname.replace('/api/v1/admin', '');
      if (endpoint === '/auth/session') return route.fulfill({ status: authenticated ? 200 : 401, json: authenticated ? session : { error: 'unauthorized' } });
      if (endpoint === '/auth/login') { authenticated = true; return route.fulfill({ json: session, headers: { 'Set-Cookie': '__session=synthetic; HttpOnly; SameSite=Strict; Path=/' } }); }
      if (endpoint === '/auth/logout') { assert.equal(request.headers()['x-csrf-token'], 'synthetic-csrf'); authenticated = false; return route.fulfill({ json: { authenticated: false } }); }
      assert(authenticated, `Unauthenticated request to ${endpoint}`);
      if (request.method() !== 'GET') { assert.equal(request.headers()['x-csrf-token'], 'synthetic-csrf'); writes.push(endpoint); }
      if (endpoint === '/config') return route.fulfill({ json: config });
      if (endpoint === '/skills') return route.fulfill({ json: { skills: [], revision: 1 } });
      if (endpoint === '/scenario-scripts') return route.fulfill({ json: { skills: [], revision: 1 } });
      if (endpoint === '/rerank-models') return route.fulfill({ json: { data: [] } });
      if (endpoint === '/corpus/sources' && request.method() === 'GET') return route.fulfill({ json: { items: imported && !deleted ? [source] : [], next_cursor: null } });
      if (endpoint === '/corpus/records') return route.fulfill({ json: { items: imported && !deleted ? [record] : [], next_cursor: null } });
      if (endpoint === '/corpus/jobs') return route.fulfill({ json: { items: [...jobs.values()].reverse(), next_cursor: null } });
      if (endpoint === '/corpus/imports') return route.fulfill({ json: { items: draft ? [draft] : [], next_cursor: null } });
      if (endpoint === '/corpus/imports/synthetic-import') return route.fulfill({ json: draft });
      if (endpoint === '/corpus/imports/preview') {
        const queued = { import_id: 'synthetic-import', filename: source.filename, title: source.title, kind: 'law', status: 'parse_queued', parse_revision: 1, parse_job_id: 'synthetic-scan-job' };
        draft = { ...queued, status: 'mapping_required', available_fields: ['自訂本文', '資料網址'], suggested_mapping: {}, sample_rows: [{ 自訂本文: record.content, 資料網址: 'https://example.test/source' }] };
        jobs.set('synthetic-scan-job', { job_id: 'synthetic-scan-job', kind: 'corpus_parse', status: 'succeeded', result: { import_id: 'synthetic-import', status: 'mapping_required', parse_revision: 1 } });
        return route.fulfill({ status: 202, json: queued });
      }
      if (endpoint === '/corpus/imports/synthetic-import/mapping') {
        assert.deepEqual(request.postDataJSON(), { column_mapping: { content: '自訂本文', source_url: '資料網址' }, parse_revision: 1 });
        draft = preview;
        jobs.set('synthetic-mapped-job', { job_id: 'synthetic-mapped-job', kind: 'corpus_parse', status: 'succeeded', result: { import_id: 'synthetic-import', status: 'preview', parse_revision: 2 } });
        return route.fulfill({ status: 202, json: { ...draft, status: 'parse_queued', parse_job_id: 'synthetic-mapped-job' } });
      }
      if (endpoint === '/corpus/imports/synthetic-import/confirm') {
        assert.equal(request.postDataJSON().plan_hash, 'synthetic-plan'); imported = true; draft = { ...preview, status: 'confirmed' };
        jobs.set('synthetic-import-job', { job_id: 'synthetic-import-job', kind: 'corpus_import', status: 'succeeded', result: { source_id: source.source_id } });
        return route.fulfill({ status: 202, json: { job_id: 'synthetic-import-job', status: 'queued' } });
      }
      if (endpoint.startsWith('/jobs/')) {
        const [, , id, action] = endpoint.split('/');
        if (action === 'retry') { assert.equal(request.postDataJSON().acknowledge_duplicate_cost, true); jobs.set(id, { ...jobs.get(id), status: 'queued', error: null }); }
        if (action === 'cancel') jobs.set(id, { ...jobs.get(id), status: 'cancelled' });
        assert(jobs.has(id), `Unknown job ${id}`);
        return route.fulfill({ json: jobs.get(id) });
      }
      if (endpoint === '/corpus/sources/synthetic-source/download') return route.fulfill({ contentType: 'text/plain', body: record.content });
      if (endpoint === '/corpus/sources/synthetic-source') {
        if (request.method() === 'DELETE') { assert.equal(request.postDataJSON().revision, 1); deleted = true; jobs.set('synthetic-delete-job', { job_id: 'synthetic-delete-job', kind: 'corpus_delete', status: 'succeeded', result: { source_id: source.source_id } }); return route.fulfill({ status: 202, json: { job_id: 'synthetic-delete-job', status: 'queued' } }); }
        return route.fulfill({ json: source });
      }
      throw new Error(`Unexpected API ${request.method()} ${endpoint}`);
    });
    try {
      await page.goto(base);
      config = await page.evaluate(async () => ({ ...(await import('/src/test/behaviorFixture.ts')).behaviorConfig, revision: 'synthetic-revision' }));
      await page.getByLabel('管理密碼', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, `${width}-login.png`), fullPage: true });
      await page.getByLabel('管理密碼', { exact: true }).fill('synthetic-password');
      await page.getByRole('button', { name: '登入', exact: true }).click();
      await page.getByRole('button', { name: '檢索資料庫', exact: true }).click();
      await page.getByRole('button', { name: '加入資料', exact: true }).click();
      await page.getByLabel('來源檔案').setInputFiles({ name: 'synthetic.csv', mimeType: 'text/csv', buffer: Buffer.from(`自訂本文,資料網址\n${record.content},https://example.test/source`) });
      await page.getByLabel('來源名稱').fill(source.title);
      await page.getByRole('button', { name: '產生匯入預覽', exact: true }).click();
      const mapping = page.getByRole('region', { name: '結構化資料欄位對應' });
      await mapping.waitFor();
      assert.equal(await mapping.getByRole('button', { name: '確認欄位並產生預覽' }).isEnabled(), false);
      assert.deepEqual(writes, ['/corpus/imports/preview']);
      await mapping.getByLabel('片段內容（必填）').selectOption('自訂本文');
      await mapping.getByLabel('來源網址', { exact: true }).selectOption('資料網址');
      await page.getByRole('button', { name: '系統設定', exact: true }).click();
      await page.getByRole('button', { name: '檢索資料庫', exact: true }).click();
      assert.equal(await mapping.getByLabel('片段內容（必填）').inputValue(), '自訂本文');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      await page.screenshot({ path: path.join(output, `${width}-mapping.png`), fullPage: true });
      await mapping.getByRole('button', { name: '確認欄位並產生預覽' }).click();
      await page.getByText('片段 1 ·', { exact: false }).click();
      await page.getByText(record.content, { exact: true }).waitFor();
      const confirm = page.getByRole('button', { name: '確認並開始 embedding', exact: true });
      assert.equal(await confirm.isEnabled(), false);
      assert.deepEqual(writes, ['/corpus/imports/preview', '/corpus/imports/synthetic-import/mapping']);
      await page.screenshot({ path: path.join(output, `${width}-preview.png`), fullPage: true });
      await page.reload();
      await page.getByRole('button', { name: '檢索資料庫', exact: true }).click();
      await page.getByRole('button', { name: '加入資料', exact: true }).click();
      await page.getByRole('region', { name: '可恢復的匯入草稿' }).getByRole('button', { name: new RegExp(source.title) }).click();
      await page.getByRole('checkbox', { name: /我已檢查全部片段/ }).check();
      await confirm.click();
      await page.getByText('工作狀態：完成', { exact: false }).waitFor();
      await page.getByRole('button', { name: '完整來源', exact: true }).click();
      await page.getByRole('button', { name: source.title, exact: true }).click();
      await page.getByText('synthetic-chunk-1 ·', { exact: false }).click();
      await page.getByText(record.content, { exact: true }).waitFor();
      await page.getByLabel('刪除來源確認名稱').fill('wrong');
      assert.equal(await page.getByRole('button', { name: '確認刪除完整來源', exact: true }).isEnabled(), false);
      await page.screenshot({ path: path.join(output, `${width}-source.png`), fullPage: true });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Page must not overflow horizontally');
      await page.getByLabel('刪除來源確認名稱').fill(source.title);
      await page.getByRole('button', { name: '確認刪除完整來源', exact: true }).click();
      await page.getByText('尚無已核對的完整來源。', { exact: false }).waitFor();
      jobs.set('synthetic-review-job', { job_id: 'synthetic-review-job', kind: 'corpus_import', status: 'needs_review', error: { detail: '合成測試：外部請求結果不明' } });
      await page.getByRole('button', { name: '重新整理工作', exact: true }).click();
      await page.getByRole('button', { name: /匯入資料 · 需要檢查/ }).click();
      const retry = page.getByRole('button', { name: '重試工作', exact: true });
      assert.equal(await retry.isEnabled(), false);
      await page.getByRole('checkbox', { name: /重試可能產生重複費用/ }).check();
      await retry.click();
      await page.getByRole('button', { name: '取消工作', exact: true }).click();
      await page.getByRole('article', { name: '工作詳細狀態' }).getByText('匯入資料：已取消', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, `${width}-jobs.png`), fullPage: true });
      assert.equal(await page.evaluate(() => localStorage.getItem('harassment_bot_admin_token')), null);
      assert.equal(await page.evaluate(() => document.cookie.includes('__session')), false);
      assert((await page.context().cookies()).some(cookie => cookie.name === '__session' && cookie.httpOnly));
      await page.getByRole('button', { name: '登出', exact: true }).click();
      await page.getByLabel('管理密碼', { exact: true }).waitFor();
      assert.deepEqual(errors, []);
      checks.push({ width, login_logout: true, cookie_http_only: true, queued_parsing: true, explicit_column_mapping: true, draft_survives_reload: true, mapping_survives_tab_change: true, review_retry_cancel: true, preview_before_confirm: true, source_full_text: true, exact_delete_name: true, csrf_mutations: true, no_horizontal_overflow: true, writes, blocked_external_hosts: [...new Set(blocked)] });
    } catch (error) {
      await page.screenshot({ path: path.join(output, `${width}-failure.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally { await page.close(); }
  }
} finally { await browser.close(); }
await writeFile(path.join(output, 'verification.json'), JSON.stringify({ synthetic: true, live_model_calls: 0, checks }, null, 2));
console.log(JSON.stringify({ synthetic: true, checks }));
