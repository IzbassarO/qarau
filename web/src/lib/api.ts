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
