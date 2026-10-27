import fixture from '../test/fixtures/finding.json';
import type { Finding } from '../lib/findings';

/** The task's reference bundle has an explicit evidence preview deep link.
 * Bundle the sample so previews do not depend on server roots, Referer or API origin.
 * Call only after an empty/unsupported API response; real observations take precedence.
 */
export function referenceEvidenceFor(
  runId: string,
  requestedFinding: string | null,
): Finding | undefined {
  if (runId !== 'notre-dame-p3e') return undefined;
  if (requestedFinding !== 'first' && requestedFinding !== String(fixture.finding.id))
    return undefined;
  return fixture.finding as Finding;
}
