import { beforeEach, expect, it, vi } from 'vitest';
import { loadPdf } from './pdfPreview';

const pdfJs = vi.hoisted(() => ({ getDocument: vi.fn(), destroy: vi.fn(), render: vi.fn() }));
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: {}, getDocument: pdfJs.getDocument }));

beforeEach(() => {
  vi.clearAllMocks();
  pdfJs.destroy.mockResolvedValue(undefined);
});

it('closes the worker when a pending document is aborted, without exposing it for rendering', async () => {
  let finish!: (document: unknown) => void;
  pdfJs.getDocument.mockReturnValue({
    promise: new Promise((resolve) => (finish = resolve)),
    destroy: pdfJs.destroy,
  });
  const controller = new AbortController();
  const loading = loadPdf(new ArrayBuffer(0), controller.signal);
  controller.abort();
  expect(pdfJs.destroy).toHaveBeenCalledTimes(1);
  finish({ numPages: 3 });
  await expect(loading).rejects.toMatchObject({ name: 'AbortError' });
  expect(pdfJs.destroy).toHaveBeenCalledTimes(1);
});

it('does not paint a page that finishes loading after cancellation', async () => {
  let finish!: (page: unknown) => void;
  const getPage = vi.fn(() => new Promise((resolve) => (finish = resolve)));
  pdfJs.getDocument.mockReturnValue({
    promise: Promise.resolve({ numPages: 3, getPage }),
    destroy: pdfJs.destroy,
  });
  const controller = new AbortController();
  const preview = await loadPdf(new ArrayBuffer(0), controller.signal);
  const painting = preview.renderFirstPage(document.createElement('canvas'));
  controller.abort();
  finish({ render: pdfJs.render });
  await expect(painting).rejects.toMatchObject({ name: 'AbortError' });
  await preview.destroy();
  expect(pdfJs.render).not.toHaveBeenCalled();
  expect(pdfJs.destroy).toHaveBeenCalledTimes(1);
});
