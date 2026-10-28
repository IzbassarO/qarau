// Tester screenshot of one app page, signed in first (owner, 2026-10-19: "/" showed only the
// login form, so P3e's run list could never be judged). Run from web/ so Playwright resolves:
//   node shoot.mjs <url> <png> [width,height] [wait_ms] [light|dark] [ru|kk|en]
// The theme is emulated as prefers-color-scheme; empty = the browser default. The locale is
// the app's own choice (localStorage qarau.lang, web/src/i18n), set before any page script.
// Credentials come only from the environment (QARAU_TEST_USER / QARAU_TEST_PASSWORD, from
// devteam/.env) and are never printed. Without them, or for a route with `?signed-out` (the
// login page itself), the page is shot as it is.
// After the fixed wait it waits until the page is ready, up to QARAU_SHOOT_READY_S (config
// gates.screenshot_ready_timeout_s, 30 s): no [aria-busy="true"] or [data-loading] element and
// no request in flight for 500 ms (UI-report, 2026-10-28: the report was shot while its PDF was
// still being made). Still busy then: shot anyway, and stdout says `NOT READY after N s: why`.
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(path.join(process.cwd(), 'package.json'));
const { chromium } = require('@playwright/test');

const [url, png, size = '1440,900', wait = '4000', theme = '', lang = ''] = process.argv.slice(2);
if (!['', 'light', 'dark'].includes(theme)) throw new Error(`theme must be light or dark: ${theme}`);
if (!['', 'ru', 'kk', 'en'].includes(lang)) throw new Error(`locale must be ru, kk or en: ${lang}`);
const [width, height] = size.split(',').map(Number);
const user = process.env.QARAU_TEST_USER;
const password = process.env.QARAU_TEST_PASSWORD;

const browser = await chromium.launch();
try {
  const page = await browser.newPage({
    viewport: { width, height },
    ...(theme ? { colorScheme: theme } : {}),
  });
  if (lang) await page.addInitScript((l) => localStorage.setItem('qarau.lang', l), lang);
  if (user && password && !new URL(url).searchParams.has('signed-out')) {
    await page.goto(new URL('/', url).href); // A1b login (src/auth/LoginPage.tsx)
    const field = page.locator('#login-password');
    if (await field.count()) {
      await page.locator('#login-username').fill(user);
      await field.fill(password);
      await page.locator('form button[type="submit"]').click();
      const signedIn = field.waitFor({ state: 'detached', timeout: 15000 }).then(() => true);
      const refused = page
        .getByRole('alert')
        .waitFor({ timeout: 15000 })
        .then(() => false);
      if (!(await Promise.race([signedIn, refused]).catch(() => false)))
        throw new Error('sign-in failed: check QARAU_TEST_USER / QARAU_TEST_PASSWORD in devteam/.env');
    }
  }
  let inflight = 0;
  let quiet = Date.now();
  const settle = () => {
    if (--inflight <= 0) [inflight, quiet] = [0, Date.now()];
  };
  page.on('request', () => inflight++);
  page.on('requestfinished', settle);
  page.on('requestfailed', settle);
  await page.goto(url); // same tab: the session (sessionStorage) carries over
  await page.waitForTimeout(Number(wait));
  const readyS = Number(process.env.QARAU_SHOOT_READY_S || 30);
  const busy = page.locator('[aria-busy="true"], [data-loading]');
  const end = Date.now() + readyS * 1000;
  let why;
  for (;;) {
    const n = await busy.count();
    why = [
      n && `${n} element(s) with aria-busy="true" or data-loading`,
      (inflight || Date.now() - quiet < 500) && `network not idle (${inflight} request(s) in flight)`,
    ]
      .filter(Boolean)
      .join(', ');
    if (!why || Date.now() > end) break;
    await page.waitForTimeout(250);
  }
  await page.screenshot({ path: png });
  // stdout carries only this line, never the login
  if (why) process.stdout.write(`NOT READY after ${readyS} s: ${why}\n`);
} finally {
  await browser.close();
}
