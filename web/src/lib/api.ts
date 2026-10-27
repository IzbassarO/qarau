import { authHeader } from '../auth/auth';

/** Older servers and the Vite run catalog do not implement inspection CRUD. */
export class ApiUnavailableError extends Error {}

export async function readApi(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, { signal, headers: authHeader() });
  if ([404, 405, 501].includes(response.status)) throw new ApiUnavailableError(path);
  if (!response.ok) throw new Error(String(response.status));
  // Vite's SPA fallback answers unknown API paths with index.html and HTTP 200.
  if (response.headers.get('Content-Type')?.includes('text/html'))
    throw new ApiUnavailableError(path);
  return response.json();
}

export async function postApi(path: string, body: unknown, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, {
    method: 'POST',
    signal,
    headers: { ...authHeader(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(String(response.status));
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
