/**
 * Dashboard — enterprise health overview.
 * Shows system status, key metrics, and version info.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  FileText,
  Loader2,
  RefreshCw,
  Server,
  XCircle,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';
import { useConnectionStore } from '@/stores/connection-store';
import { useScopeStore } from '@/stores/scope-store';
import { adminApi, type LookingGlassMetrics } from '@/services/admin-api';

// ── Helpers ───────────────────────────────────────────────────────

type StatusLevel = 'ok' | 'warn' | 'error' | 'unknown';

function StatusIcon({ level }: { level: StatusLevel }) {
  if (level === 'ok') return <CheckCircle2 className="size-5 text-state-success" />;
  if (level === 'warn') return <AlertTriangle className="size-5 text-state-warning" />;
  if (level === 'error') return <XCircle className="size-5 text-state-error" />;
  return <Activity className="size-5 text-text-muted" />;
}

function MetricCard({
  label,
  value,
  icon: Icon,
  subtext,
}: {
  label: string;
  value: string | number;
  icon: React.ElementType;
  subtext?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        <div className="flex size-10 items-center justify-center rounded-lg bg-accent/10">
          <Icon className="size-5 text-accent" />
        </div>
        <div>
          <p className="text-2xl font-bold text-text-primary">{value}</p>
          <p className="text-xs text-text-muted">{label}</p>
          {subtext && <p className="text-[11px] text-text-secondary">{subtext}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function SystemCheckRow({
  label,
  level,
  detail,
}: {
  label: string;
  level: StatusLevel;
  detail?: string;
}) {
  return (
    <div className="flex items-center gap-3 py-2">
      <StatusIcon level={level} />
      <div className="flex-1">
        <span className="text-sm font-medium text-text-primary">{label}</span>
        {detail && <p className="text-xs text-text-secondary">{detail}</p>}
      </div>
      <span
        className={cn(
          'rounded-full px-2 py-0.5 text-[11px] font-medium',
          level === 'ok' && 'bg-state-success/10 text-state-success',
          level === 'warn' && 'bg-state-warning/10 text-state-warning',
          level === 'error' && 'bg-state-error/10 text-state-error',
          level === 'unknown' && 'bg-bg-card text-text-muted',
        )}
      >
        {level === 'ok' ? 'Healthy' : level === 'warn' ? 'Warning' : level === 'error' ? 'Error' : 'Unknown'}
      </span>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────

export function DashboardPage() {
  const { isConnected, isAdmin, healthData, checkConnection, isChecking } = useConnectionStore();
  const { workspace, collection, tenants } = useScopeStore();

  const [metrics, setMetrics] = useState<LookingGlassMetrics | null>(null);
  const [qdrantStatus, setQdrantStatus] = useState<StatusLevel>('unknown');
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiVersion, setApiVersion] = useState<string | null>(null);

  useEffect(() => {
    fetch('/')
      .then((r) => (r.ok ? r.json() : null))
      .then((info) => setApiVersion(info?.version ?? null))
      .catch(() => setApiVersion(null));
  }, []);

  const loadMetrics = async () => {
    if (!isAdmin) return;
    setMetricsLoading(true);
    setError(null);
    try {
      const [m, q] = await Promise.all([
        adminApi.lookingGlassMetrics(),
        adminApi.lookingGlassQdrant(),
      ]);
      setMetrics(m);
      setQdrantStatus(q && typeof q === 'object' ? 'ok' : 'error');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setQdrantStatus('unknown');
    } finally {
      setMetricsLoading(false);
    }
  };

  useEffect(() => {
    if (isAdmin) loadMetrics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  const totalRuns = metrics?.workflow_runs?.total ?? 0;
  const successRate = totalRuns > 0
    ? `${((1 - (metrics?.workflow_runs?.failure_rate ?? 0)) * 100).toFixed(1)}%`
    : '—';

  return (
    <PageShell className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Dashboard</h1>
          <p className="text-sm text-text-secondary">System overview and operational health</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            checkConnection();
            loadMetrics();
          }}
          disabled={isChecking || metricsLoading}
        >
          {(isChecking || metricsLoading) ? (
            <Loader2 className="mr-1.5 size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 size-3.5" />
          )}
          Refresh
        </Button>
      </div>

      {/* System health checks */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold">System Health</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          <SystemCheckRow
            label="Atlas API"
            level={isConnected ? 'ok' : 'error'}
            detail={
              isConnected
                ? `Environment: ${(healthData as Record<string, string>)?.env ?? 'unknown'}`
                : 'Unable to reach the Atlas API'
            }
          />
          <Separator />
          <SystemCheckRow
            label="Admin Access"
            level={isAdmin ? 'ok' : 'warn'}
            detail={isAdmin ? 'Authenticated with admin token' : 'No admin token — limited access'}
          />
          <Separator />
          <SystemCheckRow
            label="Vector Store (Qdrant)"
            level={isAdmin ? qdrantStatus : 'unknown'}
            detail={
              !isAdmin
                ? 'Requires admin access'
                : qdrantStatus === 'ok'
                  ? 'Connected and responsive'
                  : 'Could not reach Qdrant'
            }
          />
          <Separator />
          <SystemCheckRow
            label="Scope Configuration"
            level={workspace ? 'ok' : 'warn'}
            detail={
              workspace
                ? `Active: ${workspace}${collection ? ` › ${collection}` : ''}`
                : 'No workspace selected — choose one in the sidebar'
            }
          />
        </CardContent>
      </Card>

      {/* Metrics strip */}
      {isAdmin && metrics && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard
            label="Total Runs"
            value={totalRuns}
            icon={Activity}
          />
          <MetricCard
            label="Success Rate"
            value={successRate}
            icon={CheckCircle2}
          />
          <MetricCard
            label="Review Tasks"
            value={metrics.hitl?.total ?? 0}
            icon={FileText}
            subtext="HITL items pending"
          />
          <MetricCard
            label="Feedback Items"
            value={metrics.cleanup_feedback?.total ?? 0}
            icon={Database}
          />
        </div>
      )}

      {/* Info cards row */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold">Environment</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">API Version</span>
              <span className="font-mono text-text-primary">{apiVersion ?? '—'}</span>
            </div>
            <Separator />
            <div className="flex justify-between">
              <span className="text-text-muted">Environment</span>
              <span className="font-mono text-text-primary">
                {(healthData as Record<string, string>)?.env ?? '—'}
              </span>
            </div>
            <Separator />
            <div className="flex justify-between">
              <span className="text-text-muted">Workspaces</span>
              <span className="font-mono text-text-primary">{tenants.length || '—'}</span>
            </div>
            <Separator />
            <div className="flex justify-between">
              <span className="text-text-muted">UI</span>
              <span className="font-mono text-text-primary">React SPA</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold">Quick Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Link
              to="/upload"
              className="flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium text-text-secondary hover:bg-bg-card hover:text-text-primary"
            >
              <FileText className="size-4" />
              Upload a document
            </Link>
            <Link
              to="/search"
              className="flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium text-text-secondary hover:bg-bg-card hover:text-text-primary"
            >
              <Server className="size-4" />
              Search the collection
            </Link>
            <Link
              to="/review"
              className="flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium text-text-secondary hover:bg-bg-card hover:text-text-primary"
            >
              <AlertTriangle className="size-4" />
              Review flagged content
            </Link>
          </CardContent>
        </Card>
      </div>

      {error && (
        <p className="text-xs text-state-error">Error loading metrics: {error}</p>
      )}
    </PageShell>
  );
}
