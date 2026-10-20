// Tester screenshot of one app page, signed in first (owner, 2026-10-19: "/" showed only the
// login form, so P3e's run list could never be judged). Run from web/ so Playwright resolves:
//   node shoot.mjs <url> <png> [width,height] [wait_ms]
// Credentials come only from the environment (QARAU_TEST_USER / QARAU_TEST_PASSWORD, from
// devteam/.env) and are never printed. Without them, or for a route with `?signed-out` (the
// login page itself), the page is shot as it is.
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(path.join(process.cwd(), 'package.json'));
const { chromium } = require('@playwright/test');

const [url, png, size = '1440,900', wait = '4000'] = process.argv.slice(2);
const [width, height] = size.split(',').map(Number);
const user = process.env.QARAU_TEST_USER;
const password = process.env.QARAU_TEST_PASSWORD;

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width, height } });
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
  await page.goto(url); // same tab: the session (sessionStorage) carries over
  await page.waitForTimeout(Number(wait));
  await page.screenshot({ path: png });
} finally {
  await browser.close();
}
