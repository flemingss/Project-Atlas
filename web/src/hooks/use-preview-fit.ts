/**
 * usePreviewFit — Shared hook for preview surface fit-mode + zoom.
 *
 * Uses a ResizeObserver to track container dimensions and recalculates
 * derived zoom when the container or fit mode changes.
 *
 * Fit modes:
 *   - fit-page:   Scale content to fit both dimensions inside the container
 *   - fit-width:  Scale content to match container width (may overflow vertically)
 *   - actual-size: 1:1 pixel mapping (zoom = 1.0)
 *   - manual:     User-controlled zoom via +/- buttons or slider
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export type FitMode = 'fit-page' | 'fit-width' | 'actual-size' | 'manual';

interface UsePreviewFitOptions {
  /** Initial fit mode */
  initialFitMode?: FitMode;
  /** Initial manual zoom level */
  initialZoom?: number;
  /** Natural (content) width in pixels — used when computing fit-page / fit-width */
  contentWidth?: number;
  /** Natural (content) height in pixels */
  contentHeight?: number;
}

interface PreviewFitResult {
  /** Attach to the scrollable container element */
  containerRef: React.RefCallback<HTMLElement>;
  /** Container dimensions (updated by ResizeObserver) */
  containerSize: { width: number; height: number };
  /** Current fit mode */
  fitMode: FitMode;
  /** Derived zoom value (changes with fit mode + container size) */
  zoom: number;
  /** Change the fit mode */
  setFitMode: (mode: FitMode) => void;
  /** Set manual zoom (also switches to 'manual' mode) */
  setZoom: (z: number) => void;
  /**
   * Compute the zoom for a given content size.
   * Useful when content dimensions are known only at render time.
   */
  computeZoom: (contentW: number, contentH: number) => number;
}

export function usePreviewFit({
  initialFitMode = 'fit-page',
  initialZoom = 1.0,
  contentWidth = 0,
  contentHeight = 0,
}: UsePreviewFitOptions = {}): PreviewFitResult {
  const [fitMode, setFitModeState] = useState<FitMode>(initialFitMode);
  const [manualZoom, setManualZoom] = useState(initialZoom);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });

  // Ref for the ResizeObserver
  const observerRef = useRef<ResizeObserver | null>(null);
  const elementRef = useRef<HTMLElement | null>(null);

  // Ref callback for the container
  const containerRef = useCallback((node: HTMLElement | null) => {
    // Disconnect old observer
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }

    elementRef.current = node;
    if (!node) return;

    // Read initial size
    const { width, height } = node.getBoundingClientRect();
    setContainerSize({ width, height });

    // Observe resize
    observerRef.current = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width: w, height: h } = entry.contentRect;
        setContainerSize({ width: w, height: h });
      }
    });
    observerRef.current.observe(node);
  }, []);

  // Clean up observer on unmount
  useEffect(() => {
    return () => {
      observerRef.current?.disconnect();
    };
  }, []);

  // Compute zoom given content dimensions
  const computeZoom = useCallback(
    (cw: number, ch: number): number => {
      if (containerSize.width === 0 || containerSize.height === 0) return 1;
      if (cw === 0 || ch === 0) return 1;

      const padding = 32; // 16px on each side
      const availW = containerSize.width - padding;
      const availH = containerSize.height - padding;

      switch (fitMode) {
        case 'fit-page':
          return Math.min(availW / cw, availH / ch, 1);
        case 'fit-width':
          return Math.min(availW / cw, 1);
        case 'actual-size':
          return 1;
        case 'manual':
          return manualZoom;
      }
    },
    [containerSize, fitMode, manualZoom],
  );

  // Derived zoom when content dimensions are provided up-front
  const zoom =
    contentWidth > 0 && contentHeight > 0
      ? computeZoom(contentWidth, contentHeight)
      : fitMode === 'manual'
        ? manualZoom
        : fitMode === 'actual-size'
          ? 1
          : 1;

  const setFitMode = useCallback((mode: FitMode) => {
    setFitModeState(mode);
  }, []);

  const setZoom = useCallback(
    (z: number) => {
      setManualZoom(z);
      if (fitMode !== 'manual') setFitModeState('manual');
    },
    [fitMode],
  );

  return {
    containerRef,
    containerSize,
    fitMode,
    zoom,
    setFitMode,
    setZoom,
    computeZoom,
  };
}
