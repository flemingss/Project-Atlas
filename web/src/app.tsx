import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'sonner';

import { ThemeProvider } from './components/theme-provider';
import { ErrorBoundary } from './components/error-boundary';
import { AppLayout } from './layouts/app-layout';
import { AdminLayout } from './layouts/admin-layout';
import { DashboardPage } from './pages/dashboard';
import { UploadPage } from './pages/upload';
import { LibraryPage } from './pages/library';
import { SearchPage } from './pages/search';
import { ReviewPage } from './pages/review';
import { AdminHealthPage } from './pages/admin/health';
import { AdminCleanupPage } from './pages/admin/cleanup';
import { AdminGroupsPage } from './pages/admin/groups';
import { AdminDangerPage } from './pages/admin/danger';
import { EditorPage } from './pages/editor';
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
        <BrowserRouter basename="/app">
          <TokenBootstrap />
          <Routes>
            <Route element={<ErrorBoundary><AppLayout /></ErrorBoundary>}>
              <Route index element={<ErrorBoundary><DashboardPage /></ErrorBoundary>} />
              <Route path="upload" element={<ErrorBoundary><UploadPage /></ErrorBoundary>} />
              <Route path="library" element={<ErrorBoundary><LibraryPage /></ErrorBoundary>} />
              <Route path="search" element={<ErrorBoundary><SearchPage /></ErrorBoundary>} />
              <Route path="review" element={<ErrorBoundary><ReviewPage /></ErrorBoundary>} />
              <Route path="admin" element={<ErrorBoundary><AdminLayout /></ErrorBoundary>}>
                <Route index element={<Navigate to="health" replace />} />
                <Route path="health" element={<ErrorBoundary><AdminHealthPage /></ErrorBoundary>} />
                <Route path="cleanup" element={<ErrorBoundary><AdminCleanupPage /></ErrorBoundary>} />
                <Route path="groups" element={<ErrorBoundary><AdminGroupsPage /></ErrorBoundary>} />
                <Route path="danger" element={<ErrorBoundary><AdminDangerPage /></ErrorBoundary>} />
              </Route>
              <Route path="doc/:docId" element={<ErrorBoundary><EditorPage /></ErrorBoundary>} />
              <Route path="run/:runId" element={<ErrorBoundary><EditorPage /></ErrorBoundary>} />
              <Route path="vlm-ingest" element={<ErrorBoundary><VlmIngestPage /></ErrorBoundary>} />
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
