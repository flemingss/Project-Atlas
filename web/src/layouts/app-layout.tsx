import {
  FileText,
  Home,
  Library,
  Moon,
  PanelLeft,
  Search,
  Settings,
  Sun,
  Upload,
  UserCheck,
} from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet';
import { useThemeContext } from '@/components/theme-provider';
import { AppSidebar } from '@/components/app-sidebar';
import { useConnectionStore } from '@/stores/connection-store';
import { useScopeStore } from '@/stores/scope-store';
import { useMobile } from '@/hooks/use-mobile';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: Home },
  { to: '/ingest', label: 'Ingest', icon: Upload },
  { to: '/library', label: 'Library', icon: Library },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/review', label: 'Review', icon: UserCheck },
] as const;

export function AppLayout() {
  const { isDark, toggleTheme } = useThemeContext();
  const isAdmin = useConnectionStore((s) => s.isAdmin);
  const isConnected = useConnectionStore((s) => s.isConnected);
  const { workspace, project, collection } = useScopeStore();
  const isMobile = useMobile();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const sidebarContent = <AppSidebar />;

  return (
    <div className="flex h-full flex-col">
      {/* ── Top bar ── */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-bg-surface px-4">
        {/* Mobile sidebar toggle */}
        {isMobile && (
          <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
                <PanelLeft className="size-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[280px] p-0">
              {sidebarContent}
            </SheetContent>
          </Sheet>
        )}

        <NavLink to="/" className="flex items-center gap-2">
          <FileText className="size-5 text-accent" />
          <span className="text-sm font-bold tracking-wide text-accent">ATLAS</span>
        </NavLink>

        {/* Scope breadcrumb */}
        {workspace && (
          <>
            <span className="text-text-muted">›</span>
            <span className="hidden text-xs text-text-secondary sm:inline">
              {workspace}
              {project && <span className="text-text-muted"> › </span>}
              {project}
              {collection && <span className="text-text-muted"> › </span>}
              {collection}
            </span>
          </>
        )}

        <span className="text-text-muted">›</span>

        <nav className="flex items-center gap-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors',
                  isActive
                    ? 'bg-accent/10 text-accent'
                    : 'text-text-secondary hover:bg-bg-card hover:text-text-primary',
                )
              }
            >
              <Icon className="size-3.5" />
              <span className="hidden lg:inline">{label}</span>
            </NavLink>
          ))}

          {/* Admin nav (conditional on token) */}
          {isAdmin && (
            <NavLink
              to="/admin/health"
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors',
                  isActive
                    ? 'bg-accent/10 text-accent'
                    : 'text-text-secondary hover:bg-bg-card hover:text-text-primary',
                )
              }
            >
              <Settings className="size-3.5" />
              <span className="hidden lg:inline">Admin</span>
            </NavLink>
          )}
        </nav>

        <div className="flex-1" />

        {/* Connection dot */}
        <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
          <span
            className={cn(
              'inline-block size-2 rounded-full',
              isConnected ? 'bg-state-success' : 'bg-state-error',
            )}
          />
          <span className="hidden sm:inline">{isConnected ? 'Connected' : 'Offline'}</span>
        </div>

        {/* Theme toggle */}
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleTheme}
          title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          className="h-8 w-8 p-0"
        >
          {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>
      </header>

      {/* ── Body: sidebar + content ── */}
      <div className="flex flex-1 overflow-hidden">
        {!isMobile && sidebarContent}
        <main className="flex flex-1 flex-col overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
