import type { Lang } from '../i18n';
import { authHeader } from '../auth/auth';

/** HTTP failure from the authenticated API transport. */
export class ApiError extends Error {
  constructor(
    public readonly path: string,
    public readonly status?: number,
    public readonly detail?: string,
  ) {
    // Keep the status message used by existing authentication/error views.
    super(status === undefined ? path : String(status));
    this.name = 'ApiError';
  }
}

/** Older servers and the Vite run catalog do not implement inspection CRUD. */
export class ApiUnavailableError extends ApiError {}

/** Raw response access for authenticated binary endpoints such as inspection PDFs. */
export function fetchApi(path: string, signal?: AbortSignal): Promise<Response> {
  return fetch(path, { signal, headers: authHeader() });
}

/** FastAPI's own error words, including request-validation messages. */
export async function apiErrorMessage(response: Response): Promise<string> {
  const body = await response.text();
  if (!body.trim() || response.headers.get('Content-Type')?.includes('text/html')) return '';
  try {
    const payload: unknown = JSON.parse(body);
    if (payload && typeof payload === 'object' && 'detail' in payload) {
      const detail: unknown = payload.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail))
        return detail
          .map((item: { msg?: unknown }) => (typeof item?.msg === 'string' ? item.msg : ''))
          .filter(Boolean)
          .join('\n');
    }
  } catch {
    // Unhandled server failures can be plain text rather than FastAPI JSON.
    return body;
  }
  return '';
}

export async function requestApi(path: string, options: RequestInit = {}): Promise<unknown> {
  const response = await fetch(path, {
    ...options,
    headers: { ...authHeader(), ...options.headers },
  });
  if ([404, 405, 501].includes(response.status))
    throw new ApiUnavailableError(path, response.status, await apiErrorMessage(response));
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      path,
      response.status,
      typeof body?.detail === 'string' ? body.detail : undefined,
    );
  }
  // Vite's SPA fallback answers unknown API paths with index.html and HTTP 200.
  if (response.headers.get('Content-Type')?.includes('text/html'))
    throw new ApiUnavailableError(path);
  return response.json();
}

export async function readApi(path: string, signal?: AbortSignal): Promise<unknown> {
  return requestApi(path, { signal });
}

/** S1 reruns the named step and its successors; the API supplies all identifiers. */
export async function rerunInspectionStep(inspectionId: string, runId: string, step: string) {
  const response = await fetch(
    `/api/inspections/${encodeURIComponent(inspectionId)}/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(step)}/rerun`,
    { method: 'POST', headers: authHeader() },
  );
  if (!response.ok) throw new Error(String(response.status));
}

export type ReportPdf =
  | { state: 'generating' }
  | { state: 'ready'; blob: Blob; hash: string }
  | { state: 'failed'; message: string };

export class ReportApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Authenticated binary report transport. The server supplies the body hash. */
export async function fetchReportPdf(
  id: string | number,
  lang: Lang,
  signal?: AbortSignal,
): Promise<ReportPdf> {
  try {
    const response = await fetchApi(
      `/api/inspections/${encodeURIComponent(id)}/report.pdf?lang=${lang}`,
      signal,
    );
    if (response.status === 202) return { state: 'generating' };
    if (!response.ok) throw new ReportApiError(await apiErrorMessage(response), response.status);
    const hash = response.headers.get('X-Report-SHA256');
    if (!hash || !response.headers.get('Content-Type')?.includes('application/pdf'))
      return { state: 'failed', message: '' };
    return { state: 'ready', blob: await response.blob(), hash };
  } catch (error) {
    if (signal?.aborted || error instanceof ReportApiError) throw error;
    return { state: 'failed', message: '' };
  }
}
