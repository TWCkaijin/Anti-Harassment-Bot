/** Synthetic local API only: privacy, annotation, publication, comparison and history. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5184';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-rerank-smoke';
await mkdir(output, { recursive: true });
const fixture = JSON.parse(await readFile(new URL('./fixtures/rerank/hand-calculated.json', import.meta.url)));
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1050 } });
    page.setDefaultTimeout(15000);
    let config, loggedIn = false, dataset, preparation = { status: 'none', result: null };
    const runs = [], requests = [], errors = [], published = {};
    const docs = fixture.results[0].case.annotations.map(a => a.document);
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
      if (!path.includes('/admin/')) return route.continue();
      const body = request.postData() ? request.postDataJSON() : null;
      if (body) requests.push({ path, body });
      const reply = json => route.fulfill({ json });
      const session = { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' };
      if (path.endsWith('/auth/session')) return route.fulfill({ status: loggedIn ? 200 : 401, json: loggedIn ? session : { authenticated: false } });
      if (path.endsWith('/auth/login')) { loggedIn = true; return reply(session); }
      if (path.endsWith('/config')) { assert.equal(request.method(), 'GET'); return reply(config); }
      if (path.endsWith('/rerank-models')) return reply({ data: [{ id: 'synthetic', name: 'Synthetic' }] });
      if (path.endsWith('/scenario-scripts')) return reply({ skills: [] });
      if (path.endsWith('/rerank-tests/datasets')) {
        if (body) dataset = { id: 'a'.repeat(32), name: body.name, revision: 1, published_revision: null, archived: false, cases: [], case_count: 0 };
        return reply(body ? dataset : { items: dataset ? [dataset] : [], next_cursor: null });
      }
      if (path.endsWith('/preparation')) return reply(preparation);
      if (path.endsWith('/prepare')) { preparation = { status: 'succeeded', result: { documents: [docs[0], docs[1]] } }; return reply(preparation); }
      if (path.endsWith('/documents/resolve')) return reply(docs.find(d => d.doc_id === body.doc_id));
      if (path.endsWith('/rerank-tests/documents')) return reply({ items: [docs[2]].map(d => ({ ...d, id: d.doc_id })), next_cursor: null });
      if (/\/cases(?:\/[^/]+)?$/.test(path) && body) {
        assert(!body.query.includes('private@example.com'));
        const row = { ...body, id: 'b'.repeat(32), query_sha256: '0'.repeat(64), annotations: body.annotations.map(a => ({ relevance: a.relevance, document: docs.find(d => d.doc_id === a.doc_id) })) };
        dataset.cases = [row]; dataset.case_count = 1; dataset.revision++; return reply(dataset);
      }
      if (path.endsWith('/publish')) { dataset.published_revision = dataset.revision; published[dataset.revision] = structuredClone(dataset.cases); return reply({ revision: dataset.revision }); }
      if (path.includes('/revisions/')) return reply({ cases: published[path.split('/').at(-1)] });
      if (/\/datasets\/[a-f0-9]+$/.test(path)) return reply(dataset);
      if (path.endsWith('/rerank-tests/runs')) {
        if (!body) return reply({ items: runs, next_cursor: null });
        const run = { ...structuredClone(fixture), id: `run-${runs.length + 1}`, dataset_id: dataset.id, dataset_revision: body.revision, settings: body.settings };
        run.results[0].case_id = dataset.cases[0].id;
        runs.unshift(run); return reply(run);
      }
      if (path.includes('/runs/')) return reply(runs.find(r => r.id === path.split('/').at(-1)));
      return reply({ skills: [] });
    });
    await page.goto(base + '/#rerank');
    config = await page.evaluate(async () => ({ ...(await import('/src/test/behaviorFixture.ts')).behaviorConfig, rag_rerank_model: 'synthetic', rag_rerank_candidate_k: 3, rag_retrieval_top_k: 2 }));
    await page.getByLabel('管理密碼', { exact: true }).fill('synthetic-password');
    await page.getByRole('button', { name: '登入', exact: true }).click();
    await page.getByLabel('資料集名稱', { exact: true }).fill('合成測試集');
    await page.getByRole('button', { name: '建立資料集', exact: true }).click();
    await page.getByLabel('查詢原文（只留在本機，最多 4,000 字）').fill('聯絡 private@example.com，合成問題');
    assert(await page.getByRole('button', { name: '儲存題目與標註' }).isDisabled());
    await page.getByRole('button', { name: '本機去識別預覽' }).click();
    await page.getByRole('checkbox', { name: '我已確認此去識別文字可儲存及送出' }).check();
    await page.getByRole('button', { name: '儲存題目與標註' }).click();
    await page.getByRole('checkbox', { name: '我已確認本次外部呼叫用途及送出內容' }).check();
    await page.getByRole('button', { name: '取得標註候選' }).click();
    await page.getByRole('button', { name: '載入 2 個候選供標註' }).click();
    for (const doc of docs.slice(0, 2)) await page.getByLabel(`相關性 ${doc.unit_id}`, { exact: true }).selectOption('relevant');
    await page.getByText('從檢索資料庫補選候選外來源', { exact: true }).click();
    await page.getByRole('button', { name: '瀏覽資料庫', exact: true }).click();
    await page.getByRole('button', { name: '加入標註', exact: true }).click();
    await page.getByLabel(`相關性 ${docs[2].unit_id}`, { exact: true }).selectOption('relevant');
    await page.getByRole('checkbox', { name: '已人工核對所有標註及候選外相關來源，確認標準答案' }).check();
    await page.getByRole('button', { name: '儲存題目與標註' }).click();
    await page.getByRole('button', { name: '發布確認版本' }).click();
    await page.getByRole('button', { name: '先執行 5 題' }).click();
    await page.getByText('已完成 1/1', { exact: false }).waitFor();
    assert.equal(runs.length, 1);
    await page.getByText(/合成查詢 · ok/).click();
    await page.screenshot({ path: `${output}/${width}-comparison.png`, fullPage: true });
    assert(await page.getByText('漏檢索：law:C', { exact: true }).isVisible());
    await page.getByRole('button', { name: '用相同候選重跑', exact: true }).click();
    await page.waitForFunction(() => document.body.textContent.includes('run-2'));
    assert.equal(requests.filter(r => r.path.endsWith('/runs')).at(-1).body.reuse_run_id, 'run-1');
    assert.equal(requests.filter(r => r.path.endsWith('/prepare')).length, 1);
    await page.reload();
    await page.getByLabel('執行歷史', { exact: true }).selectOption('run-1');
    await page.getByText('已完成 1/1', { exact: false }).waitFor();
    assert(!JSON.stringify(requests.filter(r => r.path.includes('/rerank-tests'))).includes('private@example.com'));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, 'page must fit viewport');
    assert.deepEqual(errors, []);
    checks.push({ width, privacy: true, annotation: true, candidateOutsideGold: true, publish: true, compare: true, reuse: true, history: true, errors });
    await page.close();
  }
} finally { await browser.close(); }
await writeFile(`${output}/checks.json`, JSON.stringify(checks, null, 2));
console.log(JSON.stringify(checks));
