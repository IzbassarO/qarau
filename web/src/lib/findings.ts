import { queryOptions } from '@tanstack/react-query';
import { ApiError, readApi, requestApi } from './api';
import type { Key } from '../i18n';

/** Read-only DEF1 findings contract, with effective status supplied by REV1. */
export type FindingClass = 'corrosion' | 'crack' | 'leak' | 'deformation' | 'coating' | 'other';
export type Severity = 'critical' | 'watch';
export type Polygon = readonly (readonly number[])[];

export type Verdict = 'confirm' | 'reject' | 'reclass';
export interface Decision {
  id: number;
  finding_id: number;
  user_id: number;
  user: string;
  verdict: Verdict;
  comment: string | null;
  new_class: FindingClass | null;
  new_severity: Severity | null;
  created_at: string;
}

export interface DecisionInput {
  verdict: Verdict;
  comment: string;
  new_class?: FindingClass;
}

export function decisionErrorKey(error: unknown): Key {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'decisions.error.session';
    if (error.status === 403) return 'decisions.error.permission';
    if (error.status === 404) return 'decisions.error.missing';
    if (error.status === 422) return 'decisions.error.validation';
  }
  return 'decisions.error.connection';
}

export async function getDecisions(findingId: number, signal?: AbortSignal): Promise<Decision[]> {
  return (await readApi(`/api/findings/${findingId}/decisions`, signal)) as Decision[];
}

export async function postDecision(findingId: number, input: DecisionInput): Promise<Decision> {
  return (await requestApi(`/api/findings/${findingId}/decisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      verdict: input.verdict,
      comment: input.comment,
      ...(input.new_class === undefined ? {} : { new_class: input.new_class }),
    }),
  })) as Decision;
}

/** EvidenceOut has no dimensions; use the run manifest or the natural image size. */
export interface EvidenceFrame {
  frame_id: number;
  image_url: string;
  /** Image pixel coordinates [u, v]. */
  outline: number[][];
  confidence: number;
}

export interface Finding {
  id: number;
  inspection_id: number;
  run_id: string;
  class: FindingClass;
  severity: Severity;
  /** OpenAPI declares a string, including proposed and decision-derived states. */
  status: string;
  comment: string | null;
  area_units: number;
  area_m2: number | null;
  scale: string;
  point_indices: number[];
  points: number[][];
  evidence: EvidenceFrame[];
}

export const findingKeys = {
  all: ['findings'] as const,
  inspection: (id: number) => ['findings', 'inspection', id] as const,
  run: (runId: string) => ['findings', 'run', runId] as const,
  detail: (id: number) => ['findings', 'detail', id] as const,
  decisions: (id: number) => ['decisions', id] as const,
};

export const decisionsOptions = (id: number) =>
  queryOptions({
    queryKey: findingKeys.decisions(id),
    queryFn: ({ signal }) => getDecisions(id, signal),
    retry: false,
  });

async function listInspectionFindings(
  inspectionId: number,
  signal?: AbortSignal,
): Promise<Finding[]> {
  return (await readApi(`/api/inspections/${inspectionId}/findings`, signal)) as Finding[];
}

export async function getFinding(id: number, signal?: AbortSignal): Promise<Finding> {
  return (await readApi(`/api/findings/${id}`, signal)) as Finding;
}

/** Viewer URLs use run_dir; Finding.run_id is the database ID. Resolve both via RunOut.
 * There is no GET /runs/:id/findings in DEF1. Never infer an inspection from a filename.
 */
export async function listFindings(runId: string, signal?: AbortSignal): Promise<Finding[]> {
  const inspections = (await readApi('/api/inspections', signal)) as {
    id: number;
    run_id?: string | null;
  }[];
  // S1 catalogs may already name the requested database run. Avoid fetching the
  // run history of every unrelated inspection when this association is explicit.
  const known = inspections.filter((inspection) => inspection.run_id === runId);
  if (known.length) {
    const findings = await Promise.all(known.map(({ id }) => listInspectionFindings(id, signal)));
    return findings.flat().filter((finding) => finding.run_id === runId);
  }
  const runs = await Promise.all(
    inspections.map(async ({ id }) => {
      const rows = (await readApi(`/api/inspections/${id}/runs`, signal)) as {
        id: string;
        inspection_id: number;
        run_dir: string;
      }[];
      return rows.filter((run) => run.id === runId || run.run_dir === runId);
    }),
  );
  const matches = runs.flat();
  const findings = await Promise.all(
    [...new Set(matches.map((run) => run.inspection_id))].map((id) =>
      listInspectionFindings(id, signal),
    ),
  );
  return findings.flat().filter((finding) => matches.some((run) => run.id === finding.run_id));
}

export const findingsForRunOptions = (runId: string) =>
  queryOptions({
    queryKey: findingKeys.run(runId),
    queryFn: ({ signal }) => listFindings(runId, signal),
    retry: false,
    retryOnMount: false,
    refetchOnMount: false,
  });

export const findingOptions = (id: number) =>
  queryOptions({
    queryKey: findingKeys.detail(id),
    queryFn: ({ signal }) => getFinding(id, signal),
    staleTime: 30_000,
    retry: false,
    retryOnMount: false,
    refetchOnMount: false,
  });

/** A metric area is meaningful only when the API also identifies its scale source. */
export function formatArea(
  finding: Pick<Finding, 'area_m2' | 'area_units' | 'scale'>,
  t: (key: Key) => string,
): string {
  if (finding.area_m2 !== null && finding.scale !== 'not set')
    return `${finding.area_m2.toFixed(1)} ${t('evidence.squareMetres')}`;
  return `${finding.area_units.toFixed(1)} ${t('evidence.areaUnitsWithoutScale')}`;
}
