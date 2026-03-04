/**
 * Admin Health page — Pipeline metrics, Looking Glass views, diagnostics.
 */
import { useState, useEffect, useCallback } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  FolderSearch,
  HardDrive,
  Loader2,
  RefreshCw,
  Server,
  Trash2,
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
import { ConfirmDialog } from '@/components/confirm-dialog';
import {
  adminApi,
  type LookingGlassMetrics,
  type RunSummary,
  type LookingGlassQdrant,
  type OrphanGroup,
  type OrphanScanResult,
  type DanglingRun,
} from '@/services/admin-api';
import { useScopeStore } from '@/stores/scope-store';
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
                  <TableRow key={r.id}>
                    <TableCell className="font-mono text-xs">{r.id}</TableCell>
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

      <Separator />

      {/* Orphan maintenance */}
      <OrphanSection />

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

// ---------------------------------------------------------------------------
// Orphan Maintenance Section
// ---------------------------------------------------------------------------

function OrphanSection() {
  const [scanResult, setScanResult] = useState<OrphanScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [open, setOpen] = useState(false);
  const [adopting, setAdopting] = useState<string | null>(null); // key of group being adopted

  // Use the global scope store — whatever the user picked in the sidebar cascade
  const { workspace, project, collection } = useScopeStore();
  const scopeReady = !!(workspace && project && collection);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      const result = await adminApi.scanOrphans();
      setScanResult(result);
      // Auto-expand if orphans found
      if (result.orphan_groups > 0 || (result.dangling_runs?.length ?? 0) > 0) setOpen(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Orphan scan failed');
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    scan();
  }, [scan]);

  const handleDeleteAll = async () => {
    try {
      const result = await adminApi.deleteOrphans();
      toast.success(`Deleted ${result.deleted_groups} orphan group(s)`);
      setScanResult(result);
      if (result.orphan_groups === 0) setOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleAdopt = async (group: OrphanGroup) => {
    if (!scopeReady) return;
    const key = `${group.tenant_id}/${group.project_id}/${group.doc_id}/${group.doc_version}`;
    setAdopting(key);
    try {
      const result = await adminApi.adoptOrphanGroup({
        old_tenant_id: group.tenant_id,
        old_project_id: group.project_id,
        old_doc_id: group.doc_id,
        old_doc_version: group.doc_version,
        tenant_id: workspace,
        project_id: project,
        corpus_id: collection,
      });
      if (result.ok) {
        toast.success(`Adopted ${group.doc_id} → ${workspace}/${project}/${collection}`);
        scan();
      } else {
        toast.error('Adoption failed');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Adoption failed');
    } finally {
      setAdopting(null);
    }
  };

  const orphans = scanResult?.sample_orphans ?? [];
  const danglingRuns = scanResult?.dangling_runs ?? [];
  const hasOrphans = (scanResult?.orphan_groups ?? 0) > 0;
  const hasDangling = danglingRuns.length > 0;
  const hasIssues = hasOrphans || hasDangling;

  const handleDeleteDangling = async (run: DanglingRun) => {
    try {
      const result = await adminApi.deleteDanglingRun(run.run_id);
      if (result.ok) {
        toast.success(`Deleted dangling run ${run.run_id} (${run.doc_id})`);
        scan(); // refresh
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  return (
    <>
      <Collapsible open={open} onOpenChange={setOpen}>
        <Card>
          <CollapsibleTrigger asChild>
            <CardHeader className="cursor-pointer pb-2">
              <CardTitle className="flex items-center gap-2 text-sm">
                <FolderSearch className="size-4" />
                Orphan maintenance
                {scanResult && (
                  <Badge
                    variant={hasIssues ? 'destructive' : 'outline'}
                    className="ml-1 text-[10px]"
                  >
                    {hasIssues
                      ? [hasOrphans ? `${scanResult.orphan_groups} orphan` : '', hasDangling ? `${danglingRuns.length} dangling` : ''].filter(Boolean).join(' · ')
                      : 'Clean'}
                  </Badge>
                )}
                <Badge variant="outline" className="ml-auto text-[10px]">
                  {open ? 'Collapse' : 'Expand'}
                </Badge>
              </CardTitle>
              <CardDescription className="text-xs">
                Qdrant chunks with no matching pipeline run, and DB runs with no indexed content.
              </CardDescription>
            </CardHeader>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={scan} disabled={scanning}>
                  {scanning
                    ? <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                    : <RefreshCw className="mr-1.5 size-3.5" />}
                  Re-scan
                </Button>
                {hasOrphans && (
                  <ConfirmDialog
                    title="Delete all orphan groups?"
                    description={`This will permanently remove ${scanResult?.orphan_points_estimated ?? 0} orphaned Qdrant points across ${scanResult?.orphan_groups ?? 0} group(s). This cannot be undone.`}
                    confirmLabel="Delete all"
                    variant="destructive"
                    onConfirm={handleDeleteAll}
                  >
                    <Button variant="destructive" size="sm">
                      <Trash2 className="mr-1.5 size-3.5" />
                      Delete all orphans
                    </Button>
                  </ConfirmDialog>
                )}
              </div>

              {hasOrphans && (
                <p className="text-xs text-text-muted">
                  Adopt target: <span className="font-mono font-semibold">{scopeReady ? `${workspace} / ${project} / ${collection}` : 'no scope selected — pick one in the sidebar'}</span>
                </p>
              )}

              {scanResult && scanResult.scanned_points > 0 && (
                <p className="text-xs text-text-muted">
                  Scanned {scanResult.scanned_points.toLocaleString()} points (max {scanResult.max_points.toLocaleString()})
                </p>
              )}

              {orphans.length === 0 ? (
                <div className="flex items-center gap-2 py-4 text-sm text-text-muted">
                  <CheckCircle2 className="size-4 text-state-success" />
                  No orphaned chunks found
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Workspace</TableHead>
                      <TableHead className="text-xs">Project</TableHead>
                      <TableHead className="text-xs">Doc ID</TableHead>
                      <TableHead className="text-xs">Version</TableHead>
                      <TableHead className="text-xs text-right">Points</TableHead>
                      <TableHead className="text-xs text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {orphans.map((g) => (
                      <TableRow key={`${g.tenant_id}/${g.project_id}/${g.doc_id}/${g.doc_version}`}>
                        <TableCell className="font-mono text-xs">{g.tenant_id}</TableCell>
                        <TableCell className="font-mono text-xs">{g.project_id}</TableCell>
                        <TableCell className="max-w-[200px] truncate font-mono text-xs" title={g.doc_id}>
                          {g.doc_id}
                        </TableCell>
                        <TableCell className="text-xs">{g.doc_version}</TableCell>
                        <TableCell className="text-right text-xs">{g.points}</TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1">
                            <ConfirmDialog
                              title={`Adopt ${g.doc_id}?`}
                              description={`Move to ${workspace} / ${project} / ${collection}. A synthetic pipeline run will be created and Qdrant payloads updated.`}
                              confirmLabel="Adopt"
                              onConfirm={() => handleAdopt(g)}
                            >
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                disabled={!scopeReady || adopting === `${g.tenant_id}/${g.project_id}/${g.doc_id}/${g.doc_version}`}
                              >
                                {adopting === `${g.tenant_id}/${g.project_id}/${g.doc_id}/${g.doc_version}`
                                  ? <Loader2 className="mr-1 size-3 animate-spin" />
                                  : null}
                                Adopt
                              </Button>
                            </ConfirmDialog>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}

              {scanResult && scanResult.orphan_groups > 20 && (
                <p className="text-xs text-text-muted">
                  Showing first 20 of {scanResult.orphan_groups} groups
                </p>
              )}

              {/* Dangling runs — DB records with no Qdrant chunks */}
              <Separator />
              <h4 className="text-xs font-semibold text-text-secondary">Dangling runs (DB only, no indexed chunks)</h4>

              {danglingRuns.length === 0 ? (
                <div className="flex items-center gap-2 py-4 text-sm text-text-muted">
                  <CheckCircle2 className="size-4 text-state-success" />
                  No dangling runs found
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Run</TableHead>
                      <TableHead className="text-xs">Doc ID</TableHead>
                      <TableHead className="text-xs">Version</TableHead>
                      <TableHead className="text-xs">Status</TableHead>
                      <TableHead className="text-xs">Node</TableHead>
                      <TableHead className="text-xs">Created</TableHead>
                      <TableHead className="text-xs text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {danglingRuns.map((r) => (
                      <TableRow key={r.run_id}>
                        <TableCell className="font-mono text-xs">#{r.run_id}</TableCell>
                        <TableCell className="max-w-[200px] truncate font-mono text-xs" title={r.doc_id}>
                          {r.doc_id}
                        </TableCell>
                        <TableCell className="text-xs">{r.doc_version}</TableCell>
                        <TableCell className="text-xs">
                          <Badge variant={r.status === 'complete' ? 'outline' : 'destructive'} className="text-[10px]">
                            {r.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs">{r.current_node}</TableCell>
                        <TableCell className="text-xs">
                          {r.created_at ? new Date(r.created_at).toLocaleDateString() : '—'}
                        </TableCell>
                        <TableCell className="text-right">
                          <ConfirmDialog
                            title={`Delete dangling run #${r.run_id}?`}
                            description={`This will remove the DB record for ${r.doc_id} (v${r.doc_version}). The run has no indexed Qdrant chunks.`}
                            confirmLabel="Delete"
                            variant="destructive"
                            onConfirm={() => handleDeleteDangling(r)}
                          >
                            <Button variant="destructive" size="sm" className="h-7 text-xs">
                              <Trash2 className="mr-1 size-3" />
                              Delete
                            </Button>
                          </ConfirmDialog>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>
    </>
  );
}
