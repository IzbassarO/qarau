import { ApiError, ApiUnavailableError, readApi, requestApi } from './api';
import type { Lang } from '../i18n';
import type { Finding } from './findings';

export interface ReportDecision {
  id: number;
  user: string;
  verdict: 'confirm' | 'reject' | 'reclass';
}

export interface ReportFinding extends Finding {
  decisions: ReportDecision[];
}

export interface ReportSummary {
  inspectionId: number;
  object: string | null;
  date: string;
  findings: ReportFinding[];
  /** API coverage prose or COV1-b totals. */
  coverage: string | InspectionCoverage | null;
}

interface Inspection {
  id: number;
  site_id: number;
  created_at: string;
}

async function json<T>(path: string, signal?: AbortSignal): Promise<T> {
  try {
    return (await readApi(path, signal)) as T;
  } catch (error) {
    if (error instanceof ApiError)
      throw new ReportApiError(error.detail ?? error.message, error.status ?? 0);
    throw error;
  }
}

/** Resolve the API inspection associated with a viewer run. */
export async function reportInspectionForRun(runId: string, signal?: AbortSignal) {
  const inspections = await json<{ id: number; run_id?: string | null }[]>(
    '/api/inspections',
    signal,
  );
  const known = inspections.find((inspection) => inspection.run_id === runId);
  if (known) return known.id;
  const runs = (
    await Promise.all(
      inspections.map(({ id }) =>
        json<{ id: string; run_dir: string; inspection_id: number }[]>(
          `/api/inspections/${id}/runs`,
          signal,
        ),
      ),
    )
  ).flat();
  const exact = runs.find((run) => run.id === runId || run.run_dir === runId);
  if (exact) return exact.inspection_id;
  // The same named demo compatibility route as inspectionForReport below.
  return runId === 'notre-dame'
    ? (runs.find((run) => run.id === 'notre-dame-p3e' || run.run_dir === 'notre-dame-p3e')
        ?.inspection_id ?? null)
    : null;
}

async function inspectionForReport(id: string, signal?: AbortSignal): Promise<Inspection> {
  try {
    return await json<Inspection>(`/api/inspections/${encodeURIComponent(id)}`, signal);
  } catch (error) {
    if (
      !(error instanceof ReportApiError) ||
      ![404, 422].includes(error.status) ||
      /^\d+$/.test(id)
    )
      throw error;
    // Named routes resolve through the seeded run's API association.
    const inspectionId = await reportInspectionForRun(
      id === 'demo' || id === 'notre-dame' ? 'notre-dame-p3e' : id,
      signal,
    );
    if (inspectionId === null) throw error;
    return json<Inspection>(`/api/inspections/${inspectionId}`, signal);
  }
}

export async function fetchReportSummary(id: string, signal?: AbortSignal): Promise<ReportSummary> {
  const inspection = await inspectionForReport(id, signal);
  const [sites, findings, coverage] = await Promise.all([
    json<{ id: number; name: string }[]>('/api/sites', signal),
    json<Finding[]>(`/api/inspections/${inspection.id}/findings`, signal),
    getInspectionCoverage(inspection.id, signal),
  ]);
  return {
    inspectionId: inspection.id,
    object: sites.find((site) => site.id === inspection.site_id)?.name ?? null,
    date: inspection.created_at,
    findings: await Promise.all(
      findings.map(async (finding) => ({
        ...finding,
        decisions: await json<ReportDecision[]>(`/api/findings/${finding.id}/decisions`, signal),
      })),
    ),
    coverage,
  };
}

/** COV1-b percentages are supplied by the API, never inferred from findings. */
export interface InspectionCoverage {
  computed: boolean;
  totals: Record<'inspected_ok' | 'finding' | 'not_seen', number>;
  regions: { id: string; state: 'inspected_ok' | 'finding' | 'not_seen' }[];
}

async function getInspectionCoverage(id: number, signal?: AbortSignal) {
  const response = (await requestApi(`/api/inspections/${id}/coverage`, {
    signal,
    responseType: 'response',
  })) as Response;
  if (response.status === 404 || response.status === 204) return null;
  if (!response.ok) throw new ReportApiError(await apiErrorMessage(response), response.status);
  const body = await response.text();
  if (!body.trim()) return null;
  const coverage = JSON.parse(body) as InspectionCoverage | null;
  return coverage?.computed && coverage.totals && coverage.regions?.length ? coverage : null;
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

async function apiErrorMessage(response: Response): Promise<string> {
  const body = await response.text();
  if (!body.trim() || response.headers.get('Content-Type')?.includes('text/html')) return '';
  try {
    const payload = JSON.parse(body) as { detail?: unknown };
    if (typeof payload?.detail === 'string') return payload.detail;
    if (Array.isArray(payload?.detail))
      return payload.detail
        .map((item: { msg?: unknown }) => (typeof item?.msg === 'string' ? item.msg : ''))
        .filter(Boolean)
        .join('\n');
  } catch {
    return body;
  }
  return '';
}

/** RPT1 returns application/pdf with the body hash in X-Report-SHA256. */
export async function fetchReportPdf(
  id: string | number,
  lang: Lang,
  signal?: AbortSignal,
): Promise<ReportPdf> {
  try {
    const response = (await requestApi(
      `/api/inspections/${encodeURIComponent(id)}/report.pdf?lang=${lang}`,
      { signal, responseType: 'response' },
    )) as Response;
    if (response.status === 202) return { state: 'generating' };
    if (!response.ok) throw new ReportApiError(await apiErrorMessage(response), response.status);
    const hash = response.headers.get('X-Report-SHA256');
    if (!hash || !response.headers.get('Content-Type')?.includes('application/pdf'))
      return { state: 'failed', message: '' };
    return { state: 'ready', blob: await response.blob(), hash };
  } catch (error) {
    if (signal?.aborted || error instanceof ReportApiError || error instanceof ApiUnavailableError)
      throw error;
    return { state: 'failed', message: '' };
  }
}
