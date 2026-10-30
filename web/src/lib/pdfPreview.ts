import { getDocument, GlobalWorkerOptions, type RenderTask } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export interface PdfPreview {
  numPages: number;
  renderFirstPage(canvas: HTMLCanvasElement): Promise<void>;
  destroy(): Promise<void>;
}

/** Load one preview document; aborting also stops its render and worker. */
export async function loadPdf(bytes: ArrayBuffer, signal?: AbortSignal): Promise<PdfPreview> {
  signal?.throwIfAborted();
  const loading = getDocument({ data: new Uint8Array(bytes) });
  let rendering: RenderTask | undefined;
  let destroyed: Promise<void> | undefined;
  function destroy() {
    if (!destroyed) {
      signal?.removeEventListener('abort', abort);
      rendering?.cancel();
      destroyed = loading.destroy().catch(() => {});
    }
    return destroyed;
  }
  function abort() {
    void destroy();
  }
  signal?.addEventListener('abort', abort, { once: true });
  try {
    const document = await loading.promise;
    signal?.throwIfAborted();
    return {
      numPages: document.numPages,
      async renderFirstPage(canvas) {
        const page = await document.getPage(1);
        signal?.throwIfAborted();
        const viewport = page.getViewport({ scale: 1.5 });
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        rendering = page.render({ canvas, viewport });
        try {
          await rendering.promise;
        } finally {
          rendering = undefined;
        }
      },
      destroy,
    };
  } catch (error) {
    await destroy();
    throw error;
  }
}
