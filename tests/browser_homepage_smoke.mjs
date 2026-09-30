/** Local-only homepage visual/interaction regression. Start Vite and the
 * synthetic tests.browser_fixture_server first. No dependency is downloaded.
 * PLAYWRIGHT_MODULE selects an existing Playwright installation.
 * Run with --stage before to capture the unmodified baseline, or --stage after.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const stage = process.argv[process.argv.indexOf('--stage') + 1];
assert(['before', 'after'].includes(stage), 'Specify --stage before or --stage after');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5173';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Use the local synthetic service');
const output = path.join(process.env.BROWSER_ARTIFACTS || '/tmp/harass-homepage-smoke', stage);
await mkdir(output, { recursive: true });
const result = { stage, synthetic: true, live_model_calls: 0, checks: [] };
const browser = await chromium.launch({ channel: 'chrome', headless: true });

const variants = [
  { name: 'desktop', width: 1440, height: 1000 },
  { name: 'mobile', width: 390, height: 844 },
  { name: 'narrow', width: 320, height: 740 },
];

const labels = {
  'zh-TW': { brand: '溫暖守護', title: '先從您想說的開始', subtitle: '一起釐清下一步',
    description: '整理處境、了解權益，找到可用的協助。', input: '想說的情況或問題', addImage: '加入圖片',
    suggestions: '可以從這裡開始', suggestion: '我想了解自己的權益', send: '傳送訊息',
    open: '開啟側邊欄', close: '關閉', settings: '設定', newChat: '開啟新對話' },
  en: { brand: 'Warm Support', title: 'Start with what you want to share', subtitle: 'Explore the next step together',
    description: 'Make sense of your situation, understand your rights, and explore available support.',
    input: 'Your situation or question', addImage: 'Add an image', suggestions: 'Start here',
    suggestion: 'I want to understand my rights', send: 'Send message', open: 'Open sidebar',
    close: 'Close', settings: 'Settings', newChat: 'New Chat' },
};

async function assertNamedButtons(scope) {
  for (const button of await scope.getByRole('button').all()) {
    if (!await button.isVisible()) continue;
    const snapshot = await button.ariaSnapshot();
    assert(!/^- button(?:\s+\[[^\]]+\])?:?\s*$/m.test(snapshot), `Unnamed button: ${snapshot}`);
  }
}

async function assertBrandMark(mark) {
  await mark.waitFor({ state: 'visible' });
  assert(await mark.evaluate(image => image.complete && image.naturalWidth > 0 && image.naturalHeight > 0));
  assert.match(await mark.getAttribute('src'), /brand-mark\.svg$/);
  const box = await mark.boundingBox();
  assert(box.width >= 24 && box.height >= 24);
}

async function verifyHomepage(page, mobile, text) {
  const main = page.locator('main');
  await main.getByRole('heading', { name: text.brand, exact: true }).waitFor();
  await main.getByRole('heading', { name: `${text.title} ${text.subtitle}`, exact: true }).waitFor();
  await main.getByText(text.description, { exact: true }).waitFor();
  assert.equal(await main.getByText(/AI 助理已就緒|AI Assistant Ready|您的平靜生活|Your peaceful life/).count(), 0);
  const input = main.getByRole('textbox', { name: text.input, exact: true });
  const upload = main.getByRole('button', { name: text.addImage, exact: true });
  const send = main.getByRole('button', { name: text.send, exact: true });
  assert.equal(await send.isDisabled(), true);
  assert.equal(await main.getByRole('group', { name: text.suggestions, exact: true }).getByRole('button').count(), 3);
  await assertNamedButtons(main);
  const [inputBox, uploadBox, sendBox] = await Promise.all([input.boundingBox(), upload.boundingBox(), send.boundingBox()]);
  assert(inputBox.width >= 48 && uploadBox.x + uploadBox.width <= inputBox.x + 1 && inputBox.x + inputBox.width <= sendBox.x + 1,
    'Input, image and send controls must not overlap');
  const placeholder = await input.evaluate(node => {
    const style = getComputedStyle(node);
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    context.fontKerning = style.fontKerning;
    const spacing = Number.parseFloat(style.letterSpacing) || 0;
    return {
      measured_width: context.measureText(node.placeholder).width + spacing * [...node.placeholder].length,
      content_width: node.clientWidth - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight),
      font: context.font,
    };
  });
  assert(placeholder.measured_width <= placeholder.content_width + 1,
    `Single-row placeholder would wrap and be clipped: ${JSON.stringify(placeholder)}`);
  const geometry = await main.evaluate(node => {
    const rects = [...node.querySelectorAll('header, h2, p, textarea, button')].filter(element => {
      const style = getComputedStyle(element), rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width && rect.height;
    }).map(element => ({ tag: element.tagName, left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right }));
    const title = node.querySelector('header h2');
    return { width: innerWidth, client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth,
      title_clipped: title.scrollWidth > title.clientWidth + 1, rects };
  });
  assert(geometry.scroll <= geometry.client + 1, 'Document has horizontal overflow');
  assert(geometry.rects.every(rect => rect.left >= -1 && rect.right <= geometry.width + 1), 'Homepage content exceeds viewport width');
  assert.equal(geometry.title_clipped, false, 'The concise brand title must fit the header');
  assert.equal(await page.locator('[class*="material-symbols"], [class*="material-icons"]').count(), 0);
  const body = await page.locator('body').innerText();
  assert(!/\b(auto_awesome|arrow_forward|admin_panel_settings|chevron_left|chevron_right|chat_bubble|expand_less|menu_book)\b/.test(body), 'Material ligature text leaked into UI');
  if (mobile) await assertBrandMark(main.locator('header [data-brand-mark]'));
  else await assertBrandMark(page.locator('aside [data-brand-mark]'));
  const documentTitle = await page.title();
  assert(documentTitle.startsWith(text.brand) && !/vite/i.test(documentTitle));
  return { input_width: inputBox.width, header_title_clipped: false, horizontal_overflow: false,
    document_title: documentTitle, placeholder };
}

async function verifyFavicon(page) {
  const href = await page.locator('link[rel="icon"]').getAttribute('href');
  const url = new URL(href, base);
  assert(['127.0.0.1', 'localhost'].includes(url.hostname));
  const response = await page.request.get(url.href);
  assert.equal(response.status(), 200);
  assert.match(response.headers()['content-type'], /svg/);
  const svg = await response.text();
  assert.match(svg, /<svg\b/);
  assert(!/vite|vitejs|vitejs\.dev/i.test(svg), 'Favicon must use the project brand');
  const rendered = await page.evaluate(src => new Promise(resolve => {
    const image = new Image();
    image.onload = () => resolve(image.naturalWidth > 0 && image.naturalHeight > 0);
    image.onerror = () => resolve(false);
    image.src = src;
  }), url.href);
  assert.equal(rendered, true, 'SVG favicon must decode as an image');
  return { pathname: url.pathname, status: response.status(), rendered };
}

async function verifySettingsAndInput(page, mobile, text, requests, outputName) {
  if (mobile) await page.getByRole('button', { name: text.open, exact: true }).click();
  else {
    await page.getByRole('button', { name: '收起對話欄', exact: true }).click();
    await page.getByRole('button', { name: '展開對話欄', exact: true }).click();
    await page.locator('aside').getByRole('button', { name: text.settings, exact: true }).waitFor();
  }
  await assertBrandMark(page.locator('aside [data-brand-mark]'));
  await assertNamedButtons(page.locator('aside'));
  await page.locator('aside').getByRole('button', { name: text.settings, exact: true }).click();
  await page.getByRole('heading', { name: text.settings, exact: true }).waitFor();
  await page.getByRole('button', { name: 'English', exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, `${outputName}-settings.png`), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: text.close, exact: true }).last().focus();
  await page.keyboard.press('Enter');
  await page.getByRole('heading', { name: text.settings, exact: true }).waitFor({ state: 'hidden' });
  // Opening settings already closes the mobile sidebar via App's handler.
  if (mobile) await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1);

  const input = page.getByRole('textbox', { name: text.input, exact: true });
  await input.focus();
  await input.fill('請說明一般法律資訊');
  await page.keyboard.press('Shift+Enter');
  assert.match(await input.inputValue(), /\n$/);
  assert.equal(requests.length, 0, 'Shift+Enter must not submit');
  await input.fill('請說明一般法律資訊');
  await page.getByRole('button', { name: text.send, exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.getByText('這是本機合成測試。我會依你更正後的情況整理資訊。', { exact: true }).waitFor();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('harass_bot_conversations') || '[]')
    .flatMap(session => session.messages).some(message => message.role === 'assistant' && message.processingTrace?.outcome === 'complete'));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].contract_version, 2);
  await page.screenshot({ path: path.join(output, `${outputName}-synthetic-answer.png`), fullPage: true, animations: 'disabled' });
  if (mobile) await page.getByRole('button', { name: text.open, exact: true }).click();
  await page.locator('aside').getByRole('button', { name: text.newChat, exact: true }).click();
  const suggestion = page.getByRole('group', { name: text.suggestions, exact: true }).getByRole('button', { name: text.suggestion, exact: true });
  await suggestion.focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => JSON.parse(localStorage.getItem('harass_bot_conversations') || '[]')
    .flatMap(session => session.messages).filter(message => message.role === 'assistant' && !message.isError && !message.isCancelled).length === 2);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].message, text.suggestion);
}

try {
  for (const locale of ['zh-TW', 'en']) {
    for (const variant of variants) {
      const name = `${variant.name}-${locale}`;
      await rm(path.join(output, `${name}-failure.png`), { force: true });
      const mobile = variant.width < 1024;
      const context = await browser.newContext({ viewport: { width: variant.width, height: variant.height }, isMobile: mobile, hasTouch: mobile });
      const blocked = [];
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (['127.0.0.1', 'localhost'].includes(url.hostname) || ['blob:', 'data:'].includes(url.protocol)) return route.continue();
        blocked.push({ hostname: url.hostname, pathname: url.pathname, material_font: /Material(?:\+|%20| |_)Symbols|Material(?:\+|%20| |_)Icons/i.test(url.href) });
        return route.abort();
      });
      await context.addInitScript(locale => localStorage.setItem('harass_bot_locale', locale), locale);
      const page = await context.newPage();
      page.setDefaultTimeout(12_000);
      const errors = [];
      const requests = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => {
        if (request.method() === 'POST' && request.url().includes('/chat/')) requests.push(request.postDataJSON());
      });
      try {
        await page.goto(base, { waitUntil: 'domcontentloaded' });
        await page.getByText(locale === 'en' ? 'Connected' : '已連線', { exact: true }).waitFor({ state: 'attached' });
        const checks = stage === 'after' ? await verifyHomepage(page, mobile, labels[locale]) : {};
        const favicon = stage === 'after' ? await verifyFavicon(page) : undefined;
        await page.screenshot({ path: path.join(output, `${name}-homepage.png`), fullPage: true, animations: 'disabled' });
        const copy = await page.locator('main').innerText();
        const documentWidth = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
        if (mobile) {
          await page.getByRole('button', { name: locale === 'en' ? 'Open sidebar' : '開啟側邊欄', exact: true }).click();
          if (stage === 'after') await assertBrandMark(page.locator('aside [data-brand-mark]'));
          await page.screenshot({ path: path.join(output, `${name}-sidebar.png`), fullPage: true, animations: 'disabled' });
          await page.locator('aside').getByRole('button', { name: locale === 'en' ? 'Close' : '關閉', exact: true }).click();
        }
        if (stage === 'after') {
          await verifySettingsAndInput(page, mobile, labels[locale], requests, name);
          assert.equal(blocked.filter(request => request.material_font).length, 0, 'No Material font request may be attempted');
          assert.deepEqual(errors, []);
        }
        result.checks.push({ variant: name, status: stage === 'after' ? 'passed' : 'captured', copy,
          document_width: documentWidth, ...checks, favicon, chat_requests: requests.length,
          blocked_requests: blocked, page_errors: errors,
          ...(stage === 'after' ? { verified: ['new-brand-and-concise-copy', 'no-ready-badge', 'visible-brand-mark', 'svg-favicon-200-and-decode',
            'no-material-ligatures-or-font-request', 'no-horizontal-overflow-or-input-overlap', 'placeholder-fits-rendered-font-width', 'named-homepage-and-sidebar-buttons',
            'sidebar-open-close', 'settings-keyboard-close', 'shift-enter-no-submit', 'keyboard-send-synthetic-answer', 'keyboard-suggestion'] } : {}) });
      } catch (error) {
        await page.screenshot({ path: path.join(output, `${name}-failure.png`), fullPage: true, animations: 'disabled' }).catch(() => {});
        result.checks.push({ variant: name, status: 'failed', error: String(error), page_errors: errors });
        throw error;
      } finally { await context.close(); }
    }
  }
  if (stage === 'after') {
    const context = await browser.newContext({ viewport: { width: 600, height: 230 } });
    await context.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    const page = await context.newPage();
    const src = new URL('/brand-mark.svg', base).href;
    await page.setContent(`<html><body style="margin:0;padding:28px;background:#fff8f3;color:#342012;font:14px sans-serif"><p>Brand mark · actual SVG at 16 / 24 / 32 / 48 px</p><div style="display:flex;align-items:center;gap:48px;margin-top:28px">${[16,24,32,48].map(size => `<figure style="margin:0;text-align:center"><img src="${src}" width="${size}" height="${size}" alt="${size} pixels"><figcaption>${size} px</figcaption></figure>`).join('')}</div></body></html>`);
    await page.waitForFunction(() => [...document.images].every(image => image.complete && image.naturalWidth > 0));
    await page.screenshot({ path: path.join(output, 'brand-mark-sizes.png'), fullPage: true });
    await context.close();
  }
  result.status = stage === 'after' ? 'passed' : 'captured';
  console.log(JSON.stringify({ stage, status: result.status, artifacts: output, variants: result.checks.length }, null, 2));
} catch (error) {
  result.status = 'failed';
  throw error;
} finally {
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
  await browser.close();
}
