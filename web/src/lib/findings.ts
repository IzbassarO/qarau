import { queryOptions } from '@tanstack/react-query';
import { postApi, readApi } from './api';

/** DEF1 / REV1 OpenAPI vocabulary; roles are the backend's viewer < engineer < admin. */
export type FindingClass = 'corrosion' | 'crack' | 'leak' | 'deformation' | 'coating' | 'other';
export type Severity = 'critical' | 'watch';
export type Role = 'viewer' | 'engineer' | 'admin';
export type Verdict = 'confirm' | 'reject' | 'reclass';
export type Polygon = readonly (readonly number[])[];

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

export interface DecisionIn {
  verdict: Verdict;
  comment?: string | null;
  new_class?: FindingClass | null;
  new_severity?: Severity | null;
}

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

export const findingKeys = {
  all: ['findings'] as const,
  inspection: (id: number) => ['findings', 'inspection', id] as const,
  run: (runId: string) => ['findings', 'run', runId] as const,
  detail: (id: number) => ['findings', 'detail', id] as const,
  decisions: (id: number) => ['findings', 'detail', id, 'decisions'] as const,
};

export async function listFindings(inspectionId: number, signal?: AbortSignal): Promise<Finding[]> {
  return (await readApi(`/api/inspections/${inspectionId}/findings`, signal)) as Finding[];
}

export async function getFinding(id: number, signal?: AbortSignal): Promise<Finding> {
  return (await readApi(`/api/findings/${id}`, signal)) as Finding;
}

export async function listDecisions(id: number, signal?: AbortSignal): Promise<Decision[]> {
  return (await readApi(`/api/findings/${id}/decisions`, signal)) as Decision[];
}

export async function postDecision(
  id: number,
  body: DecisionIn,
  signal?: AbortSignal,
): Promise<Decision> {
  return (await postApi(`/api/findings/${id}/decisions`, body, signal)) as Decision;
}

/** Viewer URLs use run_dir; Finding.run_id is the database ID. Resolve both via RunOut.
 * There is no GET /runs/:id/findings in DEF1. Never infer an inspection from a filename.
 */
export async function listFindingsForRun(runId: string, signal?: AbortSignal): Promise<Finding[]> {
  const inspections = (await readApi('/api/inspections', signal)) as { id: number }[];
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
    [...new Set(matches.map((run) => run.inspection_id))].map((id) => listFindings(id, signal)),
  );
  return findings.flat().filter((finding) => matches.some((run) => run.id === finding.run_id));
}

export const findingsForRunOptions = (runId: string) =>
  queryOptions({
    queryKey: findingKeys.run(runId),
    queryFn: ({ signal }) => listFindingsForRun(runId, signal),
    retry: false,
  });

export const findingOptions = (id: number) =>
  queryOptions({
    queryKey: findingKeys.detail(id),
    queryFn: ({ signal }) => getFinding(id, signal),
    retry: false,
  });

export const decisionsOptions = (id: number) =>
  queryOptions({
    queryKey: findingKeys.decisions(id),
    queryFn: ({ signal }) => listDecisions(id, signal),
    retry: false,
  });
