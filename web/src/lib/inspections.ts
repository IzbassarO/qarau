import { useQuery } from '@tanstack/react-query';
import type { Manifest, Source, Stats } from './bundle';

export type InspectionStatus = 'queued' | 'running' | 'completed' | 'failed' | 'unknown';

export interface InspectionSummary {
  id: string;
  siteId: string;
  title: string;
  createdAt: string | null;
  status: InspectionStatus;
}

export interface Site {
  id: string;
  /** Empty for unnamed/unassigned sites; their labels are translated by the UI. */
  name: string;
  inspections: InspectionSummary[];
}

export interface RunSummary {
  run_id: string;
  source?: Partial<Source> | null;
  stats?: Partial<Stats> | null;
  site?: { id: string; name?: string } | null;
  site_id?: string | null;
  site_name?: string | null;
  title?: string | null;
  created_at?: string | null;
  status?: string | null;
  stages?: { name: string; status: string }[];
}

export const UNASSIGNED_SITE_ID = 'qarau:unassigned';

export function inspectionStatus(status?: string | null): InspectionStatus {
  switch (status) {
    case 'queued':
    case 'running':
    case 'completed':
    case 'failed':
      return status;
    default:
      return 'unknown';
  }
}

const timestamp = (date: string | null) => (date ? Date.parse(date) : -Infinity);

/** Pure adapter: never invent site associations, dates or successful stages. */
export function groupRunsIntoSites(runs: readonly RunSummary[]): Site[] {
  const sites = new Map<string, Site>();
  for (const run of runs) {
    const siteId = run.site?.id || run.site_id || UNASSIGNED_SITE_ID;
    const name = run.site?.name || run.site_name || '';
    let site = sites.get(siteId);
    if (!site) {
      site = { id: siteId, name, inspections: [] };
      sites.set(siteId, site);
    } else if (!site.name && name) site.name = name;
    site.inspections.push({
      id: run.run_id,
      siteId,
      title: run.title || run.source?.name || run.run_id,
      createdAt:
        run.created_at && Number.isFinite(Date.parse(run.created_at)) ? run.created_at : null,
      status: inspectionStatus(run.status),
    });
  }
  const newestFirst = (a: InspectionSummary, b: InspectionSummary) =>
    timestamp(b.createdAt) - timestamp(a.createdAt) || a.id.localeCompare(b.id);
  const groups = Array.from(sites.values());
  for (const site of groups) site.inspections.sort(newestFirst);
  return groups.sort((a, b) => newestFirst(a.inspections[0], b.inspections[0]));
}

export async function fetchRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  const response = await fetch('/api/runs', { signal });
  if (!response.ok) throw new Error(String(response.status));
  const runs: unknown = await response.json();
  if (
    !Array.isArray(runs) ||
    !runs.every((run) => run && typeof run.run_id === 'string' && run.run_id.length > 0)
  )
    throw new Error('Invalid runs response');
  return runs as RunSummary[];
}

export function useRuns(enabled = true) {
  return useQuery({
    queryKey: ['runs'],
    queryFn: ({ signal }) => fetchRuns(signal),
    enabled,
    retry: false,
  });
}

export function processingSteps(run?: RunSummary, manifest?: Manifest) {
  const timings = run?.stats?.processing_s ?? manifest?.stats.processing_s ?? {};
  const steps = new Map(
    Object.keys(timings).map((name) => [name, { name, status: 'unknown' as InspectionStatus }]),
  );
  for (const stage of run?.stages ?? [])
    steps.set(stage.name, { name: stage.name, status: inspectionStatus(stage.status) });
  return Array.from(steps.values()).map((step) => ({ ...step, seconds: timings[step.name] }));
}
