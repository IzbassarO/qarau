import { authHeader } from '../auth/auth';
import { parse, serialize, type Roi } from '../viewer/roi';
import { fetchRuns, UNASSIGNED_SITE_ID, type RunSummary } from './inspections';

const roiUrl = (siteId: string) => `/api/sites/${encodeURIComponent(siteId)}/roi`;

/** A missing site association belongs to this run, never the shared unassigned bucket. */
export function roiSiteId(runId: string, runs: readonly RunSummary[]): string {
  const run = runs.find((item) => item.run_id === runId);
  const siteId = run?.site?.id || run?.site_id;
  return siteId && siteId !== UNASSIGNED_SITE_ID ? siteId : runId;
}

/** Catalog failures must remain visible instead of silently creating a different site ROI. */
export async function resolveRoiSiteId(runId: string, signal?: AbortSignal): Promise<string> {
  return roiSiteId(runId, await fetchRuns(signal));
}

function validatedRoi(input: unknown, siteId: string): Roi {
  const result = parse(input);
  if (!result.ok) throw new Error(result.error);
  if (result.value.site_id !== siteId)
    throw new Error('ROI site_id does not match the requested site');
  return result.value;
}

/** Only an absent ROI is null. Network, authorization and malformed response errors propagate. */
export async function loadRoi(siteId: string, signal?: AbortSignal): Promise<Roi | null> {
  const response = await fetch(roiUrl(siteId), { signal, headers: authHeader() });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`ROI load failed: HTTP ${response.status}`);
  return validatedRoi(await response.json(), siteId);
}

export async function saveRoi(siteId: string, roi: Roi, signal?: AbortSignal): Promise<Roi> {
  const body = serialize(validatedRoi(roi, siteId));
  const response = await fetch(roiUrl(siteId), {
    method: 'PUT',
    signal,
    headers: { ...authHeader(), 'Content-Type': 'application/json' },
    body,
  });
  if (!response.ok) throw new Error(`ROI save failed: HTTP ${response.status}`);
  return validatedRoi(await response.json(), siteId);
}

export async function deleteRoi(siteId: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(roiUrl(siteId), {
    method: 'DELETE',
    signal,
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`ROI clear failed: HTTP ${response.status}`);
}
