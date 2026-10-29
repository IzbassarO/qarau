import { readFileSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import { readDenseFixture } from '../src/fixtures/dense-bundle/readFixture';
import fixture from '../src/test/fixtures/report-summary.json' with { type: 'json' };
import coverageFixture from '../src/test/fixtures/report-coverage.json' with { type: 'json' };
import { reportJson } from '../src/test/reportRoutes';

// Original, hand-authored one-page PDF. No third-party document content.
const pdf = readFileSync(new URL('../src/test/fixtures/report.pdf', import.meta.url));
const hash = 'a278f21895176e40699b4de5dda3b7aa385e82e13995d4bab0beff341aa5d558';

async function authenticate(page: Page, lang = 'en') {
  await page.addInitScript((locale) => {
    localStorage.setItem('qarau.lang', locale);
    sessionStorage.setItem('qarau.auth.session', 'Bearer report-test-session');
  }, lang);
}

async function mockApi(page: Page) {
  const pdfLanguages: string[] = [];
  const { manifest, files } = readDenseFixture();
  await page.route('**/api/**', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    expect(request.headers().authorization).toBe('Bearer report-test-session');
    if (url.pathname === '/api/runs') return route.fulfill({ json: [] });
    if (url.pathname === '/api/runs/notre-dame/manifest.json')
      return route.fulfill({ json: { ...manifest, run_id: 'notre-dame' } });
    const bundleFile = files.get(url.pathname.replace('/api/runs/notre-dame/files/', ''));
    if (bundleFile)
      return route.fulfill({
        body: bundleFile,
        contentType: url.pathname.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream',
      });
    if (url.pathname === '/api/inspections/demo-id')
      return route.fulfill({ json: fixture.inspection });
    if (url.pathname === '/api/inspections/37/report.pdf') {
      pdfLanguages.push(url.searchParams.get('lang')!);
      return route.fulfill({
        body: pdf,
        contentType: 'application/pdf',
        headers: { 'X-Report-SHA256': hash },
      });
    }
    return route.fulfill(reportJson(url.pathname));
  });
  return pdfLanguages;
}

async function expectPainted(page: Page, lang = 'en') {
  await expect(page.locator('.report-state')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('iframe, embed, object')).toHaveCount(0);
  const canvas = page.locator('canvas.report-preview');
  await expect(canvas).toBeVisible();
  await expect(page.locator('.report-page-count')).toHaveText(
    { en: '1 of 1 pages', ru: 'Страница 1 из 1', kk: '1 беттің 1-беті' }[lang]!,
  );
  expect(
    await canvas.evaluate((element: HTMLCanvasElement) => {
      const context = element.getContext('2d')!;
      const pixels = context.getImageData(0, 0, element.width, element.height).data;
      // The PDF page is opaque white. Its text must paint darker pixels.
      for (let i = 0; i < pixels.length; i += 4)
        if (pixels[i + 3] > 0 && pixels[i] < 200 && pixels[i + 1] < 200 && pixels[i + 2] < 200)
          return true;
      return false;
    }),
  ).toBe(true);
  return canvas;
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
    reportLink: 'Report',
    summary: 'Summary',
    ready: 'Report is ready.',
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
    reportLink: 'Отчёт',
    summary: 'Сводка',
    ready: 'Отчёт готов.',
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
    reportLink: 'Есеп',
    summary: 'Қорытынды',
    ready: 'Есеп дайын.',
  },
] as const;

test('demo report shows API coverage and downloads the fixture PDF', async ({ page }) => {
  await authenticate(page);
  await mockApi(page);
  await page.route('**/api/inspections/37/coverage', (route) =>
    route.fulfill({ json: coverageFixture }),
  );
  await page.goto('/inspections/demo/report');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Inspection report');
  await expect(
    page.getByText('Inspected and clear: 61.5 %. Findings: 23.1 %. Not seen: 15.4 %.'),
  ).toBeVisible();
  await expect(page.getByText('Areas not seen: north-wall-upper')).toBeVisible();
  await expectPainted(page);
  await expect(page.getByTitle(hash, { exact: true })).toHaveText('a278f2189517…1aa5d558');
  const link = page.getByRole('link', { name: 'Download PDF', exact: true });
  await expect(link).toHaveAttribute('download', 'report-demo-en.pdf');
  const downloadEvent = page.waitForEvent('download');
  await link.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('report-demo-en.pdf');
  expect(await download.failure()).toBeNull();
  expect((await stat((await download.path())!)).size).toBe(619);
  expect(await readFile((await download.path())!)).toEqual(pdf);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

for (const locale of locales) {
  for (const theme of ['light', 'dark'] as const) {
    test(`report preview and fixture download: ${locale.lang}, ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme === 'light' ? 'dark' : 'light' });
      await page.addInitScript(() => localStorage.setItem('qarau.lang', 'en'));
      const requests = await mockApi(page);
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
        window.scrollTo(0, 0);
      });
      await expect(page.getByText('Tank T-104, north wall', { exact: true })).toHaveCount(2);
      const critical = page.getByRole('region', { name: locale.critical, exact: true });
      await expect(critical.getByText('Course 3, 2.1 m from the north seam')).toBeVisible();
      await expect(critical.getByText(locale.confirmed, { exact: true })).toBeVisible();
      await expect(critical.getByText(locale.decider, { exact: true })).toBeVisible();
      await expect(page.getByText('reference: Letter sheet, frame 18')).toBeVisible();
      await expect(page.getByText(locale.coverage, { exact: true })).toBeVisible();
      await expect(page.locator('.report-document')).toHaveCSS(
        'background-color',
        theme === 'light' ? 'rgb(255, 255, 255)' : 'rgb(38, 38, 38)',
      );

      const languages = page.getByRole('group', { name: locale.language });
      await expect(languages.getByRole('button')).toHaveCount(3);
      await expect(
        page.getByRole('banner').getByRole('group', { name: locale.language }),
      ).toBeVisible();
      await expect(languages).toBeInViewport();
      const preview = await expectPainted(page, locale.lang);
      await expect(preview).toHaveAttribute('aria-label', locale.preview);
      await expect(preview).toBeInViewport();
      const previewBox = (await preview.boundingBox())!;
      expect(previewBox.y).toBeGreaterThanOrEqual(0);
      expect(previewBox.y + previewBox.height).toBeLessThanOrEqual(page.viewportSize()!.height);
      const hashLabel = page.getByTitle(hash, { exact: true });
      await expect(hashLabel).toHaveAttribute('title', hash);
      await expect(hashLabel).toHaveText('a278f2189517…1aa5d558');
      await expect(hashLabel).toBeInViewport();
      expect(
        await page.locator('.report-hash').evaluate((el) => el.scrollWidth <= el.clientWidth),
      ).toBe(true);
      if (page.viewportSize()!.width === 390) {
        expect(previewBox.height).toBeLessThanOrEqual(192);
        await expect(page.getByRole('heading', { level: 1 })).toBeInViewport();
      }
      const downloadButton = page.getByRole('link', { name: locale.download, exact: true });
      await expect(downloadButton).toHaveAttribute('download', `report-37-${locale.lang}.pdf`);
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
      expect((await stat((await download.path())!)).size).toBe(619);
      expect(await readFile((await download.path())!)).toEqual(pdf);
      expect(requests).toEqual([locale.lang]);
      await expect(page.getByRole('alert')).toHaveCount(0);
    });

    test(`report reached from inspections: ${locale.lang}, ${theme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await authenticate(page, locale.lang);
      const requests = await mockApi(page);
      await page.goto('/inspections');
      await expect(page.locator('.inspections-start')).toHaveAttribute('aria-busy', 'false');
      await expect(page.locator('[data-inspection-id="37"]')).toBeVisible();
      const reportLink = page.getByRole('link', { name: locale.reportLink, exact: true });
      await expect(reportLink).toHaveAttribute('href', '/inspections/37/report');
      await expect(reportLink).toBeInViewport();
      await reportLink.click();
      await expect(page).toHaveURL('/inspections/37/report');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('html')).toHaveAttribute('lang', locale.lang);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(locale.title);
      const summary = page.getByRole('article', { name: locale.summary, exact: true });
      await expect(summary.getByText('Tank T-104, north wall', { exact: true })).toHaveCount(2);
      await expect(summary.getByText('reference: Letter sheet, frame 18')).toBeVisible();
      await expect(summary.getByText(locale.coverage, { exact: true })).toBeVisible();
      await expect(
        page.getByRole('group', { name: locale.language }).getByRole('button'),
      ).toHaveCount(3);
      await expect(page.getByRole('region', { name: locale.preview, exact: true })).toHaveAttribute(
        'aria-busy',
        'false',
      );
      await expect(page.getByRole('status')).toHaveText(locale.ready);
      await expectPainted(page, locale.lang);
      await expect(page.locator('canvas.report-preview')).toHaveAttribute(
        'aria-label',
        locale.preview,
      );
      await expect(page.locator('canvas.report-preview')).toBeInViewport();
      const languageButtons = page
        .getByRole('group', { name: locale.language })
        .getByRole('button');
      for (const button of await languageButtons.all()) await expect(button).toBeInViewport();
      const downloadLink = page.getByRole('link', { name: locale.download, exact: true });
      await expect(downloadLink).toHaveAttribute('download', `report-37-${locale.lang}.pdf`);
      await expect(downloadLink).toHaveAttribute('href', /^blob:/);
      await expect(downloadLink).toBeInViewport();
      await expect(page.getByTitle(hash, { exact: true })).toHaveText('a278f2189517…1aa5d558');
      await expect(page.getByTitle(hash, { exact: true })).toBeInViewport();
      await expect(page.getByRole('alert')).toHaveCount(0);
      await expect(page.locator('body')).not.toContainText(/\b(?:report|evidence)\.[a-zA-Z]/);
      expect(requests).toEqual([locale.lang]);
    });
  }
}

test('named demo report resolves its API id, switches PDF language and shows unknown-id errors', async ({
  page,
}) => {
  await authenticate(page);
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
  await expectPainted(page);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.goto('/inspections/notre-dame/report');
  await expectPainted(page);
  await page.getByRole('button', { name: 'Қазақша', exact: true }).click();
  await expectPainted(page);
  await expect.poll(() => requests).toEqual(['en', 'en', 'kk']);
  // Wait for the Kazakh Blob, not the previous language's preview.
  const downloadLink = page.getByRole('link', { name: 'Download PDF', exact: true });
  await expect(downloadLink).toHaveAttribute('download', 'report-notre-dame-kk.pdf');
  await expect(downloadLink).toHaveAttribute('href', /^blob:/);
  const downloadEvent = page.waitForEvent('download');
  await downloadLink.click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('report-notre-dame-kk.pdf');
  expect(await download.failure()).toBeNull();
  expect((await stat((await download.path())!)).size).toBe(619);
  expect(await readFile((await download.path())!)).toEqual(pdf);
  await page.goto('/inspections/unknown/report');
  await expect(page.getByRole('alert')).toContainText('inspection not found');
  await expect(page.locator('canvas.report-preview')).toHaveCount(0);
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Download PDF', exact: true })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Download PDF', exact: true })).toBeDisabled();
  await expect(page.getByTitle(hash, { exact: true })).toHaveCount(0);
});

test('inspection list and /viewer/notre-dame links open the same API inspection report', async ({
  page,
}) => {
  await authenticate(page);
  const requests = await mockApi(page);
  await page.goto('/inspections');
  const listLink = page.getByRole('link', { name: 'Report', exact: true });
  await expect(listLink).toHaveAttribute('href', '/inspections/37/report');
  await listLink.click();
  await expect(page).toHaveURL('/inspections/37/report');
  await expectPainted(page);
  await expect(page.getByText('Tank T-104, north wall', { exact: true })).toHaveCount(2);
  await expect(page.getByRole('alert')).toHaveCount(0);

  await page.goto('/viewer/notre-dame');
  const viewerLink = page.getByRole('link', { name: 'Report', exact: true });
  await expect(viewerLink).toHaveAttribute('href', '/inspections/37/report');
  await expect(viewerLink).toBeInViewport();
  await viewerLink.click();
  await expect(page).toHaveURL('/inspections/37/report');
  await expectPainted(page);
  await expect(page.getByTitle(hash, { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Download PDF', exact: true })).toHaveAttribute(
    'download',
    'report-37-en.pdf',
  );
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(requests).toEqual(['en', 'en']);
});

test('the default dev API renders the viewer demo report without fixture findings', async ({
  page,
}) => {
  await authenticate(page);
  await page.goto('/inspections/demo/report');
  await expectPainted(page);
  await expect(page.getByText('Tank farm №3', { exact: true })).toHaveCount(2);
  await expect(page.getByText('Coverage not computed', { exact: true })).toBeVisible();
  await expect(page.getByText('No findings', { exact: true })).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Download PDF', exact: true })).toHaveAttribute(
    'href',
    /^blob:/,
  );
  await expect(page.getByRole('alert')).toHaveCount(0);
});
