/** Browser WASM diagnostic on an independently written synthetic PII set.
 * Run only against local Vite. Raw inputs and character spans stay in local artifacts.
 * This set is agent-curated and requires human adjudication before it can be gold data.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5197';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const output = process.env.BROWSER_ARTIFACTS || '/private/tmp/rag-browser-masking-validation-2026-11-10/manual-holdout';
const profile = process.env.BROWSER_PROFILE_DIR || '/private/tmp/rag-browser-masking-validation-2026-11-10/chrome-profile';
const fixturePath = path.resolve('admin/tests/fixtures/browser_pii_manual_holdout.json');
const fixtureBytes = await readFile(fixturePath);
const fixture = JSON.parse(fixtureBytes.toString());
const cases = fixture.cases;
const types = ['person', 'organization', 'address', 'contact', 'identifier'];
assert.equal(fixture.provenance.humanReviewed, false);
assert.equal(fixture.provenance.syntheticOnly, true);
assert(Array.isArray(cases) && cases.length >= 10);
assert.equal(new Set(cases.map(item => item.id)).size, cases.length);
for (const item of cases) {
  assert(typeof item.text === 'string' && item.text.length > 0);
  assert(Array.isArray(item.expected));
  for (const entity of item.expected) {
    assert(types.includes(entity.type));
    assert.equal(item.text.split(entity.text).length - 1, 1, `${item.id}: expected span must occur once`);
  }
  assert.equal(new Set(item.expected.map(entity => `${entity.type}:${entity.text}`)).size, item.expected.length);
}
const original = JSON.parse(await readFile('admin/frontend/src/masking-lab/fixtures.json', 'utf8'));
const oldText = original.map(item => item.text).join('\n');
for (const item of cases) {
  assert(!original.some(existing => existing.text === item.text), `Reused existing fixture: ${item.id}`);
  for (const entity of item.expected) assert(!oldText.includes(entity.text), `Reused existing annotated span: ${item.id}`);
}
for (const type of types) assert(cases.some(item => item.expected.some(entity => entity.type === type)), `Missing ${type}`);
assert(cases.some(item => item.expected.length === 0));
assert(cases.filter(item => [...item.text].length >= 200 && [...item.text].length <= 500).length >= 5);
await mkdir(output, { recursive: true });

const sourceHashes = {};
for (const filename of ['config.ts', 'runtime.ts', 'runtime.worker.ts', 'extraction.ts', 'rules.ts', 'wordpiece.ts']) {
  sourceHashes[filename] = createHash('sha256').update(await readFile(path.join('admin/frontend/src/masking-lab', filename))).digest('hex');
}
const fixtureHash = createHash('sha256').update(fixtureBytes).digest('hex');
const context = await chromium.launchPersistentContext(profile, {
  channel: process.env.BROWSER_CHANNEL || 'chrome',
  headless: process.env.BROWSER_HEADLESS !== 'false',
  args: ["--disable-webgpu"], viewport: { width: 1440, height: 900 }, serviceWorkers: 'block',
});
const requests = [], violations = [], pageErrors = [];
await context.route('**/*', async route => {
  const req = route.request(), url = new URL(req.url());
  if (url.hostname === 'fonts.googleapis.com') return route.fulfill({ contentType: 'text/css', body: '' });
  if (url.pathname.endsWith('/auth/session')) return route.fulfill({ json: { authenticated: true, csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z', environment: 'preview' } });
  if (url.pathname.endsWith('/config')) return route.fulfill({ json: { openrouter_model: 'synthetic/not-used', agent_prompt_sections: {}, rag_collections: {}, environment_document_id: 'synthetic', source: 'local' } });
  if (url.pathname.endsWith('/scenario-scripts')) return route.fulfill({ json: { skills: [] } });
  if (url.pathname.endsWith('/rerank-models')) return route.fulfill({ json: { data: [] } });
  const record = { host: url.hostname, path: url.pathname, method: req.method() };
  requests.push(record);
  const allowed = req.method() === 'GET' && !req.postData()
    && url.origin === new URL(base).origin && !url.pathname.startsWith('/api/');
  if (!allowed) { violations.push(record); return route.abort(); }
  return route.continue();
});

const page = context.pages()[0] || await context.newPage();
page.on('pageerror', error => pageErrors.push(error.message.slice(0, 1000)));
const rank95 = values => values.length ? [...values].sort((a, b) => a - b)[Math.ceil(values.length * 0.95) - 1] : null;
const ratio = (numerator, denominator) => denominator ? numerator / denominator : null;
function score(results) {
  const perType = {};
  for (const type of types) {
    let truePositive = 0, falsePositive = 0, falseNegative = 0;
    for (const item of results) {
      const expected = new Set(item.expected.filter(e => e.type === type).map(e => e.text));
      const detected = new Set(item.detected.filter(e => e.type === type).map(e => e.text));
      truePositive += [...expected].filter(text => detected.has(text)).length;
      falsePositive += [...detected].filter(text => !expected.has(text)).length;
      falseNegative += [...expected].filter(text => !detected.has(text)).length;
    }
    perType[type] = { truePositive, falsePositive, falseNegative,
      expected: truePositive + falseNegative, detected: truePositive + falsePositive,
      precision: ratio(truePositive, truePositive + falsePositive), recall: ratio(truePositive, truePositive + falseNegative) };
  }
  const totals = Object.values(perType).reduce((total, item) => {
    total.truePositive += item.truePositive;
    total.falsePositive += item.falsePositive;
    total.falseNegative += item.falseNegative;
    return total;
  }, { truePositive: 0, falsePositive: 0, falseNegative: 0 });
  const longCases = results.filter(item => item.characters >= 200 && item.characters <= 500);
  const allLatencies = results.map(item => item.elapsedMs).sort((a, b) => a - b);
  const validOutputs = results.filter(item => item.error === null).length;
  const cleanNegatives = results.filter(item => item.expected.length === 0);
  return {
    attemptedCases: results.length, fixtureCases: cases.length, validOutputs,
    validOutputRate: ratio(validOutputs, results.length), errors: results.filter(item => item.error).map(item => ({ id: item.id, code: item.error })),
    ...totals, precision: ratio(totals.truePositive, totals.truePositive + totals.falsePositive),
    recall: ratio(totals.truePositive, totals.truePositive + totals.falseNegative), perType,
    cleanNegatives: { cases: cleanNegatives.length, correctlyEmpty: cleanNegatives.filter(item => !item.error && item.detected.length === 0).length },
    warmedLatencyMs: { count: allLatencies.length, median: allLatencies[Math.floor((allLatencies.length - 1) / 2)] ?? null, p95: rank95(allLatencies) },
    warmed200To500CharactersLatencyMs: { count: longCases.length, p95: rank95(longCases.map(item => item.elapsedMs)),
      all: longCases.map(item => ({ id: item.id, characters: item.characters, elapsedMs: item.elapsedMs, error: item.error })) },
  };
}

let state = 'started', lastCount = -1;
async function save() {
  const browserState = await page.evaluate(() => window.__manualPiiHoldout ?? null).catch(() => null);
  const results = browserState?.results ?? [];
  const report = { schemaVersion: 1, state, createdAt: new Date().toISOString(), fixtureHash, sourceHashes,
    fixtureProvenance: fixture.provenance, browser: { version: context.browser()?.version() ?? null, userAgent: browserState?.userAgent ?? null,
      fullVersionList: browserState?.fullVersionList ?? null, adapterInfo: browserState?.adapterInfo ?? null,
      host: { platform: os.platform(), arch: os.arch(), cpu: os.cpus()[0]?.model ?? null, memoryBytes: os.totalmem() } },
    model: browserState?.model ?? null, load: browserState?.load ?? null, warmup: browserState?.warmup ?? null,
    score: score(results), cases: results, requests, violations, pageErrors,
    caveat: 'Agent-curated synthetic diagnostic set pending independent human adjudication; not a production acceptance gate.' };
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  return browserState;
}
try {
  await page.goto(base);
  await page.getByRole('button', { name: '本機遮罩驗證', exact: true }).click();
  await page.getByRole('heading', { name: '瀏覽器自動遮罩驗證' }).waitFor();
  state = 'running';
  await page.evaluate(async fixtureCases => {
    const { BrowserMaskingRuntime } = await import('/src/masking-lab/runtime.ts');
    const config = await import('/src/masking-lab/config.ts');
    const ua = navigator.userAgentData;
    const full = ua ? await ua.getHighEntropyValues(['fullVersionList']).catch(() => null) : null;
    const record = window.__manualPiiHoldout = {
      status: 'loading', results: [], load: [], warmup: null, error: null,
      userAgent: navigator.userAgent, fullVersionList: full?.fullVersionList ?? null,
      adapterInfo: { executionProvider: "wasm" },
      model: { id: config.TINY_MODEL, revision: config.TINY_REVISION, runtimeVersion: config.ORT_VERSION, rulesVersion: config.RULES_VERSION },
    };
    void (async () => {
      let runtime = new BrowserMaskingRuntime();
      try {
        record.load.push(await runtime.load());
        record.status = 'warmup';
        const warmupStart = performance.now();
        try { const warm = await runtime.detect('這是一則沒有身分資料的本機測試。');
          record.warmup = { elapsedMs: performance.now() - warmupStart, entities: warm.entities, spans: warm.spans, error: null };
        } catch (error) { record.warmup = { elapsedMs: performance.now() - warmupStart, entities: [], spans: [], error: error.code ?? 'evaluation_failed' }; }
        for (const item of fixtureCases) {
          record.status = `case:${item.id}`;
          const start = performance.now();
          let detected = [], spans = [], error = null, modelElapsedMs = null, chunks = null;
          try { const result = await runtime.detect(item.text);
            detected = result.entities; spans = result.spans; modelElapsedMs = result.elapsedMs; chunks = result.chunks;
          } catch (failure) { error = failure.code ?? 'evaluation_failed'; }
          record.results.push({ id: item.id, category: item.category, characters: [...item.text].length,
            source: item.text, expected: item.expected, detected, spans, error, chunks, modelElapsedMs,
            elapsedMs: performance.now() - start });
          if (['worker_failed', 'model_timeout', 'model_not_ready'].includes(error)) {
            runtime.dispose(); runtime = new BrowserMaskingRuntime();
            record.status = `reloading_after:${item.id}`;
            record.load.push(await runtime.load());
          }
        }
        record.status = 'completed';
      } catch (error) { record.error = error.code ?? String(error).slice(0, 200); record.status = 'failed'; }
      finally { runtime.dispose(); }
    })();
  }, cases);
  const deadline = Date.now() + 1_800_000;
  while (Date.now() < deadline) {
    const current = await save();
    if (current?.results.length !== lastCount) {
      lastCount = current?.results.length ?? 0;
      console.log(`completed ${lastCount}/${cases.length}: ${current?.status ?? 'unknown'}`);
    }
    if (current?.status === 'completed' || current?.status === 'failed') break;
    await page.waitForTimeout(1000);
  }
  const current = await save();
  assert.equal(current?.status, 'completed', current?.error ?? 'Evaluation timed out');
  assert.equal(current.results.length, cases.length);
  assert.equal(violations.length, 0, 'Unexpected outbound request');
  assert.equal(pageErrors.length, 0, 'Browser page error');
  state = 'completed';
  await save();
  console.log('score', JSON.stringify(score(current.results)));
} catch (error) {
  state = `failed:${error.message}`;
  await save();
  console.error(state);
  process.exitCode = 1;
} finally { await context.close(); }
