import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import { ModeSwitch, NavigationSettings } from './ui';

beforeEach(() => setLang('en'));

it('shows Orbit active and the future modes disabled with a coming-soon explanation', () => {
  render(<ModeSwitch onOrbit={vi.fn()} />);
  expect(screen.getByRole('button', { name: 'Orbit' }).getAttribute('aria-pressed')).toBe('true');
  for (const name of ['Walk', 'Observer']) {
    const button = screen.getByRole('button', { name }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe('Coming soon');
  }
});

it('ignores keys 2 and 3 while those modes are disabled and uses 1 for Orbit', () => {
  const onOrbit = vi.fn();
  render(<ModeSwitch onOrbit={onOrbit} />);
  const group = screen.getByRole('group', { name: 'Navigation mode' });
  fireEvent.keyDown(group, { key: '2' });
  fireEvent.keyDown(group, { key: '3' });
  expect(onOrbit).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Orbit' }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.keyDown(group, { key: '1' });
  expect(onOrbit).toHaveBeenCalledTimes(1);
});

it('exposes the sensitivity range and forwards the chosen setting', () => {
  const onSensitivityChange = vi.fn();
  render(<NavigationSettings sensitivity={1} onSensitivityChange={onSensitivityChange} />);
  fireEvent.click(screen.getByText('Settings'));
  const slider = screen.getByLabelText(/Navigation sensitivity/);
  expect(slider.getAttribute('min')).toBe('0.25');
  expect(slider.getAttribute('max')).toBe('4');
  fireEvent.change(slider, { target: { value: '0.5' } });
  expect(onSensitivityChange).toHaveBeenCalledWith(0.5);
});
