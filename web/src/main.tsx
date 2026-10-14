import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router';
import './index.css';
import { RunsPage } from './pages/RunsPage';
import { ViewerPage } from './pages/ViewerPage';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RunsPage />} />
          <Route path="/viewer/:runId" element={<ViewerPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
