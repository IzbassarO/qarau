import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import * as upload from '../lib/upload';
import { NewInspectionDialog } from './NewInspectionDialog';

const sites = [
  { id: 1, org_id: 1, name: 'North site' },
  { id: 2, org_id: 1, name: 'Tank farm' },
];
const inspection = {
  id: 7,
  site_id: 1,
  mode: 'quick_scan' as const,
  status: 'created',
  created_at: '2026-10-23T00:00:00Z',
};

beforeEach(() => {
  setLang('en');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(sites)));
  vi.spyOn(upload, 'createInspection').mockResolvedValue(inspection);
  vi.spyOn(upload, 'uploadFile').mockResolvedValue({ runId: 'run-1' });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function chooseVideo(size = 10_485_760) {
  await screen.findByRole('option', { name: 'North site' });
  fireEvent.change(screen.getByLabelText('Site'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText(/Choose a video/), {
    target: {
      files: [new File([new Uint8Array(size)], 'survey.mp4', { type: 'video/mp4' })],
    },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Upload video' }));
}

it('loads the site selector from GET /api/sites and requires a site and video', async () => {
  render(<NewInspectionDialog onClose={() => {}} />);
  expect(screen.getByText('Loading sites…', { selector: 'p' })).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Upload video' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  expect(await screen.findByRole('option', { name: 'North site' })).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Tank farm' })).toBeTruthy();
  expect(fetch).toHaveBeenCalledWith(
    '/api/sites',
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(screen.queryByRole('alert')).toBeNull();
});

it('creates the inspection for the selected API site', async () => {
  const onUploaded = vi.fn();
  render(<NewInspectionDialog onClose={() => {}} onUploaded={onUploaded} />);
  await screen.findByRole('option', { name: 'Tank farm' });
  fireEvent.change(screen.getByLabelText('Site'), { target: { value: '2' } });
  expect((screen.getByLabelText('Site') as HTMLSelectElement).value).toBe('2');
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/sites', {
    signal: expect.any(AbortSignal),
    headers: {},
  });
  fireEvent.change(screen.getByLabelText(/Choose a video/), {
    target: { files: [new File(['video'], 'survey.mp4', { type: 'video/mp4' })] },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Upload video' }));
  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(7));
  expect(upload.createInspection).toHaveBeenCalledExactlyOnceWith(
    { siteId: 2, filename: 'survey.mp4', size: 5, contentType: 'video/mp4' },
    expect.any(AbortSignal),
  );
});

it('rejects a non-video file with a visible translated message', () => {
  render(<NewInspectionDialog onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText(/Choose a video/), {
    target: { files: [new File(['text'], 'notes.txt', { type: 'text/plain' })] },
  });
  expect(screen.getByRole('alert').textContent).toBe(
    'Choose a video file. This file type is not supported.',
  );
});

it('rejects an empty video, then clears the error for a non-empty video', async () => {
  render(<NewInspectionDialog onClose={() => {}} />);
  const input = screen.getByLabelText(/Choose a video/);
  fireEvent.change(input, {
    target: { files: [new File([], 'empty.mp4', { type: 'video/mp4' })] },
  });
  expect(screen.getByRole('alert').textContent).toContain('empty');
  fireEvent.change(input, {
    target: { files: [new File(['video'], 'survey.mp4', { type: 'video/mp4' })] },
  });
  await screen.findByRole('option', { name: 'North site' });
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText('Selected video: survey.mp4')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Site'), { target: { value: '1' } });
  expect((screen.getByRole('button', { name: 'Upload video' }) as HTMLButtonElement).disabled).toBe(
    false,
  );
});

it('shows 4.0 MB / 10.0 MB, 40 % and MB/s during upload, then cancels', async () => {
  let options: Parameters<typeof upload.uploadFile>[2];
  vi.mocked(upload.uploadFile).mockImplementation((_id, _file, value) => {
    options = value;
    return new Promise((_resolve, reject) =>
      value?.signal?.addEventListener('abort', () => reject(new upload.UploadError('cancelled'))),
    );
  });
  const onClose = vi.fn();
  render(<NewInspectionDialog onClose={onClose} />);
  await chooseVideo();
  await waitFor(() => expect(upload.uploadFile).toHaveBeenCalledOnce());
  act(() =>
    options?.onProgress?.({
      sentBytes: 4_194_304,
      totalBytes: 10_485_760,
      percent: 40,
      bytesPerSecond: 2_097_152,
    }),
  );
  expect(screen.getByText('4.0 MB / 10.0 MB')).toBeTruthy();
  expect(screen.getByText('40 %')).toBeTruthy();
  expect(screen.getByText('2.0 MB/s')).toBeTruthy();
  expect(screen.getByRole('progressbar').getAttribute('value')).toBe('40');
  expect((screen.getByLabelText('Site') as HTMLSelectElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel upload' }));
  expect(options?.signal?.aborted).toBe(true);
  expect(screen.getByRole('alert').textContent).toContain('Upload cancelled');
  expect(screen.getByRole('alert').textContent).toBe(
    'Upload cancelled. You can continue uploading this video or choose another file.',
  );
  expect(onClose).not.toHaveBeenCalled();
});

it.each([
  ['unsupported_type', 'Choose a video file. This file type is not supported.'],
  [
    'too_large',
    'This video is too large. Choose a file up to 10 GB, or below the server’s upload limit.',
  ],
  ['network', 'Connection lost. Check your connection, then retry to continue the upload.'],
  ['cancelled', 'Upload cancelled. You can continue uploading this video or choose another file.'],
  [
    'server',
    'The server could not accept the video. Check your access and try again, or contact your administrator.',
  ],
] as const)('shows the i18n message for %s', async (code, expected) => {
  vi.mocked(upload.uploadFile).mockRejectedValue(new upload.UploadError(code));
  render(<NewInspectionDialog onClose={() => {}} />);
  await chooseVideo();
  expect((await screen.findByRole('alert')).textContent).toBe(expected);
  if (code === 'network') expect(screen.getByRole('button', { name: 'Retry upload' })).toBeTruthy();
});

it('shows the literal unsupported_type message when creation is rejected with HTTP 415', async () => {
  vi.mocked(upload.createInspection).mockRejectedValue(
    new upload.UploadError('unsupported_type', 415),
  );
  const onUploaded = vi.fn();
  render(<NewInspectionDialog onClose={() => {}} onUploaded={onUploaded} />);
  await chooseVideo(5);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'Choose a video file. This file type is not supported.',
  );
  expect(upload.uploadFile).not.toHaveBeenCalled();
  expect(onUploaded).not.toHaveBeenCalled();
});

it('shows the literal too_large message after an HTTP 413 upload rejection', async () => {
  vi.mocked(upload.uploadFile).mockRejectedValue(new upload.UploadError('too_large', 413));
  const onUploaded = vi.fn();
  render(<NewInspectionDialog onClose={() => {}} onUploaded={onUploaded} />);
  await chooseVideo(5);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'This video is too large. Choose a file up to 10 GB, or below the server’s upload limit.',
  );
  expect(screen.getByText('Selected video: survey.mp4')).toBeTruthy();
  expect(onUploaded).not.toHaveBeenCalled();
});

it('shows the literal network message and Retry while keeping the selected site and video', async () => {
  vi.mocked(upload.uploadFile).mockRejectedValue(new upload.UploadError('network'));
  const onClose = vi.fn();
  render(<NewInspectionDialog onClose={onClose} />);
  await chooseVideo(5);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'Connection lost. Check your connection, then retry to continue the upload.',
  );
  expect(screen.getByRole('button', { name: 'Retry upload' }).textContent).toBe('Retry upload');
  expect((screen.getByLabelText('Site') as HTMLSelectElement).value).toBe('1');
  expect(screen.getByText('Selected video: survey.mp4')).toBeTruthy();
  expect(onClose).not.toHaveBeenCalled();
});

it('shows the literal server message for HTTP 403 and does not close or select an inspection', async () => {
  vi.mocked(upload.uploadFile).mockRejectedValue(new upload.UploadError('server', 403));
  const onClose = vi.fn();
  const onUploaded = vi.fn();
  render(<NewInspectionDialog onClose={onClose} onUploaded={onUploaded} />);
  await chooseVideo(5);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'The server could not accept the video. Check your access and try again, or contact your administrator.',
  );
  expect(onClose).not.toHaveBeenCalled();
  expect(onUploaded).not.toHaveBeenCalled();
});

it('rejects oversized files before creating an inspection', () => {
  render(<NewInspectionDialog onClose={() => {}} />);
  const file = new File(['video'], 'huge.mp4', { type: 'video/mp4' });
  Object.defineProperty(file, 'size', { value: 10_737_418_241 });
  fireEvent.change(screen.getByLabelText(/Choose a video/), { target: { files: [file] } });
  expect(screen.getByRole('alert').textContent).toContain('too large');
  expect(upload.createInspection).not.toHaveBeenCalled();
});

it('retries a network failure with the same inspection and session, then closes and selects it', async () => {
  vi.mocked(upload.uploadFile).mockRejectedValueOnce(new upload.UploadError('network'));
  const onClose = vi.fn();
  const onUploaded = vi.fn();
  render(<NewInspectionDialog onClose={onClose} onUploaded={onUploaded} />);
  await chooseVideo();
  fireEvent.click(await screen.findByRole('button', { name: 'Retry upload' }));
  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(7));
  expect(upload.createInspection).toHaveBeenCalledOnce();
  expect(upload.uploadFile).toHaveBeenCalledTimes(2);
  expect(vi.mocked(upload.uploadFile).mock.calls[0][2]?.session).toBe(
    vi.mocked(upload.uploadFile).mock.calls[1][2]?.session,
  );
  expect(onClose).toHaveBeenCalledOnce();
});

it('shows an empty-sites state and keeps Start disabled', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  render(<NewInspectionDialog onClose={() => {}} />);
  expect(
    await screen.findByText(
      'No sites available. Ask your administrator to create a site before uploading.',
    ),
  ).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Upload video' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows a site-load error and reloads sites on retry', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(Response.json(sites)),
  );
  render(<NewInspectionDialog onClose={() => {}} />);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'Could not load sites. Check your connection and try again.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(await screen.findByRole('option', { name: 'North site' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('renders file validation errors in Russian and Kazakh', () => {
  for (const [lang, expected] of [
    ['ru', 'Выберите видеофайл'],
    ['kk', 'Бейне файлды таңдаңыз'],
  ] as const) {
    setLang(lang);
    const view = render(<NewInspectionDialog onClose={() => {}} />);
    fireEvent.change(view.container.querySelector('input')!, {
      target: { files: [new File(['x'], 'bad.png', { type: 'image/png' })] },
    });
    expect(screen.getByRole('alert').textContent).toContain(expected);
    view.unmount();
  }
});

it.each(['escape', 'close', 'unmount'])('aborts an active transfer on %s', async (action) => {
  let signal: AbortSignal | undefined;
  vi.mocked(upload.uploadFile).mockImplementation((_id, _file, options) => {
    signal = options?.signal;
    return new Promise(() => {});
  });
  const onClose = vi.fn();
  const view = render(<NewInspectionDialog onClose={onClose} />);
  await chooseVideo();
  await waitFor(() => expect(upload.uploadFile).toHaveBeenCalledOnce());
  if (action === 'escape')
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { bubbles: true }));
  else if (action === 'close') fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  else view.unmount();
  expect(signal?.aborted).toBe(true);
  if (action !== 'unmount') expect(onClose).toHaveBeenCalledOnce();
});

it.each(['en', 'ru', 'kk'] as const)(
  'shows a calm unavailable state for the Vite catalog without inventing sites (%s)',
  async (lang) => {
    setLang(lang);
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response('<!doctype html><html></html>', {
            headers: { 'Content-Type': 'text/html' },
          }),
      ),
    );
    render(<NewInspectionDialog onClose={() => {}} />);
    const messages = {
      en: ['Site selection is unavailable', 'Upload video'],
      ru: ['Выбор площадки недоступен', 'Загрузить видео'],
      kk: ['Алаңды таңдау қолжетімсіз', 'Бейнені жүктеу'],
    };
    expect(await screen.findByRole('option', { name: messages[lang][0] })).toBeTruthy();
    expect((screen.getByRole('combobox') as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(
      (screen.getByRole('button', { name: messages[lang][1] }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(upload.createInspection).not.toHaveBeenCalled();
  },
);

it.each([401, 403, 503])('keeps site HTTP %i failures visible with Retry', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status })));
  render(<NewInspectionDialog onClose={() => {}} />);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'Could not load sites. Check your connection and try again.',
  );
  expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
});
