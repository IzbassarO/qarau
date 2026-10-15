import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { setLang } from '../i18n';
import { groupRunsIntoSites } from '../lib/inspections';
import { Sidebar } from './Sidebar';
import { NewInspectionDialog } from './NewInspectionDialog';

const sites = groupRunsIntoSites([
  { run_id: 'north-1', title: 'North survey', site: { id: 'north', name: 'North site' } },
  { run_id: 'south-1', title: 'South survey', site: { id: 'south', name: 'South site' } },
  { run_id: 'unassigned', title: 'Walkdown' },
]);

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <MemoryRouter initialEntries={['/']}>
      <Sidebar sites={sites} onNavigate={() => {}} onNewInspection={() => setOpen(true)} />
      {open && <NewInspectionDialog onClose={() => setOpen(false)} />}
    </MemoryRouter>
  );
}

beforeEach(() => setLang('en'));

describe('Sidebar', () => {
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
    expect(screen.getByText(/Video upload is not available yet/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Close' })[0]);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
