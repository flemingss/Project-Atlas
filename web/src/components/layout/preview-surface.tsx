/**
 * PreviewSurface — Standardized container for document/image/PDF previews.
 *
 * Provides built-in fit-mode controls (Fit Page, Fit Width, Actual Size, Manual Zoom)
 * and a ResizeObserver to recalculate dimensions on container resize.
 *
 * This eliminates bespoke zoom/fit logic on every preview page.
 *
 * Usage:
 *   <PreviewSurface fitMode="fit-page" onFitModeChange={setFitMode}>
 *     <img src={url} style={{ width: computedWidth }} />
 *   </PreviewSurface>
 */
import * as React from 'react';
import { Maximize, Minimize2, ScanEye, ZoomIn, ZoomOut } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { usePreviewFit, FitMode } from '@/hooks/use-preview-fit';

interface PreviewSurfaceProps {
  /** Current fit mode */
  fitMode?: FitMode;
  /** Callback when user changes fit mode */
  onFitModeChange?: (mode: FitMode) => void;
  /** Initial zoom (used in manual mode), defaults to 1.0 */
  initialZoom?: number;
  /** Whether to show the fit-mode toolbar */
  showControls?: boolean;
  /** Extra class on outer wrapper */
  className?: string;
  /** Content to render inside the preview area */
  children: React.ReactNode;
}

export function PreviewSurface({
  fitMode: controlledFitMode,
  onFitModeChange,
  initialZoom = 1.0,
  showControls = true,
  className,
  children,
}: PreviewSurfaceProps) {
  const {
    containerRef,
    fitMode,
    zoom,
    setFitMode: internalSetFitMode,
    setZoom,
  } = usePreviewFit({
    initialFitMode: controlledFitMode ?? 'fit-page',
    initialZoom,
  });

  const setFitMode = onFitModeChange ?? internalSetFitMode;

  const FIT_MODES: Array<{ mode: FitMode; icon: typeof Maximize; label: string }> = [
    { mode: 'fit-page', icon: Maximize, label: 'Fit Page' },
    { mode: 'fit-width', icon: Minimize2, label: 'Fit Width' },
    { mode: 'actual-size', icon: ScanEye, label: 'Actual Size' },
  ];

  return (
    <div className={cn('flex flex-1 flex-col overflow-hidden', className)}>
      {/* ── Toolbar ── */}
      {showControls && (
        <div className="flex shrink-0 items-center gap-1 border-b border-border bg-bg-surface px-3 py-1.5">
          {FIT_MODES.map(({ mode, icon: Icon, label }) => (
            <Button
              key={mode}
              variant={fitMode === mode ? 'secondary' : 'ghost'}
              size="sm"
              title={label}
              onClick={() => setFitMode(mode)}
              className="h-7 px-2"
            >
              <Icon className="size-3.5" />
            </Button>
          ))}

          <span className="mx-1 h-4 w-px bg-border" />

          <Button
            variant="ghost"
            size="sm"
            title="Zoom Out"
            onClick={() => {
              setFitMode('manual');
              setZoom(Math.max(0.1, zoom - 0.1));
            }}
            className="h-7 px-2"
          >
            <ZoomOut className="size-3.5" />
          </Button>

          <span className="min-w-[3rem] text-center text-xs text-text-secondary">
            {Math.round(zoom * 100)}%
          </span>

          <Button
            variant="ghost"
            size="sm"
            title="Zoom In"
            onClick={() => {
              setFitMode('manual');
              setZoom(Math.min(5, zoom + 0.1));
            }}
            className="h-7 px-2"
          >
            <ZoomIn className="size-3.5" />
          </Button>
        </div>
      )}

      {/* ── Preview area ── */}
      <div
        ref={containerRef}
        className="flex flex-1 items-start justify-center overflow-auto bg-bg-surface/50 p-4"
      >
        {children}
      </div>
    </div>
  );
}

export type { FitMode };
export { usePreviewFit };
