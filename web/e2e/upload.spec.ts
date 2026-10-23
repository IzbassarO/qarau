import { expect, test } from '@playwright/test';
import en from '../src/i18n/en.json' with { type: 'json' };
import ru from '../src/i18n/ru.json' with { type: 'json' };
import kk from '../src/i18n/kk.json' with { type: 'json' };

for (const theme of ['light', 'dark'] as const) {
  for (const [lang, t] of Object.entries({ en, ru, kk })) {
    test(`uploads an in-memory video and lists the uploaded inspection: ${lang}, ${theme}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.addInitScript((locale) => {
        localStorage.setItem('qarau.lang', locale);
        sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
      }, lang);
      const inspection = {
        id: 41,
        site_id: 3,
        mode: 'quick_scan',
        status: 'created',
        created_at: '2026-10-23T12:00:00Z',
      };
      const offsets: number[] = [];
      let created = false;
      let uploaded = false;
      let stored = 0;
      let finish!: () => void;
      const finishGate = new Promise<void>((resolve) => {
        finish = resolve;
      });
      await page.route('**/api/**', async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        expect(request.headers().authorization).toBe('Bearer test-api-value');
        if (url.pathname === '/api/auth') return route.fulfill({ json: { role: 'engineer' } });
        if (url.pathname === '/api/sites')
          return route.fulfill({ json: [{ id: 3, org_id: 1, name: 'North site' }] });
        if (url.pathname === '/api/inspections') {
          if (request.method() === 'POST') {
            expect(request.postDataJSON()).toEqual({ site_id: 3, mode: 'quick_scan' });
            created = true;
            return route.fulfill({ status: 201, json: inspection });
          }
          return route.fulfill({ json: created ? [inspection] : [] });
        }
        if (url.pathname === '/api/inspections/41') return route.fulfill({ json: inspection });
        if (url.pathname === '/api/inspections/41/runs')
          return route.fulfill({
            json: [
              {
                id: 'run-41',
                inspection_id: 41,
                run_dir: 'run-41',
                status: uploaded ? 'uploaded' : 'uploading',
                progress: 0,
              },
            ],
          });
        if (url.pathname === '/api/inspections/41/video') {
          expect(request.postDataJSON()).toEqual({ name: 'tiny.mp4', size: 307_200 });
          return route.fulfill({ status: 201, json: { upload_id: 12, run_id: 'run-41' } });
        }
        if (url.pathname === '/api/inspections/41/video/chunks') {
          expect(url.searchParams.get('upload_id')).toBe('12');
          if (request.method() === 'PUT') {
            offsets.push(Number(url.searchParams.get('offset')));
            expect(request.headers()['content-type']).toBe('application/octet-stream');
            stored += request.postDataBuffer()!.length;
          }
          return route.fulfill({ json: { next_offset: stored } });
        }
        if (url.pathname === '/api/inspections/41/video/complete') {
          expect(request.postDataJSON()).toEqual({
            upload_id: 12,
            sha256: '1aa95500ae5e4a46b5a00c5a36346e2a4b22714fdc7251cb70bbc83a128cba79',
          });
          await finishGate;
          uploaded = true;
          return route.fulfill({
            json: {
              run_id: 'run-41',
              file: 'video.mp4',
              size: 307_200,
              sha256: request.postDataJSON().sha256,
            },
          });
        }
        throw new Error(`Unexpected upload request: ${request.method()} ${url.pathname}`);
      });
      await page.goto('/?new-inspection');
      const dialog = page.getByRole('dialog', { name: t['inspection.new'] });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByLabel(t['upload.site'])).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
      await dialog.getByLabel(t['upload.site']).selectOption('3');
      await dialog.locator('input[type="file"]').setInputFiles({
        name: 'tiny.mp4',
        mimeType: 'video/mp4',
        buffer: Buffer.alloc(307_200, 7),
      });
      await dialog.getByRole('button', { name: t['upload.start'] }).click();
      await expect(dialog.getByRole('progressbar')).toHaveAttribute('value', '100');
      await expect(dialog.getByText('100 %', { exact: true })).toBeVisible();
      await expect(dialog.getByText(t['upload.finishing'])).toBeVisible();
      finish();
      await expect(dialog).not.toBeVisible();
      await expect(page).toHaveURL('/inspections/41');
      const entry = page
        .getByRole('navigation')
        .getByRole('link', { name: new RegExp(`${t['shell.inspection']} 41`) });
      await expect(entry).toBeVisible();
      await expect(entry.getByText(t['status.uploaded'], { exact: true })).toBeVisible();
      await expect(entry).toHaveAttribute('aria-current', 'page');
      await expect(page.getByText(t['processing.uploadedWaiting'])).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
      expect(offsets).toEqual([0]);
      expect(stored).toBe(307_200);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    });
  }
}
