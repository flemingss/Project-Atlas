import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'sonner';

import { getAdminToken } from './services/shared';
import { ThemeProvider } from './components/theme-provider';
import { ErrorBoundary } from './components/error-boundary';
import { AppLayout } from './layouts/app-layout';
import { AdminLayout } from './layouts/admin-layout';
import { DashboardPage } from './pages/dashboard';
import { LibraryPage } from './pages/library';
import { SearchPage } from './pages/search';
import { ReviewPage } from './pages/review';
import { AdminHealthPage } from './pages/admin/health';
import { AdminCleanupPage } from './pages/admin/cleanup';
import { AdminGroupsPage } from './pages/admin/groups';
import { AdminDangerPage } from './pages/admin/danger';
import { EditorPage } from './pages/editor';
import { IngestPage } from './pages/ingest';

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
  // Consumes a one-time ?token= bootstrap and scrubs it from the URL.
  useEffect(() => {
    getAdminToken();
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
              <Route path="ingest" element={<ErrorBoundary><IngestPage /></ErrorBoundary>} />
              <Route path="upload" element={<Navigate to="/ingest" replace />} />
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
              <Route path="vlm-ingest" element={<Navigate to="/ingest" replace />} />
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
