/**
 * Admin Health page — Pipeline metrics, Looking Glass views, diagnostics.
 */
import { useState, useEffect } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  HardDrive,
  Loader2,
  RefreshCw,
  Server,
  XCircle,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { adminApi, type LookingGlassMetrics, type RunSummary, type LookingGlassQdrant } from '@/services/admin-api';
import { toast } from 'sonner';

export function AdminHealthPage() {
  const [metrics, setMetrics] = useState<LookingGlassMetrics | null>(null);
  const [qdrant, setQdrant] = useState<LookingGlassQdrant | null>(null);
  const [ledgerSummary, setLedgerSummary] = useState<Record<string, unknown> | null>(null);
  const [inFlight, setInFlight] = useState<unknown[] | null>(null);
  const [failures, setFailures] = useState<Record<string, unknown> | null>(null);
  const [inventory, setInventory] = useState<Record<string, unknown> | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [selfTestResult, setSelfTestResult] = useState<Record<string, unknown> | null>(null);
  const [selfTestRunning, setSelfTestRunning] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [m, q, ls, inf, fa, inv, r] = await Promise.allSettled([
        adminApi.lookingGlassMetrics(),
        adminApi.lookingGlassQdrant(),
        adminApi.lookingGlassLedgerSummary(),
        adminApi.lookingGlassInFlight(),
        adminApi.lookingGlassFailures(),
        adminApi.lookingGlassInventory(),
        adminApi.listRuns({ limit: 20 }),
      ]);
      if (m.status === 'fulfilled') setMetrics(m.value);
      if (q.status === 'fulfilled') setQdrant(q.value);
      if (ls.status === 'fulfilled') setLedgerSummary(ls.value);
      if (inf.status === 'fulfilled') setInFlight(inf.value);
      if (fa.status === 'fulfilled') setFailures(fa.value);
      if (inv.status === 'fulfilled') setInventory(inv.value);
      if (r.status === 'fulfilled') setRuns(r.value);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const runSelfTest = async () => {
    setSelfTestRunning(true);
    try {
      const result = await adminApi.selfTest();
      setSelfTestResult(result);
      toast.success('Self-test complete');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Self-test failed');
    } finally {
      setSelfTestRunning(false);
    }
  };

  const totalRuns = metrics?.workflow_runs?.total ?? 0;
  const successRate = metrics
    ? totalRuns > 0
      ? ((1 - (metrics.workflow_runs.failure_rate ?? 0)) * 100).toFixed(1)
      : '—'
    : '—';

  return (
    <PageShell className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Health & metrics</h1>
          <p className="text-sm text-text-secondary">
            Pipeline observability, Looking Glass, and diagnostics
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadAll} disabled={loading}>
            {loading ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 size-3.5" />}
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={runSelfTest} disabled={selfTestRunning}>
            {selfTestRunning ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <Server className="mr-1.5 size-3.5" />}
            Self-test
          </Button>
        </div>
      </div>

      {/* Metrics strip */}
      {metrics && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard icon={Activity} label="Total runs" value={totalRuns} />
          <MetricCard icon={CheckCircle2} label="Successes" value={totalRuns - (metrics.node_runs?.failed ?? 0)} color="text-state-success" />
          <MetricCard icon={XCircle} label="Failures" value={metrics.node_runs?.failed ?? 0} color="text-state-error" />
          <MetricCard icon={Clock} label="HITL waiting" value={metrics.hitl?.total ?? 0} color="text-state-warning" />
          <MetricCard icon={Activity} label="Success rate" value={`${successRate}%`} />
        </div>
      )}

      {/* Qdrant status */}
      <JsonSection title="Qdrant cluster" icon={<Database className="size-4" />} data={qdrant} />

      {/* Ledger summary */}
      <JsonSection title="Ledger summary" icon={<Activity className="size-4" />} data={ledgerSummary} />

      {/* In-flight */}
      <JsonSection title="In-flight runs" icon={<Loader2 className="size-4" />} data={inFlight} />

      {/* Failures */}
      <JsonSection title="Recent failures" icon={<AlertTriangle className="size-4" />} data={failures} />

      {/* Inventory */}
      <JsonSection title="Document inventory" icon={<HardDrive className="size-4" />} data={inventory} />

      <Separator />

      {/* Recent runs table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Recent pipeline runs</CardTitle>
          <CardDescription className="text-xs">Last 20 runs across all tenants</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">Run ID</TableHead>
                <TableHead className="text-xs">Doc ID</TableHead>
                <TableHead className="text-xs">Version</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="text-xs">Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-6 text-center text-sm text-text-muted">
                    No runs found
                  </TableCell>
                </TableRow>
              ) : (
                runs.map((r) => (
                  <TableRow key={r.run_id}>
                    <TableCell className="font-mono text-xs">{r.run_id}</TableCell>
                    <TableCell className="max-w-[160px] truncate font-mono text-xs">
                      {r.doc_id ?? '—'}
                    </TableCell>
                    <TableCell className="text-xs">{r.doc_version ?? '—'}</TableCell>
                    <TableCell>
                      <Badge
                        variant={r.status === 'completed' ? 'default' : r.status === 'failed' ? 'destructive' : 'secondary'}
                        className="text-[11px]"
                      >
                        {r.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-text-muted">
                      {r.created_at ? new Date(r.created_at).toLocaleString() : '—'}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Self-test result */}
      {selfTestResult && (
        <JsonSection title="Self-test result" icon={<Server className="size-4" />} data={selfTestResult} defaultOpen />
      )}
    </PageShell>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <Icon className={`size-5 ${color ?? 'text-text-muted'}`} />
        <div>
          <p className={`text-lg font-bold ${color ?? 'text-text-primary'}`}>{value}</p>
          <p className="text-[11px] text-text-muted">{label}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function JsonSection({
  title,
  icon,
  data,
  defaultOpen = false,
}: {
  title: string;
  icon: React.ReactNode;
  data: Record<string, unknown> | unknown[] | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!data) return null;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card>
        <CollapsibleTrigger asChild>
          <CardHeader className="cursor-pointer pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              {icon}
              {title}
              <Badge variant="outline" className="ml-auto text-[10px]">
                {open ? 'Collapse' : 'Expand'}
              </Badge>
            </CardTitle>
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent>
            <pre className="max-h-[400px] overflow-auto rounded-md bg-bg-card p-3 font-mono text-xs">
              {JSON.stringify(data, null, 2)}
            </pre>
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}
