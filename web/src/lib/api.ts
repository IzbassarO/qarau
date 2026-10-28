import { authHeader } from '../auth/auth';

/** HTTP failure from the authenticated API transport. */
export class ApiError extends Error {
  constructor(
    public readonly path: string,
    public readonly status?: number,
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

export async function readApi(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetchApi(path, signal);
  if ([404, 405, 501].includes(response.status))
    throw new ApiUnavailableError(path, response.status);
  if (!response.ok) throw new ApiError(path, response.status);
  // Vite's SPA fallback answers unknown API paths with index.html and HTTP 200.
  if (response.headers.get('Content-Type')?.includes('text/html'))
    throw new ApiUnavailableError(path);
  return response.json();
}

/** S1 reruns the named step and its successors; the API supplies all identifiers. */
export async function rerunInspectionStep(inspectionId: string, runId: string, step: string) {
  const response = await fetch(
    `/api/inspections/${encodeURIComponent(inspectionId)}/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(step)}/rerun`,
    { method: 'POST', headers: authHeader() },
  );
  if (!response.ok) throw new Error(String(response.status));
}
