/**
 * Unified Ingest wizard page.
 *
 * Step 1:  Method + Upload   (all methods)
 * Step 2:  Configure         (Docling: backend picker · VLM: DPI/crop/prompt)
 * Step 3:  Pages             (VLM only — page grid)
 * Step 4:  Process           (Docling: submit + poll · VLM: page-by-page)
 * Step 5:  Review            (VLM: per-page editor + stitch · Docling: result)
 * Step 6:  Commit            (all file methods)
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardPaste,
  Download,
  Eye,
  FileDown,
  FileText,
  FileUp,
  Loader2,
  Minus,
  Play,
  Plus,
  RotateCcw,
  Save,
  Settings,
  Square,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Checkbox } from '@/components/ui/checkbox';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useScopeStore } from '@/stores/scope-store';
import { useIngestDefaultsStore } from '@/stores/ingest-defaults-store';
import { vlmIngestApi } from '@/services/vlm-ingest-api';
import { ragApi, type IngestResponse } from '@/services/rag-api';
import { useVlmIngestStore, VLM_SESSION_STORAGE_KEY, type WizardStep } from '@/stores/vlm-ingest-store';
import { MaskEditor } from '@/components/mask-editor';
import {
  useStartSession,
  useStartSessionUpload,
  useUpdateConfig,
  useVlmSession,
  isSessionNotFoundError,
  useProcessPage,
  useProcessAll,
  useStitch,
  useCommit,
  useExportConfig,
  useDeleteSession,
  useUpdatePageResult,
  useVlmThumbnails,
} from '@/hooks/use-vlm-ingest';

// ── Types ─────────────────────────────────────────────────────────

export type IngestMethod = 'docling' | 'vlm' | 'import' | 'paste';

type UnifiedStep =
  | 'method'      // Choose method + upload
  | 'configure'   // Method-specific configuration
  | 'pages'       // VLM: page grid
  | 'processing'  // VLM: page-by-page · Docling: pipeline run
  | 'review'      // VLM: per-page review + stitch
  | 'commit';     // VLM: commit to artifact

// Map unified steps → step defs per method
function getSteps(method: IngestMethod | null): { key: UnifiedStep; label: string; icon: React.ElementType }[] {
  const base = [{ key: 'method' as const, label: 'Method', icon: FileUp }];

  if (!method || method === 'paste' || method === 'import') {
    // Minimal flow — method step handles everything
    return base;
  }
  if (method === 'docling') {
    return [
      ...base,
      { key: 'configure', label: 'Configure', icon: Settings },
      { key: 'processing', label: 'Process', icon: Zap },
    ];
  }
  // VLM — full wizard
  return [
    ...base,
    { key: 'configure', label: 'Configure', icon: Settings },
    { key: 'pages', label: 'Pages', icon: Eye },
    { key: 'processing', label: 'Process', icon: Zap },
    { key: 'review', label: 'Review', icon: Eye },
    { key: 'commit', label: 'Commit', icon: Save },
  ];
}

// ── Main component ────────────────────────────────────────────────

export function IngestPage() {
  const [method, setMethod] = useState<IngestMethod | null>(null);
  const [unifiedStep, setUnifiedStep] = useState<UnifiedStep>('method');

  // VLM store (used for VLM flow)
  const vlmStore = useVlmIngestStore();
  const deleteSession = useDeleteSession();
  const sessionQuery = useVlmSession(vlmStore.sessionId);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);

  useEffect(() => {
    if (sessionQuery.error && isSessionNotFoundError(sessionQuery.error)) {
      markSessionExpired('The backend session no longer exists (server restart/reload).');
    }
  }, [sessionQuery.error, markSessionExpired]);

  // Keep wizard page state in sync with the server — this is what makes the
  // progress bar move during bulk (server-side) processing.
  const syncPagesFromServer = useVlmIngestStore((s) => s.syncPagesFromServer);
  useEffect(() => {
    if (sessionQuery.data) syncPagesFromServer(sessionQuery.data);
  }, [sessionQuery.data, syncPagesFromServer]);

  // Re-attach after a page refresh: the wizard lives in memory, but the
  // backend session (and any bulk processing) keeps running. If a session id
  // was persisted and the server still knows it, rehydrate and jump to the
  // step matching the server's state.
  const resumeSession = useVlmIngestStore((s) => s.resumeSession);
  useEffect(() => {
    if (useVlmIngestStore.getState().sessionId) return;
    // Prefer an explicit ?vlm_session= deep-link, then the persisted id.
    const fromUrl = new URLSearchParams(window.location.search).get('vlm_session');
    let saved: string | null = fromUrl;
    if (!saved) {
      try {
        saved = localStorage.getItem(VLM_SESSION_STORAGE_KEY);
      } catch {
        return;
      }
    }
    if (!saved) return;
    vlmIngestApi
      .getSession(saved)
      .then((session) => {
        resumeSession(session);
        setMethod('vlm');
        toast.info(`Re-attached to session: ${session.source_filename} (${session.status})`);
      })
      .catch(() => {
        try {
          localStorage.removeItem(VLM_SESSION_STORAGE_KEY);
        } catch { /* ignore */ }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const steps = getSteps(method);
  const currentIdx = steps.findIndex((s) => s.key === unifiedStep);

  const handleReset = useCallback(() => {
    setMethod(null);
    setUnifiedStep('method');
    vlmStore.reset();
  }, [vlmStore]);

  // When VLM store step changes, sync unified step (for VLM sub-steps)
  useEffect(() => {
    if (method !== 'vlm') return;
    // Map VLM wizard steps to unified steps
    const vlmToUnified: Record<WizardStep, UnifiedStep> = {
      start: 'method',
      configure: 'configure',
      pages: 'pages',
      processing: 'processing',
      review: 'review',
      stitch: 'review', // stitch is part of review in unified
      commit: 'commit',
    };
    const mapped = vlmToUnified[vlmStore.step];
    if (mapped && mapped !== unifiedStep) {
      setUnifiedStep(mapped);
    }
  }, [method, vlmStore.step, unifiedStep]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Step indicator */}
      <div className="flex shrink-0 items-center gap-1 border-b border-border bg-bg-surface px-4 py-2">
        {steps.map((s, i) => {
          const isActive = i === currentIdx;
          const isDone = i < currentIdx;
          const Icon = s.icon;
          return (
            <div key={s.key} className="flex items-center gap-1">
              {i > 0 && (
                <div className={cn('h-px w-6', isDone ? 'bg-accent' : 'bg-border')} />
              )}
              <button
                disabled={i > currentIdx}
                onClick={() => {
                  if (i <= currentIdx) {
                    setUnifiedStep(s.key);
                    // Sync VLM store if needed
                    if (method === 'vlm') {
                      const unifiedToVlm: Partial<Record<UnifiedStep, WizardStep>> = {
                        method: 'start',
                        configure: 'configure',
                        pages: 'pages',
                        processing: 'processing',
                        review: 'review',
                        commit: 'commit',
                      };
                      const vs = unifiedToVlm[s.key];
                      if (vs) vlmStore.setStep(vs);
                    }
                  }
                }}
                className={cn(
                  'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                  isActive && 'bg-accent/10 text-accent',
                  isDone && 'text-accent/70',
                  !isActive && !isDone && 'text-text-muted',
                  i <= currentIdx && 'cursor-pointer hover:bg-bg-card',
                )}
              >
                <Icon className="size-3.5" />
                <span className="hidden sm:inline">{s.label}</span>
              </button>
            </div>
          );
        })}

        <div className="flex-1" />

        {/* Status (VLM) */}
        {method === 'vlm' && (
          <span
            className={cn(
              'text-xs',
              vlmStore.status === 'error' && 'text-red-400',
              vlmStore.status === 'busy' && 'text-yellow-400',
              vlmStore.status === 'idle' && 'text-text-muted',
            )}
          >
            {vlmStore.statusText}
          </span>
        )}

        {/* Discard (when in-progress) */}
        {(method || vlmStore.sessionId) && unifiedStep !== 'method' && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-2 text-red-400 hover:text-red-300"
            onClick={() => {
              if (vlmStore.sessionId) deleteSession.mutate(vlmStore.sessionId);
              handleReset();
            }}
          >
            <Trash2 className="mr-1 size-3.5" />
            Discard
          </Button>
        )}
      </div>

      {/* Step content */}
      <div className="flex flex-1 flex-col overflow-hidden p-4">
        {/* VLM session expired banner */}
        {method === 'vlm' && vlmStore.sessionExpired && (
          <Card className="mx-auto mb-4 flex max-w-2xl shrink-0 items-start justify-between gap-4 border-red-500/30 bg-red-500/5 p-4">
            <div>
              <h3 className="text-sm font-semibold text-red-300">VLM session expired</h3>
              <p className="mt-1 text-sm text-red-200">
                {vlmStore.sessionExpiredReason || 'The backend session is no longer available.'}
              </p>
              <p className="mt-1 text-xs text-text-muted">
                This usually happens after API restart/reload. Start a new session to continue.
              </p>
            </div>
            <Button onClick={handleReset}>Start Over</Button>
          </Card>
        )}

        {/* Route to step content */}
        {unifiedStep === 'method' && (
          <MethodStep
            method={method}
            setMethod={setMethod}
            onAdvance={(step) => setUnifiedStep(step)}
          />
        )}
        {unifiedStep === 'configure' && method === 'vlm' && <VlmConfigureStep />}
        {unifiedStep === 'configure' && method === 'docling' && (
          <DoclingConfigureStep onNext={() => setUnifiedStep('processing')} onBack={() => setUnifiedStep('method')} />
        )}
        {unifiedStep === 'pages' && method === 'vlm' && <VlmPagesStep />}
        {unifiedStep === 'processing' && method === 'vlm' && <VlmProcessingStep />}
        {unifiedStep === 'processing' && method === 'docling' && (
          <DoclingProcessingStep onBack={() => setUnifiedStep('configure')} onReset={handleReset} />
        )}
        {unifiedStep === 'review' && method === 'vlm' && <VlmReviewStep />}
        {unifiedStep === 'commit' && method === 'vlm' && <VlmCommitStep />}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// STEP 1: Method + Upload
// ═══════════════════════════════════════════════════════════════════

interface MethodStepProps {
  method: IngestMethod | null;
  setMethod: (m: IngestMethod) => void;
  onAdvance: (step: UnifiedStep) => void;
}

const METHOD_OPTIONS: { value: IngestMethod; label: string; desc: string; icon: React.ElementType }[] = [
  { value: 'docling', label: 'Docling / Layout', desc: 'Deterministic PDF parsing via backend pipeline', icon: FileText },
  { value: 'vlm', label: 'VLM', desc: 'Interactive page-by-page vision language model', icon: Zap },
  { value: 'import', label: 'Import', desc: 'Pre-processed markdown or JSON — skip parsing', icon: FileDown },
  { value: 'paste', label: 'Write / Paste', desc: 'Free-text content — direct indexing', icon: ClipboardPaste },
];

function MethodStep({ method, setMethod, onAdvance }: MethodStepProps) {
  return (
    <div className="mx-auto flex max-w-2xl flex-1 flex-col gap-6 overflow-auto">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-text-primary">Ingest</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Choose how to add content to your knowledge base.
        </p>
      </div>

      {/* Method picker */}
      <div className="grid grid-cols-2 gap-3">
        {METHOD_OPTIONS.map((opt) => {
          const Icon = opt.icon;
          const selected = method === opt.value;
          return (
            <button
              key={opt.value}
              onClick={() => setMethod(opt.value)}
              className={cn(
                'flex flex-col items-start gap-1.5 rounded-lg border-2 p-4 text-left transition-all',
                selected
                  ? 'border-accent bg-accent/5'
                  : 'border-border hover:border-accent/30 hover:bg-bg-card',
              )}
            >
              <div className="flex items-center gap-2">
                <Icon className={cn('size-5', selected ? 'text-accent' : 'text-text-muted')} />
                <span className={cn('text-sm font-medium', selected ? 'text-accent' : 'text-text-primary')}>
                  {opt.label}
                </span>
              </div>
              <span className="text-xs text-text-secondary">{opt.desc}</span>
            </button>
          );
        })}
      </div>

      {/* Method-specific content */}
      {method === 'vlm' && <VlmMethodContent onAdvance={onAdvance} />}
      {method === 'docling' && <DoclingMethodContent onAdvance={onAdvance} />}
      {method === 'import' && <ImportMethodContent />}
      {method === 'paste' && <PasteMethodContent />}
    </div>
  );
}

// ── VLM method content (Step 1 sub-section) ───────────────────────

function VlmMethodContent({ onAdvance }: { onAdvance: (step: UnifiedStep) => void }) {
  const [runIdInput, setRunIdInput] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const defaults = useIngestDefaultsStore();
  const vlmStore = useVlmIngestStore();

  const startSession = useStartSession();
  const startUpload = useStartSessionUpload();

  // Pre-apply defaults when VLM session starts
  useEffect(() => {
    if (vlmStore.sessionId && vlmStore.step === 'configure') {
      vlmStore.setGlobalConfig({
        dpi: defaults.dpi,
        cropTop: defaults.cropTop,
        cropBottom: defaults.cropBottom,
        cropLeft: defaults.cropLeft,
        cropRight: defaults.cropRight,
        systemPrompt: defaults.systemPrompt,
      });
      onAdvance('configure');
    }
  }, [vlmStore.sessionId, vlmStore.step]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRunIdStart = useCallback(() => {
    const id = parseInt(runIdInput.trim(), 10);
    if (isNaN(id) || id <= 0) {
      toast.error('Enter a valid run ID (positive integer)');
      return;
    }
    startSession.mutate({ run_id: id });
  }, [runIdInput, startSession]);

  const handleUpload = useCallback(() => {
    if (!file) return;
    startUpload.mutate({ file });
  }, [file, startUpload]);

  const isBusy = vlmStore.status === 'busy';

  return (
    <>
      {/* From run ID */}
      <Card className="flex flex-col gap-3 p-4">
        <Label className="text-sm font-medium">From existing pipeline run</Label>
        <div className="flex gap-2">
          <Input
            placeholder="Run ID (e.g. 42)"
            value={runIdInput}
            onChange={(e) => setRunIdInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleRunIdStart()}
            disabled={isBusy}
          />
          <Button onClick={handleRunIdStart} disabled={isBusy || !runIdInput.trim()}>
            {isBusy ? <Loader2 className="size-4 animate-spin" /> : <Play className="mr-1 size-4" />}
            Start
          </Button>
        </div>
      </Card>

      <div className="flex items-center gap-3 text-text-muted">
        <div className="h-px flex-1 bg-border" />
        <span className="text-xs">or</span>
        <div className="h-px flex-1 bg-border" />
      </div>

      {/* Upload PDF */}
      <Card className="flex flex-col gap-3 p-4">
        <Label className="text-sm font-medium">Upload a PDF</Label>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <div className="flex gap-2">
          <Button variant="outline" className="flex-1" onClick={() => fileRef.current?.click()} disabled={isBusy}>
            <FileUp className="mr-1 size-4" />
            {file ? file.name : 'Choose PDF…'}
          </Button>
          <Button onClick={handleUpload} disabled={isBusy || !file}>
            {isBusy ? <Loader2 className="size-4 animate-spin" /> : <Play className="mr-1 size-4" />}
            Upload
          </Button>
        </div>
      </Card>
    </>
  );
}

// ── Docling method content (Step 1 sub-section) ───────────────────

function DoclingMethodContent({ onAdvance }: { onAdvance: (step: UnifiedStep) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [docName, setDocName] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  return (
    <Card className="flex flex-col gap-4 p-4">
      <Label className="text-sm font-medium">Upload a document</Label>
      <input
        ref={fileRef}
        type="file"
        accept=".pdf,application/pdf,.txt,.md,.html,.htm,.json"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) {
            setFile(f);
            if (!docName) setDocName(f.name);
          }
        }}
      />
      <div
        onClick={() => fileRef.current?.click()}
        className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-border py-8 hover:border-accent/50 hover:bg-bg-card"
      >
        <FileUp className="size-8 text-text-muted" />
        <span className="text-sm text-text-secondary">
          {file ? file.name : 'Click to select a file or drag & drop'}
        </span>
      </div>

      <div className="space-y-1">
        <Label className="text-xs">Document name</Label>
        <Input
          value={docName}
          onChange={(e) => setDocName(e.target.value)}
          placeholder="Auto-populated from filename"
          className="h-8 text-xs"
        />
      </div>

      <Button
        onClick={() => {
          if (!file) {
            toast.error('Select a file first');
            return;
          }
          // Store in a way that DoclingConfigureStep and DoclingProcessingStep can access
          sessionStorage.setItem('docling_file_name', file.name);
          sessionStorage.setItem('docling_doc_name', docName || file.name);
          // We need a way to pass the File object — use a module-level ref
          _doclingFileRef.current = file;
          onAdvance('configure');
        }}
        disabled={!file}
        className="w-full"
      >
        Next: Configure
        <ArrowRight className="ml-1 size-4" />
      </Button>
    </Card>
  );
}

// Module-level ref to pass File object between steps (avoid serialization)
const _doclingFileRef: { current: File | null } = { current: null };

// ── Import method content ─────────────────────────────────────────

function ImportMethodContent() {
  const [file, setFile] = useState<File | null>(null);
  const [docName, setDocName] = useState('');
  const [searchable, setSearchable] = useState(true);
  const [sensitive, setSensitive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<IngestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const { workspace, project, collection } = useScopeStore();

  const handleImport = async () => {
    if (!file) return;
    setUploading(true);
    setResult(null);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      // Backend requires doc_id (there is no doc_name field server-side).
      fd.append('doc_id', docName.trim() || file.name.replace(/\.[^.]+$/, ''));
      fd.append('is_finalized', String(searchable));
      fd.append('is_sensitive', String(sensitive));
      if (workspace) fd.append('tenant_id', workspace);
      if (project) fd.append('project_id', project);
      if (collection) fd.append('corpus_id', collection);
      const resp = await ragApi.ingestFile(fd);
      setResult(resp);
      if (resp.ok) toast.success(`Imported ${resp.chunks_upserted} chunks`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <Card className="flex flex-col gap-4 p-4">
        <Label className="text-sm font-medium">Import pre-processed content</Label>
        <p className="text-xs text-text-secondary">
          Upload markdown, text, or JSON that has already been extracted from a document.
          This skips PDF parsing and goes straight to chunking and indexing.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt,.json,.html,.htm"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) {
              setFile(f);
              if (!docName) setDocName(f.name.replace(/\.[^.]+$/, ''));
            }
          }}
        />
        <div
          onClick={() => fileRef.current?.click()}
          className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-border py-8 hover:border-accent/50 hover:bg-bg-card"
        >
          <FileDown className="size-8 text-text-muted" />
          <span className="text-sm text-text-secondary">
            {file ? file.name : 'Click to select a file'}
          </span>
        </div>

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

        <Button onClick={handleImport} disabled={uploading || !file} className="w-full">
          {uploading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <FileDown className="mr-2 size-4" />}
          Import & Index
        </Button>
      </Card>

      {result && <IngestResultCard result={result} />}
      {error && <IngestErrorCard error={error} />}
    </>
  );
}

// ── Paste/Write method content ────────────────────────────────────

function PasteMethodContent() {
  const [textContent, setTextContent] = useState('');
  const [docName, setDocName] = useState('');
  const [searchable, setSearchable] = useState(true);
  const [sensitive, setSensitive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<IngestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { workspace, project, collection } = useScopeStore();

  const handleSubmit = async () => {
    if (!textContent.trim()) return;
    setUploading(true);
    setResult(null);
    setError(null);
    try {
      const docId = docName.trim();
      if (!docId) {
        toast.error('Enter a document name — it becomes the doc ID.');
        setUploading(false);
        return;
      }
      const resp = await ragApi.ingestText({
        text: textContent,
        doc_id: docId,
        is_finalized: searchable,
        is_sensitive: sensitive,
        tenant_id: workspace || undefined,
        project_id: project || undefined,
        corpus_id: collection || undefined,
      });
      setResult(resp);
      if (resp.ok) toast.success(`Indexed ${resp.chunks_upserted} chunks`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <Card className="flex flex-col gap-4 p-4">
        <Label className="text-sm font-medium">Write or paste content</Label>
        <p className="text-xs text-text-secondary">
          This content will be chunked and indexed directly — no PDF parsing or VLM processing.
        </p>
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

        <Button onClick={handleSubmit} disabled={uploading || !textContent.trim()} className="w-full">
          {uploading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <ClipboardPaste className="mr-2 size-4" />}
          Index Content
        </Button>
      </Card>

      {result && <IngestResultCard result={result} />}
      {error && <IngestErrorCard error={error} />}
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════
// STEP 2: Configure (Docling)
// ═══════════════════════════════════════════════════════════════════

function DoclingConfigureStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const defaults = useIngestDefaultsStore();
  const [backend, setBackend] = useState<'auto' | 'auto_layout' | 'layout' | 'docling'>(defaults.parserBackend);
  const [searchable, setSearchable] = useState(true);
  const [sensitive, setSensitive] = useState(false);
  const [saveAsDefault, setSaveAsDefault] = useState(false);

  const docName = sessionStorage.getItem('docling_doc_name') || '';
  const fileName = sessionStorage.getItem('docling_file_name') || '';

  const handleNext = () => {
    // Store config for processing step
    sessionStorage.setItem('docling_backend', backend);
    sessionStorage.setItem('docling_searchable', String(searchable));
    sessionStorage.setItem('docling_sensitive', String(sensitive));

    if (saveAsDefault) {
      defaults.setParserBackend(backend);
    }
    onNext();
  };

  const BACKENDS = [
    { value: 'auto', label: 'Auto', desc: 'Try Docling first, fall back to Layout' },
    { value: 'auto_layout', label: 'Auto Layout', desc: 'Try Layout first, fall back to Docling' },
    { value: 'docling', label: 'Docling', desc: 'Docling only' },
    { value: 'layout', label: 'Layout (DeepDoc)', desc: 'Layout-based parser only' },
  ] as const;

  return (
    <div className="mx-auto flex max-w-lg flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Configure Processing</h2>
        <p className="text-sm text-text-secondary">
          {fileName} → {docName}
        </p>
      </div>

      <Card className="flex flex-col gap-4 p-4">
        <div>
          <Label className="text-sm font-medium">Parser Backend</Label>
          <p className="text-xs text-text-secondary mt-1">
            Choose how the PDF is parsed and text is extracted.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-2">
          {BACKENDS.map((b) => (
            <button
              key={b.value}
              onClick={() => setBackend(b.value)}
              className={cn(
                'flex flex-col items-start gap-0.5 rounded-md border-2 p-3 text-left transition-all',
                backend === b.value
                  ? 'border-accent bg-accent/5'
                  : 'border-border hover:border-accent/30',
              )}
            >
              <span className={cn('text-sm font-medium', backend === b.value ? 'text-accent' : 'text-text-primary')}>
                {b.label}
              </span>
              <span className="text-xs text-text-secondary">{b.desc}</span>
            </button>
          ))}
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

        <label className="flex items-center gap-2 text-xs text-text-secondary">
          <Checkbox checked={saveAsDefault} onCheckedChange={(c) => setSaveAsDefault(c === true)} />
          Save as default settings
        </label>
      </Card>

      <div className="flex justify-between">
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <Button onClick={handleNext}>
          Start Processing
          <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// STEP 4: Process (Docling)
// ═══════════════════════════════════════════════════════════════════

function DoclingProcessingStep({ onBack, onReset }: { onBack: () => void; onReset: () => void }) {
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<IngestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasRun = useRef(false);
  const { workspace, project, collection } = useScopeStore();

  const docName = sessionStorage.getItem('docling_doc_name') || '';
  const searchable = sessionStorage.getItem('docling_searchable') !== 'false';
  const sensitive = sessionStorage.getItem('docling_sensitive') === 'true';

  useEffect(() => {
    if (hasRun.current) return;
    const file = _doclingFileRef.current;
    if (!file) {
      setError('File reference lost — please go back and re-upload.');
      return;
    }
    hasRun.current = true;
    setUploading(true);

    const fd = new FormData();
    fd.append('file', file);
    if (docName) fd.append('doc_name', docName);
    fd.append('is_finalized', String(searchable));
    fd.append('is_sensitive', String(sensitive));
    if (workspace) fd.append('tenant_id', workspace);
    if (project) fd.append('project_id', project);
    if (collection) fd.append('corpus_id', collection);

    ragApi
      .ingestFile(fd)
      .then((resp) => {
        setResult(resp);
        if (resp.ok) toast.success(`Ingested ${resp.chunks_upserted} chunks`);
        else toast.error(resp.error_message || 'Ingest failed');
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
        toast.error('Ingest failed');
      })
      .finally(() => {
        setUploading(false);
        _doclingFileRef.current = null;
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="mx-auto flex max-w-lg flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Processing</h2>
        <p className="text-sm text-text-secondary">
          {docName || 'Document'} is being processed through the pipeline.
        </p>
      </div>

      {uploading && (
        <Card className="flex items-center gap-3 p-6">
          <Loader2 className="size-6 animate-spin text-accent" />
          <div>
            <p className="text-sm font-medium text-text-primary">Processing document…</p>
            <p className="text-xs text-text-secondary">
              Parsing, chunking, and indexing. This may take a moment.
            </p>
          </div>
        </Card>
      )}

      {result && <IngestResultCard result={result} />}
      {error && <IngestErrorCard error={error} />}

      {!uploading && (
        <div className="flex justify-between">
          <Button variant="outline" onClick={onBack} disabled={!!result}>
            <ArrowLeft className="mr-1 size-4" />
            Back
          </Button>
          <Button onClick={onReset}>
            Ingest Another
            <ArrowRight className="ml-1 size-4" />
          </Button>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// VLM STEPS — Delegate to existing VLM wizard components
// ═══════════════════════════════════════════════════════════════════

// Step 2: VLM Configure — imported from existing VLM page
function VlmConfigureStep() {
  const store = useVlmIngestStore();
  const updateConfig = useUpdateConfig();
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);
  const defaults = useIngestDefaultsStore();
  const [saveAsDefault, setSaveAsDefault] = useState(false);
  const [previewPage, setPreviewPage] = useState(0);
  const [previewZoom, setPreviewZoom] = useState(1);
  const [previewFitMode, setPreviewFitMode] = useState<'fit-page' | 'fit-width' | 'actual' | 'manual'>('fit-page');
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewNaturalSize, setPreviewNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const previewViewportRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (store.pageCount <= 0) {
      setPreviewPage(0);
      return;
    }
    setPreviewPage((p) => Math.max(0, Math.min(p, store.pageCount - 1)));
  }, [store.pageCount]);

  useEffect(() => {
    if (!store.sessionId || store.pageCount <= 0) {
      setPreviewSrc((old) => {
        if (old) URL.revokeObjectURL(old);
        return null;
      });
      return;
    }

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);

    vlmIngestApi
      .previewImage(store.sessionId, previewPage, {
        dpi: store.dpi,
        cropTop: store.cropTop,
        cropBottom: store.cropBottom,
        cropLeft: store.cropLeft,
        cropRight: store.cropRight,
        applyCrop: false,
      })
      .then((blob) => {
        if (cancelled) return;
        const next = URL.createObjectURL(blob);
        setPreviewSrc((old) => {
          if (old) URL.revokeObjectURL(old);
          return next;
        });
      })
      .catch((err) => {
        if (cancelled) return;
        if (isSessionNotFoundError(err)) {
          markSessionExpired('The backend session was lost while loading PDF preview.');
        }
        setPreviewError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [markSessionExpired, previewPage, store.sessionId, store.pageCount, store.dpi, store.cropTop, store.cropBottom, store.cropLeft, store.cropRight]);

  useEffect(() => {
    return () => {
      setPreviewSrc((old) => {
        if (old) URL.revokeObjectURL(old);
        return null;
      });
    };
  }, []);

  useEffect(() => {
    if (!previewSrc) {
      setPreviewNaturalSize(null);
      return;
    }
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled) return;
      setPreviewNaturalSize({ width: image.naturalWidth, height: image.naturalHeight });
    };
    image.src = previewSrc;
    return () => { cancelled = true; };
  }, [previewSrc]);

  useEffect(() => {
    if (!previewNaturalSize || !previewViewportRef.current) return;
    if (previewFitMode === 'manual' || previewFitMode === 'actual') return;

    const updateZoom = () => {
      const viewport = previewViewportRef.current;
      if (!viewport) return;
      const availableWidth = Math.max(1, viewport.clientWidth - 20);
      const availableHeight = Math.max(1, viewport.clientHeight - 20);
      const widthScale = availableWidth / previewNaturalSize.width;
      const heightScale = availableHeight / previewNaturalSize.height;
      const nextZoom = previewFitMode === 'fit-width'
        ? widthScale
        : Math.min(widthScale, heightScale) * 0.98;
      setPreviewZoom(Math.max(0.2, Math.min(2.5, nextZoom)));
    };

    updateZoom();
    const viewport = previewViewportRef.current;
    const observer = new ResizeObserver(() => updateZoom());
    observer.observe(viewport);
    window.addEventListener('resize', updateZoom);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', updateZoom);
    };
  }, [previewFitMode, previewNaturalSize]);

  const handleApply = useCallback(() => {
    if (!store.sessionId) return;
    updateConfig.mutate({
      sid: store.sessionId,
      req: {
        dpi: store.dpi,
        crop_top: store.cropTop,
        crop_bottom: store.cropBottom,
        crop_left: store.cropLeft,
        crop_right: store.cropRight,
        system_prompt: store.systemPrompt || null,
      },
    });
  }, [store, updateConfig]);

  const handleNext = useCallback(() => {
    handleApply();
    if (saveAsDefault) {
      defaults.setVlmDefaults({
        dpi: store.dpi,
        cropTop: store.cropTop,
        cropBottom: store.cropBottom,
        cropLeft: store.cropLeft,
        cropRight: store.cropRight,
        systemPrompt: store.systemPrompt,
      });
      toast.success('VLM defaults saved');
    }
    store.setStep('pages');
  }, [handleApply, store, saveAsDefault, defaults]);

  return (
    <div className="flex flex-1 flex-col min-h-0 gap-3">
      <div className="w-full shrink-0 px-4">
        <h2 className="text-lg font-semibold text-text-primary">VLM Settings</h2>
        <p className="mt-1 text-sm text-text-secondary">
          These defaults apply to all pages. You can override per-page in the next step.
        </p>
        <p className="mt-1 text-xs text-text-muted">
          {store.filename} — {store.pageCount} pages
        </p>
      </div>

      <div className="flex flex-1 min-h-0 min-w-0 flex-col gap-4 overflow-auto px-4 xl:flex-row xl:overflow-hidden">
        <Card className="flex shrink-0 flex-col gap-4 p-4 xl:w-[360px] xl:overflow-auto">
          {/* DPI */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm">Render DPI</Label>
              <span className="text-xs text-text-muted">{store.dpi}</span>
            </div>
            <Slider min={72} max={400} step={1} value={[store.dpi]} onValueChange={([v]) => store.setGlobalConfig({ dpi: v })} />
          </div>

          {/* Crop margins */}
          <div className="grid grid-cols-2 gap-3">
            {(['cropTop', 'cropBottom', 'cropLeft', 'cropRight'] as const).map((key) => (
              <div key={key} className="flex flex-col gap-1">
                <Label className="text-xs text-text-secondary">
                  Crop {key.replace('crop', '')}
                </Label>
                <Slider
                  min={0} max={0.25} step={0.005}
                  value={[store[key]]}
                  onValueChange={([v]) => store.setGlobalConfig({ [key]: v })}
                />
                <span className="text-right text-xs text-text-muted">
                  {(store[key] * 100).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>

          {/* System prompt */}
          <div className="flex flex-col gap-1">
            <Label className="text-sm">System Prompt (optional)</Label>
            <textarea
              className="min-h-[80px] rounded-md border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
              placeholder="Custom instructions for the VLM…"
              value={store.systemPrompt}
              onChange={(e) => store.setGlobalConfig({ systemPrompt: e.target.value })}
            />
          </div>

          {/* Save as default */}
          <label className="flex items-center gap-2 text-xs text-text-secondary">
            <Checkbox checked={saveAsDefault} onCheckedChange={(c) => setSaveAsDefault(c === true)} />
            Save as default settings
          </label>
        </Card>

        {/* PDF Preview */}
        <Card className="flex flex-1 min-h-0 min-w-0 flex-col gap-3 p-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-text-primary">PDF Preview</h3>
              <p className="text-xs text-text-muted">Raw page with crop guides overlaid.</p>
            </div>
            <div className="flex items-center gap-1">
              <Button variant="outline" size="sm" onClick={() => { setPreviewFitMode('manual'); setPreviewZoom((z) => Math.max(0.2, z - 0.1)); }} disabled={!previewSrc}>
                <Minus className="size-3.5" />
              </Button>
              <Button variant="outline" size="sm" onClick={() => { setPreviewFitMode('actual'); setPreviewZoom(1); }} disabled={!previewSrc}>
                {Math.round(previewZoom * 100)}%
              </Button>
              <Button variant="outline" size="sm" onClick={() => { setPreviewFitMode('manual'); setPreviewZoom((z) => Math.min(2.5, z + 0.1)); }} disabled={!previewSrc}>
                <Plus className="size-3.5" />
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {(['fit-page', 'fit-width', 'actual'] as const).map((mode) => (
              <Button
                key={mode}
                variant={previewFitMode === mode ? 'default' : 'outline'}
                size="sm"
                onClick={() => { setPreviewFitMode(mode); if (mode === 'actual') setPreviewZoom(1); }}
                disabled={!previewSrc}
              >
                {mode === 'fit-page' ? 'Fit Page' : mode === 'fit-width' ? 'Fit Width' : 'Actual Size'}
              </Button>
            ))}
          </div>

          {/* Page nav */}
          <div className="flex items-center justify-between rounded border border-border bg-bg-base px-2 py-1">
            <Button variant="ghost" size="sm" onClick={() => setPreviewPage((p) => Math.max(0, p - 1))} disabled={previewPage <= 0}>
              <ChevronLeft className="mr-1 size-4" /> Prev
            </Button>
            <span className="text-xs text-text-secondary">
              Page {store.pageCount > 0 ? previewPage + 1 : 0} / {store.pageCount}
            </span>
            <Button variant="ghost" size="sm" onClick={() => setPreviewPage((p) => Math.min(store.pageCount - 1, p + 1))} disabled={previewPage >= store.pageCount - 1}>
              Next <ChevronRight className="ml-1 size-4" />
            </Button>
          </div>

          {/* Preview image */}
          <div ref={previewViewportRef} className="relative flex-1 overflow-hidden rounded border border-border bg-bg-base p-2">
            {previewLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg-base/70 text-sm text-text-muted">
                <Loader2 className="mr-2 size-4 animate-spin" /> Rendering preview…
              </div>
            )}
            {!previewLoading && previewError && (
              <div className="flex h-full items-center justify-center text-sm text-red-300">
                Preview error: {previewError}
              </div>
            )}
            {!previewLoading && !previewError && !previewSrc && (
              <div className="flex h-full items-center justify-center text-sm text-text-muted">
                No preview available.
              </div>
            )}
            {!previewError && previewSrc && (
              <div className="flex h-full w-full items-center justify-center overflow-hidden">
                <div className="relative" style={{ transform: `scale(${previewZoom})`, transformOrigin: 'center center' }}>
                  <img src={previewSrc} alt={`Preview page ${previewPage + 1}`} className="block h-auto w-auto max-h-none max-w-none rounded border border-border/70" />
                  <div className="pointer-events-none absolute inset-0">
                    <div className="absolute left-0 right-0 border-t-2 border-red-500" style={{ top: `${store.cropTop * 100}%` }} />
                    <div className="absolute left-0 right-0 border-b-2 border-red-500" style={{ bottom: `${store.cropBottom * 100}%` }} />
                    <div className="absolute top-0 bottom-0 border-l-2 border-red-500" style={{ left: `${store.cropLeft * 100}%` }} />
                    <div className="absolute top-0 bottom-0 border-r-2 border-red-500" style={{ right: `${store.cropRight * 100}%` }} />
                  </div>
                </div>
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* Navigation */}
      <div className="flex w-full shrink-0 justify-between px-4">
        <Button variant="outline" onClick={() => store.setStep('start')}>
          <ArrowLeft className="mr-1 size-4" /> Back
        </Button>
        <Button onClick={handleNext}>
          Next: Review Pages <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// Step 3: VLM Pages
function VlmPagesStep() {
  const store = useVlmIngestStore();
  const setThumbnails = useVlmIngestStore((s) => s.setThumbnails);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);
  const { data: thumbnails, isLoading, error: thumbnailsError } = useVlmThumbnails(store.sessionId);
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const updateConfig = useUpdateConfig();
  const analysisRanRef = useRef(false);

  useEffect(() => {
    if (thumbnails) setThumbnails(thumbnails);
  }, [thumbnails, setThumbnails]);

  useEffect(() => {
    if (thumbnailsError && isSessionNotFoundError(thumbnailsError)) {
      markSessionExpired('The backend session was lost while loading page thumbnails.');
    }
  }, [thumbnailsError, markSessionExpired]);

  // Fetch page analysis once when entering this step
  useEffect(() => {
    const sid = store.sessionId;
    if (!sid || analysisRanRef.current) return;
    const hasAnalysis = store.pages.some((p) => p.contentClass != null);
    if (hasAnalysis) { analysisRanRef.current = true; return; }
    analysisRanRef.current = true;
    setAnalysisLoading(true);
    vlmIngestApi
      .getPageAnalysis(sid)
      .then((resp) => {
        const currentStore = useVlmIngestStore.getState();
        const overrides: Array<{ page_num: number; mask_regions: Array<{ x: number; y: number; w: number; h: number }> }> = [];

        for (const [pageStr, analysis] of Object.entries(resp.pages)) {
          const pageNum = parseInt(pageStr, 10);
          currentStore.setPageAnalysis(pageNum, analysis);

          // Auto-suggest masks for image-heavy / image-only pages
          if (
            (analysis.content_class === 'image-heavy' || analysis.content_class === 'image-only') &&
            analysis.image_rects?.length > 0
          ) {
            currentStore.autoSuggestMasks(pageNum);
            overrides.push({ page_num: pageNum, mask_regions: analysis.image_rects });
          }
        }

        // Sync auto-masks to backend in one call
        if (overrides.length > 0 && sid) {
          updateConfig.mutate({
            sid,
            req: {
              page_overrides: overrides.map((o) => ({
                page_num: o.page_num,
                mask_regions: o.mask_regions,
              })),
            },
          });
          const imageHeavy = overrides.length;
          toast.info(`Auto-masked ${imageHeavy} image-heavy page${imageHeavy !== 1 ? 's' : ''}`);
        }
      })
      .catch((err) => {
        console.warn('Page analysis failed:', err);
        toast.error('Page analysis unavailable — mask suggestions disabled');
      })
      .finally(() => setAnalysisLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.sessionId]);

  // Load preview image for selected page
  useEffect(() => {
    if (selectedPage == null || !store.sessionId) {
      setPreviewSrc(null);
      return;
    }
    let cancelled = false;
    vlmIngestApi
      .previewImage(store.sessionId, selectedPage, { dpi: 150 })
      .then((blob) => {
        if (!cancelled) setPreviewSrc(URL.createObjectURL(blob));
      })
      .catch(() => {
        if (!cancelled) setPreviewSrc(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedPage, store.sessionId]);

  const togglePage = useCallback(
    (pageNum: number) => {
      const page = store.pages[pageNum];
      if (!page) return;
      store.setPageEnabled(pageNum, !page.enabled);
      if (store.sessionId) {
        updateConfig.mutate({
          sid: store.sessionId,
          req: { page_overrides: [{ page_num: pageNum, enabled: !page.enabled }] },
        });
      }
    },
    [store, updateConfig],
  );

  const handleOverrideDpi = useCallback(
    (pageNum: number, dpi: number) => {
      store.setPageOverride(pageNum, { dpiOverride: dpi });
      if (store.sessionId) {
        updateConfig.mutate({
          sid: store.sessionId,
          req: { page_overrides: [{ page_num: pageNum, dpi }] },
        });
      }
    },
    [store, updateConfig],
  );

  // Sync mask regions to backend
  const syncMasks = useCallback(
    (pageNum: number, masks: Array<{ x: number; y: number; w: number; h: number }>) => {
      if (!store.sessionId) return;
      updateConfig.mutate({
        sid: store.sessionId,
        req: { page_overrides: [{ page_num: pageNum, mask_regions: masks }] },
      });
    },
    [store.sessionId, updateConfig],
  );

  const handleAddMask = useCallback(
    (pageNum: number, rect: { x: number; y: number; w: number; h: number }) => {
      store.addMaskRegion(pageNum, rect);
      const page = store.pages[pageNum];
      const updated = [...(page?.maskRegions ?? []), rect];
      syncMasks(pageNum, updated);
    },
    [store, syncMasks],
  );

  const handleRemoveMask = useCallback(
    (pageNum: number, index: number) => {
      store.removeMaskRegion(pageNum, index);
      const page = store.pages[pageNum];
      const updated = (page?.maskRegions ?? []).filter((_, i) => i !== index);
      syncMasks(pageNum, updated);
    },
    [store, syncMasks],
  );

  const handleAcceptSuggestion = useCallback(
    (pageNum: number, rect: { x: number; y: number; w: number; h: number }) => {
      const page = store.pages[pageNum];
      const exists = page?.maskRegions.some(
        (m) => Math.abs(m.x - rect.x) < 0.001 && Math.abs(m.y - rect.y) < 0.001,
      );
      if (!exists) {
        handleAddMask(pageNum, rect);
      }
    },
    [store.pages, handleAddMask],
  );

  const handleAutoSuggest = useCallback(
    (pageNum: number) => {
      store.autoSuggestMasks(pageNum);
      const page = store.pages[pageNum];
      syncMasks(pageNum, page?.imageRects ?? []);
    },
    [store, syncMasks],
  );

  const contentClassBadge = (cls: string | null) => {
    if (!cls) return null;
    const variants: Record<string, { label: string; className: string }> = {
      'text-native': { label: 'Text', className: 'bg-green-500/15 text-green-400 border-green-500/30' },
      'image-heavy': { label: 'Image-Heavy', className: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30' },
      'image-only': { label: 'Image-Only', className: 'bg-red-500/15 text-red-400 border-red-500/30' },
    };
    const v = variants[cls];
    if (!v) return null;
    return <span className={cn('rounded-full border px-1.5 py-0.5 text-[9px] font-medium', v.className)}>{v.label}</span>;
  };

  const selectedPageData = selectedPage != null ? store.pages[selectedPage] : null;

  return (
    <div className="flex flex-col gap-4 overflow-auto">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Page Selection</h2>
          <p className="text-sm text-text-secondary">
            {store.totalEnabled} of {store.pageCount} pages enabled
            {analysisLoading && <span className="ml-2 text-text-muted">(analyzing…)</span>}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => store.pages.forEach((p) => store.setPageEnabled(p.pageNum, true))}>Enable All</Button>
          <Button variant="outline" size="sm" onClick={() => store.pages.forEach((p) => store.setPageEnabled(p.pageNum, false))}>Disable All</Button>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-text-muted">
          <Loader2 className="size-4 animate-spin" /> Loading thumbnails…
        </div>
      )}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
        {store.pages.map((page) => {
          const thumb = store.thumbnails[page.pageNum];
          return (
            <Card
              key={page.pageNum}
              className={cn(
                'relative flex flex-col items-center gap-1 p-2 transition-all cursor-pointer',
                !page.enabled && 'opacity-40',
                selectedPage === page.pageNum && 'ring-2 ring-accent',
              )}
              onClick={() => setSelectedPage(page.pageNum === selectedPage ? null : page.pageNum)}
            >
              <div className="flex h-[120px] w-full items-center justify-center overflow-hidden rounded bg-bg-base">
                {thumb?.thumbnail ? (
                  <img src={thumb.thumbnail} alt={`Page ${page.pageNum + 1}`} className="max-h-full max-w-full object-contain" />
                ) : (
                  <span className="text-xs text-text-muted">No preview</span>
                )}
              </div>
              <div className="flex w-full items-center justify-between">
                <div className="flex items-center gap-1">
                  <span className="text-xs font-medium text-text-primary">Page {page.pageNum + 1}</span>
                  {contentClassBadge(page.contentClass)}
                </div>
                <Switch checked={page.enabled} onCheckedChange={() => togglePage(page.pageNum)} className="scale-75" />
              </div>
              {page.maskRegions.length > 0 && (
                <span className="absolute left-1 top-1 rounded-full bg-red-500/20 px-1.5 py-0.5 text-[9px] font-medium text-red-400">
                  {page.maskRegions.length} mask{page.maskRegions.length !== 1 ? 's' : ''}
                </span>
              )}
              {page.status !== 'pending' && (
                <span className={cn(
                  'absolute right-1 top-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                  page.status === 'done' && 'bg-green-500/20 text-green-400',
                  page.status === 'error' && 'bg-red-500/20 text-red-400',
                  page.status === 'processing' && 'bg-yellow-500/20 text-yellow-400',
                  page.status === 'skipped' && 'bg-gray-500/20 text-gray-400',
                )}>
                  {page.status}
                </span>
              )}
            </Card>
          );
        })}
      </div>

      {/* Full-screen overlay for page detail */}
      {selectedPage != null && selectedPageData && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setSelectedPage(null)}>
          <div
            className="relative mx-4 flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-border bg-bg-card shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3">
              <div className="flex items-center gap-3">
                <h3 className="text-base font-semibold text-text-primary">
                  Page {selectedPage + 1}
                </h3>
                {contentClassBadge(selectedPageData.contentClass)}
                {selectedPageData.imageRatio != null && (
                  <span className="text-xs text-text-muted">
                    {Math.round(selectedPageData.imageRatio * 100)}% image area
                  </span>
                )}
                {selectedPageData.maskRegions.length > 0 && (
                  <span className="text-xs text-red-400">
                    {selectedPageData.maskRegions.length} mask{selectedPageData.maskRegions.length !== 1 ? 's' : ''} applied
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {/* Page navigation */}
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={selectedPage <= 0}
                  onClick={() => setSelectedPage(selectedPage - 1)}
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <span className="text-xs text-text-muted">{selectedPage + 1} / {store.pageCount}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={selectedPage >= store.pageCount - 1}
                  onClick={() => setSelectedPage(selectedPage + 1)}
                >
                  <ChevronRight className="size-4" />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setSelectedPage(null)}>
                  <X className="size-4" />
                </Button>
              </div>
            </div>

            {/* Body */}
            <div className="flex flex-1 gap-5 overflow-auto p-5">
              {/* Left: Mask editor with preview */}
              <div className="flex-1 min-w-0 overflow-auto">
                {previewSrc ? (
                  <MaskEditor
                    imageSrc={previewSrc}
                    suggestions={selectedPageData.imageRects.filter(
                      (ir) => !selectedPageData.maskRegions.some(
                        (m) => Math.abs(m.x - ir.x) < 0.001 && Math.abs(m.y - ir.y) < 0.001,
                      ),
                    )}
                    masks={selectedPageData.maskRegions}
                    onAddMask={(rect) => handleAddMask(selectedPage, rect)}
                    onRemoveMask={(idx) => handleRemoveMask(selectedPage, idx)}
                    onAcceptSuggestion={(rect) => handleAcceptSuggestion(selectedPage, rect)}
                  />
                ) : (
                  <div className="flex h-64 items-center justify-center rounded bg-bg-base text-sm text-text-muted">
                    <Loader2 className="mr-2 size-4 animate-spin" /> Loading preview…
                  </div>
                )}
              </div>
              {/* Right: Settings panel */}
              <div className="flex w-52 shrink-0 flex-col gap-3">
                <div className="flex items-center justify-between">
                  <Label className="text-xs">Enabled</Label>
                  <Switch checked={selectedPageData.enabled} onCheckedChange={() => togglePage(selectedPage)} />
                </div>
                <div className="flex items-center justify-between">
                  <Label className="text-xs">DPI Override</Label>
                  <Input type="number" className="w-20 text-right" min={72} max={400}
                    value={selectedPageData.dpiOverride ?? store.dpi}
                    onChange={(e) => handleOverrideDpi(selectedPage!, parseInt(e.target.value, 10))}
                  />
                </div>
                <hr className="border-border" />
                {selectedPageData.imageRects.length > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleAutoSuggest(selectedPage)}
                  >
                    <Zap className="mr-1 size-3" /> Auto-Mask Images
                  </Button>
                )}
                {selectedPageData.maskRegions.length > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-red-400 hover:text-red-300"
                    onClick={() => {
                      store.clearMaskRegions(selectedPage);
                      syncMasks(selectedPage, []);
                    }}
                  >
                    <Trash2 className="mr-1 size-3" /> Clear All Masks
                  </Button>
                )}
                <Button variant="outline" size="sm" onClick={() => {
                  store.setPageOverride(selectedPage!, { dpiOverride: null, cropTopOverride: null, cropBottomOverride: null, cropLeftOverride: null, cropRightOverride: null });
                }}>
                  <RotateCcw className="mr-1 size-3" /> Reset to Global
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="flex justify-between">
        <Button variant="outline" onClick={() => store.setStep('configure')}>
          <ArrowLeft className="mr-1 size-4" /> Back
        </Button>
        <Button onClick={() => store.setStep('processing')} disabled={store.totalEnabled === 0}>
          Start Processing <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// Step 4: VLM Processing
function VlmProcessingStep() {
  const store = useVlmIngestStore();
  const processPage = useProcessPage();
  const processAll = useProcessAll();
  const abortRef = useRef(false);
  const [mode, setMode] = useState<'bulk' | 'page'>('bulk');

  const enabledPages = store.pages.filter((p) => p.enabled);
  const donePages = enabledPages.filter((p) => p.status === 'done' || p.status === 'skipped' || p.status === 'error');
  const progress = enabledPages.length > 0 ? Math.round((donePages.length / enabledPages.length) * 100) : 0;

  // ── Page-by-page callbacks ────────────────────────────────────
  const processNext = useCallback(async () => {
    const current = useVlmIngestStore.getState();
    if (!current.sessionId) return;
    const next = current.pages.find((p) => p.enabled && p.status === 'pending');
    if (!next) {
      toast.success('All pages processed!');
      current.setStep('review');
      return;
    }
    processPage.mutate(
      { sid: current.sessionId, req: { page_num: next.pageNum } },
      {
        onSuccess: () => {
          const latest = useVlmIngestStore.getState();
          if (latest.autoProcess && !abortRef.current) {
            setTimeout(() => processNext(), 100);
          }
        },
      },
    );
  }, [processPage]);

  const handleStart = useCallback(() => { abortRef.current = false; processNext(); }, [processNext]);
  const handleStop = useCallback(() => { abortRef.current = true; store.setProcessing(false); }, [store]);

  // ── Bulk callback ─────────────────────────────────────────────
  const handleBulk = useCallback(() => {
    if (!store.sessionId) return;
    processAll.mutate(store.sessionId);
  }, [store.sessionId, processAll]);

  const allDone = enabledPages.every((p) => p.status === 'done' || p.status === 'skipped' || p.status === 'error');
  const isBulkRunning = processAll.isPending;

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6 overflow-auto">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Processing Pages</h2>
        <p className="text-sm text-text-secondary">
          {mode === 'bulk'
            ? 'All pages are processed sequentially on the server in a single request.'
            : 'Each page is sent to the VLM individually.'}
        </p>
      </div>

      {/* Mode toggle */}
      {!store.isProcessing && !isBulkRunning && !allDone && (
        <div className="flex gap-2">
          <Button variant={mode === 'bulk' ? 'default' : 'outline'} size="sm" onClick={() => setMode('bulk')}>
            <Zap className="mr-1 size-3.5" /> Bulk (Server)
          </Button>
          <Button variant={mode === 'page' ? 'default' : 'outline'} size="sm" onClick={() => setMode('page')}>
            <Settings className="mr-1 size-3.5" /> Page-by-Page
          </Button>
        </div>
      )}

      {/* Progress bar */}
      <Card className="p-4">
        <div className="flex items-center justify-between text-sm text-text-secondary">
          <span>{donePages.length} / {enabledPages.length} pages</span>
          <span>{progress}%</span>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-bg-base">
          <div className="h-full rounded-full bg-accent transition-all duration-300" style={{ width: `${progress}%` }} />
        </div>
        {isBulkRunning && (
          <p className="mt-2 flex items-center gap-1 text-xs text-text-muted">
            <Loader2 className="size-3 animate-spin" /> Server is processing all pages…
          </p>
        )}
        {!isBulkRunning && store.currentProcessingPage != null && (
          <p className="mt-2 text-xs text-text-muted">Processing page {store.currentProcessingPage + 1}…</p>
        )}
      </Card>

      {/* Page-by-page list (shown when in page mode or when pages have started) */}
      {(mode === 'page' || donePages.length > 0) && !isBulkRunning && (
        <div className="flex flex-col gap-1">
          {enabledPages.map((page) => (
            <div key={page.pageNum} className={cn(
              'flex items-center justify-between rounded-md px-3 py-1.5 text-sm',
              page.status === 'done' && 'text-green-400',
              page.status === 'error' && 'text-red-400',
              page.status === 'processing' && 'text-yellow-400',
              page.status === 'pending' && 'text-text-muted',
              page.status === 'skipped' && 'text-gray-400',
            )}>
              <span>Page {page.pageNum + 1}</span>
              <span className="flex items-center gap-1 text-xs">
                {page.status === 'processing' && <Loader2 className="size-3 animate-spin" />}
                {page.status === 'done' && <Check className="size-3" />}
                {page.status === 'error' && <X className="size-3" />}
                {page.status}
                {page.model && ` (${page.model})`}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-3">
        {mode === 'page' && (
          <div className="flex items-center gap-2">
            <Switch checked={store.autoProcess} onCheckedChange={store.setAutoProcess} />
            <Label className="text-sm">Auto-advance</Label>
          </div>
        )}
        <div className="flex-1" />

        {mode === 'bulk' && !isBulkRunning && !allDone && (
          <Button onClick={handleBulk}>
            <Zap className="mr-1 size-4" /> Process All
          </Button>
        )}
        {isBulkRunning && (
          <div className="flex items-center gap-2 text-sm text-text-muted">
            <Loader2 className="size-4 animate-spin" /> Running…
          </div>
        )}

        {mode === 'page' && !store.isProcessing && !allDone && (
          <Button onClick={handleStart}>
            <Play className="mr-1 size-4" /> {donePages.length > 0 ? 'Resume' : 'Start'}
          </Button>
        )}
        {mode === 'page' && store.isProcessing && (
          <Button variant="outline" onClick={handleStop}>
            <Square className="mr-1 size-4" /> Stop
          </Button>
        )}
      </div>

      <div className="flex justify-between">
        <Button variant="outline" onClick={() => store.setStep('pages')} disabled={store.isProcessing || isBulkRunning}>
          <ArrowLeft className="mr-1 size-4" /> Back
        </Button>
        <Button onClick={() => store.setStep('review')} disabled={donePages.length === 0 || store.isProcessing || isBulkRunning}>
          Review Results <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// Step 5: VLM Review + Stitch (combined)
function VlmReviewStep() {
  const store = useVlmIngestStore();
  const updatePageResult = useUpdatePageResult();
  const exportConfig = useExportConfig();
  const [selectedPage, setSelectedPage] = useState(0);
  const [editingMd, setEditingMd] = useState('');
  const [showStitch, setShowStitch] = useState(false);

  const donePages = store.pages.filter((p) => p.status === 'done' || p.status === 'error');

  useEffect(() => {
    const page = store.pages[selectedPage];
    if (page) setEditingMd(page.markdown);
  }, [selectedPage, store.pages]);

  // Show stitch view when stitch result arrives
  useEffect(() => {
    if (store.stitchResult && store.step === 'stitch') {
      setShowStitch(true);
    }
  }, [store.stitchResult, store.step]);

  const handleSave = useCallback(() => {
    if (!store.sessionId) return;
    updatePageResult.mutate(
      { sid: store.sessionId, pageNum: selectedPage, markdown: editingMd },
      { onSuccess: () => { store.setPageMarkdown(selectedPage, editingMd); } },
    );
  }, [store, selectedPage, editingMd, updatePageResult]);

  const stitch = useStitch();
  const handleStitch = useCallback(() => {
    if (!store.sessionId) return;
    stitch.mutate(store.sessionId);
  }, [store, stitch]);

  if (showStitch) {
    // Stitch view
    return (
      <div className="flex flex-col gap-4 overflow-auto">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-text-primary">Stitched Document</h2>
            {store.stitchResult && (
              <p className="text-sm text-text-secondary">
                {store.stitchResult.pages_processed} pages •{' '}
                {store.stitchResult.duplicate_lines_removed} dupes removed •{' '}
                {store.stitchResult.tables_merged} tables merged •{' '}
                {store.stitchResult.headings_merged} headings merged
              </p>
            )}
          </div>
          <Button variant="outline" size="sm" onClick={() => { if (store.sessionId) exportConfig.mutate(store.sessionId); }}>
            <Download className="mr-1 size-3.5" /> Export Config
          </Button>
        </div>

        <textarea
          className="min-h-[500px] w-full rounded-md border border-border bg-bg-base p-3 font-mono text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
          value={store.finalMarkdown}
          onChange={(e) => store.setFinalMarkdown(e.target.value)}
        />
        <div className="text-right text-xs text-text-muted">{store.finalMarkdown.length.toLocaleString()} characters</div>

        <div className="flex justify-between">
          <Button variant="outline" onClick={() => setShowStitch(false)}>
            <ArrowLeft className="mr-1 size-4" /> Back to Review
          </Button>
          <Button onClick={() => store.setStep('commit')}>
            Commit <ArrowRight className="ml-1 size-4" />
          </Button>
        </div>
      </div>
    );
  }

  // Per-page review view
  return (
    <div className="flex flex-col gap-4 overflow-auto">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Review Results</h2>
          <p className="text-sm text-text-secondary">Review and correct VLM output for each page before stitching.</p>
        </div>
      </div>

      <div className="flex gap-4">
        <div className="flex w-40 shrink-0 flex-col gap-1 overflow-auto">
          {donePages.map((page) => (
            <button
              key={page.pageNum}
              onClick={() => setSelectedPage(page.pageNum)}
              className={cn(
                'rounded-md px-3 py-2 text-left text-sm transition-colors',
                selectedPage === page.pageNum ? 'bg-accent/10 text-accent' : 'text-text-secondary hover:bg-bg-card',
                page.status === 'error' && 'text-red-400',
              )}
            >
              Page {page.pageNum + 1}
              <span className="ml-1 text-xs text-text-muted">({page.markdown.length} chars)</span>
            </button>
          ))}
        </div>

        <div className="flex flex-1 flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-text-secondary">
              Page {selectedPage + 1} — {store.pages[selectedPage]?.model || 'no model'}
            </span>
            <Button variant="outline" size="sm" onClick={handleSave} disabled={editingMd === store.pages[selectedPage]?.markdown}>
              <Save className="mr-1 size-3" /> Save Correction
            </Button>
          </div>
          <textarea
            className="min-h-[400px] w-full flex-1 rounded-md border border-border bg-bg-base p-3 font-mono text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            value={editingMd}
            onChange={(e) => setEditingMd(e.target.value)}
          />
        </div>
      </div>

      <div className="flex justify-between">
        <Button variant="outline" onClick={() => store.setStep('processing')}>
          <ArrowLeft className="mr-1 size-4" /> Back
        </Button>
        <Button onClick={handleStitch} disabled={store.status === 'busy'}>
          {store.status === 'busy' ? <Loader2 className="mr-1 size-4 animate-spin" /> : <Zap className="mr-1 size-4" />}
          Stitch Pages
        </Button>
      </div>
    </div>
  );
}

// Step 6: VLM Commit
function VlmCommitStep() {
  const store = useVlmIngestStore();
  const commit = useCommit();
  const exportConfig = useExportConfig();
  const { workspace, project, collection } = useScopeStore();

  const normalizeScope = (value: string): string | undefined => {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  };

  const handleCommit = useCallback(() => {
    if (!store.sessionId) return;
    commit.mutate({
      sid: store.sessionId,
      req: {
        markdown: store.finalMarkdown || null,
        feed_pipeline: true,
        tenant_id: normalizeScope(workspace),
        project_id: normalizeScope(project),
        corpus_id: normalizeScope(collection),
      },
    });
  }, [store, commit, workspace, project, collection]);

  return (
    <div className="mx-auto flex max-w-lg flex-col gap-6 overflow-auto">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-text-primary">Commit</h2>
        <p className="mt-1 text-sm text-text-secondary">Save the stitched markdown and index it into the search collection.</p>
      </div>

      <Card className="flex flex-col gap-3 p-4">
        <div className="flex justify-between text-sm">
          <span className="text-text-secondary">Document</span>
          <span className="text-text-primary">{store.filename}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-secondary">Pages processed</span>
          <span className="text-text-primary">{store.pages.filter((p) => p.status === 'done').length} / {store.pageCount}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-secondary">Output size</span>
          <span className="text-text-primary">{store.finalMarkdown.length.toLocaleString()} chars</span>
        </div>
        {store.runId && (
          <div className="flex justify-between text-sm">
            <span className="text-text-secondary">Run ID</span>
            <span className="text-text-primary">#{store.runId}</span>
          </div>
        )}
      </Card>

      <div className="flex flex-col gap-2">
        <Button onClick={handleCommit} disabled={store.status === 'busy'} className="w-full">
          {store.status === 'busy' ? <Loader2 className="mr-1 size-4 animate-spin" /> : <Save className="mr-1 size-4" />}
          Commit & Index
        </Button>
        {!store.runId && (
          <p className="text-center text-xs text-text-muted">No existing run — a new workflow run will be created on commit.</p>
        )}
        <Button variant="outline" className="w-full" onClick={() => { navigator.clipboard.writeText(store.finalMarkdown); toast.success('Markdown copied to clipboard'); }}>
          Copy Markdown
        </Button>
        <Button variant="outline" className="w-full" onClick={() => { if (store.sessionId) exportConfig.mutate(store.sessionId); }}>
          <Download className="mr-1 size-4" /> Export Config for Reuse
        </Button>
      </div>

      <div className="flex justify-start">
        <Button variant="outline" onClick={() => store.setStep('stitch')}>
          <ArrowLeft className="mr-1 size-4" /> Back
        </Button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Shared result/error cards
// ═══════════════════════════════════════════════════════════════════

function IngestResultCard({ result }: { result: IngestResponse }) {
  return (
    <Card className={result.error_message ? 'border-state-error' : 'border-state-success'}>
      <CardContent className="flex items-start gap-3 p-4">
        {result.error_message ? (
          <X className="mt-0.5 size-5 shrink-0 text-state-error" />
        ) : (
          <Check className="mt-0.5 size-5 shrink-0 text-state-success" />
        )}
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-semibold text-text-primary">{result.doc_id}</p>
          <div className="flex flex-wrap gap-2 text-xs text-text-secondary">
            <span>ID: {result.doc_id}</span>
            <span>•</span>
            <span>{result.chunks_upserted} chunks</span>
          </div>
          <Badge variant={result.ok ? 'default' : 'secondary'}>{result.ok ? 'Ingested' : 'Failed'}</Badge>
          {result.error_message && <p className="text-xs text-state-error">{result.error_message}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

function IngestErrorCard({ error }: { error: string }) {
  return (
    <Card className="border-state-error">
      <CardContent className="flex items-center gap-3 p-4">
        <X className="size-5 shrink-0 text-state-error" />
        <p className="text-sm text-state-error">{error}</p>
      </CardContent>
    </Card>
  );
}
