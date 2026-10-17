import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import { ModeSwitch, NavigationSettings } from './ui';

beforeEach(() => setLang('en'));

it('shows Orbit active by default, enables Observer and keeps Walk unavailable', () => {
  const onObserver = vi.fn();
  render(<ModeSwitch onOrbit={vi.fn()} onObserver={onObserver} />);
  expect(screen.getByRole('button', { name: 'Orbit' }).getAttribute('aria-pressed')).toBe('true');
  const observer = screen.getByRole('button', { name: 'Observer' }) as HTMLButtonElement;
  expect(observer.disabled).toBe(false);
  expect(observer.getAttribute('aria-pressed')).toBe('false');
  fireEvent.click(observer);
  expect(onObserver).toHaveBeenCalledOnce();
  const walk = screen.getByRole('button', { name: 'Walk' }) as HTMLButtonElement;
  expect(walk.disabled).toBe(true);
  expect(walk.title).toBe('Coming soon');
});

it('ignores keys 2 and 3 and uses 1 for Orbit', () => {
  const onOrbit = vi.fn();
  const onObserver = vi.fn();
  render(<ModeSwitch onOrbit={onOrbit} onObserver={onObserver} />);
  const group = screen.getByRole('group', { name: 'Navigation mode' });
  fireEvent.keyDown(group, { key: '2' });
  fireEvent.keyDown(group, { key: '3' });
  expect(onOrbit).not.toHaveBeenCalled();
  expect(onObserver).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Orbit' }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.keyDown(group, { key: '1' });
  expect(onOrbit).toHaveBeenCalledTimes(1);
});

it('toggles Observer with F without repeating or bubbling a second toggle to the viewer', () => {
  const onOrbit = vi.fn();
  const onObserver = vi.fn();
  const onKeyDown = vi.fn();
  const view = render(
    <div onKeyDown={onKeyDown}>
      <ModeSwitch onOrbit={onOrbit} onObserver={onObserver} />
    </div>,
  );
  const group = screen.getByRole('group', { name: 'Navigation mode' });
  fireEvent.keyDown(group, { key: 'f', code: 'KeyF' });
  expect(onObserver).toHaveBeenCalledOnce();
  expect(onOrbit).not.toHaveBeenCalled();
  expect(onKeyDown).not.toHaveBeenCalled();
  fireEvent.keyDown(group, { key: 'f', code: 'KeyF', repeat: true });
  expect(onObserver).toHaveBeenCalledOnce();
  view.rerender(
    <div onKeyDown={onKeyDown}>
      <ModeSwitch orbitActive={false} onOrbit={onOrbit} onObserver={onObserver} />
    </div>,
  );
  expect(screen.getByRole('button', { name: 'Observer' }).getAttribute('aria-pressed')).toBe(
    'true',
  );
  expect(screen.getByRole('button', { name: 'Orbit' }).getAttribute('aria-pressed')).toBe('false');
  fireEvent.keyDown(group, { key: 'F', code: 'KeyF' });
  expect(onOrbit).toHaveBeenCalledOnce();
  expect(onKeyDown).not.toHaveBeenCalled();
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
