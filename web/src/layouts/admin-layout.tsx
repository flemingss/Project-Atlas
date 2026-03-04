/**
 * AdminLayout — wrapper for /admin/* routes with a horizontal sub-tab bar.
 */
import { NavLink, Outlet } from 'react-router-dom';
import { Activity, FileWarning, FolderTree, ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useConnectionStore } from '@/stores/connection-store';
import { AuthGate } from '@/components/auth-gate';

const ADMIN_TABS = [
  { to: '/admin/health', label: 'Health & Metrics', icon: Activity },
  { to: '/admin/cleanup', label: 'Cleanup & Feedback', icon: FileWarning },
  { to: '/admin/groups', label: 'Groups', icon: FolderTree },
  { to: '/admin/danger', label: 'Danger Zone', icon: ShieldAlert },
] as const;

export function AdminLayout() {
  const isAdmin = useConnectionStore((s) => s.isAdmin);

  if (!isAdmin) {
    return <AuthGate message="The Admin panel requires an admin token. Enter it in the sidebar to continue." />;
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Sub-tab bar */}
      <nav className="flex shrink-0 items-center gap-1 border-b border-border bg-bg-surface px-4 py-1">
        {ADMIN_TABS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                isActive
                  ? 'bg-accent/10 text-accent'
                  : 'text-text-secondary hover:bg-bg-card hover:text-text-primary',
                to === '/admin/danger' && isActive && 'bg-state-error/10 text-state-error',
              )
            }
          >
            <Icon className="size-3.5" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Admin page content */}
      <div className="flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  );
}
