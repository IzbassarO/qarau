import { useQuery } from '@tanstack/react-query';
import { findingsForRunOptions, type Finding } from '../lib/findings';

const noFindings: readonly Finding[] = [];

/** Shared by the geometry-loading page and the ready viewer. API emptiness is authoritative. */
export function useRunEvidence(runId: string, enabled = true) {
  const api = useQuery({
    ...findingsForRunOptions(runId),
    enabled: enabled && runId !== 'demo',
    staleTime: 30_000,
  });
  return { api, findings: api.data ?? noFindings };
}
