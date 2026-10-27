import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LANGS, setLang, type Lang } from '../i18n';
import { processingSteps, type ProcessingStep } from '../lib/processing';
import { getInspection } from '../lib/inspections';
import { ProcessingSteps } from './ProcessingSteps';

beforeEach(() => setLang('en'));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each([
  ['pending', 'Pending'],
  ['running', 'Running'],
  ['done', 'Done'],
  ['failed', 'Failed'],
  ['skipped', 'Not run'],
  ['unknown', 'Unknown state'],
] as const)('renders %s with a text state and an icon', (status, label) => {
  render(<ProcessingSteps steps={[{ id: 'ingest', status }]} />);
  const row = screen.getByRole('listitem');
  expect(within(row).getByText('Video import')).toBeTruthy();
  expect(within(row).getByText(label)).toBeTruthy();
  expect(row.getAttribute('data-state')).toBe(status);
  expect(row.classList.contains('qv-row')).toBe(true);
  expect(row.closest('section')?.className).toBe('qv-card processing-section');
  expect(screen.getByRole('heading', { name: 'Processing steps' }).className).toBe('qv-heading');
  expect(within(row).getByText(label).classList.contains('qv-status')).toBe(true);
  expect(row.querySelector('svg')).toBeTruthy();
});

it.each<Lang>(['en', 'ru', 'kk'])(
  'shows expanded plain-language errors and what to do in %s',
  (lang) => {
    setLang(lang);
    const t = LANGS[lang];
    render(
      <ProcessingSteps
        steps={[{ id: 'reconstruction', status: 'failed', errorCode: 'too_few_registered_frames' }]}
      />,
    );
    expect(screen.getByText(t['processing.error.tooFewRegisteredFrames.title'])).toBeTruthy();
    expect(
      screen.getByText(t['processing.error.tooFewRegisteredFrames.title']).closest('.qv-notice')
        ?.className,
    ).toBe('qv-notice step-error');
    expect(screen.getByText(t['processing.whatWentWrong'])).toBeTruthy();
    expect(screen.getByText(t['processing.whatToDo'])).toBeTruthy();
    expect(screen.getByText(t['processing.error.tooFewRegisteredFrames.whatToDo'])).toBeTruthy();
  },
);

it('shows pending successors of a failure as not run', () => {
  render(
    <ProcessingSteps
      steps={[
        { id: 'ingest', status: 'done' },
        { id: 'frames', status: 'done' },
        { id: 'reconstruction', status: 'failed', errorCode: 'reconstruction_failed' },
        { id: 'export', status: 'pending' },
      ]}
    />,
  );
  const rows = screen.getAllByRole('listitem');
  expect(within(rows[3]).getByText('Not run')).toBeTruthy();
  expect(screen.queryByText('Pending')).toBeNull();
  expect(rows.map((row) => row.getAttribute('data-state'))).toEqual([
    'done',
    'done',
    'failed',
    'skipped',
  ]);
});

it('keeps unknown stages, statuses, error codes and raw detail accessible with fallback advice', () => {
  render(
    <ProcessingSteps
      steps={[
        { id: 'new_stage', status: 'unknown', reportedStatus: 'waiting_for_device' },
        {
          id: 'frames',
          status: 'failed',
          errorCode: 'future_failure',
          detail: 'Camera metadata is missing. <script>unsafe()</script>',
        },
      ]}
    />,
  );
  expect(screen.getByText('Unknown stage')).toBeTruthy();
  expect(screen.getByText('new_stage')).toBeTruthy();
  expect(screen.getByText('new_stage').className).toBe('qv-mono qv-muted step-raw');
  expect(screen.getByText('waiting_for_device')).toBeTruthy();
  expect(screen.getByText(LANGS.en['processing.error.unknown.whatToDo'])).toBeTruthy();
  fireEvent.click(screen.getByText('Processing details'));
  expect(screen.getByText('future_failure')).toBeTruthy();
  expect(screen.getByText('Camera metadata is missing. <script>unsafe()</script>')).toBeTruthy();
  expect(document.querySelector('.step-detail script')).toBeNull();
});

it('updates running elapsed time and stops its timer when finished', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-14T05:30:00Z'));
  const step: ProcessingStep = {
    id: 'frames',
    status: 'running',
    startedAt: '2026-10-14T05:30:00Z',
  };
  const view = render(<ProcessingSteps steps={[step]} />);
  expect(screen.getByText('0 s')).toBeTruthy();
  expect(view.container.querySelector('.motion-safe\\:animate-spin')).toBeTruthy();
  act(() => vi.advanceTimersByTime(2000));
  expect(screen.getByText('2 s')).toBeTruthy();
  view.rerender(
    <ProcessingSteps steps={[{ ...step, status: 'done', finishedAt: '2026-10-14T05:30:02Z' }]} />,
  );
  expect(vi.getTimerCount()).toBe(0);
});

it('shows a localized empty state', () => {
  render(<ProcessingSteps steps={[]} />);
  expect(screen.getByText(LANGS.en['inspection.noStages'])).toBeTruthy();
  expect(screen.getByRole('status').className).toBe('qv-card__body qv-caption');
});

it('renders the S1 step fixture returned by GET /api/inspections/{id}', async () => {
  const fetch = vi.fn().mockResolvedValue(
    Response.json({
      id: 7,
      site_id: 1,
      mode: 'quick_scan',
      status: 'running',
      run_id: 'run-7',
      steps: [
        { name: 'probe', status: 'done', duration_s: 1 },
        { name: 'frames', status: 'done', duration_s: 4.2 },
        { name: 'sparse', status: 'running' },
        { name: 'export', status: 'pending' },
      ],
    }),
  );
  vi.stubGlobal('fetch', fetch);
  render(<ProcessingSteps steps={processingSteps(await getInspection('7'))} />);
  expect(screen.getAllByRole('listitem').map((row) => row.getAttribute('data-state'))).toEqual([
    'done',
    'done',
    'running',
    'pending',
  ]);
  expect(screen.getByText('4.2 s')).toBeTruthy();
  expect(screen.getByText('Video check')).toBeTruthy();
  expect(screen.getByText('3D reconstruction')).toBeTruthy();
  expect(screen.queryByText('Unknown stage')).toBeNull();
  expect(fetch).toHaveBeenCalledWith('/api/inspections/7', expect.anything());
});

it.each<Lang>(['en', 'ru', 'kk'])(
  'shows uploaded waiting for processing without invented steps (%s)',
  (lang) => {
    setLang(lang);
    render(<ProcessingSteps steps={processingSteps({ status: 'uploaded' })} uploaded />);
    expect(screen.getByText(LANGS[lang]['processing.uploadedWaiting'])).toBeTruthy();
    expect(screen.queryByRole('listitem')).toBeNull();
  },
);

it('shows the literal uploaded waiting state from GET /api/inspections/7 with no S1 steps', async () => {
  const fetch = vi.fn().mockResolvedValue(
    Response.json({
      id: 7,
      site_id: 1,
      status: 'uploaded',
      run_id: 'run-7',
      steps: [],
    }),
  );
  vi.stubGlobal('fetch', fetch);
  const detail = await getInspection('7');
  render(
    <ProcessingSteps steps={processingSteps(detail)} uploaded={detail.status === 'uploaded'} />,
  );
  expect(screen.getByRole('status').textContent).toBe('Video uploaded, waiting for processing.');
  expect(screen.queryByRole('listitem')).toBeNull();
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/inspections/7', {
    signal: undefined,
    headers: {},
  });
});
