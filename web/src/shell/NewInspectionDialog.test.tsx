import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import { NewInspectionDialog } from './NewInspectionDialog';

beforeEach(() => setLang('en'));

it('rejects a non-video file with a visible translated message', () => {
  render(<NewInspectionDialog onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText(/Choose a video/), {
    target: { files: [new File(['text'], 'notes.txt', { type: 'text/plain' })] },
  });
  expect(screen.getByRole('alert').textContent).toContain('Choose a video file');
});

it('rejects an empty video, then clears the error for a non-empty video', () => {
  render(<NewInspectionDialog onClose={() => {}} />);
  const input = screen.getByLabelText(/Choose a video/);
  fireEvent.change(input, {
    target: { files: [new File([], 'empty.mp4', { type: 'video/mp4' })] },
  });
  expect(screen.getByRole('alert').textContent).toContain('empty');
  fireEvent.change(input, {
    target: { files: [new File(['video'], 'survey.mp4', { type: 'video/mp4' })] },
  });
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText(/Selected video: survey.mp4/)).toBeTruthy();
  expect(screen.getByText(/Video upload is not available yet/)).toBeTruthy();
  expect(
    (screen.getByRole('button', { name: 'Start processing' }) as HTMLButtonElement).disabled,
  ).toBe(true);
});

it('renders file validation errors in Russian and Kazakh', () => {
  for (const [lang, expected] of [
    ['ru', 'Выберите видеофайл'],
    ['kk', 'Бейне файлды таңдаңыз'],
  ] as const) {
    setLang(lang);
    const view = render(<NewInspectionDialog onClose={() => {}} />);
    fireEvent.change(view.container.querySelector('input')!, {
      target: { files: [new File(['x'], 'bad.png', { type: 'image/png' })] },
    });
    expect(screen.getByRole('alert').textContent).toContain(expected);
    view.unmount();
  }
});

it('handles the native Escape cancellation', () => {
  const onClose = vi.fn();
  render(<NewInspectionDialog onClose={onClose} />);
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { bubbles: true }));
  expect(onClose).toHaveBeenCalledOnce();
});
