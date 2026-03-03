/**
 * PDF Viewer component — renders a single page of a PDF via pdfjs-dist
 * with page navigation, zoom, and optional crop overlay.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import {
  ChevronLeft,
  ChevronRight,
  Minus,
  Plus,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useEditorStore } from '@/stores/editor-store';

// Set worker src (Vite handles the import)
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

interface PdfViewerProps {
  pdfUrl: string | null;
}

export function PdfViewer({ pdfUrl }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const {
    currentPage,
    totalPages,
    zoomScale,
    vlm,
    setCurrentPage,
    setZoomScale,
  } = useEditorStore();

  const [pdfDoc, setPdfDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [rendering, setRendering] = useState(false);
  const [pageInput, setPageInput] = useState('1');

  // Load PDF document when URL changes
  useEffect(() => {
    if (!pdfUrl) return;
    let cancelled = false;

    const stored = localStorage.getItem('atlas_admin_token');
    const queryToken = (new URLSearchParams(window.location.search).get('token') || '').trim();
    const token = (stored && stored.trim()) ? stored : (queryToken || null);
    if (!stored && token) {
      localStorage.setItem('atlas_admin_token', token);
    }
    const loadingTask = pdfjs.getDocument({
      url: pdfUrl,
      httpHeaders: token ? { 'X-Atlas-Admin-Token': token } : undefined,
    });

    loadingTask.promise
      .then((doc) => {
        if (!cancelled) setPdfDoc(doc);
      })
      .catch((err) => {
        if (!cancelled) console.error('PDF load error:', err);
      });

    return () => {
      cancelled = true;
    };
  }, [pdfUrl]);

  // Render current page
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current) return;
    let cancelled = false;
    setRendering(true);

    (async () => {
      try {
        // pdfjs pages are 1-indexed
        const page = await pdfDoc.getPage(currentPage + 1);
        if (cancelled) return;

        const viewport = page.getViewport({ scale: zoomScale * 1.5 });
        const canvas = canvasRef.current!;
        const ctx = canvas.getContext('2d')!;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await page.render({ canvasContext: ctx, viewport }).promise;
      } catch (err) {
        console.error('Page render error:', err);
      } finally {
        if (!cancelled) setRendering(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pdfDoc, currentPage, zoomScale]);

  // Sync pageInput display
  useEffect(() => {
    setPageInput(String(currentPage + 1));
  }, [currentPage]);

  // Draw crop overlay
  useEffect(() => {
    const canvas = canvasRef.current;
    const overlay = overlayRef.current;
    if (!canvas || !overlay) return;
    if (!vlm.showCropOverlay || !pdfDoc) {
      overlay.style.display = 'none';
      return;
    }

    const w = canvas.width;
    const h = canvas.height;
    overlay.width = w;
    overlay.height = h;
    overlay.style.display = 'block';

    const ctx = overlay.getContext('2d')!;
    ctx.clearRect(0, 0, w, h);

    const cropTopPx = Math.round(h * vlm.cropTop);
    const cropBottomPx = Math.round(h * vlm.cropBottom);

    // Semi-transparent red bars
    ctx.fillStyle = 'rgba(248, 113, 113, 0.35)';
    if (cropTopPx > 0) ctx.fillRect(0, 0, w, cropTopPx);
    if (cropBottomPx > 0) ctx.fillRect(0, h - cropBottomPx, w, cropBottomPx);

    // Dashed lines
    ctx.strokeStyle = 'rgba(248, 113, 113, 0.8)';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);

    if (cropTopPx > 0) {
      ctx.beginPath();
      ctx.moveTo(0, cropTopPx);
      ctx.lineTo(w, cropTopPx);
      ctx.stroke();
    }
    if (cropBottomPx > 0) {
      ctx.beginPath();
      ctx.moveTo(0, h - cropBottomPx);
      ctx.lineTo(w, h - cropBottomPx);
      ctx.stroke();
    }

    // Labels
    ctx.setLineDash([]);
    ctx.font = '11px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';

    if (cropTopPx > 14) {
      ctx.fillText(
        `Crop top ${Math.round(vlm.cropTop * 100)}%`,
        w / 2,
        cropTopPx / 2 + 4,
      );
    }
    if (cropBottomPx > 14) {
      ctx.fillText(
        `Crop bottom ${Math.round(vlm.cropBottom * 100)}%`,
        w / 2,
        h - cropBottomPx / 2 + 4,
      );
    }
  }, [pdfDoc, currentPage, zoomScale, vlm]);

  const handlePageInputChange = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key !== 'Enter') return;
      const p = parseInt(pageInput, 10) - 1;
      if (p >= 0 && p < totalPages) setCurrentPage(p);
    },
    [pageInput, totalPages, setCurrentPage],
  );

  if (!pdfUrl) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-text-muted">
        <span className="text-5xl opacity-40">📄</span>
        <p className="text-sm">
          Enter a Document ID and click Load to view the source PDF
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* ── PDF Toolbar ── */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border bg-bg-secondary px-3 py-1.5">
        <div className="flex items-center gap-1 text-xs">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={currentPage <= 0}
            onClick={() => setCurrentPage(currentPage - 1)}
          >
            <ChevronLeft className="size-3.5" />
          </Button>
          <Input
            className="h-6 w-10 px-1 text-center text-xs"
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            onKeyDown={handlePageInputChange}
          />
          <span className="text-text-muted">/ {totalPages}</span>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={currentPage >= totalPages - 1}
            onClick={() => setCurrentPage(currentPage + 1)}
          >
            <ChevronRight className="size-3.5" />
          </Button>
        </div>

        <div className="flex-1" />

        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => setZoomScale(zoomScale - 0.25)}
        >
          <Minus className="size-3.5" />
        </Button>
        <span className="min-w-[36px] text-center text-[11px] text-text-secondary">
          {Math.round(zoomScale * 100)}%
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => setZoomScale(zoomScale + 0.25)}
        >
          <Plus className="size-3.5" />
        </Button>
      </div>

      {/* ── Canvas viewport ── */}
      <div
        ref={containerRef}
        className="relative flex flex-1 items-start justify-center overflow-auto bg-[#2a2a3e] p-3"
      >
        {rendering && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/60 text-sm text-text-secondary">
            Rendering page…
          </div>
        )}
        <div className="relative inline-block">
          <canvas ref={canvasRef} className="shadow-lg" />
          <canvas
            ref={overlayRef}
            className="pointer-events-none absolute left-0 top-0"
          />
        </div>
      </div>
    </div>
  );
}
