/**
 * AppSidebar — collapsible sidebar with connection + scope selector.
 * Persistent on desktop, Sheet-based on mobile.
 */
import { useEffect, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Globe,
  Loader2,
  Lock,
  RefreshCw,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { useConnectionStore } from '@/stores/connection-store';
import { useScopeStore } from '@/stores/scope-store';

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className={cn(
          'inline-block size-2 rounded-full',
          ok ? 'bg-state-success' : 'bg-state-error',
        )}
      />
      <span className={ok ? 'text-text-primary' : 'text-text-muted'}>{label}</span>
    </div>
  );
}

export function AppSidebar({ className }: { className?: string }) {
  const {
    isConnected,
    isAdmin,
    isChecking,
    error: connError,
    checkConnection,
    setToken,
    getToken,
  } = useConnectionStore();

  const {
    workspace,
    project,
    collection,
    tenants,
    projects,
    corpora,
    isLoading: scopeLoading,
    setWorkspace,
    setProject,
    setCollection,
    loadTenants,
    refreshAll,
  } = useScopeStore();

  const [tokenInput, setTokenInput] = useState(getToken() ?? '');
  const [connOpen, setConnOpen] = useState(!isConnected);

  // Auto-connect on mount
  useEffect(() => {
    checkConnection();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load tenants when connected + admin
  useEffect(() => {
    if (isConnected && isAdmin) {
      loadTenants();
    }
  }, [isConnected, isAdmin, loadTenants]);

  // Auto-collapse connection section after connecting
  useEffect(() => {
    if (isConnected) setConnOpen(false);
  }, [isConnected]);

  const handleConnect = () => {
    if (tokenInput.trim()) {
      setToken(tokenInput.trim());
    }
    checkConnection();
  };

  return (
    <aside
      className={cn(
        'flex h-full w-[260px] shrink-0 flex-col border-r border-border bg-bg-surface',
        className,
      )}
    >
      {/* ── Connection ── */}
      <div className="px-3 pt-3">
        <button
          onClick={() => setConnOpen(!connOpen)}
          className="flex w-full items-center gap-1.5 text-xs font-semibold text-text-secondary hover:text-text-primary"
        >
          {connOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          Connection
        </button>
      </div>

      {connOpen && (
        <div className="space-y-2 px-3 py-2">
          <div className="space-y-1">
            <Label className="text-[11px]">Admin Token</Label>
            <Input
              type="password"
              placeholder="Enter admin token…"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              className="h-7 text-xs"
              onKeyDown={(e) => e.key === 'Enter' && handleConnect()}
            />
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-7 w-full text-xs"
            onClick={handleConnect}
            disabled={isChecking}
          >
            {isChecking ? (
              <Loader2 className="mr-1.5 size-3 animate-spin" />
            ) : (
              <Globe className="mr-1.5 size-3" />
            )}
            Test connection
          </Button>
          {connError && (
            <p className="text-[11px] text-state-error">{connError}</p>
          )}
        </div>
      )}

      <div className="space-y-1 px-3 py-2">
        <StatusPill ok={isConnected} label={isConnected ? 'Connected to Atlas' : 'Not connected'} />
        <StatusPill ok={isAdmin} label={isAdmin ? 'Admin access' : 'No admin token'} />
      </div>

      <Separator />

      {/* ── Scope selector ── */}
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-text-secondary">Scope</span>
          {isAdmin && (
            <Button
              variant="ghost"
              size="sm"
              className="h-5 w-5 p-0"
              onClick={() => refreshAll()}
              disabled={scopeLoading}
            >
              <RefreshCw className={cn('size-3', scopeLoading && 'animate-spin')} />
            </Button>
          )}
        </div>

        {isAdmin ? (
          <>
            <div className="space-y-1">
              <Label className="text-[11px]">Workspace</Label>
              <Select value={workspace} onValueChange={setWorkspace}>
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue placeholder="Select workspace…" />
                </SelectTrigger>
                <SelectContent>
                  {tenants.map((t) => (
                    <SelectItem key={t.tenant_id} value={t.tenant_id} className="text-xs">
                      {t.display_name || t.tenant_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label className="text-[11px]">Project</Label>
              <Select value={project} onValueChange={setProject} disabled={!workspace}>
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue placeholder={workspace ? 'Select project…' : '—'} />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.project_id} value={p.project_id} className="text-xs">
                      {p.display_name || p.project_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label className="text-[11px]">Collection</Label>
              <Select value={collection} onValueChange={setCollection} disabled={!project}>
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue placeholder={project ? 'Select collection…' : '—'} />
                </SelectTrigger>
                <SelectContent>
                  {corpora.map((c) => (
                    <SelectItem key={c.corpus_id} value={c.corpus_id} className="text-xs">
                      {c.display_name || c.corpus_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2 rounded-md bg-bg-card px-3 py-2">
            <Lock className="size-3.5 text-text-muted" />
            <span className="text-xs text-text-muted">Connect with admin token to select scope</span>
          </div>
        )}

        {/* Active scope breadcrumb */}
        {workspace && (
          <div className="rounded-md bg-bg-card px-3 py-2">
            <div className="text-[11px] font-medium text-text-muted">Active scope</div>
            <div className="mt-1 text-xs font-semibold text-text-primary">
              {workspace}
              {project && <span className="text-text-muted"> › </span>}
              {project}
              {collection && <span className="text-text-muted"> › </span>}
              {collection}
            </div>
          </div>
        )}
      </div>

      {/* ── Bottom status ── */}
      <div className="border-t border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
          {isConnected ? (
            <Wifi className="size-3 text-state-success" />
          ) : (
            <WifiOff className="size-3 text-state-error" />
          )}
          Atlas Operator Console
        </div>
      </div>
    </aside>
  );
}
