import type { InspectionSummary, Site } from '../lib/inspections';

/** Fixed chronology and counts, independent of the clock, locale and API. */
export const inspectionsFixture = [
  {
    id: '105',
    siteName: 'Pipe rack PR-2, bay 7',
    date: '2026-10-26T11:05:00Z',
    status: 'uploaded',
    findingsCount: null,
    runId: 'inspection-uploaded',
  },
  {
    id: '104',
    siteName: 'Tank T-104, north wall',
    date: '2026-10-26T09:41:00Z',
    status: 'queued',
    findingsCount: null,
    runId: 'inspection-queued',
  },
  {
    id: '103',
    siteName: 'Tank T-112, roof',
    date: '2026-10-25T16:22:00Z',
    status: 'running',
    findingsCount: 2,
    runId: 'inspection-running',
  },
  {
    id: '102',
    siteName: 'Separator V-301, shell',
    date: '2026-10-25T10:03:00Z',
    status: 'done',
    findingsCount: 4,
    runId: 'inspection-done',
  },
  {
    id: '101',
    siteName: 'Pipe rack PR-5, bays 12 to 14',
    date: '2026-10-17T15:48:00Z',
    status: 'failed',
    findingsCount: null,
    runId: 'inspection-failed',
  },
] as const satisfies readonly InspectionSummary[];

export const inspectionFixtureSites: Site[] = inspectionsFixture.map((inspection) => ({
  id: inspection.id,
  name: inspection.siteName,
  inspections: [
    {
      id: inspection.id,
      siteId: inspection.id,
      title: inspection.siteName,
      source: null,
      createdAt: inspection.date,
      status: inspection.status,
      runId: inspection.runId,
    },
  ],
}));
