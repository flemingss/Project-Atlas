/**
 * Admin Groups page — Manage tenants, projects, and corpora (CRUD).
 */
import { useState, useEffect } from 'react';
import {
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
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
  type Tenant,
  type Project,
  type Corpus,
} from '@/services/admin-api';
import { useScopeStore } from '@/stores/scope-store';
import { toast } from 'sonner';

export function AdminGroupsPage() {
  const { refreshAll } = useScopeStore();

  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [loading, setLoading] = useState(false);

  // Tenant form
  const [newTenantId, setNewTenantId] = useState('');
  const [newTenantName, setNewTenantName] = useState('');

  // Project form
  const [newProjectId, setNewProjectId] = useState('');
  const [newProjectTenant, setNewProjectTenant] = useState('');
  const [newProjectName, setNewProjectName] = useState('');

  // Corpus form
  const [newCorpusId, setNewCorpusId] = useState('');
  const [newCorpusProject, setNewCorpusProject] = useState('');
  const [newCorpusTenant, setNewCorpusTenant] = useState('');
  const [newCorpusName, setNewCorpusName] = useState('');

  const loadAll = async () => {
    setLoading(true);
    try {
      const [t, p, c] = await Promise.all([
        adminApi.listTenants(),
        adminApi.listProjects(),
        adminApi.listCorpora(),
      ]);
      setTenants(t);
      setProjects(p);
      setCorpora(c);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load groups');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  // ── Create handlers ─────────────────────────────────────────
  const handleCreateTenant = async () => {
    if (!newTenantId) return;
    try {
      await adminApi.createTenant({
        tenant_id: newTenantId,
        display_name: newTenantName || undefined,
      });
      toast.success(`Tenant "${newTenantId}" created`);
      setNewTenantId('');
      setNewTenantName('');
      loadAll();
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Create failed');
    }
  };

  const handleCreateProject = async () => {
    if (!newProjectId || !newProjectTenant) return;
    try {
      await adminApi.createProject({
        project_id: newProjectId,
        tenant_id: newProjectTenant,
        display_name: newProjectName || undefined,
      });
      toast.success(`Project "${newProjectId}" created`);
      setNewProjectId('');
      setNewProjectName('');
      loadAll();
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Create failed');
    }
  };

  const handleCreateCorpus = async () => {
    if (!newCorpusId || !newCorpusProject || !newCorpusTenant) return;
    try {
      await adminApi.createCorpus({
        corpus_id: newCorpusId,
        project_id: newCorpusProject,
        tenant_id: newCorpusTenant,
        display_name: newCorpusName || undefined,
      });
      toast.success(`Collection "${newCorpusId}" created`);
      setNewCorpusId('');
      setNewCorpusName('');
      loadAll();
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Create failed');
    }
  };

  // ── Delete handlers ─────────────────────────────────────────
  const handleDeleteTenant = async (id: string) => {
    try {
      await adminApi.deleteTenant(id);
      toast.success(`Tenant "${id}" deleted`);
      loadAll();
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleDeleteProject = async (id: string) => {
    try {
      await adminApi.deleteProject(id);
      toast.success(`Project "${id}" deleted`);
      loadAll();
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleDeleteCorpus = async (id: string) => {
    try {
      await adminApi.deleteCorpus(id);
      toast.success(`Collection "${id}" deleted`);
      loadAll();
      refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  return (
    <PageShell className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Groups</h1>
          <p className="text-sm text-text-secondary">
            Manage workspaces (tenants), projects, and collections (corpora)
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadAll} disabled={loading}>
          {loading ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 size-3.5" />}
          Refresh
        </Button>
      </div>

      {/* Stats */}
      <div className="flex gap-4">
        <Badge variant="outline" className="text-xs">{tenants.length} Workspaces</Badge>
        <Badge variant="outline" className="text-xs">{projects.length} Projects</Badge>
        <Badge variant="outline" className="text-xs">{corpora.length} Collections</Badge>
      </div>

      {/* ── Tenants ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Workspaces (Tenants)</CardTitle>
          <CardDescription className="text-xs">Top-level organizational unit</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {/* Create form */}
          <div className="flex gap-2">
            <Input
              placeholder="Workspace ID"
              value={newTenantId}
              onChange={(e) => setNewTenantId(e.target.value)}
              className="h-8 text-xs"
            />
            <Input
              placeholder="Display name (optional)"
              value={newTenantName}
              onChange={(e) => setNewTenantName(e.target.value)}
              className="h-8 text-xs"
            />
            <Button size="sm" onClick={handleCreateTenant} disabled={!newTenantId}>
              <Plus className="mr-1.5 size-3" />
              Add
            </Button>
          </div>

          {/* Table */}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">ID</TableHead>
                <TableHead className="text-xs">Display Name</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {tenants.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="py-4 text-center text-xs text-text-muted">
                    No workspaces
                  </TableCell>
                </TableRow>
              ) : (
                tenants.map((t) => (
                  <TableRow key={t.tenant_id}>
                    <TableCell className="font-mono text-xs">{t.tenant_id}</TableCell>
                    <TableCell className="text-xs">{t.display_name ?? '—'}</TableCell>
                    <TableCell>
                      <ConfirmDialog
                        title="Delete workspace"
                        description={`Delete workspace "${t.tenant_id}"? This may affect projects and collections under it.`}
                        confirmLabel="Delete"
                        variant="destructive"
                        onConfirm={() => handleDeleteTenant(t.tenant_id)}
                      >
                        <Button variant="ghost" size="icon" className="size-6">
                          <Trash2 className="size-3" />
                        </Button>
                      </ConfirmDialog>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* ── Projects ────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Projects</CardTitle>
          <CardDescription className="text-xs">Projects belong to a workspace</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Select value={newProjectTenant} onValueChange={setNewProjectTenant}>
              <SelectTrigger className="h-8 w-40 text-xs">
                <SelectValue placeholder="Workspace" />
              </SelectTrigger>
              <SelectContent>
                {tenants.map((t) => (
                  <SelectItem key={t.tenant_id} value={t.tenant_id}>
                    {t.tenant_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              placeholder="Project ID"
              value={newProjectId}
              onChange={(e) => setNewProjectId(e.target.value)}
              className="h-8 text-xs"
            />
            <Input
              placeholder="Name (optional)"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
              className="h-8 text-xs"
            />
            <Button size="sm" onClick={handleCreateProject} disabled={!newProjectId || !newProjectTenant}>
              <Plus className="mr-1.5 size-3" />
              Add
            </Button>
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">ID</TableHead>
                <TableHead className="text-xs">Workspace</TableHead>
                <TableHead className="text-xs">Name</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {projects.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="py-4 text-center text-xs text-text-muted">
                    No projects
                  </TableCell>
                </TableRow>
              ) : (
                projects.map((p) => (
                  <TableRow key={p.project_id}>
                    <TableCell className="font-mono text-xs">{p.project_id}</TableCell>
                    <TableCell className="text-xs">{p.tenant_id}</TableCell>
                    <TableCell className="text-xs">{p.display_name ?? '—'}</TableCell>
                    <TableCell>
                      <ConfirmDialog
                        title="Delete project"
                        description={`Delete project "${p.project_id}"?`}
                        confirmLabel="Delete"
                        variant="destructive"
                        onConfirm={() => handleDeleteProject(p.project_id)}
                      >
                        <Button variant="ghost" size="icon" className="size-6">
                          <Trash2 className="size-3" />
                        </Button>
                      </ConfirmDialog>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* ── Corpora ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Collections (Corpora)</CardTitle>
          <CardDescription className="text-xs">Collections belong to a project</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Select value={newCorpusTenant} onValueChange={setNewCorpusTenant}>
              <SelectTrigger className="h-8 w-32 text-xs">
                <SelectValue placeholder="Workspace" />
              </SelectTrigger>
              <SelectContent>
                {tenants.map((t) => (
                  <SelectItem key={t.tenant_id} value={t.tenant_id}>
                    {t.tenant_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={newCorpusProject} onValueChange={setNewCorpusProject}>
              <SelectTrigger className="h-8 w-32 text-xs">
                <SelectValue placeholder="Project" />
              </SelectTrigger>
              <SelectContent>
                {projects
                  .filter((p) => !newCorpusTenant || p.tenant_id === newCorpusTenant)
                  .map((p) => (
                    <SelectItem key={p.project_id} value={p.project_id}>
                      {p.project_id}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <Input
              placeholder="Collection ID"
              value={newCorpusId}
              onChange={(e) => setNewCorpusId(e.target.value)}
              className="h-8 text-xs"
            />
            <Input
              placeholder="Name (optional)"
              value={newCorpusName}
              onChange={(e) => setNewCorpusName(e.target.value)}
              className="h-8 text-xs"
            />
            <Button
              size="sm"
              onClick={handleCreateCorpus}
              disabled={!newCorpusId || !newCorpusProject || !newCorpusTenant}
            >
              <Plus className="mr-1.5 size-3" />
              Add
            </Button>
          </div>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">ID</TableHead>
                <TableHead className="text-xs">Project</TableHead>
                <TableHead className="text-xs">Workspace</TableHead>
                <TableHead className="text-xs">Name</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {corpora.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-4 text-center text-xs text-text-muted">
                    No collections
                  </TableCell>
                </TableRow>
              ) : (
                corpora.map((c) => (
                  <TableRow key={c.corpus_id}>
                    <TableCell className="font-mono text-xs">{c.corpus_id}</TableCell>
                    <TableCell className="text-xs">{c.project_id}</TableCell>
                    <TableCell className="text-xs">{c.tenant_id}</TableCell>
                    <TableCell className="text-xs">{c.display_name ?? '—'}</TableCell>
                    <TableCell>
                      <ConfirmDialog
                        title="Delete collection"
                        description={`Delete collection "${c.corpus_id}"?`}
                        confirmLabel="Delete"
                        variant="destructive"
                        onConfirm={() => handleDeleteCorpus(c.corpus_id)}
                      >
                        <Button variant="ghost" size="icon" className="size-6">
                          <Trash2 className="size-3" />
                        </Button>
                      </ConfirmDialog>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </PageShell>
  );
}
