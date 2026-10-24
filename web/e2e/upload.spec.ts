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
      let processing = false;
      let stored = 0;
      let continueUpload!: () => void;
      const chunkGate = new Promise<void>((resolve) => {
        continueUpload = resolve;
      });
      let finish!: () => void;
      const finishGate = new Promise<void>((resolve) => {
        finish = resolve;
      });
      await page.route('**/api/**', async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        expect(request.headers().authorization).toBe('Bearer test-api-value');
        // Sign-in validates the bearer session through the existing read-only runs route.
        if (url.pathname === '/api/runs') return route.fulfill({ json: [] });
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
        if (url.pathname === '/api/inspections/41')
          return route.fulfill({
            json: {
              ...inspection,
              status: processing ? 'running' : 'created',
              run_id: 'run-41',
              progress: processing ? 50 : 0,
              current_step: processing ? 'sparse' : null,
              steps: processing
                ? [
                    { name: 'probe', status: 'done', duration_s: 1 },
                    { name: 'frames', status: 'done', duration_s: 4.2 },
                    { name: 'sparse', status: 'running' },
                    { name: 'export', status: 'pending' },
                  ]
                : [],
            },
          });
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
          expect(request.postDataJSON()).toEqual({ name: 'fixture.mp4', size: 5_242_880 });
          return route.fulfill({ status: 201, json: { upload_id: 12, run_id: 'run-41' } });
        }
        if (url.pathname === '/api/inspections/41/video/chunks') {
          expect(url.searchParams.get('upload_id')).toBe('12');
          if (request.method() === 'PUT') {
            const offset = Number(url.searchParams.get('offset'));
            offsets.push(offset);
            expect(request.headers()['content-type']).toBe('application/octet-stream');
            if (offset === 4_194_304) await chunkGate;
            stored += request.postDataBuffer()!.length;
          }
          return route.fulfill({ json: { next_offset: stored } });
        }
        if (url.pathname === '/api/inspections/41/video/complete') {
          expect(request.postDataJSON()).toEqual({
            upload_id: 12,
            sha256: 'e2925fdd17c83c77e9a4f957fd170b2a72b48ac55968943dcf962f2b853c88a9',
          });
          await finishGate;
          uploaded = true;
          return route.fulfill({
            json: {
              run_id: 'run-41',
              file: 'video.mp4',
              size: 5_242_880,
              sha256: request.postDataJSON().sha256,
            },
          });
        }
        throw new Error(`Unexpected upload request: ${request.method()} ${url.pathname}`);
      });
      await page.goto('/?new-inspection');
      await page.getByLabel(t['auth.username']).fill('test-operator');
      await page.getByLabel(t['auth.password']).fill('test-api-value');
      await page.getByRole('button', { name: t['auth.signIn'], exact: true }).click();
      const dialog = page.getByRole('dialog', { name: t['inspection.new'] });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByLabel(t['upload.site'])).toBeVisible();
      await expect(page.getByRole('alert')).toHaveCount(0);
      await dialog.getByLabel(t['upload.site']).selectOption('3');
      await dialog.locator('input[type="file"]').setInputFiles({
        name: 'fixture.mp4',
        mimeType: 'video/mp4',
        buffer: Buffer.alloc(5_242_880, 7),
      });
      await dialog.getByRole('button', { name: t['upload.start'] }).click();
      await expect(dialog.getByRole('progressbar')).toHaveAttribute('value', '80');
      await expect(dialog.getByText('80 %', { exact: true })).toBeVisible();
      await expect(dialog.getByText(t['upload.sending'])).toBeVisible();
      await expect(dialog.getByRole('button', { name: t['upload.cancel'] })).toBeVisible();
      await expect(dialog.getByLabel(t['upload.site'])).toBeDisabled();
      await expect(page.getByRole('alert')).toHaveCount(0);
      continueUpload();
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
      expect(offsets).toEqual([0, 4_194_304]);
      expect(stored).toBe(5_242_880);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      processing = true;
      const steps = page.getByRole('list', { name: t['inspection.processing'] });
      await expect(steps).toBeVisible();
      await expect(steps.getByText(t['stage.probe'])).toBeVisible();
      await expect(steps.getByText(t['stage.reconstruction'])).toBeVisible();
      await expect(steps.locator('[data-state="done"]')).toHaveCount(2);
      await expect(steps.locator('[data-state="running"]')).toHaveCount(1);
      await expect(steps.locator('[data-state="pending"]')).toHaveCount(1);
      await expect(page.getByRole('alert')).toHaveCount(0);
    });
  }
}
