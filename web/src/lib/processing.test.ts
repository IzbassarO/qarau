import { describe, expect, it } from 'vitest';
import { LANGS } from '../i18n';
import { errorMessage, processingSteps, stepDuration, type StageStatus } from './processing';

describe('processingSteps', () => {
  it('maps pipeline StageResult fields and puts stages in pipeline order without mutating the run', () => {
    const run = {
      stages: [
        { name: 'export', status: 'pending' },
        { name: 'frames', status: 'ok', duration_s: 4.2 },
        {
          name: 'ingest',
          status: 'completed',
          started_at: '2026-10-14T05:30:00Z',
          finished_at: '2026-10-14T05:30:01Z',
        },
        {
          name: 'reconstruction',
          status: 'failed',
          error_code: 'too_few_registered_frames',
          detail: 'Only two frames matched.',
        },
      ],
    };
    const before = structuredClone(run);
    const steps = processingSteps(run);
    expect(steps.map(({ id, status }) => [id, status])).toEqual([
      ['ingest', 'done'],
      ['frames', 'done'],
      ['reconstruction', 'failed'],
      ['export', 'skipped'],
    ]);
    expect(steps[0]).toMatchObject({
      startedAt: '2026-10-14T05:30:00Z',
      finishedAt: '2026-10-14T05:30:01Z',
    });
    expect(steps[1].durationSeconds).toBe(4.2);
    expect(steps[2]).toMatchObject({
      errorCode: 'too_few_registered_frames',
      detail: 'Only two frames matched.',
    });
    expect(run).toEqual(before);
  });

  it.each<StageStatus>(['pending', 'running', 'done', 'failed', 'skipped'])(
    'preserves the %s state',
    (status) => {
      expect(processingSteps({ stages: [{ name: 'ingest', status }] })[0].status).toBe(status);
    },
  );

  it('keeps unknown statuses, stages and timing-only stages visible, including prototype-like names', () => {
    const steps = processingSteps({
      stages: [
        { name: 'frames', status: 'awaiting_gpu' },
        { name: 'new_stage', status: 'ok' },
        { name: 'toString', status: 'running' },
      ],
      stats: { processing_s: { future_stage: 9 } },
    });
    expect(steps).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'frames',
          status: 'unknown',
          reportedStatus: 'awaiting_gpu',
        }),
        expect.objectContaining({ id: 'new_stage', status: 'unknown', reportedStatus: 'ok' }),
        expect.objectContaining({ id: 'toString', status: 'unknown', reportedStatus: 'running' }),
        expect.objectContaining({ id: 'future_stage', status: 'unknown', durationSeconds: 9 }),
      ]),
    );
  });

  it('does not infer success from current API timings or a completed run', () => {
    const steps = processingSteps({
      status: 'completed',
      stats: { processing_s: { frames: 1, reconstruction: 5 } },
    });
    expect(steps.every((step) => step.status === 'unknown')).toBe(true);
    expect(steps[1].durationSeconds).toBe(1);
    expect(processingSteps().every((step) => step.status === 'unknown')).toBe(true);
  });

  it('uses manifest timings when the list API has no timings', () => {
    const steps = processingSteps(undefined, {
      stats: {
        frames_extracted: 3,
        frames_dropped: 0,
        frames_registered: 2,
        points: 8,
        processing_s: { reconstruction: 5 },
      },
    });
    expect(steps[2]).toMatchObject({ status: 'unknown', durationSeconds: 5 });
  });

  it('shows missing successors of a failure as not run and retains explicit unfamiliar states', () => {
    const steps = processingSteps({
      stages: [
        { name: 'frames', status: 'failed' },
        { name: 'export', status: 'new_status' },
      ],
    });
    expect(steps[2].status).toBe('skipped');
    expect(steps[3]).toMatchObject({ status: 'unknown', reportedStatus: 'new_status' });
  });

  it('normalizes queued runs and preserves completed or running retry results after an earlier failure', () => {
    expect(processingSteps({ status: 'queued' }).every((step) => step.status === 'pending')).toBe(
      true,
    );
    const steps = processingSteps({
      stages: [
        { name: 'ingest', status: 'failed' },
        { name: 'frames', status: 'running' },
        { name: 'reconstruction', status: 'done' },
      ],
    });
    expect(steps.map((step) => step.status)).toEqual(['failed', 'running', 'done', 'skipped']);
  });

  it('preserves string and structured pipeline errors', () => {
    expect(
      processingSteps({
        stages: [{ name: 'frames', status: 'failed', error: 'StageError: no frames' }],
      })[1].detail,
    ).toBe('StageError: no frames');
    expect(
      processingSteps({
        stages: [
          {
            name: 'reconstruction',
            status: 'failed',
            error: { code: 'timeout', detail: 'Worker limit reached.' },
          },
        ],
      })[2],
    ).toMatchObject({ errorCode: 'timeout', detail: 'Worker limit reached.' });
  });

  it('retains malformed and duplicate entries rather than crashing or hiding them', () => {
    const payload = JSON.parse(
      '{"stages":[null,{"name":"frames","status":42},{"name":"frames","status":"ok"}],"stats":{"processing_s":{"frames":-1}}}',
    );
    const steps = processingSteps(payload);
    expect(steps.filter((step) => step.id === 'frames')).toHaveLength(2);
    expect(steps.find((step) => step.id === '')?.status).toBe('unknown');
    expect(steps.find((step) => step.id === 'frames')?.durationSeconds).toBeUndefined();
    expect(() => processingSteps(JSON.parse('{"stages":{}}'))).not.toThrow();
  });
});

describe('errorMessage', () => {
  it.each([
    'video_unreadable',
    'too_few_frames',
    'too_few_registered_frames',
    'reconstruction_failed',
    'timeout',
    'unknown',
  ])('provides localized explanations and recovery advice for %s', (code) => {
    for (const locale of Object.values(LANGS)) {
      for (const key of Object.values(errorMessage(code)))
        expect(locale[key].trim().length).toBeGreaterThan(0);
    }
  });

  it.each(['a_future_error', '__proto__', 'toString', '', undefined])(
    'falls back for an unknown error code: %s',
    (code) => {
      expect(errorMessage(code)).toEqual(errorMessage('unknown'));
    },
  );
});

it('calculates durations safely and leaves unstarted stages without a duration', () => {
  const startedAt = '2026-10-14T05:30:00Z';
  const finishedAt = '2026-10-14T05:30:04Z';
  expect(stepDuration({ id: 'frames', status: 'done', startedAt, finishedAt }, 0)).toBe(4);
  expect(stepDuration({ id: 'frames', status: 'running', startedAt }, Date.parse(finishedAt))).toBe(
    4,
  );
  expect(stepDuration({ id: 'frames', status: 'failed', durationSeconds: 3.5 }, 0)).toBe(3.5);
  expect(stepDuration({ id: 'frames', status: 'done', startedAt: 'bad' }, 0)).toBeUndefined();
  expect(
    stepDuration({ id: 'frames', status: 'done', startedAt: finishedAt, finishedAt: startedAt }, 0),
  ).toBeUndefined();
  expect(stepDuration({ id: 'export', status: 'skipped', durationSeconds: 1 }, 0)).toBeUndefined();
});
