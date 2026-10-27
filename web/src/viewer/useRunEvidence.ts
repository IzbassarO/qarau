import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiUnavailableError } from '../lib/api';
import { findingsForRunOptions } from '../lib/findings';
import { referenceEvidenceFor } from '../fixtures/evidence';

/** Shared by the geometry-loading page and the ready viewer; Query deduplicates reads. */
export function useRunEvidence(runId: string, requestedFinding: string | null, enabled = true) {
  const api = useQuery({
    ...findingsForRunOptions(runId),
    enabled: enabled && runId !== 'demo',
    // Reuse the response when the loading panel hands off to the full viewer.
    staleTime: 30_000,
  });
  const reference = referenceEvidenceFor(runId, requestedFinding);
  const preview =
    (!api.isError && api.data?.length === 0) || api.error instanceof ApiUnavailableError
      ? reference
      : undefined;
  const findings = useMemo(
    () => (api.data?.length ? api.data : preview ? [preview] : []),
    [api.data, preview],
  );
  return { api, reference, preview, findings };
}
