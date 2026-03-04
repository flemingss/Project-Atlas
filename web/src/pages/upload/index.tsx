/**
 * Upload page — file/text ingest + processing history.
 */
import { useState, useRef } from 'react';
import {
  FileUp,
  Loader2,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  FileText,
  RefreshCw,
} from 'lucide-react';
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Separator } from '@/components/ui/separator';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useScopeStore } from '@/stores/scope-store';
import { useConnectionStore } from '@/stores/connection-store';
import { ragApi, type IngestResponse } from '@/services/rag-api';
import { adminApi, type RunSummary } from '@/services/admin-api';

export function UploadPage() {
  const { workspace, project, collection } = useScopeStore();
  const { isAdmin } = useConnectionStore();

  // ── Upload form state ──
  const [mode, setMode] = useState<'file' | 'text'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [textContent, setTextContent] = useState('');
  const [docName, setDocName] = useState('');
  const [docId, setDocId] = useState('');
  const [docVersion, setDocVersion] = useState('1');
  const [mimeOverride, setMimeOverride] = useState('');
  const [searchable, setSearchable] = useState(true);
  const [sensitive, setSensitive] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<IngestResponse | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // ── Processing history ──
  const [historyOpen, setHistoryOpen] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [maxRows, setMaxRows] = useState(25);

  const handleUpload = async () => {
    setUploading(true);
    setResult(null);
    setUploadError(null);
    try {
      let resp: IngestResponse;
      if (mode === 'file') {
        if (!file) throw new Error('No file selected');
        const fd = new FormData();
        fd.append('file', file);
        if (docName) fd.append('doc_name', docName);
        if (docId) fd.append('doc_id', docId);
        if (docVersion) fd.append('doc_version', docVersion);
        if (mimeOverride) fd.append('mime_type', mimeOverride);
        fd.append('is_finalized', String(searchable));
        fd.append('is_sensitive', String(sensitive));
        if (workspace) fd.append('tenant_id', workspace);
        if (project) fd.append('project_id', project);
        if (collection) fd.append('corpus_id', collection);
        resp = await ragApi.ingestFile(fd);
      } else {
        if (!textContent.trim()) throw new Error('No text provided');
        resp = await ragApi.ingestText({
          text: textContent,
          doc_name: docName || undefined,
          doc_id: docId || undefined,
          doc_version: docVersion || undefined,
          is_finalized: searchable,
          is_sensitive: sensitive,
          tenant_id: workspace || undefined,
          project_id: project || undefined,
          corpus_id: collection || undefined,
        });
      }
      setResult(resp);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const loadRuns = async () => {
    setRunsLoading(true);
    try {
      const data = await adminApi.listRuns({ limit: maxRows });
      setRuns(data);
    } catch {
      // ignore
    } finally {
      setRunsLoading(false);
    }
  };

  return (
    <PageShell className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text-primary">Upload</h1>
        <p className="text-sm text-text-secondary">Add documents to your collection</p>
      </div>

      <Tabs value={mode} onValueChange={(v) => setMode(v as 'file' | 'text')}>
        <TabsList>
          <TabsTrigger value="file">Upload file</TabsTrigger>
          <TabsTrigger value="text">Write or paste content</TabsTrigger>
        </TabsList>

        <TabsContent value="file" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Add a document</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div
                onClick={() => fileRef.current?.click()}
                className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-border py-8 hover:border-accent/50 hover:bg-bg-card"
              >
                <FileUp className="size-8 text-text-muted" />
                <span className="text-sm text-text-secondary">
                  {file ? file.name : 'Click to select a file or drag & drop'}
                </span>
                <input
                  ref={fileRef}
                  type="file"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) {
                      setFile(f);
                      if (!docName) setDocName(f.name);
                    }
                  }}
                />
              </div>

              <div className="space-y-1">
                <Label className="text-xs">Document name</Label>
                <Input
                  value={docName}
                  onChange={(e) => setDocName(e.target.value)}
                  placeholder="my-document.pdf"
                  className="h-8 text-xs"
                />
              </div>

              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-xs">
                  <Checkbox
                    checked={searchable}
                    onCheckedChange={(c) => setSearchable(c === true)}
                  />
                  Include in search results
                </label>
                <label className="flex items-center gap-2 text-xs">
                  <Checkbox
                    checked={sensitive}
                    onCheckedChange={(c) => setSensitive(c === true)}
                  />
                  Sensitive
                </label>
              </div>

              <button
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary"
              >
                {showAdvanced ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
                Advanced options
              </button>

              {showAdvanced && (
                <div className="space-y-3 rounded-md bg-bg-card p-3">
                  <div className="space-y-1">
                    <Label className="text-[11px]">Document ID</Label>
                    <Input value={docId} onChange={(e) => setDocId(e.target.value)} className="h-7 text-xs" placeholder="Auto-generated if empty" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[11px]">Document version</Label>
                    <Input value={docVersion} onChange={(e) => setDocVersion(e.target.value)} className="h-7 text-xs" />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[11px]">MIME type override</Label>
                    <Input value={mimeOverride} onChange={(e) => setMimeOverride(e.target.value)} className="h-7 text-xs" placeholder="Auto-detected if empty" />
                  </div>
                </div>
              )}

              <Button onClick={handleUpload} disabled={uploading || !file} className="w-full">
                {uploading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <FileUp className="mr-2 size-4" />}
                Upload and index
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="text" className="mt-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Write or paste content</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Textarea
                value={textContent}
                onChange={(e) => setTextContent(e.target.value)}
                placeholder="Paste or type your content here…"
                className="min-h-[200px] text-sm"
              />

              <div className="space-y-1">
                <Label className="text-xs">Document name</Label>
                <Input
                  value={docName}
                  onChange={(e) => setDocName(e.target.value)}
                  placeholder="my-document"
                  className="h-8 text-xs"
                />
              </div>

              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-xs">
                  <Checkbox checked={searchable} onCheckedChange={(c) => setSearchable(c === true)} />
                  Include in search results
                </label>
                <label className="flex items-center gap-2 text-xs">
                  <Checkbox checked={sensitive} onCheckedChange={(c) => setSensitive(c === true)} />
                  Sensitive
                </label>
              </div>

              <Button onClick={handleUpload} disabled={uploading || !textContent.trim()} className="w-full">
                {uploading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <FileText className="mr-2 size-4" />}
                Upload and index
              </Button>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ── Upload result ── */}
      {result && (
        <Card className={result.error_message ? 'border-state-error' : 'border-state-success'}>
          <CardContent className="flex items-start gap-3 p-4">
            {result.error_message ? (
              <XCircle className="mt-0.5 size-5 shrink-0 text-state-error" />
            ) : (
              <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-state-success" />
            )}
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-semibold text-text-primary">
                {result.doc_id}
              </p>
              <div className="flex flex-wrap gap-2 text-xs text-text-secondary">
                <span>ID: {result.doc_id}</span>
                <span>•</span>
                <span>{result.chunks_upserted} chunks</span>
              </div>
              <div className="flex gap-2">
                <Badge variant={result.ok ? 'default' : 'secondary'}>
                  {result.ok ? 'Ingested' : 'Failed'}
                </Badge>
              </div>
              {result.error_message && (
                <p className="text-xs text-state-error">{result.error_message}</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}
      {uploadError && (
        <Card className="border-state-error">
          <CardContent className="flex items-center gap-3 p-4">
            <XCircle className="size-5 shrink-0 text-state-error" />
            <p className="text-sm text-state-error">{uploadError}</p>
          </CardContent>
        </Card>
      )}

      {/* ── Processing history ── */}
      {isAdmin && (
        <>
          <Separator />
          <button
            onClick={() => {
              setHistoryOpen(!historyOpen);
              if (!historyOpen && runs.length === 0) loadRuns();
            }}
            className="flex items-center gap-1.5 text-sm font-medium text-text-secondary hover:text-text-primary"
          >
            {historyOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
            Processing history
          </button>

          {historyOpen && (
            <Card>
              <CardContent className="space-y-3 p-4">
                <div className="flex items-center gap-3">
                  <div className="space-y-1">
                    <Label className="text-[11px]">Max rows</Label>
                    <Input
                      type="number"
                      value={maxRows}
                      onChange={(e) => setMaxRows(Number(e.target.value) || 25)}
                      className="h-7 w-20 text-xs"
                    />
                  </div>
                  <Button variant="outline" size="sm" onClick={loadRuns} disabled={runsLoading} className="mt-4">
                    {runsLoading ? <Loader2 className="mr-1.5 size-3 animate-spin" /> : <RefreshCw className="mr-1.5 size-3" />}
                    Refresh
                  </Button>
                </div>

                {runs.length > 0 ? (
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="text-xs">Run ID</TableHead>
                          <TableHead className="text-xs">Status</TableHead>
                          <TableHead className="text-xs">Doc ID</TableHead>
                          <TableHead className="text-xs">Version</TableHead>
                          <TableHead className="text-xs">Updated</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {runs.map((r) => (
                          <TableRow key={r.run_id}>
                            <TableCell className="font-mono text-xs">{r.run_id}</TableCell>
                            <TableCell>
                              <Badge variant={r.status === 'completed' ? 'default' : 'secondary'} className="text-[11px]">
                                {r.status}
                              </Badge>
                            </TableCell>
                            <TableCell className="max-w-[150px] truncate text-xs">{r.doc_id ?? '—'}</TableCell>
                            <TableCell className="text-xs">{r.doc_version ?? '—'}</TableCell>
                            <TableCell className="text-xs">{r.updated_at ?? r.created_at ?? '—'}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                ) : (
                  <p className="text-xs text-text-muted">No runs loaded yet.</p>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </PageShell>
  );
}
