/**
 * Admin Danger Zone page — DB reset, config restore, collection import.
 */
import { useState, useEffect } from 'react';
import {
  AlertTriangle,
  Database,
  Download,
  Loader2,
  RotateCcw,
  ShieldAlert,
  Upload,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Separator } from '@/components/ui/separator';
import { ConfirmDialog } from '@/components/confirm-dialog';
import { adminApi, type ConfigVersionSummary } from '@/services/admin-api';
import { toast } from 'sonner';

/** Reset-database and restore-stock-config use the backend's *strict* admin
 *  dependency: unlike every other admin route, they refuse to run when no
 *  ATLAS_ADMIN_TOKEN is configured — deliberately, so a dev-mode stack with
 *  open admin endpoints can never wipe data by accident. Translate that 401
 *  into something actionable instead of surfacing the raw detail string. */
const STRICT_AUTH_HINT =
  'This operation requires ATLAS_ADMIN_TOKEN to be set on the API (it is refused ' +
  'even in dev, by design). Set it and reopen the SPA as /app?token=<token>. ' +
  'For routine test-data flushes, use scripts/flush.ps1 instead.';

function destructiveError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (msg.includes('401') || msg.includes('Admin token not configured')) {
    return STRICT_AUTH_HINT;
  }
  return msg;
}

export function AdminDangerPage() {
  // DB Reset
  const [resetPostgres, setResetPostgres] = useState(true);
  const [clearQdrant, setClearQdrant] = useState(true);
  const [clearArtifacts, setClearArtifacts] = useState(true);
  const [resetting, setResetting] = useState(false);

  // Config restore
  const [restorePipeline, setRestorePipeline] = useState(true);
  const [restoreModels, setRestoreModels] = useState(true);
  const [restoring, setRestoring] = useState(false);

  // Config versions
  const [configVersions, setConfigVersions] = useState<ConfigVersionSummary[]>([]);
  const [reloading, setReloading] = useState(false);

  // Collection import
  const [importCorpus, setImportCorpus] = useState('');
  const [importing, setImporting] = useState(false);

  // Scoped export
  const [exportTenant, setExportTenant] = useState('');
  const [exportProject, setExportProject] = useState('');
  const [exportCorpus, setExportCorpus] = useState('');
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    adminApi.configVersions().then(setConfigVersions).catch(() => {});
  }, []);

  // ── Handlers ────────────────────────────────────────────────

  const handleResetDb = async () => {
    setResetting(true);
    try {
      await adminApi.resetDb({
        postgres: resetPostgres,
        qdrant: clearQdrant,
        artifacts: clearArtifacts,
      });
      toast.success('Database reset complete');
    } catch (e) {
      toast.error(destructiveError(e));
    } finally {
      setResetting(false);
    }
  };

  const handleRestoreConfig = async () => {
    setRestoring(true);
    try {
      await adminApi.restoreStockConfig({
        pipeline: restorePipeline,
        models: restoreModels,
      });
      toast.success('Stock config restored');
    } catch (e) {
      toast.error(destructiveError(e));
    } finally {
      setRestoring(false);
    }
  };

  const handleReloadYaml = async () => {
    setReloading(true);
    try {
      await adminApi.reloadYaml();
      toast.success('YAML config reloaded from disk');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Reload failed');
    } finally {
      setReloading(false);
    }
  };

  const handleActivateVersion = async (id: number) => {
    try {
      await adminApi.activateConfigVersion(id);
      toast.success(`Config version ${id} activated`);
      const updated = await adminApi.configVersions();
      setConfigVersions(updated);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Activation failed');
    }
  };

  const handleImportCorpus = async (file: File) => {
    if (!importCorpus) {
      toast.error('Select a collection first');
      return;
    }
    setImporting(true);
    const fd = new FormData();
    fd.append('file', file);
    try {
      await adminApi.importCorpus(importCorpus, fd);
      toast.success(`Import into "${importCorpus}" complete`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  };

  const handleScopedExport = async () => {
    setExporting(true);
    try {
      // scope is required and must match how deep the filters go: the most
      // specific field the operator filled in decides it.
      const scope = exportCorpus ? 'corpus' : exportProject ? 'project' : 'tenant';
      const resp = await adminApi.exportScoped({
        scope,
        tenant_id: exportTenant || undefined,
        project_id: exportProject || undefined,
        corpus_id: exportCorpus || undefined,
      });
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'atlas-export.zip';
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Export downloaded');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  return (
    <PageShell className="space-y-6">
      <div className="flex items-center gap-2">
        <ShieldAlert className="size-5 text-state-error" />
        <div>
          <h1 className="text-xl font-bold text-state-error">Danger zone</h1>
          <p className="text-sm text-text-secondary">
            Destructive operations — proceed with caution
          </p>
        </div>
      </div>

      {/* ── DB Reset ───────────────────────────────────────────── */}
      <Card className="border-state-error/30">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm text-state-error">
            <Database className="size-4" />
            Reset database
          </CardTitle>
          <CardDescription className="text-xs">
            Wipe data stores. This is permanent and cannot be undone.
            <span className="mt-1 block text-text-muted">
              Requires <code className="font-mono">ATLAS_ADMIN_TOKEN</code> on the API — refused
              even in dev, by design. For routine test-data flushes use{' '}
              <code className="font-mono">scripts/flush.ps1</code>.
            </span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={resetPostgres} onCheckedChange={(c) => setResetPostgres(c === true)} />
              PostgreSQL (all tables)
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={clearQdrant} onCheckedChange={(c) => setClearQdrant(c === true)} />
              Qdrant (vector store)
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={clearArtifacts} onCheckedChange={(c) => setClearArtifacts(c === true)} />
              Artifacts (files on disk)
            </label>
          </div>
          <ConfirmDialog
            title="Reset database"
            description="This will permanently delete all selected data stores. This action cannot be reversed."
            confirmLabel="Reset now"
            variant="destructive"
            onConfirm={handleResetDb}
          >
            <Button variant="destructive" disabled={resetting}>
              {resetting ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <AlertTriangle className="mr-1.5 size-3.5" />}
              Reset database
            </Button>
          </ConfirmDialog>
        </CardContent>
      </Card>

      {/* ── Config Restore ─────────────────────────────────────── */}
      <Card className="border-state-error/30">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm text-state-error">
            <RotateCcw className="size-4" />
            Restore stock config
          </CardTitle>
          <CardDescription className="text-xs">
            Overwrite current YAML with the bundled defaults
            <span className="mt-1 block text-text-muted">
              Requires <code className="font-mono">ATLAS_ADMIN_TOKEN</code> on the API — refused
              even in dev, by design.
            </span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={restorePipeline} onCheckedChange={(c) => setRestorePipeline(c === true)} />
              Pipeline config (pipeline.yaml)
            </label>
            <label className="flex items-center gap-2 text-xs">
              <Checkbox checked={restoreModels} onCheckedChange={(c) => setRestoreModels(c === true)} />
              Models config (models.yaml)
            </label>
          </div>
          <ConfirmDialog
            title="Restore stock config"
            description="This will overwrite your current configuration with the factory defaults."
            confirmLabel="Restore"
            variant="destructive"
            onConfirm={handleRestoreConfig}
          >
            <Button variant="destructive" disabled={restoring}>
              {restoring ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RotateCcw className="mr-1.5 size-3.5" />}
              Restore to defaults
            </Button>
          </ConfirmDialog>
        </CardContent>
      </Card>

      {/* ── Reload YAML ─────────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Reload YAML from disk</CardTitle>
          <CardDescription className="text-xs">
            Re-read pipeline.yaml and models.yaml without restarting
            <span className="mt-1 block text-text-muted">
              Safe and non-destructive: it refreshes the YAML layer only. An active
              config version (below) still takes precedence over it.
            </span>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={handleReloadYaml} disabled={reloading}>
            {reloading ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RotateCcw className="mr-1.5 size-3.5" />}
            Reload YAML
          </Button>
        </CardContent>
      </Card>

      <Separator />

      {/* ── Config Versions ─────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Config version history</CardTitle>
          <CardDescription className="text-xs">
            Activate a previous config snapshot to roll back
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {configVersions.length === 0 ? (
            <p className="text-xs text-text-muted">No config versions found</p>
          ) : (
            configVersions.map((cv) => (
              <div key={cv.id} className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                <div className="space-y-0.5">
                  <p className="text-xs font-medium">
                    Version {cv.id}
                    {cv.is_active && (
                      <Badge variant="default" className="ml-2 text-[10px]">Active</Badge>
                    )}
                  </p>
                  <p className="text-[11px] text-text-muted">
                    {cv.created_at ? new Date(cv.created_at).toLocaleString() : ''}
                    {cv.notes ? ` — ${cv.notes}` : ''}
                  </p>
                </div>
                {!cv.is_active && (
                  <ConfirmDialog
                    title={`Activate config version ${cv.id}?`}
                    description="This swaps the pipeline and model configuration for every future ingest — chunking, judge thresholds, cleanup rules and model roles all come from this snapshot. Already-indexed documents are unaffected."
                    confirmLabel="Activate"
                    onConfirm={() => handleActivateVersion(cv.id)}
                  >
                    <Button variant="outline" size="sm">
                      Activate
                    </Button>
                  </ConfirmDialog>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Separator />

      {/* ── Scoped export ─────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Download className="size-4" />
            Scoped export
          </CardTitle>
          <CardDescription className="text-xs">
            Export data filtered by workspace / project / collection
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <Label className="text-[11px]">Workspace</Label>
              <Input value={exportTenant} onChange={(e) => setExportTenant(e.target.value)} className="h-7 text-xs" placeholder="(all)" />
            </div>
            <div className="space-y-1">
              <Label className="text-[11px]">Project</Label>
              <Input value={exportProject} onChange={(e) => setExportProject(e.target.value)} className="h-7 text-xs" placeholder="(all)" />
            </div>
            <div className="space-y-1">
              <Label className="text-[11px]">Collection</Label>
              <Input value={exportCorpus} onChange={(e) => setExportCorpus(e.target.value)} className="h-7 text-xs" placeholder="(all)" />
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={handleScopedExport} disabled={exporting}>
            {exporting ? <Loader2 className="mr-1.5 size-3 animate-spin" /> : <Download className="mr-1.5 size-3" />}
            Export
          </Button>
        </CardContent>
      </Card>

      {/* ── Collection import ─────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Upload className="size-4" />
            Collection import
          </CardTitle>
          <CardDescription className="text-xs">
            Import a previously exported collection archive (.zip)
            <span className="mt-1 block text-text-muted">
              Each document is re-ingested through the full pipeline — it re-embeds and
              re-runs judge/refine, so it costs LLM calls and can route documents to
              Review. Chunks for a doc id + version already present are replaced.
            </span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <Label className="text-[11px]">Target collection</Label>
            <Input
              value={importCorpus}
              onChange={(e) => setImportCorpus(e.target.value)}
              placeholder="Collection ID"
              className="h-8 text-xs"
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={importing || !importCorpus}
            onClick={() => {
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = '.zip';
              input.onchange = (e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (file) handleImportCorpus(file);
              };
              input.click();
            }}
          >
            {importing ? <Loader2 className="mr-1.5 size-3 animate-spin" /> : <Upload className="mr-1.5 size-3" />}
            Choose file & import
          </Button>
        </CardContent>
      </Card>
    </PageShell>
  );
}
