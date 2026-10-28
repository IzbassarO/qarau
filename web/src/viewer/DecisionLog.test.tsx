import { render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it } from 'vitest';
import { setLang } from '../i18n';
import type { Decision } from '../lib/findings';
import decisions from '../test/fixtures/decisions.json';
import { DecisionLog } from './DecisionLog';

beforeEach(() => setLang('en'));

it('renders the out-of-order fixture oldest first with fixed user/comment order', () => {
  expect(decisions.map((decision) => decision.id)).toEqual([903, 901, 902]);
  render(<DecisionLog decisions={decisions as Decision[]} />);
  const entries = screen.getAllByRole('listitem');
  expect(
    entries.map((entry) => entry.querySelector('.qv-logentry__who > span')?.textContent),
  ).toEqual(['Dana Serik', 'Alex Kim', 'Mira Chen']);
  expect(entries.map((entry) => entry.querySelector('.decision-comment')?.textContent)).toEqual([
    'Pitting visible in both frames.',
    'Rust is paint',
    'Coating has peeled away.',
  ]);
  expect(entries.map((entry) => entry.querySelector('time')?.dateTime)).toEqual([
    '2026-10-25T09:00:00Z',
    '2026-10-26T10:15:00Z',
    '2026-10-27T12:30:00Z',
  ]);
  expect(within(entries[0]).getByText('Confirmed')).toBeTruthy();
  expect(within(entries[1]).getByText('Rejected')).toBeTruthy();
  expect(within(entries[2]).getByText('Reclassified')).toBeTruthy();
  expect(within(entries[2]).getByText('New class: Coating')).toBeTruthy();
  expect(entries[0].querySelector('time')?.textContent).toMatch(/25 Oct, \d{2}:\d{2}/);
  // Sorting the render must not mutate the cached API response.
  expect(decisions.map((decision) => decision.id)).toEqual([903, 901, 902]);
});

it('has no edit or delete button, link or menu item anywhere in the log', () => {
  render(<DecisionLog decisions={decisions as Decision[]} />);
  for (const role of ['button', 'link', 'menuitem']) {
    expect(
      screen.queryByRole(role, { name: /edit|delete|изменить|удалить/i, hidden: true }),
    ).toBeNull();
  }
  expect(
    screen
      .getByRole('region', { name: 'Decision log' })
      .querySelector(
        'button, a, input, textarea, select, [contenteditable="true"], [role="button"], [role="menuitem"]',
      ),
  ).toBeNull();
});

it('shows the empty state for the real seed’s empty log', () => {
  render(<DecisionLog decisions={[]} />);
  expect(screen.getByText('No decisions yet')).toBeTruthy();
  expect(screen.queryByRole('listitem')).toBeNull();
});
