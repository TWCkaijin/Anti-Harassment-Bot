/** Local synthetic processing-panel regression. Start the fixture with
 * SYNTHETIC_MODEL_DELAY_SECONDS=2 and Vite before running this script.
 * PLAYWRIGHT_MODULE may select an existing installation; nothing is downloaded.
 * Actual fixture SSE frames are paced in the test browser (never fabricated) so
 * adjacent progress/done events can be asserted separately. This is not a
 * latency benchmark. No application source or stored user data is modified.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5173';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Use a local synthetic endpoint');
const output = process.env.BROWSER_ARTIFACTS || '/tmp/harass-processing-smoke';
const storageKey = 'harass_bot_conversations';
const frameDelay = 200;
await mkdir(output, { recursive: true });
const result = {
  synthetic: true, live_model_calls: 0, browser_transport_frame_delay_ms: frameDelay,
  evidence_scope: 'Local fixture, real Flask/SSE and browser UI; not production, legal accuracy or latency evidence.',
  checks: [],
};
const browser = await chromium.launch({ channel: 'chrome', headless: true });

async function savedAssistant(page, index, outcome) {
  await page.waitForFunction(({ key, index, outcome }) => {
    const messages = JSON.parse(localStorage.getItem(key) || '[]').flatMap(s => s.messages);
    return messages.filter(m => m.role === 'assistant')[index]?.processingTrace?.outcome === outcome;
  }, { key: storageKey, index, outcome });
  return page.evaluate(({ key, index }) => JSON.parse(localStorage.getItem(key))
    .flatMap(s => s.messages).filter(m => m.role === 'assistant')[index], { key: storageKey, index });
}

async function reopenConversation(page, mobile) {
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
  if (mobile) await page.getByRole('button', { name: '開啟側邊欄', exact: true }).click();
  await page.locator('aside p.truncate').first().click();
}

async function screenshotPanel(page, filename) {
  // Capture the natural application scroll position; screenshots must not
  // conceal a control that the user cannot see after manually expanding it.
  await page.screenshot({ path: path.join(output, filename), fullPage: true, animations: 'disabled' });
}

async function assertToggleInChatViewport(page, toggle) {
  const handle = await toggle.elementHandle();
  try {
    await page.waitForFunction(button => {
      const region = button.closest('section')?.getBoundingClientRect();
      const rect = button.getBoundingClientRect();
      return region && rect.top >= region.top - 1 && rect.bottom <= region.bottom + 1
        && rect.left >= region.left - 1 && rect.right <= region.right + 1;
    }, handle);
  } finally { await handle.dispose(); }
}

try {
  for (const mobile of [false, true]) {
    const viewport = mobile ? 'mobile' : 'desktop';
    await rm(path.join(output, `${viewport}-failure.png`), { force: true });
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
      isMobile: mobile, hasTouch: mobile,
    });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      const local = ['127.0.0.1', 'localhost'].includes(url.hostname);
      return local || ['blob:', 'data:'].includes(url.protocol) ? route.continue() : route.abort();
    });
    // Retain the real fetch, bytes, event order, abort signal and endpoint. Only
    // delivery timing changes to make transient running states observable.
    await context.addInitScript(({ frameDelay }) => {
      const observation = window.__processingSmoke = { requests: [], snapshots: [] };
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const response = await originalFetch(...args);
        if (!response.url.includes('/api/v1/chat/') || !response.headers.get('content-type')?.includes('text/event-stream') || !response.body) return response;
        const request = { phases: [], route: null, model_calls: null };
        observation.requests.push(request);
        const reader = response.body.getReader();
        const decoder = new TextDecoder(), encoder = new TextEncoder();
        let cancelled = false;
        const stream = new ReadableStream({
          async start(controller) {
            let buffer = '';
            const deliver = async frame => {
              if (cancelled) return;
              const event = frame.match(/^event:\s*(\S+)/m)?.[1];
              const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
              if (raw) {
                const data = JSON.parse(raw);
                if (event === 'progress') request.phases.push(data.phase);
                if (event === 'done') {
                  request.route = data.execution?.route ?? null;
                  request.model_calls = data.execution?.model_calls ?? null;
                }
              }
              controller.enqueue(encoder.encode(frame));
              await new Promise(resolve => setTimeout(resolve, frameDelay));
            };
            try {
              while (!cancelled) {
                const { value, done } = await reader.read();
                buffer += decoder.decode(value, { stream: !done });
                let boundary;
                while (!cancelled && (boundary = buffer.indexOf('\n\n')) >= 0) {
                  const frame = buffer.slice(0, boundary + 2);
                  buffer = buffer.slice(boundary + 2);
                  await deliver(frame);
                }
                if (done) {
                  if (buffer && !cancelled) await deliver(buffer);
                  if (!cancelled) controller.close();
                  return;
                }
              }
            } catch (error) { if (!cancelled) controller.error(error); }
          },
          async cancel(reason) { cancelled = true; await reader.cancel(reason); },
        });
        return new Response(stream, { status: response.status, statusText: response.statusText, headers: response.headers });
      };
      const observer = new MutationObserver(() => {
        const snapshots = [...document.querySelectorAll('button[aria-label="處理過程"]')].map(button => {
          const panel = document.getElementById(button.getAttribute('aria-controls'));
          return {
            expanded: button.getAttribute('aria-expanded'),
            outcome: button.querySelector('[role="status"]')?.textContent,
            steps: [...(panel?.querySelectorAll('li') || [])].map(item => item.textContent),
          };
        });
        const snapshot = snapshots.at(-1);
        if (snapshot && JSON.stringify(snapshot) !== JSON.stringify(observation.snapshots.at(-1))) observation.snapshots.push(snapshot);
      });
      observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    }, { frameDelay });
    const page = await context.newPage();
    page.setDefaultTimeout(15_000);
    const errors = [];
    let chatRequests = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.method() === 'POST' && request.url().includes('/chat/')) chatRequests += 1;
    });
    try {
      await page.goto(base, { waitUntil: 'domcontentloaded' });
      await page.getByText('已連線', { exact: true }).waitFor({ state: 'attached' });
      await page.locator('textarea').fill('請說明一般法律資訊');
      await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
      const toggle = page.getByRole('button', { name: '處理過程', exact: true }).first();
      await page.getByText('正在等待模型回應', { exact: true }).waitFor();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      await page.getByText('正在檢索資料庫', { exact: true }).waitFor();
      await screenshotPanel(page, `${viewport}-waiting.png`);
      const beforeCollapse = await page.getByRole('list', { name: '已觀察到的處理步驟' }).getByRole('listitem').count();
      await toggle.click();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
      // Require a genuinely new phase while still running; completion alone
      // cannot prove that manual collapse survived an intervening update.
      await page.waitForFunction(count => window.__processingSmoke.snapshots.some(snapshot =>
        snapshot.outcome === '處理中' && snapshot.expanded === 'false' && snapshot.steps.length > count), beforeCollapse);
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
      assert.match(await toggle.textContent(), /處理中/);
      await toggle.focus();
      await page.keyboard.press('Enter');
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      const completed = await savedAssistant(page, 0, 'complete');
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false', 'Completion automatically collapses an open panel');
      assert.equal(completed.isCancelled, undefined);
      const observed = await page.evaluate(() => window.__processingSmoke.requests);
      assert.equal(observed.length, 1);
      assert.equal(observed[0].route, 'direct_retrieval');
      assert.equal(observed[0].model_calls, 1, 'One synthetic SDK call, zero external model calls');
      const phases = completed.processingTrace.steps.map(step => step.phase);
      assert(phases.includes('retrieving') && phases.includes('waiting_model') && phases.includes('validating'));
      await screenshotPanel(page, `${viewport}-complete-collapsed.png`);
      await toggle.focus();
      await page.keyboard.press('Space');
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      await assertToggleInChatViewport(page, toggle);
      assert.equal(await page.getByRole('list', { name: '已觀察到的處理步驟' }).getByRole('listitem').count(), phases.length);
      await page.getByText('顯示系統處理步驟，不代表完整內部推理或法律正確性驗證。', { exact: true }).waitFor();
      await screenshotPanel(page, `${viewport}-keyboard-expanded.png`);
      await page.keyboard.press('Enter');
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false');

      await reopenConversation(page, mobile);
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
      const restored = await savedAssistant(page, 0, 'complete');
      assert.deepEqual(restored.processingTrace, completed.processingTrace);
      await toggle.focus();
      await page.keyboard.press('Enter');
      await assertToggleInChatViewport(page, toggle);
      assert.equal(await page.getByRole('list', { name: '已觀察到的處理步驟' }).getByRole('listitem').count(), phases.length);
      await page.keyboard.press('Enter');

      await page.locator('textarea').fill('請再說明一般法律資訊');
      await page.getByRole('button', { name: '傳送訊息', exact: true }).click();
      await page.getByText('正在等待模型回應', { exact: true }).last().waitFor();
      await page.getByRole('button', { name: '停止回覆', exact: true }).click();
      const cancelled = await savedAssistant(page, 1, 'cancelled');
      assert.equal(cancelled.isCancelled, true);
      assert(cancelled.processingTrace.steps.some(step => step.phase === 'waiting_model'));
      const cancelledToggle = page.getByRole('button', { name: '處理過程', exact: true }).last();
      assert.match(await cancelledToggle.textContent(), /已停止/);
      assert.doesNotMatch(await cancelledToggle.textContent(), /回覆完成/);
      await screenshotPanel(page, `${viewport}-cancelled.png`);
      await reopenConversation(page, mobile);
      const restoredCancelled = await savedAssistant(page, 1, 'cancelled');
      assert.deepEqual(restoredCancelled.processingTrace, cancelled.processingTrace);
      assert.match(await page.getByRole('button', { name: '處理過程', exact: true }).last().textContent(), /已停止/);
      assert.equal(chatRequests, 2);
      assert.deepEqual(errors, []);
      result.checks.push({
        viewport, status: 'passed', chat_requests: chatRequests, completed_route: observed[0].route,
        completed_phases: phases, cancelled_phases: cancelled.processingTrace.steps.map(step => step.phase),
        checks: ['running-expanded', 'retrieving-and-waiting-visible', 'manual-collapse-survives-new-progress',
          'completion-auto-collapse', 'keyboard-enter-and-space', 'keyboard-expanded-toggle-in-chat-viewport', 'recorded-steps-readable',
          'reload-collapsed-with-identical-steps', 'cancelled-not-success', 'cancelled-reload', 'no-page-errors'],
      });
    } catch (error) {
      await page.screenshot({ path: path.join(output, `${viewport}-failure.png`), fullPage: true }).catch(() => {});
      result.checks.push({ viewport, status: 'failed', error: String(error), chat_requests: chatRequests, page_errors: errors });
      throw error;
    } finally { await context.close(); }
  }
  result.status = 'passed';
  console.log(JSON.stringify({ status: result.status, artifacts: output, checks: result.checks }, null, 2));
} catch (error) {
  result.status = 'failed';
  throw error;
} finally {
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
  await browser.close();
}
