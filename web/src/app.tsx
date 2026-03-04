import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'sonner';

import { ThemeProvider } from './components/theme-provider';
import { AppLayout } from './layouts/app-layout';
import { EditorPage } from './pages/editor';
import { HomePage } from './pages/home';
import { VlmIngestPage } from './pages/vlm-ingest';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

function TokenBootstrap() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = (params.get('token') || '').trim();
    if (!token) return;

    const current = localStorage.getItem('atlas_admin_token') || '';
    if (current !== token) {
      localStorage.setItem('atlas_admin_token', token);
    }
  }, []);

  return null;
}

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename="/editor">
          <TokenBootstrap />
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<HomePage />} />
              <Route path="doc/:docId" element={<EditorPage />} />
              <Route path="run/:runId" element={<EditorPage />} />
              <Route path="vlm-ingest" element={<VlmIngestPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster
          position="bottom-right"
          richColors
          closeButton
          toastOptions={{ className: 'font-sans text-sm' }}
        />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
