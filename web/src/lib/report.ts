import { apiErrorMessage, fetchApi } from './api';
import type { Lang } from '../i18n';
import type { Finding, FindingClass, Severity } from './findings';

export type ReportPdf =
  | { state: 'generating' }
  | { state: 'ready'; blob: Blob; hash: string }
  | { state: 'failed'; message: string };

export interface ReportDecision {
  id: number;
  finding_id: number;
  user_id: number;
  user: string;
  verdict: 'confirm' | 'reject' | 'reclass';
  comment: string | null;
  new_class: FindingClass | null;
  new_severity: Severity | null;
  created_at: string;
}

export interface ReportFinding extends Finding {
  decisions: ReportDecision[];
}

export interface ReportSummary {
  inspectionId: number;
  object: string | null;
  date: string;
  findings: ReportFinding[];
  /** RPT1 has no coverage JSON endpoint yet. Never estimate it in the client. */
  coverage: string | null;
}

interface Inspection {
  id: number;
  site_id: number;
  created_at: string;
}

class ReportApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function json<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetchApi(path, signal);
  if (!response.ok) throw new ReportApiError(await apiErrorMessage(response), response.status);
  return response.json() as Promise<T>;
}

/** Resolve a viewer's run to an inspection, including inspections without findings. */
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
    // The tester's named route refers to seed.py's run, not an integer DB key.
    // Confirm the association returned by the API; never hard-code the inspection ID.
    const runName = id === 'demo' || id === 'notre-dame' ? 'notre-dame-p3e' : id;
    const inspections = await json<Inspection[]>('/api/inspections', signal);
    const runs = await Promise.all(
      inspections.map((inspection) =>
        json<{ id: string; run_dir: string; inspection_id: number }[]>(
          `/api/inspections/${inspection.id}/runs`,
          signal,
        ),
      ),
    );
    const match = runs.flat().find((run) => run.id === runName || run.run_dir === runName);
    if (!match) throw error;
    return json<Inspection>(`/api/inspections/${match.inspection_id}`, signal);
  }
}

export async function fetchReportSummary(id: string, signal?: AbortSignal): Promise<ReportSummary> {
  const inspection = await inspectionForReport(id, signal);
  const [sites, findings] = await Promise.all([
    json<{ id: number; name: string }[]>('/api/sites', signal),
    json<Finding[]>(`/api/inspections/${inspection.id}/findings`, signal),
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
    coverage: null,
  };
}

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
    if (!response.ok) return { state: 'failed', message: await apiErrorMessage(response) };
    const hash = response.headers.get('X-Report-SHA256');
    if (!hash || !response.headers.get('Content-Type')?.includes('application/pdf'))
      return { state: 'failed', message: '' };
    return { state: 'ready', blob: await response.blob(), hash };
  } catch (error) {
    if (signal?.aborted) throw error;
    return { state: 'failed', message: '' };
  }
}
