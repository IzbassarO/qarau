import { ApiError, ReportApiError, apiErrorMessage, fetchApi } from './api';
import type { Finding } from './findings';
import { listInspectionFindings } from './findings';
import { getInspection, getInspectionCoverage, type InspectionCoverage } from './inspections';

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
  const response = await fetchApi(path, signal);
  if (!response.ok) throw new ReportApiError(await apiErrorMessage(response), response.status);
  return response.json() as Promise<T>;
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
    try {
      return await getInspection(id, signal);
    } catch (error) {
      if (error instanceof ApiError)
        throw new ReportApiError(error.detail ?? error.message, error.status ?? 0);
      throw error;
    }
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
    return getInspection(String(inspectionId), signal);
  }
}

export async function fetchReportSummary(id: string, signal?: AbortSignal): Promise<ReportSummary> {
  const inspection = await inspectionForReport(id, signal);
  const [sites, findings, coverage] = await Promise.all([
    json<{ id: number; name: string }[]>('/api/sites', signal),
    listInspectionFindings(inspection.id, signal),
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
