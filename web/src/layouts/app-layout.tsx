import { FileText, Home, Moon, Sun, Zap } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { useThemeContext } from '@/components/theme-provider';

const NAV_ITEMS = [
  { to: '/', label: 'Home', icon: Home },
  { to: '/vlm-ingest', label: 'VLM Ingest', icon: Zap },
] as const;

export function AppLayout() {
  const { isDark, toggleTheme } = useThemeContext();

  return (
    <div className="flex h-full flex-col">
      {/* ── Top bar ── */}
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-bg-surface px-4">
        <NavLink to="/" className="flex items-center gap-2">
          <FileText className="size-5 text-accent" />
          <span className="text-sm font-bold tracking-wide text-accent">ATLAS</span>
        </NavLink>
        <span className="text-text-muted">›</span>

        <nav className="flex items-center gap-1">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                  isActive
                    ? 'bg-accent/10 text-accent'
                    : 'text-text-secondary hover:bg-bg-card hover:text-text-primary',
                )
              }
            >
              <Icon className="size-3.5" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="flex-1" />

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

      {/* ── Page content ── */}
      <Outlet />
    </div>
  );
}
