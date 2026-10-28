import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import '@fontsource/geologica/600.css';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-sans/400-italic.css';
import '@fontsource/lora/400-italic.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import './index.css';
import { followSystemTheme } from './design/theme';
import { AppShell } from './shell/AppShell';
import { ViewerPage } from './pages/ViewerPage';
import { ReportPage } from './pages/ReportPage';
import { AuthProvider } from './auth/AuthProvider';
import { RequireAuth } from './auth/RequireAuth';

const stopFollowingSystemTheme = followSystemTheme();
if (import.meta.hot) import.meta.hot.dispose(stopFollowingSystemTheme);

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<RequireAuth />}>
              <Route path="/" element={<AppShell />} />
              <Route path="/inspections" element={<AppShell />} />
              <Route path="/inspections/demo" element={<AppShell demo />} />
              <Route path="/inspections/:id" element={<AppShell />} />
              <Route
                path="/inspections/:id/report"
                element={
                  <AppShell>
                    <ReportPage />
                  </AppShell>
                }
              />
              <Route path="/runs" element={<Navigate to="/" replace />} />
            </Route>
            <Route element={<RequireAuth allowInDev />}>
              <Route path="/viewer/:runId" element={<ViewerPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
