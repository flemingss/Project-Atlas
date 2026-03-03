/**
 * VLM Ingest wizard — multi-step interactive workflow for VLM-first PDF ingestion.
 *
 * Steps:
 * 1. Start   — Upload PDF or enter run ID
 * 2. Config  — Set global DPI, crop margins, system prompt
 * 3. Pages   — Review page grid, enable/disable, per-page overrides
 * 4. Process — Sequential VLM processing with progress
 * 5. Review  — Per-page result review + corrections
 * 6. Stitch  — Preview stitched document
 * 7. Commit  — Save to artifact store
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  FileUp,
  Loader2,
  Minus,
  Plus,
  Play,
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
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { vlmIngestApi } from '@/services/vlm-ingest-api';
import { useVlmIngestStore, type WizardStep } from '@/stores/vlm-ingest-store';
import {
  useStartSession,
  useStartSessionUpload,
  useUpdateConfig,
  useVlmSession,
  isSessionNotFoundError,
  useProcessPage,
  useStitch,
  useCommit,
  useExportConfig,
  useDeleteSession,
  useUpdatePageResult,
  useVlmThumbnails,
} from '@/hooks/use-vlm-ingest';

// ── Step definitions ──────────────────────────────────────────────

const STEPS: { key: WizardStep; label: string; icon: React.ElementType }[] = [
  { key: 'start', label: 'Start', icon: FileUp },
  { key: 'configure', label: 'Configure', icon: Settings },
  { key: 'pages', label: 'Pages', icon: Eye },
  { key: 'processing', label: 'Process', icon: Zap },
  { key: 'review', label: 'Review', icon: Eye },
  { key: 'stitch', label: 'Stitch', icon: Check },
  { key: 'commit', label: 'Commit', icon: Save },
];

function stepIndex(step: WizardStep): number {
  return STEPS.findIndex((s) => s.key === step);
}

// ── Main component ────────────────────────────────────────────────

export function VlmIngestPage() {
  const store = useVlmIngestStore();
  const deleteSession = useDeleteSession();
  const sessionQuery = useVlmSession(store.sessionId);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);

  useEffect(() => {
    if (sessionQuery.error && isSessionNotFoundError(sessionQuery.error)) {
      markSessionExpired('The backend session no longer exists (server restart/reload).');
    }
  }, [sessionQuery.error, markSessionExpired]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Step indicator */}
      <div className="flex shrink-0 items-center gap-1 border-b border-border bg-bg-surface px-4 py-2">
        {STEPS.map((s, i) => {
          const current = stepIndex(store.step);
          const isActive = i === current;
          const isDone = i < current;
          const Icon = s.icon;
          return (
            <div key={s.key} className="flex items-center gap-1">
              {i > 0 && (
                <div
                  className={cn(
                    'h-px w-6',
                    isDone ? 'bg-accent' : 'bg-border',
                  )}
                />
              )}
              <button
                disabled={i > current || !store.sessionId}
                onClick={() => i <= current && store.setStep(s.key)}
                className={cn(
                  'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                  isActive && 'bg-accent/10 text-accent',
                  isDone && 'text-accent/70',
                  !isActive && !isDone && 'text-text-muted',
                  i <= current && 'cursor-pointer hover:bg-bg-card',
                )}
              >
                <Icon className="size-3.5" />
                <span className="hidden sm:inline">{s.label}</span>
              </button>
            </div>
          );
        })}

        <div className="flex-1" />

        {/* Status */}
        <span
          className={cn(
            'text-xs',
            store.status === 'error' && 'text-red-400',
            store.status === 'busy' && 'text-yellow-400',
            store.status === 'idle' && 'text-text-muted',
          )}
        >
          {store.statusText}
        </span>

        {/* Discard button */}
        {store.sessionId && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-2 text-red-400 hover:text-red-300"
            onClick={() => {
              if (store.sessionId) deleteSession.mutate(store.sessionId);
            }}
          >
            <Trash2 className="mr-1 size-3.5" />
            Discard
          </Button>
        )}
      </div>

      {/* Step content */}
      <div className="flex flex-1 flex-col overflow-hidden p-4">
        {store.sessionExpired && (
          <Card className="mx-auto mb-4 flex max-w-2xl shrink-0 items-start justify-between gap-4 border-red-500/30 bg-red-500/5 p-4">
            <div>
              <h3 className="text-sm font-semibold text-red-300">VLM session expired</h3>
              <p className="mt-1 text-sm text-red-200">
                {store.sessionExpiredReason || 'The backend session is no longer available.'}
              </p>
              <p className="mt-1 text-xs text-text-muted">
                This usually happens after API restart/reload. Start a new session to continue.
              </p>
            </div>
            <Button
              onClick={() => {
                store.reset();
              }}
            >
              Start New Session
            </Button>
          </Card>
        )}

        {store.step === 'configure' ? (
          <ConfigureStep />
        ) : (
          <div className="flex-1 overflow-auto">
            {store.step === 'start' && <StartStep />}
            {store.step === 'pages' && <PagesStep />}
            {store.step === 'processing' && <ProcessingStep />}
            {store.step === 'review' && <ReviewStep />}
            {store.step === 'stitch' && <StitchStep />}
            {store.step === 'commit' && <CommitStep />}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Step 1: Start ─────────────────────────────────────────────────

function StartStep() {
  const [runIdInput, setRunIdInput] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const startSession = useStartSession();
  const startUpload = useStartSessionUpload();
  const store = useVlmIngestStore();

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

  const isBusy = store.status === 'busy';

  return (
    <div className="mx-auto flex max-w-lg flex-col gap-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-text-primary">VLM Ingest</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Process a PDF document page-by-page using a Vision Language Model.
        </p>
      </div>

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

      {/* Divider */}
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
          <Button
            variant="outline"
            className="flex-1"
            onClick={() => fileRef.current?.click()}
            disabled={isBusy}
          >
            <FileUp className="mr-1 size-4" />
            {file ? file.name : 'Choose PDF…'}
          </Button>
          <Button onClick={handleUpload} disabled={isBusy || !file}>
            {isBusy ? <Loader2 className="size-4 animate-spin" /> : <Play className="mr-1 size-4" />}
            Upload
          </Button>
        </div>
      </Card>

      {/* Import config */}
      <Card className="flex flex-col gap-3 p-4">
        <Label className="text-sm font-medium text-text-secondary">
          Have a saved config? Paste it after creating a session.
        </Label>
      </Card>
    </div>
  );
}

// ── Step 2: Configure ─────────────────────────────────────────────

function ConfigureStep() {
  const store = useVlmIngestStore();
  const updateConfig = useUpdateConfig();
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);
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
  }, [
    markSessionExpired,
    previewPage,
    store.sessionId,
    store.pageCount,
    store.dpi,
    store.cropTop,
    store.cropBottom,
    store.cropLeft,
    store.cropRight,
  ]);

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
    return () => {
      cancelled = true;
    };
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
    store.setStep('pages');
  }, [handleApply, store]);

  return (
    <div className="flex flex-1 flex-col min-h-0 gap-3">
      <div className="w-full shrink-0 px-4">
        <h2 className="text-lg font-semibold text-text-primary">Global Settings</h2>
        <p className="mt-1 text-sm text-text-secondary">
          These defaults apply to all pages. You can override per-page in the next step.
        </p>
        <p className="mt-1 text-xs text-text-muted">
          {store.filename} — {store.pageCount} pages
        </p>
      </div>

      <div className="flex flex-1 min-h-0 flex-col gap-4 overflow-auto px-4 xl:flex-row xl:overflow-hidden">
        <Card className="flex shrink-0 flex-col gap-4 p-4 xl:w-[360px] xl:overflow-auto">
        {/* DPI */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <Label className="text-sm">Render DPI</Label>
            <span className="text-xs text-text-muted">{store.dpi}</span>
          </div>
          <Slider
            min={72}
            max={400}
            step={1}
            value={[store.dpi]}
            onValueChange={([v]) => store.setGlobalConfig({ dpi: v })}
          />
        </div>

        {/* Crop margins */}
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-text-secondary">Crop Top</Label>
            <Slider
              min={0}
              max={0.25}
              step={0.005}
              value={[store.cropTop]}
              onValueChange={([v]) => store.setGlobalConfig({ cropTop: v })}
            />
            <span className="text-right text-xs text-text-muted">
              {(store.cropTop * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-text-secondary">Crop Bottom</Label>
            <Slider
              min={0}
              max={0.25}
              step={0.005}
              value={[store.cropBottom]}
              onValueChange={([v]) => store.setGlobalConfig({ cropBottom: v })}
            />
            <span className="text-right text-xs text-text-muted">
              {(store.cropBottom * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-text-secondary">Crop Left</Label>
            <Slider
              min={0}
              max={0.25}
              step={0.005}
              value={[store.cropLeft]}
              onValueChange={([v]) => store.setGlobalConfig({ cropLeft: v })}
            />
            <span className="text-right text-xs text-text-muted">
              {(store.cropLeft * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-text-secondary">Crop Right</Label>
            <Slider
              min={0}
              max={0.25}
              step={0.005}
              value={[store.cropRight]}
              onValueChange={([v]) => store.setGlobalConfig({ cropRight: v })}
            />
            <span className="text-right text-xs text-text-muted">
              {(store.cropRight * 100).toFixed(1)}%
            </span>
          </div>
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
        </Card>

        <Card className="flex flex-1 min-h-0 flex-col gap-3 p-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-text-primary">PDF Preview</h3>
              <p className="text-xs text-text-muted">
                Raw page with crop guides overlaid from current global settings.
              </p>
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setPreviewFitMode('manual');
                  setPreviewZoom((z) => Math.max(0.2, z - 0.1));
                }}
                disabled={!previewSrc}
              >
                <Minus className="size-3.5" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setPreviewFitMode('actual');
                  setPreviewZoom(1);
                }}
                disabled={!previewSrc}
              >
                {Math.round(previewZoom * 100)}%
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setPreviewFitMode('manual');
                  setPreviewZoom((z) => Math.min(2.5, z + 0.1));
                }}
                disabled={!previewSrc}
              >
                <Plus className="size-3.5" />
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={previewFitMode === 'fit-page' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setPreviewFitMode('fit-page')}
              disabled={!previewSrc}
            >
              Fit Page
            </Button>
            <Button
              variant={previewFitMode === 'fit-width' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setPreviewFitMode('fit-width')}
              disabled={!previewSrc}
            >
              Fit Width
            </Button>
            <Button
              variant={previewFitMode === 'actual' ? 'default' : 'outline'}
              size="sm"
              onClick={() => {
                setPreviewFitMode('actual');
                setPreviewZoom(1);
              }}
              disabled={!previewSrc}
            >
              Actual Size
            </Button>
            <span className="text-xs text-text-muted">Mode: {previewFitMode}</span>
          </div>

          <div className="flex items-center justify-between rounded border border-border bg-bg-base px-2 py-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPreviewPage((p) => Math.max(0, p - 1))}
              disabled={previewPage <= 0 || store.pageCount <= 0}
            >
              <ChevronLeft className="mr-1 size-4" />
              Prev
            </Button>
            <span className="text-xs text-text-secondary">
              Page {store.pageCount > 0 ? previewPage + 1 : 0} / {store.pageCount}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPreviewPage((p) => Math.min(store.pageCount - 1, p + 1))}
              disabled={store.pageCount <= 0 || previewPage >= store.pageCount - 1}
            >
              Next
              <ChevronRight className="ml-1 size-4" />
            </Button>
          </div>

          <div ref={previewViewportRef} className="relative flex-1 overflow-hidden rounded border border-border bg-bg-base p-2">
            {previewLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg-base/70 text-sm text-text-muted">
                <Loader2 className="mr-2 size-4 animate-spin" />
                Rendering preview…
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
                <div
                  className="relative"
                  style={{
                    transform: `scale(${previewZoom})`,
                    transformOrigin: 'center center',
                  }}
                >
                  <img
                    src={previewSrc}
                    alt={`Preview page ${previewPage + 1}`}
                    className="block h-auto w-auto max-h-none max-w-none rounded border border-border/70"
                  />

                  <div className="pointer-events-none absolute inset-0">
                    <div
                      className="absolute left-0 right-0 border-t-2 border-red-500"
                      style={{ top: `${store.cropTop * 100}%` }}
                    />
                    <div
                      className="absolute left-0 right-0 border-b-2 border-red-500"
                      style={{ bottom: `${store.cropBottom * 100}%` }}
                    />
                    <div
                      className="absolute top-0 bottom-0 border-l-2 border-red-500"
                      style={{ left: `${store.cropLeft * 100}%` }}
                    />
                    <div
                      className="absolute top-0 bottom-0 border-r-2 border-red-500"
                      style={{ right: `${store.cropRight * 100}%` }}
                    />
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
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <Button onClick={handleNext}>
          Next: Review Pages
          <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 3: Pages ─────────────────────────────────────────────────

function PagesStep() {
  const store = useVlmIngestStore();
  const setThumbnails = useVlmIngestStore((s) => s.setThumbnails);
  const markSessionExpired = useVlmIngestStore((s) => s.markSessionExpired);
  const { data: thumbnails, isLoading, error: thumbnailsError } = useVlmThumbnails(store.sessionId);
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const updateConfig = useUpdateConfig();

  // Load thumbnails into store
  useEffect(() => {
    if (thumbnails) setThumbnails(thumbnails);
  }, [thumbnails, setThumbnails]);

  useEffect(() => {
    if (thumbnailsError && isSessionNotFoundError(thumbnailsError)) {
      markSessionExpired('The backend session was lost while loading page thumbnails.');
    }
  }, [thumbnailsError, markSessionExpired]);

  const togglePage = useCallback(
    (pageNum: number) => {
      const page = store.pages[pageNum];
      if (!page) return;
      store.setPageEnabled(pageNum, !page.enabled);
      if (store.sessionId) {
        updateConfig.mutate({
          sid: store.sessionId,
          req: {
            page_overrides: [{ page_num: pageNum, enabled: !page.enabled }],
          },
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
          req: {
            page_overrides: [{ page_num: pageNum, dpi }],
          },
        });
      }
    },
    [store, updateConfig],
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Page Selection</h2>
          <p className="text-sm text-text-secondary">
            {store.totalEnabled} of {store.pageCount} pages enabled
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => store.pages.forEach((p) => store.setPageEnabled(p.pageNum, true))}
          >
            Enable All
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => store.pages.forEach((p) => store.setPageEnabled(p.pageNum, false))}
          >
            Disable All
          </Button>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-text-muted">
          <Loader2 className="size-4 animate-spin" />
          Loading thumbnails…
        </div>
      )}

      {/* Thumbnail grid */}
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
              {/* Thumbnail */}
              <div className="flex h-[120px] w-full items-center justify-center overflow-hidden rounded bg-bg-base">
                {thumb?.thumbnail ? (
                  <img
                    src={thumb.thumbnail}
                    alt={`Page ${page.pageNum + 1}`}
                    className="max-h-full max-w-full object-contain"
                  />
                ) : (
                  <span className="text-xs text-text-muted">No preview</span>
                )}
              </div>

              {/* Page label */}
              <div className="flex w-full items-center justify-between">
                <span className="text-xs font-medium text-text-primary">
                  Page {page.pageNum + 1}
                </span>
                <Switch
                  checked={page.enabled}
                  onCheckedChange={() => togglePage(page.pageNum)}
                  className="scale-75"
                />
              </div>

              {/* Status badge */}
              {page.status !== 'pending' && (
                <span
                  className={cn(
                    'absolute right-1 top-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                    page.status === 'done' && 'bg-green-500/20 text-green-400',
                    page.status === 'error' && 'bg-red-500/20 text-red-400',
                    page.status === 'processing' && 'bg-yellow-500/20 text-yellow-400',
                    page.status === 'skipped' && 'bg-gray-500/20 text-gray-400',
                  )}
                >
                  {page.status}
                </span>
              )}
            </Card>
          );
        })}
      </div>

      {/* Per-page config panel */}
      {selectedPage != null && store.pages[selectedPage] && (
        <Card className="mx-auto w-full max-w-md p-4">
          <h3 className="mb-2 text-sm font-semibold text-text-primary">
            Page {selectedPage + 1} Settings
          </h3>
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <Label className="text-xs">DPI Override</Label>
              <Input
                type="number"
                className="w-24 text-right"
                min={72}
                max={400}
                value={store.pages[selectedPage].dpiOverride ?? store.dpi}
                onChange={(e) =>
                  handleOverrideDpi(selectedPage!, parseInt(e.target.value, 10))
                }
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                store.setPageOverride(selectedPage!, {
                  dpiOverride: null,
                  cropTopOverride: null,
                  cropBottomOverride: null,
                  cropLeftOverride: null,
                  cropRightOverride: null,
                });
              }}
            >
              <RotateCcw className="mr-1 size-3" />
              Reset to Global
            </Button>
          </div>
        </Card>
      )}

      {/* Navigation */}
      <div className="flex justify-between">
        <Button variant="outline" onClick={() => store.setStep('configure')}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <Button onClick={() => store.setStep('processing')} disabled={store.totalEnabled === 0}>
          Start Processing
          <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 4: Processing ────────────────────────────────────────────

function ProcessingStep() {
  const store = useVlmIngestStore();
  const processPage = useProcessPage();
  const abortRef = useRef(false);

  const enabledPages = store.pages.filter((p) => p.enabled);
  const donePages = enabledPages.filter(
    (p) => p.status === 'done' || p.status === 'skipped' || p.status === 'error',
  );
  const progress = enabledPages.length > 0
    ? Math.round((donePages.length / enabledPages.length) * 100)
    : 0;

  const processNext = useCallback(async () => {
    // Always read fresh state to avoid stale-closure loops
    const current = useVlmIngestStore.getState();
    if (!current.sessionId) return;
    const next = current.pages.find(
      (p) => p.enabled && p.status === 'pending',
    );
    if (!next) {
      toast.success('All pages processed!');
      current.setStep('review');
      return;
    }
    processPage.mutate(
      { sid: current.sessionId, req: { page_num: next.pageNum } },
      {
        onSuccess: () => {
          // Auto-advance if enabled — read fresh state again
          const latest = useVlmIngestStore.getState();
          if (latest.autoProcess && !abortRef.current) {
            setTimeout(() => processNext(), 100);
          }
        },
      },
    );
  }, [processPage]);

  const handleStart = useCallback(() => {
    abortRef.current = false;
    processNext();
  }, [processNext]);

  const handleStop = useCallback(() => {
    abortRef.current = true;
    store.setProcessing(false);
  }, [store]);

  const allDone = enabledPages.every(
    (p) => p.status === 'done' || p.status === 'skipped' || p.status === 'error',
  );

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Processing Pages</h2>
        <p className="text-sm text-text-secondary">
          Each page is sent to the VLM individually — no cross-page context.
        </p>
      </div>

      {/* Progress bar */}
      <Card className="p-4">
        <div className="flex items-center justify-between text-sm text-text-secondary">
          <span>
            {donePages.length} / {enabledPages.length} pages
          </span>
          <span>{progress}%</span>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-bg-base">
          <div
            className="h-full rounded-full bg-accent transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
        {store.currentProcessingPage != null && (
          <p className="mt-2 text-xs text-text-muted">
            Processing page {store.currentProcessingPage + 1}…
          </p>
        )}
      </Card>

      {/* Page status list */}
      <div className="flex flex-col gap-1">
        {enabledPages.map((page) => (
          <div
            key={page.pageNum}
            className={cn(
              'flex items-center justify-between rounded-md px-3 py-1.5 text-sm',
              page.status === 'done' && 'text-green-400',
              page.status === 'error' && 'text-red-400',
              page.status === 'processing' && 'text-yellow-400',
              page.status === 'pending' && 'text-text-muted',
              page.status === 'skipped' && 'text-gray-400',
            )}
          >
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

      {/* Controls */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Switch
            checked={store.autoProcess}
            onCheckedChange={store.setAutoProcess}
          />
          <Label className="text-sm">Auto-advance</Label>
        </div>
        <div className="flex-1" />

        {!store.isProcessing && !allDone && (
          <Button onClick={handleStart}>
            <Play className="mr-1 size-4" />
            {donePages.length > 0 ? 'Resume' : 'Start'}
          </Button>
        )}
        {store.isProcessing && (
          <Button variant="outline" onClick={handleStop}>
            <Square className="mr-1 size-4" />
            Stop
          </Button>
        )}
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <Button variant="outline" onClick={() => store.setStep('pages')}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <Button onClick={() => store.setStep('review')} disabled={donePages.length === 0}>
          Review Results
          <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 5: Review ────────────────────────────────────────────────

function ReviewStep() {
  const store = useVlmIngestStore();
  const updatePageResult = useUpdatePageResult();
  const [selectedPage, setSelectedPage] = useState(0);
  const [editingMd, setEditingMd] = useState('');

  const donePages = store.pages.filter(
    (p) => p.status === 'done' || p.status === 'error',
  );

  // Initialize editing markdown
  useEffect(() => {
    const page = store.pages[selectedPage];
    if (page) setEditingMd(page.markdown);
  }, [selectedPage, store.pages]);

  const handleSave = useCallback(() => {
    if (!store.sessionId) return;
    updatePageResult.mutate(
      { sid: store.sessionId, pageNum: selectedPage, markdown: editingMd },
      {
        onSuccess: () => {
          store.setPageMarkdown(selectedPage, editingMd);
        },
      },
    );
  }, [store, selectedPage, editingMd, updatePageResult]);

  const stitch = useStitch();
  const handleStitch = useCallback(() => {
    if (!store.sessionId) return;
    stitch.mutate(store.sessionId);
  }, [store, stitch]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Review Results</h2>
          <p className="text-sm text-text-secondary">
            Review and correct VLM output for each page before stitching.
          </p>
        </div>
      </div>

      <div className="flex gap-4">
        {/* Page selector */}
        <div className="flex w-40 shrink-0 flex-col gap-1 overflow-auto">
          {donePages.map((page) => (
            <button
              key={page.pageNum}
              onClick={() => setSelectedPage(page.pageNum)}
              className={cn(
                'rounded-md px-3 py-2 text-left text-sm transition-colors',
                selectedPage === page.pageNum
                  ? 'bg-accent/10 text-accent'
                  : 'text-text-secondary hover:bg-bg-card',
                page.status === 'error' && 'text-red-400',
              )}
            >
              Page {page.pageNum + 1}
              <span className="ml-1 text-xs text-text-muted">
                ({page.markdown.length} chars)
              </span>
            </button>
          ))}
        </div>

        {/* Editor */}
        <div className="flex flex-1 flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-text-secondary">
              Page {selectedPage + 1} — {store.pages[selectedPage]?.model || 'no model'}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={handleSave}
              disabled={editingMd === store.pages[selectedPage]?.markdown}
            >
              <Save className="mr-1 size-3" />
              Save Correction
            </Button>
          </div>
          <textarea
            className="min-h-[400px] w-full flex-1 rounded-md border border-border bg-bg-base p-3 font-mono text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            value={editingMd}
            onChange={(e) => setEditingMd(e.target.value)}
          />
        </div>
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <Button variant="outline" onClick={() => store.setStep('processing')}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
        <Button onClick={handleStitch} disabled={store.status === 'busy'}>
          {store.status === 'busy' ? (
            <Loader2 className="mr-1 size-4 animate-spin" />
          ) : (
            <Zap className="mr-1 size-4" />
          )}
          Stitch Pages
        </Button>
      </div>
    </div>
  );
}

// ── Step 6: Stitch ────────────────────────────────────────────────

function StitchStep() {
  const store = useVlmIngestStore();
  const exportConfig = useExportConfig();

  return (
    <div className="flex flex-col gap-4">
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
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              if (store.sessionId) exportConfig.mutate(store.sessionId);
            }}
          >
            <Download className="mr-1 size-3.5" />
            Export Config
          </Button>
        </div>
      </div>

      {/* Markdown preview/edit */}
      <textarea
        className="min-h-[500px] w-full rounded-md border border-border bg-bg-base p-3 font-mono text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
        value={store.finalMarkdown}
        onChange={(e) => store.setFinalMarkdown(e.target.value)}
      />

      <div className="text-right text-xs text-text-muted">
        {store.finalMarkdown.length.toLocaleString()} characters
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <Button variant="outline" onClick={() => store.setStep('review')}>
          <ArrowLeft className="mr-1 size-4" />
          Back to Review
        </Button>
        <Button onClick={() => store.setStep('commit')}>
          Commit
          <ArrowRight className="ml-1 size-4" />
        </Button>
      </div>
    </div>
  );
}

// ── Step 7: Commit ────────────────────────────────────────────────

function CommitStep() {
  const store = useVlmIngestStore();
  const commit = useCommit();
  const exportConfig = useExportConfig();

  const handleCommit = useCallback(() => {
    if (!store.sessionId) return;
    commit.mutate({
      sid: store.sessionId,
      req: {
        markdown: store.finalMarkdown || null,
      },
    });
  }, [store, commit]);

  return (
    <div className="mx-auto flex max-w-lg flex-col gap-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-text-primary">Commit</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Save the stitched markdown as a pipeline artifact.
        </p>
      </div>

      <Card className="flex flex-col gap-3 p-4">
        <div className="flex justify-between text-sm">
          <span className="text-text-secondary">Document</span>
          <span className="text-text-primary">{store.filename}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-secondary">Pages processed</span>
          <span className="text-text-primary">
            {store.pages.filter((p) => p.status === 'done').length} / {store.pageCount}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-secondary">Output size</span>
          <span className="text-text-primary">
            {store.finalMarkdown.length.toLocaleString()} chars
          </span>
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
          {store.status === 'busy' ? (
            <Loader2 className="mr-1 size-4 animate-spin" />
          ) : (
            <Save className="mr-1 size-4" />
          )}
          Commit to Artifact Store
        </Button>
        {!store.runId && (
          <p className="text-center text-xs text-text-muted">
            No existing run — a new workflow run will be created on commit.
          </p>
        )}
        <Button
          variant="outline"
          className="w-full"
          onClick={() => {
            // Copy markdown to clipboard
            navigator.clipboard.writeText(store.finalMarkdown);
            toast.success('Markdown copied to clipboard');
          }}
        >
          Copy Markdown
        </Button>
        <Button
          variant="outline"
          className="w-full"
          onClick={() => {
            if (store.sessionId) exportConfig.mutate(store.sessionId);
          }}
        >
          <Download className="mr-1 size-4" />
          Export Config for Reuse
        </Button>
      </div>

      {/* Navigation */}
      <div className="flex justify-start">
        <Button variant="outline" onClick={() => store.setStep('stitch')}>
          <ArrowLeft className="mr-1 size-4" />
          Back
        </Button>
      </div>
    </div>
  );
}
