import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import { AuthProvider } from './AuthProvider';
import { RequireAuth } from './RequireAuth';

function renderViewer() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/viewer/demo']}>
          <Routes>
            <Route element={<RequireAuth allowInDev />}>
              <Route path="/viewer/:runId" element={<div data-testid="viewer" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  setLang('en');
});
afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllEnvs();
});

it('keeps the standalone dev viewer public without a session', () => {
  vi.stubEnv('DEV', true);
  renderViewer();
  expect(screen.getByTestId('viewer')).toBeTruthy();
  expect(screen.queryByLabelText('Password')).toBeNull();
});

it('guards the same viewer route in production', () => {
  vi.stubEnv('DEV', false);
  renderViewer();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
  expect(screen.queryByTestId('viewer')).toBeNull();
});

it('opens the production viewer with a stored session', () => {
  vi.stubEnv('DEV', false);
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  renderViewer();
  expect(screen.getByTestId('viewer')).toBeTruthy();
  expect(screen.queryByLabelText('Password')).toBeNull();
});
