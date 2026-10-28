import { readFileSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import fixture from '../src/test/fixtures/report-summary.json' with { type: 'json' };

// Original, hand-authored one-page PDF. No third-party document content.
const pdf = readFileSync(new URL('../src/test/fixtures/report.pdf', import.meta.url));
const hash = '5e317abc851bdb197835b4a473d2fed04bb0051eed5c8956234256cbfef9fb6c';

async function mockApi(page: Page) {
  const pdfLanguages: string[] = [];
  await page.route('**/api/**', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    expect(request.headers().authorization).toBe('Bearer report-test-session');
    if (url.pathname === '/api/runs') return route.fulfill({ json: [] });
    if (url.pathname === '/api/inspections/demo-id' || url.pathname === '/api/inspections/37')
      return route.fulfill({ json: fixture.inspection });
    if (url.pathname === '/api/inspections/notre-dame' || url.pathname === '/api/inspections/demo')
      return route.fulfill({ status: 422, json: fixture.validation });
    if (url.pathname === '/api/inspections') return route.fulfill({ json: [fixture.inspection] });
    if (url.pathname === '/api/inspections/37/runs') return route.fulfill({ json: fixture.runs });
    if (url.pathname === '/api/sites') return route.fulfill({ json: fixture.sites });
    if (url.pathname === '/api/inspections/37/findings')
      return route.fulfill({ json: fixture.findings });
    const decisions: Record<string, unknown> = fixture.decisions;
    const match = /^\/api\/findings\/(\d+)\/decisions$/.exec(url.pathname);
    if (match && match[1] in decisions) return route.fulfill({ json: decisions[match[1]] });
    if (url.pathname === '/api/inspections/37/report.pdf') {
      pdfLanguages.push(url.searchParams.get('lang')!);
      return route.fulfill({
        body: pdf,
        contentType: 'application/pdf',
        headers: { 'X-Report-SHA256': hash },
      });
    }
    return route.fulfill({ status: 404, json: fixture.notFound });
  });
  return pdfLanguages;
}

const locales = [
  {
    lang: 'en',
    title: 'Inspection report',
    download: 'Download PDF',
    preview: 'PDF preview',
    language: 'PDF language',
    coverage: 'Coverage not computed',
    critical: 'Critical',
    confirmed: 'Confirmed',
    decider: 'Alan Bekov, confirmed',
    signIn: 'Sign in',
    username: 'Username',
    password: 'Password',
  },
  {
    lang: 'ru',
    title: 'Отчёт об инспекции',
    download: 'Скачать PDF',
    preview: 'Предпросмотр PDF',
    language: 'Язык PDF',
    coverage: 'Покрытие не рассчитано',
    critical: 'Критично',
    confirmed: 'Подтверждено',
    decider: 'Alan Bekov, подтверждено',
    signIn: 'Войти',
    username: 'Имя пользователя',
    password: 'Пароль',
  },
  {
    lang: 'kk',
    title: 'Инспекция есебі',
    download: 'PDF жүктеп алу',
    preview: 'PDF алдын ала қарау',
    language: 'PDF тілі',
    coverage: 'Қамту есептелмеген',
    critical: 'Сындарлы',
    confirmed: 'Расталған',
    decider: 'Alan Bekov, расталды',
    signIn: 'Кіру',
    username: 'Пайдаланушы аты',
    password: 'Құпиясөз',
  },
] as const;

for (const locale of locales) {
  for (const theme of ['light', 'dark'] as const) {
    test(`report preview and fixture download: ${locale.lang}, ${theme}`, async ({ page }) => {
      // URL overrides must take precedence over saved language and system theme.
      await page.emulateMedia({ colorScheme: theme === 'light' ? 'dark' : 'light' });
      await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
      const requests = await mockApi(page);
      // Authenticate through the form and retain the requested report URL.
      await page.goto(`/inspections/37/report?theme=${theme}&lang=${locale.lang}`);
      await page.getByLabel(locale.username).fill('report-engineer');
      await page.getByLabel(locale.password).fill('report-test-session');
      await page.getByRole('button', { name: locale.signIn, exact: true }).click();
      await expect(page).toHaveURL(`/inspections/37/report?theme=${theme}&lang=${locale.lang}`);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('html')).toHaveAttribute('lang', locale.lang);
      expect(await page.evaluate(() => localStorage.getItem('qarau.lang'))).toBe('en');
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(locale.title);
      await page.evaluate(async () => {
        await document.fonts.ready;
        // Sign-in may have scrolled its form into view; inspect the report at its top.
        window.scrollTo(0, 0);
      });
      await expect(page.getByText('Tank T-104, north wall', { exact: true })).toHaveCount(2);
      const critical = page.getByRole('region', { name: locale.critical, exact: true });
      await expect(critical.getByText('Course 3, 2.1 m from the north seam')).toBeVisible();
      await expect(critical.getByText(locale.confirmed, { exact: true })).toBeVisible();
      await expect(critical.getByText(locale.decider, { exact: true })).toBeVisible();
      await expect(page.getByText('reference: Letter sheet, frame 18')).toBeVisible();
      await expect(page.getByText(locale.coverage, { exact: true })).toBeVisible();
      const languages = page.getByRole('group', { name: locale.language });
      await expect(languages.getByRole('button')).toHaveCount(3);
      await expect(
        page.getByRole('banner').getByRole('group', { name: locale.language }),
      ).toBeVisible();
      await expect(languages).toBeInViewport();
      const preview = page.locator('iframe');
      await expect(preview).toHaveAttribute('title', locale.preview);
      await expect(preview).toHaveAttribute('src', /^blob:/);
      // At the page top, an iframe below the findings fails this viewport check.
      await expect(preview).toBeInViewport();
      const previewBox = (await preview.boundingBox())!;
      expect(previewBox.y).toBeGreaterThanOrEqual(0);
      expect(previewBox.y + previewBox.height).toBeLessThanOrEqual(page.viewportSize()!.height);
      const hashLabel = page.getByTitle(hash, { exact: true });
      await expect(hashLabel).toHaveAttribute('title', hash);
      await expect(hashLabel).toHaveText('5e317abc851b…fef9fb6c');
      await expect(hashLabel).toBeInViewport();
      const downloadButton = page.getByRole('button', { name: locale.download, exact: true });
      const downloadBox = (await downloadButton.boundingBox())!;
      const hashBox = (await page.locator('.report-hash').boundingBox())!;
      expect(hashBox.y).toBeGreaterThanOrEqual(downloadBox.y);
      expect(hashBox.y + hashBox.height).toBeLessThanOrEqual(downloadBox.y + downloadBox.height);
      await expect(page.locator('.report-hash')).toHaveCSS('white-space', 'nowrap');
      await expect(page.locator('.report-hash')).toHaveCSS('text-overflow', 'ellipsis');
      await expect(downloadButton).toHaveCSS('white-space', 'nowrap');
      await preview.scrollIntoViewIfNeeded();
      await expect(downloadButton).toBeInViewport();
      await expect(downloadButton).toBeEnabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      if (page.viewportSize()!.width === 390)
        expect((await downloadButton.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      const downloadEvent = page.waitForEvent('download');
      await downloadButton.click();
      const download = await downloadEvent;
      expect(download.suggestedFilename()).toBe(`report-37-${locale.lang}.pdf`);
      expect(await download.failure()).toBeNull();
      // Literal known fixture size: never compute the expectation from the downloaded data.
      expect((await stat((await download.path())!)).size).toBe(606);
      expect(await readFile((await download.path())!)).toEqual(pdf);
      expect(requests).toEqual([locale.lang]);
      await expect(page.getByRole('alert')).toHaveCount(0);
    });
  }
}

test('named demo report resolves its API id, switches PDF language and shows unknown-id errors', async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem('qarau.lang', 'en');
    sessionStorage.setItem('qarau.auth.session', 'Bearer report-test-session');
  });
  const requests = await mockApi(page);
  await page.goto('/inspections');
  await expect(page.getByRole('link', { name: 'Report', exact: true })).toHaveAttribute(
    'href',
    '/inspections/37/report',
  );
  await page.goto('/viewer/demo');
  const reportLink = page.getByRole('link', { name: 'Report', exact: true });
  await expect(reportLink).toHaveAttribute('href', '/inspections/demo/report');
  await expect(reportLink).toBeInViewport();
  if (page.viewportSize()!.width === 390)
    expect((await reportLink.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await reportLink.click();
  await expect(page).toHaveURL('/inspections/demo/report');
  await expect(page.locator('iframe')).toHaveAttribute('src', /^blob:/);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.goto('/inspections/notre-dame/report');
  await expect(page.locator('iframe')).toHaveAttribute('src', /^blob:/);
  await page.getByRole('button', { name: 'Қазақша', exact: true }).click();
  await expect(page.locator('iframe')).toHaveAttribute('src', /^blob:/);
  await expect.poll(() => requests).toEqual(['en', 'en', 'kk']);
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download PDF', exact: true }).click();
  expect((await downloadEvent).suggestedFilename()).toBe('report-notre-dame-kk.pdf');
  await page.goto('/inspections/unknown/report');
  await expect(page.getByRole('alert')).toContainText('inspection not found');
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Download PDF', exact: true })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Download PDF', exact: true })).toBeDisabled();
  await expect(page.getByTitle(hash, { exact: true })).toHaveCount(0);
});
