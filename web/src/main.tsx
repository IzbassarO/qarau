import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router';
import './index.css';
import { AppShell } from './shell/AppShell';
import { ViewerPage } from './pages/ViewerPage';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<AppShell />} />
          <Route path="/inspections/demo" element={<AppShell demo />} />
          <Route path="/inspections/:id" element={<AppShell />} />
          <Route path="/runs" element={<Navigate to="/" replace />} />
          <Route path="/viewer/:runId" element={<ViewerPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
