/**
 * MaskEditor — SVG overlay for drawing / displaying mask regions on a page image.
 *
 * Renders an `<img>` with an absolutely-positioned `<svg>` overlay.
 * - Blue dashed rectangles: suggested image regions (from PyMuPDF analysis)
 * - Red semi-transparent rectangles: active masks (will be white-filled before VLM)
 * - Mouse drag on the SVG to draw new mask regions
 * - Click an existing mask to select it, press delete to remove
 *
 * All coordinates use fractional (0-1) system relative to the image.
 */
import { useCallback, useRef, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

export interface MaskRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface MaskEditorProps {
  imageSrc: string;
  /** Suggestions from PyMuPDF (shown as blue dashed outlines). */
  suggestions?: MaskRect[];
  /** Active mask regions (shown as red semi-transparent fills). */
  masks: MaskRect[];
  onAddMask: (rect: MaskRect) => void;
  onRemoveMask: (index: number) => void;
  onAcceptSuggestion?: (rect: MaskRect) => void;
}

const MIN_DRAG = 0.01; // minimum fractional size to count as a drawn rect

export function MaskEditor({
  imageSrc,
  suggestions = [],
  masks,
  onAddMask,
  onRemoveMask,
  onAcceptSuggestion,
}: MaskEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedMask, setSelectedMask] = useState<number | null>(null);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragCurrent, setDragCurrent] = useState<{ x: number; y: number } | null>(null);

  const toFractional = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } => {
      const el = containerRef.current;
      if (!el) return { x: 0, y: 0 };
      const rect = el.getBoundingClientRect();
      return {
        x: Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
        y: Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
      };
    },
    [],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return; // left click only
      setSelectedMask(null);
      setDragStart(toFractional(e.clientX, e.clientY));
      setDragCurrent(null);
    },
    [toFractional],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragStart) return;
      setDragCurrent(toFractional(e.clientX, e.clientY));
    },
    [dragStart, toFractional],
  );

  const handleMouseUp = useCallback(() => {
    if (dragStart && dragCurrent) {
      const x = Math.min(dragStart.x, dragCurrent.x);
      const y = Math.min(dragStart.y, dragCurrent.y);
      const w = Math.abs(dragCurrent.x - dragStart.x);
      const h = Math.abs(dragCurrent.y - dragStart.y);
      if (w > MIN_DRAG && h > MIN_DRAG) {
        onAddMask({
          x: Math.round(x * 10000) / 10000,
          y: Math.round(y * 10000) / 10000,
          w: Math.round(w * 10000) / 10000,
          h: Math.round(h * 10000) / 10000,
        });
      }
    }
    setDragStart(null);
    setDragCurrent(null);
  }, [dragStart, dragCurrent, onAddMask]);

  // Compute drag preview rect (in percent)
  const dragRect =
    dragStart && dragCurrent
      ? {
          x: Math.min(dragStart.x, dragCurrent.x) * 100,
          y: Math.min(dragStart.y, dragCurrent.y) * 100,
          w: Math.abs(dragCurrent.x - dragStart.x) * 100,
          h: Math.abs(dragCurrent.y - dragStart.y) * 100,
        }
      : null;

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={containerRef}
        className="relative select-none"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <img
          src={imageSrc}
          alt="Page preview"
          className="block h-auto w-full rounded border border-border/70"
          draggable={false}
        />
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
        >
          {/* Suggestions — blue dashed outlines */}
          {suggestions.map((s, i) => (
            <rect
              key={`sug-${i}`}
              x={s.x * 100}
              y={s.y * 100}
              width={s.w * 100}
              height={s.h * 100}
              fill="rgba(59, 130, 246, 0.08)"
              stroke="rgba(59, 130, 246, 0.6)"
              strokeWidth="0.3"
              strokeDasharray="1 0.5"
              className="pointer-events-auto cursor-pointer"
              onClick={(e) => {
                e.stopPropagation();
                onAcceptSuggestion?.(s);
              }}
            />
          ))}

          {/* Active masks — red semi-transparent fills */}
          {masks.map((m, i) => (
            <rect
              key={`mask-${i}`}
              x={m.x * 100}
              y={m.y * 100}
              width={m.w * 100}
              height={m.h * 100}
              fill="rgba(239, 68, 68, 0.25)"
              stroke={selectedMask === i ? 'rgba(239, 68, 68, 1)' : 'rgba(239, 68, 68, 0.7)'}
              strokeWidth={selectedMask === i ? '0.5' : '0.3'}
              className="pointer-events-auto cursor-pointer"
              onClick={(e) => {
                e.stopPropagation();
                setSelectedMask(i === selectedMask ? null : i);
              }}
            />
          ))}

          {/* Drag preview */}
          {dragRect && (
            <rect
              x={dragRect.x}
              y={dragRect.y}
              width={dragRect.w}
              height={dragRect.h}
              fill="rgba(239, 68, 68, 0.15)"
              stroke="rgba(239, 68, 68, 0.8)"
              strokeWidth="0.3"
              strokeDasharray="1 0.5"
            />
          )}
        </svg>
      </div>

      {/* Mask controls */}
      {masks.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-xs text-text-muted">{masks.length} mask{masks.length !== 1 ? 's' : ''}</span>
          {selectedMask != null && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs text-red-400 hover:text-red-300"
              onClick={() => {
                onRemoveMask(selectedMask);
                setSelectedMask(null);
              }}
            >
              <Trash2 className="mr-1 size-3" /> Remove
            </Button>
          )}
        </div>
      )}
      <p className="text-[10px] text-text-muted">
        Drag to draw masks. Click blue suggestions to accept. Click red masks to select, then remove.
      </p>
    </div>
  );
}
