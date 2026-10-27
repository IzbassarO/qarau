import { expect, it } from 'vitest';
import fixture from '../test/fixtures/finding.json';
import { referenceEvidenceFor } from './evidence';

it.each(['first', '701'])('includes the reference evidence for the explicit %s deep link', (id) => {
  expect(referenceEvidenceFor('notre-dame-p3e', id)).toEqual(fixture.finding);
});

it.each([
  ['notre-dame-p3e', null],
  ['notre-dame-p3e', '999'],
  ['another-run', 'first'],
  ['demo', '701'],
] as const)('does not invent reference evidence for %s?finding=%s', (run, id) => {
  expect(referenceEvidenceFor(run, id)).toBeUndefined();
});
