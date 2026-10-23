import type { Key } from '../i18n';
import type { Manifest } from './bundle';

export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface ProcessingStep {
  id: string;
  status: StageStatus | 'unknown';
  startedAt?: string;
  finishedAt?: string;
  durationSeconds?: number;
  errorCode?: string;
  detail?: string;
  /** Retain unrecognized API values so a newer pipeline is never silently hidden. */
  reportedStatus?: string;
}

export interface ProcessingStagePayload {
  name: string;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_s?: number | null;
  error_code?: string | null;
  detail?: string | null;
  error?: string | { code?: string | null; detail?: string | null; message?: string | null } | null;
}

export interface ProcessingRunPayload {
  status?: string | null;
  stages?: readonly ProcessingStagePayload[] | null;
  stats?: { processing_s?: Record<string, number> } | null;
}

export const PROCESSING_STAGES = [
  { id: 'ingest', label: 'stage.ingest' },
  { id: 'frames', label: 'stage.frames' },
  { id: 'reconstruction', label: 'stage.reconstruction' },
  { id: 'export', label: 'stage.export' },
] as const satisfies readonly { id: string; label: Key }[];

export function stageLabel(id: string): Key {
  return PROCESSING_STAGES.find((stage) => stage.id === id)?.label ?? 'processing.unknownStage';
}

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const text = (value: unknown) => (typeof value === 'string' && value.trim() ? value : undefined);
const date = (value: unknown) => {
  const result = text(value);
  return result && Number.isFinite(Date.parse(result)) ? result : undefined;
};
const seconds = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;

function status(value: unknown): ProcessingStep['status'] {
  switch (value) {
    case 'pending':
    case 'running':
    case 'done':
    case 'failed':
    case 'skipped':
      return value;
    case 'queued':
      return 'pending';
    case 'ok': // pipeline StageResult.status
    case 'completed':
      return 'done';
    default:
      return 'unknown';
  }
}

/** Pending successors of a failed stage cannot make progress until that failure is resolved. */
export function skipBlockedSteps(steps: readonly ProcessingStep[]): ProcessingStep[] {
  let failed = false;
  return steps.map((step) => {
    if (step.status === 'failed') failed = true;
    return failed && step.status === 'pending' ? { ...step, status: 'skipped' } : step;
  });
}

/** The current API only supplies timings. Never infer success from a duration or a bundle. */
export function processingSteps(
  run?: ProcessingRunPayload | null,
  manifest?: Pick<Manifest, 'stats'>,
): ProcessingStep[] {
  const payload = record(run);
  if (
    payload.status === 'uploaded' &&
    (!Array.isArray(payload.stages) || payload.stages.length === 0)
  )
    return [];
  const timings = {
    ...record(manifest?.stats.processing_s),
    ...record(record(payload.stats).processing_s),
  };
  const stages: ProcessingStep[] = (Array.isArray(payload.stages) ? payload.stages : []).map(
    (value: unknown) => {
      const stage = record(value);
      const error = record(stage.error);
      const id = text(stage.name) ?? '';
      const known = PROCESSING_STAGES.some((item) => item.id === id);
      const mappedStatus = known ? status(stage.status) : 'unknown';
      return {
        id,
        status: mappedStatus,
        startedAt: date(stage.started_at),
        finishedAt: date(stage.finished_at),
        durationSeconds: seconds(stage.duration_s) ?? seconds(timings[id]),
        errorCode: text(stage.error_code) ?? text(error.code),
        detail:
          text(stage.detail) ?? text(error.detail) ?? text(error.message) ?? text(stage.error),
        ...(mappedStatus === 'unknown' ? { reportedStatus: text(stage.status) } : {}),
      };
    },
  );
  const active = payload.status === 'queued' || payload.status === 'running';
  const ordered: ProcessingStep[] = PROCESSING_STAGES.flatMap(({ id }, index) => {
    const reported = stages.filter((stage) => stage.id === id);
    if (reported.length) return reported;
    const blocked = PROCESSING_STAGES.slice(0, index).some((earlier) =>
      stages.some((stage) => stage.id === earlier.id && stage.status === 'failed'),
    );
    return [
      {
        id,
        status: blocked ? 'skipped' : active ? 'pending' : 'unknown',
        durationSeconds: seconds(timings[id]),
      },
    ];
  });
  // Additional stages, even malformed entries and duplicates, stay visible.
  ordered.push(...stages.filter((stage) => !PROCESSING_STAGES.some(({ id }) => id === stage.id)));
  for (const id of Object.keys(timings)) {
    if (!ordered.some((stage) => stage.id === id))
      ordered.push({ id, status: 'unknown', durationSeconds: seconds(timings[id]) });
  }
  return skipBlockedSteps(ordered);
}

const ERROR_MESSAGES = {
  video_unreadable: 'videoUnreadable',
  too_few_frames: 'tooFewFrames',
  too_few_registered_frames: 'tooFewRegisteredFrames',
  reconstruction_failed: 'reconstructionFailed',
  timeout: 'timeout',
  unknown: 'unknown',
} as const;

export function errorMessage(errorCode?: string): {
  title: Key;
  whatWentWrong: Key;
  whatToDo: Key;
} {
  const name =
    errorCode && Object.hasOwn(ERROR_MESSAGES, errorCode)
      ? ERROR_MESSAGES[errorCode as keyof typeof ERROR_MESSAGES]
      : ERROR_MESSAGES.unknown;
  return {
    title: `processing.error.${name}.title`,
    whatWentWrong: `processing.error.${name}.whatWentWrong`,
    whatToDo: `processing.error.${name}.whatToDo`,
  };
}

/** An invalid, negative or missing timestamp never produces NaN in the interface. */
export function stepDuration(step: ProcessingStep, now: number): number | undefined {
  if (step.status === 'pending' || step.status === 'skipped') return undefined;
  const started = step.startedAt ? Date.parse(step.startedAt) : NaN;
  const finished = step.finishedAt
    ? Date.parse(step.finishedAt)
    : step.status === 'running'
      ? now
      : NaN;
  if (Number.isFinite(started) && Number.isFinite(finished) && finished >= started)
    return (finished - started) / 1000;
  return seconds(step.durationSeconds);
}
