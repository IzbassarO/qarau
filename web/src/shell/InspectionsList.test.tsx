import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';
import { setLang, type Lang } from '../i18n';
import { useInspections } from '../lib/inspections';
import fixture from '../test/fixtures/inspections.json';
import { InspectionsList } from './InspectionsList';
import type { InspectionActions } from './InspectionRow';

const clients: QueryClient[] = [];
beforeEach(() => setLang('en'));
afterEach(() => {
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
  sessionStorage.clear();
  setLang('ru');
});

function renderList(items = fixture.inspections.slice(0, 5), lang: Lang = 'en') {
  setLang(lang);
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/inspections') return Response.json(items);
    if (url === '/api/sites') return Response.json(fixture.sites);
    throw new Error(`Unexpected API request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  const actions = { onOpen: vi.fn(), onProgress: vi.fn(), onRetry: vi.fn() };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  function ListFromApi(props: InspectionActions) {
    const query = useInspections();
    if (query.isError) return <p role="alert">{query.error.message}</p>;
    if (!query.data) return null;
    return <InspectionsList inspections={query.data} {...props} />;
  }
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ListFromApi {...actions} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...actions, fetch };
}

it.each([
  ['Pipe rack PR-2, bay 7', 'Uploaded', 'Show progress', 'onProgress', '1'],
  ['Tank T-104, north wall', 'Queued', 'Show progress', 'onProgress', '2'],
  ['Tank T-112, roof', 'Running', 'Show progress', 'onProgress', '3'],
  ['Separator V-301, shell', 'Done', 'Open map', 'onOpen', '4'],
  ['Pipe rack PR-5, bays 12 to 14', 'Failed', 'Retry', 'onRetry', '5'],
] as const)(
  'renders %s with its literal status and single action',
  async (name, badge, label, callback, id) => {
    const actions = renderList();
    const row = await screen.findByRole('row', { name: new RegExp(name) });
    expect(within(row).getByText(badge).className).toBe('qv-status qv-status--muted');
    expect(within(row).getAllByRole('button')).toHaveLength(1);
    fireEvent.click(within(row).getByRole('button', { name: label }));
    expect(actions[callback]).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ id }));
    const calls =
      actions.onOpen.mock.calls.length +
      actions.onProgress.mock.calls.length +
      actions.onRetry.mock.calls.length;
    expect(calls).toBe(1);
  },
);

it('shows an em dash for missing findings and preserves known positive and zero counts', async () => {
  renderList();
  const queued = await screen.findByRole('row', { name: /Tank T-104, north wall/ });
  expect(within(queued).getByText('—')).toBeTruthy();
  expect(
    within(screen.getByRole('row', { name: /Separator V-301, shell/ })).getByText('4'),
  ).toBeTruthy();
  expect(
    within(screen.getByRole('row', { name: /Pipe rack PR-5, bays 12 to 14/ })).getByText('0'),
  ).toBeTruthy();
});

it('links a done inspection to its report and offers no report for processing inspections', async () => {
  renderList();
  const done = await screen.findByRole('row', { name: /Separator V-301, shell/ });
  expect(within(done).getByRole('link', { name: 'Report' }).getAttribute('href')).toBe(
    '/inspections/4/report',
  );
  for (const name of [
    'Pipe rack PR-2, bay 7',
    'Tank T-104, north wall',
    'Tank T-112, roof',
    'Pipe rack PR-5, bays 12 to 14',
  ]) {
    expect(
      within(screen.getByRole('row', { name: new RegExp(name) })).queryByRole('link', {
        name: 'Report',
      }),
    ).toBeNull();
  }
});

it('renders the empty state from an empty API without requesting legacy runs', async () => {
  const { fetch } = renderList([]);
  expect(await screen.findByText('No inspections yet')).toBeTruthy();
  expect(
    screen.getByText(
      'Shoot a wall or a pipe rack with the Letter sheet in frame, then upload the video. The first map takes about 7 minutes.',
    ),
  ).toBeTruthy();
  expect(screen.queryByRole('table')).toBeNull();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/inspections']);
});

it('sorts the rows against literal expected inspection ids', async () => {
  renderList();
  const table = await screen.findByRole('table', { name: 'Inspections' });
  expect(
    within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.dataset.inspectionId),
  ).toEqual(['2', '3', '1', '4', '5']);
});

it('renders exactly 50 of the 51 fixture inspections with the uncapped count', async () => {
  renderList(fixture.inspections);
  expect(await screen.findByText('50 of 51')).toBeTruthy();
  const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
  expect(rows).toHaveLength(50);
  expect(rows.map((row) => row.dataset.inspectionId)).toEqual([
    '2',
    '3',
    '1',
    '4',
    '5',
    '6',
    '7',
    '8',
    '9',
    '10',
    '11',
    '12',
    '13',
    '14',
    '15',
    '16',
    '17',
    '18',
    '19',
    '20',
    '21',
    '22',
    '23',
    '24',
    '25',
    '26',
    '27',
    '28',
    '29',
    '30',
    '31',
    '32',
    '33',
    '34',
    '35',
    '36',
    '37',
    '38',
    '39',
    '40',
    '41',
    '42',
    '43',
    '44',
    '45',
    '46',
    '47',
    '48',
    '49',
    '50',
  ]);
  expect(screen.getAllByRole('button')).toHaveLength(50);
});

it.each([
  ['ru', 'Инспекции', 'В очереди', 'Показать ход', 'Готово', 'Открыть карту', 'Повторить'],
  ['kk', 'Инспекциялар', 'Кезекте', 'Барысын көрсету', 'Дайын', 'Картаны ашу', 'Қайталау'],
] as const)(
  'translates the list and row actions in %s',
  async (lang, title, queued, progress, done, open, retry) => {
    renderList(fixture.inspections.slice(0, 5), lang);
    expect(await screen.findByRole('table', { name: title })).toBeTruthy();
    expect(screen.getByText(queued)).toBeTruthy();
    expect(screen.getByText(done)).toBeTruthy();
    expect(screen.getAllByRole('button', { name: progress })).toHaveLength(3);
    expect(screen.getByRole('button', { name: open })).toBeTruthy();
    expect(screen.getByRole('button', { name: retry })).toBeTruthy();
  },
);

it('uses the session API headers for both inspections and object names', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const { fetch } = renderList();
  await screen.findByRole('table');
  expect(fetch).toHaveBeenCalledWith('/api/inspections', {
    signal: expect.any(AbortSignal),
    headers: { Authorization: 'Bearer test-api-value' },
  });
  expect(fetch).toHaveBeenCalledWith('/api/sites', {
    signal: expect.any(AbortSignal),
    headers: { Authorization: 'Bearer test-api-value' },
  });
});
