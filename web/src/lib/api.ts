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

export async function requestApi(
  path: string,
  options: RequestInit & { responseType?: 'response' } = {},
): Promise<unknown> {
  const { responseType, ...requestOptions } = options;
  const response = await fetch(path, {
    ...requestOptions,
    headers: { ...authHeader(), ...options.headers },
  });
  // Reports need the PDF bytes and X-Report-SHA256 header, rather than JSON.
  if (responseType === 'response') return response;
  if ([404, 405, 501].includes(response.status))
    throw new ApiUnavailableError(path, response.status);
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
