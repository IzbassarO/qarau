import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import type { Source, Stats } from './bundle';
import type { ProcessingRunPayload, ProcessingStagePayload } from './processing';
import { authHeader } from '../auth/auth';
import { listSites, type SiteOption } from './sites';
import { ApiUnavailableError, readApi } from './api';

export type InspectionStatus = 'uploaded' | 'queued' | 'running' | 'done' | 'failed' | 'unknown';

/** The v8 list contract; dates are ISO strings from InspectionOut.created_at. */
export interface InspectionSummary {
  id: string;
  siteName: string;
  date: string;
  status: Exclude<InspectionStatus, 'unknown'>;
  findingsCount: number | null;
  runId?: string;
}

/** Existing grouped catalog metadata, including read-only legacy exports. */
export interface SiteInspectionSummary {
  id: string;
  siteId: string;
  /** The run name (run id unless the API gives a title). */
  title: string;
  /** The uploaded file the run was made from, if known. */
  source: string | null;
  createdAt: string | null;
  status: InspectionStatus;
  /** Preserve a newer server's unfamiliar state verbatim with unknown styling. */
  reportedStatus?: string;
  runId?: string;
  /** Read-only exported run from a server without inspection CRUD. */
  legacyRun?: RunSummary;
}

export interface Site {
  id: string;
  /** Empty for unnamed/unassigned sites; their labels are translated by the UI. */
  name: string;
  inspections: SiteInspectionSummary[];
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
  stages?: readonly ProcessingStagePayload[] | null;
}

export const UNASSIGNED_SITE_ID = 'qarau:unassigned';

export function inspectionStatus(status?: string | null): InspectionStatus {
  switch (status) {
    case 'uploaded':
    case 'queued':
    case 'running':
    case 'done':
    case 'failed':
      return status;
    case 'completed':
      return 'done';
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
      title: run.title || run.run_id,
      source: run.source?.name || null,
      createdAt:
        run.created_at && Number.isFinite(Date.parse(run.created_at)) ? run.created_at : null,
      status: inspectionStatus(run.status),
      ...(run.status && inspectionStatus(run.status) === 'unknown'
        ? { reportedStatus: run.status }
        : {}),
    });
  }
  const newestFirst = (a: SiteInspectionSummary, b: SiteInspectionSummary) =>
    timestamp(b.createdAt) - timestamp(a.createdAt) || a.id.localeCompare(b.id);
  const groups = Array.from(sites.values());
  for (const site of groups) site.inspections.sort(newestFirst);
  return groups.sort((a, b) => newestFirst(a.inspections[0], b.inspections[0]));
}

export async function fetchRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  const response = await fetch('/api/runs', { signal, headers: authHeader() });
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

export interface InspectionPayload extends ProcessingRunPayload {
  id: number;
  site_id: number;
  mode: 'quick_scan' | 'monitoring';
  status: string;
  created_at: string;
  title?: string;
  run_id?: string | null;
  findings_count?: number | null;
}

export interface InspectionDetail extends InspectionPayload {
  runId?: string;
}

interface InspectionRun {
  id: string;
  status: string;
}

function isInspection(value: unknown): value is InspectionPayload {
  if (!value || typeof value !== 'object') return false;
  const item = value as InspectionPayload;
  return (
    Number.isInteger(item.id) && Number.isInteger(item.site_id) && typeof item.status === 'string'
  );
}

async function withRun(
  inspection: InspectionPayload,
  signal?: AbortSignal,
): Promise<InspectionDetail> {
  if (inspection.run_id && inspection.status !== 'created')
    return { ...inspection, runId: inspection.run_id };
  // B1 completes Run.status, leaving Inspection.status at "created". S1 takes precedence.
  if (inspection.status !== 'created' && inspectionStatus(inspection.status) !== 'done')
    return inspection;
  const runs = await readApi(`/api/inspections/${inspection.id}/runs`, signal);
  if (
    !Array.isArray(runs) ||
    !runs.every((run) => typeof run?.id === 'string' && typeof run.status === 'string')
  )
    throw new Error('Invalid inspection runs response');
  // UUID ordering is not chronology. Until the API identifies the current run, do not guess.
  const run: InspectionRun | undefined = inspection.run_id
    ? runs.find((run: InspectionRun) => run.id === inspection.run_id)
    : runs.length === 1
      ? runs[0]
      : undefined;
  return {
    ...inspection,
    status:
      inspection.status === 'created' ? (run?.status ?? inspection.status) : inspection.status,
    runId: inspection.run_id ?? run?.id,
  };
}

export async function getInspection(id: string, signal?: AbortSignal): Promise<InspectionDetail> {
  const inspection = await readApi(`/api/inspections/${encodeURIComponent(id)}`, signal);
  if (!isInspection(inspection)) throw new Error('Invalid inspection response');
  return withRun(inspection, signal);
}

export function groupInspectionsIntoSites(
  inspections: readonly InspectionDetail[],
  sites: readonly SiteOption[],
): Site[] {
  const groups = groupRunsIntoSites(
    inspections.map((inspection) => ({
      run_id: String(inspection.id),
      site_id: String(inspection.site_id),
      site_name: sites.find((site) => site.id === inspection.site_id)?.name,
      title: inspection.title,
      created_at: inspection.created_at,
      status: inspection.status,
    })),
  );
  for (const group of groups)
    for (const summary of group.inspections) {
      const detail = inspections.find((inspection) => String(inspection.id) === summary.id);
      summary.runId = detail?.runId ?? detail?.run_id ?? undefined;
    }
  return groups;
}

async function listLegacyInspections(signal?: AbortSignal): Promise<Site[]> {
  const runs = await fetchRuns(signal);
  const groups = groupRunsIntoSites(runs);
  for (const group of groups)
    for (const inspection of group.inspections) {
      inspection.legacyRun = runs.find((run) => run.run_id === inspection.id);
      inspection.runId = inspection.id;
    }
  return groups;
}

export async function listInspections(signal?: AbortSignal): Promise<Site[]> {
  let payload: unknown;
  try {
    payload = await readApi('/api/inspections', signal);
  } catch (error) {
    // Fall back only for an absent route, never for an auth, network or server failure.
    if (!(error instanceof ApiUnavailableError)) throw error;
    return listLegacyInspections(signal);
  }
  if (!Array.isArray(payload) || !payload.every(isInspection))
    throw new Error('Invalid inspections response');
  // A fresh B1 database can coexist with older filesystem exports served by /api/runs.
  if (payload.length === 0) return listLegacyInspections(signal);
  const [sites, inspections] = await Promise.all([
    listSites(signal).catch((error: unknown) => {
      if (error instanceof ApiUnavailableError) return [];
      throw error;
    }),
    Promise.all(payload.map((inspection) => withRun(inspection, signal))),
  ]);
  return groupInspectionsIntoSites(inspections, sites);
}

function groupedInspectionOptions(enabled: boolean) {
  return {
    queryKey: ['inspections'],
    queryFn: ({ signal }: { signal: AbortSignal }) => listInspections(signal),
    enabled,
    retry: false,
  };
}

/** Sort a copy, preserving API order for equal dates, and retain the uncapped count. */
export function sortAndCap(items: readonly InspectionSummary[], limit = 50) {
  const count = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : 50;
  return {
    rows: [...items].sort((a, b) => Date.parse(b.date) - Date.parse(a.date)).slice(0, count),
    total: items.length,
  };
}

/** The list uses inspection CRUD only; an empty list is authoritative. */
export async function fetchInspectionSummaries(signal?: AbortSignal): Promise<InspectionSummary[]> {
  const payload = await readApi('/api/inspections', signal);
  if (!Array.isArray(payload) || !payload.every(isInspection))
    throw new Error('Invalid inspections response');
  if (payload.length === 0) return [];
  const sites = await listSites(signal);
  return Promise.all(
    payload.map(async (item): Promise<InspectionSummary> => {
      // B1 leaves inspections as created after upload; S1 identifies the current run.
      const inspection: InspectionDetail =
        item.status === 'created' ? await getInspection(String(item.id), signal) : item;
      const status = inspectionStatus(inspection.status);
      if (
        status === 'unknown' ||
        !Number.isFinite(Date.parse(inspection.created_at)) ||
        (inspection.findings_count != null &&
          (!Number.isInteger(inspection.findings_count) || inspection.findings_count < 0))
      )
        throw new Error('Invalid inspection summary');
      return {
        id: String(inspection.id),
        siteName: sites.find((site) => site.id === inspection.site_id)?.name ?? '',
        date: inspection.created_at,
        status,
        findingsCount: inspection.findings_count ?? null,
        ...(inspection.run_id ? { runId: inspection.run_id } : {}),
        ...(inspection.runId ? { runId: inspection.runId } : {}),
      };
    }),
  );
}

export function useInspections(): UseQueryResult<InspectionSummary[], Error>;
/** Compatibility for the existing site catalog until start-page wiring in part 2. */
export function useInspections(enabled: boolean): UseQueryResult<Site[], Error>;
export function useInspections(
  enabled?: boolean,
): UseQueryResult<InspectionSummary[] | Site[], Error> {
  const grouped = enabled !== undefined;
  return useQuery<InspectionSummary[] | Site[]>({
    ...(grouped
      ? groupedInspectionOptions(enabled)
      : {
          queryKey: ['inspections', 'list'],
          queryFn: ({ signal }: { signal: AbortSignal }) => fetchInspectionSummaries(signal),
          retry: false,
        }),
    refetchInterval: (query) => {
      const data = query.state.data ?? [];
      const active = (inspection: { status: InspectionStatus }) =>
        ['uploaded', 'queued', 'running', 'unknown'].includes(inspection.status);
      return data.some((item) =>
        'inspections' in item ? item.inspections.some(active) : active(item),
      )
        ? 5000
        : false;
    },
  });
}
