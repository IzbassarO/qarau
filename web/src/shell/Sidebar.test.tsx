import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LANGS, setLang, type Lang } from '../i18n';
import { createDemo } from '../fixtures/demo';
import { groupRunsIntoSites, listInspections, type Site } from '../lib/inspections';
import { Sidebar } from './Sidebar';
import { NewInspectionDialog } from './NewInspectionDialog';
import { AuthProvider } from '../auth/AuthProvider';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sites = groupRunsIntoSites([
  { run_id: 'north-1', title: 'North survey', site: { id: 'north', name: 'North site' } },
  { run_id: 'south-1', title: 'South survey', site: { id: 'south', name: 'South site' } },
  { run_id: 'unassigned', title: 'Walkdown' },
]);

function Harness({ groups = sites, demo = false }: { groups?: Site[]; demo?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/']}>
          <Sidebar
            sites={groups}
            demo={demo}
            onNavigate={() => {}}
            onNewInspection={() => setOpen(true)}
          />
          {open && <NewInspectionDialog onClose={() => setOpen(false)} />}
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  setLang('en');
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(Response.json([{ id: 1, org_id: 1, name: 'North site' }])),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe('Sidebar', () => {
  it('keeps the example badge on the demo entry and out of the footer link', () => {
    render(<Harness groups={createDemo('en').sites} demo />);
    const footerLink = screen.getByRole('link', { name: 'Example inspection' });
    expect(footerLink.getAttribute('href')).toBe('/inspections/demo');
    expect(within(footerLink).queryByText('Example', { exact: true })).toBeNull();
    expect(footerLink.querySelector('.demo-badge')).toBeNull();
    const demoEntry = screen.getByRole('link', { name: /Tank farm survey/ });
    expect(within(demoEntry).getByText('Example', { exact: true }).className).toBe('demo-badge');
  });

  it.each<Lang>(['en', 'ru', 'kk'])(
    'renders names and localized dates newest first (%s)',
    (lang) => {
      setLang(lang);
      const title = LANGS[lang]['demo.inspectionTitle'];
      const groups = groupRunsIntoSites([
        { run_id: 'older', title: `${title} 1`, created_at: '2026-10-14T12:00:00Z' },
        {
          run_id: 'newest',
          title: `${title} 3`,
          source: { name: 'source.mp4' },
          created_at: '2026-10-20T12:00:00Z',
        },
        { run_id: 'middle', title: `${title} 2`, created_at: '2026-10-16T12:00:00Z' },
      ]);
      render(<Harness groups={groups} />);
      const links = screen.getAllByRole('link', { name: new RegExp(title) });
      expect(links.map((link) => link.getAttribute('href'))).toEqual([
        '/inspections/newest',
        '/inspections/middle',
        '/inspections/older',
      ]);
      for (const [index, link] of links.entries()) {
        const inspection = groups[0].inspections[index];
        const name = within(link).getByText(inspection.title);
        expect(name.className).toBe('inspection-link-name');
        expect(name.getAttribute('title')).toBe(inspection.title);
        const date = within(link).getByRole('time');
        expect(date.getAttribute('datetime')).toBe(inspection.createdAt);
        expect(date.textContent).toBe(
          new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : lang, {
            day: 'numeric',
            month: 'short',
          }).format(new Date(inspection.createdAt!)),
        );
      }
      expect(links[0].getAttribute('title')).toBe('source.mp4');
    },
  );

  it('renders site groups, translates unassigned and selects a clicked inspection', () => {
    render(<Harness />);
    expect(screen.getByRole('button', { name: /North site/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /South site/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Unassigned/ })).toBeTruthy();
    const inspection = screen.getByRole('link', { name: /North survey/ });
    expect(inspection.getAttribute('aria-current')).toBeNull();
    fireEvent.click(inspection);
    expect(inspection.getAttribute('aria-current')).toBe('page');
    expect(inspection.classList.contains('active')).toBe(true);
    const next = screen.getByRole('link', { name: /South survey/ });
    fireEvent.click(next);
    expect(next.getAttribute('aria-current')).toBe('page');
    expect(inspection.getAttribute('aria-current')).toBeNull();
  });

  it('collapses and expands site inspections', () => {
    render(<Harness />);
    const group = screen.getByRole('button', { name: /North site/ });
    fireEvent.click(group);
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('link', { name: /North survey/ })).toBeNull();
    fireEvent.click(group);
    expect(screen.getByRole('link', { name: /North survey/ })).toBeTruthy();
  });

  it('opens the new inspection dialog from the button', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'New inspection' }));
    expect(screen.getByRole('dialog', { name: 'New inspection' })).toBeTruthy();
    expect(screen.getByLabelText('Site')).toBeTruthy();
    expect(screen.getByLabelText(/Choose a video/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' })[0]);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

it('renders visible uploaded and failed status chips', () => {
  render(
    <Harness
      groups={groupRunsIntoSites([
        { run_id: 'uploaded-1', status: 'uploaded' },
        { run_id: 'failed-1', status: 'failed' },
      ])}
    />,
  );
  expect(
    within(screen.getByRole('link', { name: /uploaded-1/ })).getByText('Uploaded').className,
  ).toBe('inspection-status-chip uploaded');
  expect(within(screen.getByRole('link', { name: /failed-1/ })).getByText('Failed').className).toBe(
    'inspection-status-chip failed',
  );
});

it('shows an unfamiliar API status verbatim with neutral unknown styling', () => {
  render(<Harness groups={groupRunsIntoSites([{ run_id: '7', status: 'awaiting_review' }])} />);
  const entry = screen.getByRole('link', { name: /Inspection 7/ });
  expect(within(entry).getByText('awaiting_review').className).toBe(
    'inspection-status-chip unknown',
  );
  expect(within(entry).queryByText('Completed')).toBeNull();
});

it('lists the uploaded inspection returned by GET /api/inspections', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/inspections')
      return Response.json([
        { id: 7, site_id: 1, status: 'uploaded', created_at: '2026-10-23T12:00:00Z' },
      ]);
    if (url === '/api/sites') return Response.json([{ id: 1, org_id: 1, name: 'North site' }]);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  render(<Harness groups={await listInspections()} />);
  const entry = screen.getByRole('link', { name: /Inspection 7/ });
  expect(entry.getAttribute('href')).toBe('/inspections/7');
  expect(within(entry).getByText('Uploaded').className).toBe('inspection-status-chip uploaded');
  const siteButton = screen.getByRole('button', { name: /North site/ });
  expect(within(siteButton).getByText('North site').textContent).toBe('North site');
  expect(within(siteButton).getByText('1').textContent).toBe('1');
  expect(fetch).toHaveBeenCalledWith('/api/inspections', expect.anything());
});
