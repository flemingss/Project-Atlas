/**
 * Library page — browse, manage, version-control, and export documents.
 */
import { useState, useEffect } from 'react';
import {
  Download,
  Eye,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useConnectionStore } from '@/stores/connection-store';
import { useScopeStore } from '@/stores/scope-store';
import { AuthGate } from '@/components/auth-gate';
import { ConfirmDialog } from '@/components/confirm-dialog';
import { adminApi, type DocInfo, type ChunkPreview } from '@/services/admin-api';
import { toast } from 'sonner';

export function LibraryPage() {
  const { isAdmin } = useConnectionStore();
  const { workspace, project, collection } = useScopeStore();

  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('');
  const [searchableOnly, setSearchableOnly] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Chunk viewer
  const [chunkDocId, setChunkDocId] = useState('');
  const [chunks, setChunks] = useState<ChunkPreview[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);

  // Version control
  const [versionDocId, setVersionDocId] = useState('');
  const [versionValue, setVersionValue] = useState('1');

  // Export
  const [exportFormat, setExportFormat] = useState<'full' | 'lean'>('full');

  const loadDocs = async () => {
    setLoading(true);
    try {
      const data = await adminApi.lookingGlassDocs(
        collection ? { corpus_id: collection } : undefined,
      );
      setDocs(data);
      setSelected(new Set());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load documents');
    } finally {
      setLoading(false);
    }
  };

  // Auto-load on mount and scope change
  useEffect(() => {
    if (isAdmin) loadDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collection, isAdmin]);

  if (!isAdmin) return <AuthGate />;

  const filteredDocs = docs.filter((d) => {
    if (searchableOnly && !d.is_finalized) return false;
    if (!filter) return true;
    return d.doc_id.toLowerCase().includes(filter.toLowerCase());
  });

  const toggleSelect = (docId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === filteredDocs.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredDocs.map((d) => d.doc_id)));
    }
  };

  const handleDeleteSelected = async () => {
    for (const docId of selected) {
      try {
        await adminApi.deleteDoc(docId, {
          tenant_id: workspace || undefined,
          project_id: project || undefined,
          corpus_id: collection || undefined,
        });
      } catch (e) {
        toast.error(`Failed to delete ${docId}: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    toast.success(`Deleted ${selected.size} document(s)`);
    loadDocs();
  };

  const handleExportSelected = async () => {
    for (const docId of selected) {
      try {
        const resp = await adminApi.exportDoc(docId, {
          tenant_id: workspace || undefined,
          project_id: project || undefined,
          corpus_id: collection || undefined,
          format: exportFormat,
        });
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${docId}.zip`;
        a.click();
        URL.revokeObjectURL(url);
      } catch {
        toast.error(`Export failed for ${docId}`);
      }
    }
  };

  const loadChunks = async () => {
    if (!chunkDocId) return;
    setChunksLoading(true);
    setChunks([]);
    try {
      // Load first 20 chunks
      const loaded: ChunkPreview[] = [];
      for (let i = 0; i < 20; i++) {
        try {
          const c = await adminApi.lookingGlassChunkPreview(chunkDocId, i);
          loaded.push(c);
        } catch {
          break; // no more chunks
        }
      }
      setChunks(loaded);
    } finally {
      setChunksLoading(false);
    }
  };

  const handleSetVersion = async () => {
    if (!versionDocId) return;
    try {
      await adminApi.setDocActiveVersion(versionDocId, versionValue);
      toast.success(`Active version set to ${versionValue} for ${versionDocId}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to set version');
    }
  };

  const handleShowVersion = async () => {
    if (!versionDocId) return;
    try {
      const v = await adminApi.getDocActiveVersion(versionDocId);
      toast.info(`Active version for ${v.doc_id}: ${v.active_doc_version}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to get version');
    }
  };

  const stats = {
    total: docs.length,
    searchable: docs.filter((d) => d.is_finalized).length,
    pending: docs.filter((d) => !d.is_finalized).length,
  };

  return (
    <PageShell className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">My Collection</h1>
          <p className="text-sm text-text-secondary">
            Browse and manage documents{collection ? ` in ${collection}` : ''}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadDocs} disabled={loading}>
          {loading ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 size-3.5" />}
          Refresh
        </Button>
      </div>

      {/* Stats strip */}
      <div className="flex gap-4">
        <div className="rounded-md bg-bg-card px-4 py-2">
          <p className="text-lg font-bold text-text-primary">{stats.total}</p>
          <p className="text-[11px] text-text-muted">Documents</p>
        </div>
        <div className="rounded-md bg-bg-card px-4 py-2">
          <p className="text-lg font-bold text-state-success">{stats.searchable}</p>
          <p className="text-[11px] text-text-muted">Searchable</p>
        </div>
        <div className="rounded-md bg-bg-card px-4 py-2">
          <p className="text-lg font-bold text-state-warning">{stats.pending}</p>
          <p className="text-[11px] text-text-muted">Pending</p>
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2 size-3.5 text-text-muted" />
          <Input
            placeholder="Filter by document ID…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-8 pl-8 text-xs"
          />
        </div>
        <label className="flex items-center gap-2 text-xs">
          <Checkbox
            checked={searchableOnly}
            onCheckedChange={(c) => setSearchableOnly(c === true)}
          />
          Searchable only
        </label>
      </div>

      {/* Documents table */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <Checkbox
                      checked={selected.size > 0 && selected.size === filteredDocs.length}
                      onCheckedChange={toggleAll}
                    />
                  </TableHead>
                  <TableHead className="text-xs">Document ID</TableHead>
                  <TableHead className="text-xs">Collection</TableHead>
                  <TableHead className="text-xs">Ver</TableHead>
                  <TableHead className="text-xs">Searchable</TableHead>
                  <TableHead className="text-xs">Sensitive</TableHead>
                  <TableHead className="text-xs">Type</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredDocs.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-sm text-text-muted">
                      {loading ? 'Loading…' : 'No documents found'}
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredDocs.map((d) => (
                    <TableRow key={d.doc_id}>
                      <TableCell>
                        <Checkbox
                          checked={selected.has(d.doc_id)}
                          onCheckedChange={() => toggleSelect(d.doc_id)}
                        />
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate font-mono text-xs">
                        {d.doc_id}
                      </TableCell>
                      <TableCell className="text-xs">{d.corpus_id}</TableCell>
                      <TableCell className="text-xs">{d.doc_version}</TableCell>
                      <TableCell>
                        <Badge variant={d.is_finalized ? 'default' : 'secondary'} className="text-[11px]">
                          {d.is_finalized ? 'Yes' : 'No'}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {d.is_sensitive && <Badge variant="outline" className="text-[11px]">Sensitive</Badge>}
                      </TableCell>
                      <TableCell className="text-xs">{d.source_mime_type ?? '—'}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Selected actions */}
      {selected.size > 0 && (
        <Card>
          <CardContent className="flex items-center gap-3 p-4">
            <span className="text-sm font-medium text-text-primary">
              {selected.size} document(s) selected
            </span>
            <div className="flex-1" />
            <Button variant="outline" size="sm" onClick={handleExportSelected}>
              <Download className="mr-1.5 size-3.5" />
              Export
            </Button>
            <ConfirmDialog
              title="Delete documents"
              description={`This will permanently delete ${selected.size} document(s). This cannot be undone.`}
              confirmLabel="Delete"
              variant="destructive"
              onConfirm={handleDeleteSelected}
            >
              <Button variant="destructive" size="sm">
                <Trash2 className="mr-1.5 size-3.5" />
                Delete
              </Button>
            </ConfirmDialog>
          </CardContent>
        </Card>
      )}

      <Separator />

      {/* Chunk viewer */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">View chunks</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              placeholder="Document ID to inspect"
              value={chunkDocId}
              onChange={(e) => setChunkDocId(e.target.value)}
              className="h-8 text-xs"
            />
            <Button variant="outline" size="sm" onClick={loadChunks} disabled={chunksLoading || !chunkDocId}>
              {chunksLoading ? <Loader2 className="mr-1.5 size-3 animate-spin" /> : <Eye className="mr-1.5 size-3" />}
              Load chunks
            </Button>
          </div>
          {chunks.length > 0 && (
            <div className="max-h-[400px] space-y-2 overflow-y-auto">
              {chunks.map((c, i) => (
                <div key={i} className="rounded-md border border-border p-3">
                  <div className="flex gap-2 text-[11px] text-text-muted">
                    <span>Chunk {c.chunk_index}</span>
                    <span>•</span>
                    <span>v{c.doc_version}</span>
                    <span>•</span>
                    <Badge variant={c.is_finalized ? 'default' : 'secondary'} className="text-[10px]">
                      {c.is_finalized ? 'Finalized' : 'Draft'}
                    </Badge>
                  </div>
                  <p className="mt-1 line-clamp-3 text-xs text-text-secondary">{c.text}</p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Version control */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Version used for answers</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-[11px]">Document ID</Label>
              <Input value={versionDocId} onChange={(e) => setVersionDocId(e.target.value)} className="h-7 text-xs" />
            </div>
            <div className="space-y-1">
              <Label className="text-[11px]">Set version</Label>
              <Input value={versionValue} onChange={(e) => setVersionValue(e.target.value)} className="h-7 text-xs" />
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleShowVersion} disabled={!versionDocId}>
              Show current version
            </Button>
            <Button variant="outline" size="sm" onClick={handleSetVersion} disabled={!versionDocId}>
              Set version
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Collection export */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Collection export</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-4 text-xs">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                checked={exportFormat === 'full'}
                onChange={() => setExportFormat('full')}
              />
              Full package
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                checked={exportFormat === 'lean'}
                onChange={() => setExportFormat('lean')}
              />
              Markdown only
            </label>
          </div>
          <Button
            size="sm"
            onClick={async () => {
              if (!collection) {
                toast.error('Select a collection first');
                return;
              }
              try {
                const resp = await adminApi.exportCorpus(collection, {
                  tenant_id: workspace || undefined,
                  project_id: project || undefined,
                  format: exportFormat,
                });
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${collection}-export.zip`;
                a.click();
                URL.revokeObjectURL(url);
                toast.success('Export downloaded');
              } catch (e) {
                toast.error(e instanceof Error ? e.message : 'Export failed');
              }
            }}
          >
            <Download className="mr-1.5 size-3.5" />
            Generate collection export
          </Button>
        </CardContent>
      </Card>
    </PageShell>
  );
}
