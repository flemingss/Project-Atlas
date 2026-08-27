/**
 * VLM Ingest wizard state store (Zustand).
 *
 * Manages the multi-step wizard workflow:
 * 1. Upload / select PDF
 * 2. Configure global defaults
 * 3. Review page grid + per-page overrides
 * 4. Process pages one-at-a-time
 * 5. Review per-page results
 * 6. Stitch + final review
 * 7. Commit
 */
import { create } from 'zustand';

import type {
  SessionSummary,
  ThumbnailEntry,
  StitchResponse,
} from '@/services/vlm-ingest-api';

// ── Types ─────────────────────────────────────────────────────────

export type WizardStep =
  | 'start'        // Upload / enter run ID
  | 'configure'    // Set global DPI, crop, system prompt
  | 'pages'        // Page grid with thumbnails, enable/disable, per-page overrides
  | 'processing'   // Sequential VLM processing with progress
  | 'review'       // Per-page result review + corrections
  | 'stitch'       // Stitched document preview
  | 'commit';      // Commit to artifact

export interface PageState {
  pageNum: number;
  enabled: boolean;
  status: string; // pending, processing, done, skipped, error
  markdown: string;
  model: string;
  error?: string;
  // Per-page overrides (null = use global)
  dpiOverride: number | null;
  cropTopOverride: number | null;
  cropBottomOverride: number | null;
  cropLeftOverride: number | null;
  cropRightOverride: number | null;
  // Content analysis (from PyMuPDF)
  contentClass: string | null; // 'text-native' | 'image-heavy' | 'image-only' | null
  imageRatio: number | null;
  imageRects: Array<{ x: number; y: number; w: number; h: number }>;
  // Mask regions (fractional 0-1 coordinates)
  maskRegions: Array<{ x: number; y: number; w: number; h: number }>;
}

export interface VlmIngestState {
  // Session
  sessionId: string | null;
  runId: number | null;
  filename: string;
  pageCount: number;
  sessionStatus: string;
  headless: boolean;

  // Wizard
  step: WizardStep;

  // Global config
  dpi: number;
  cropTop: number;
  cropBottom: number;
  cropLeft: number;
  cropRight: number;
  systemPrompt: string;

  // Pages
  pages: PageState[];
  thumbnails: ThumbnailEntry[];

  // Processing state
  currentProcessingPage: number | null;
  processedCount: number;
  totalEnabled: number;
  isProcessing: boolean;
  autoProcess: boolean;  // auto-advance through pages

  // Stitch result
  stitchResult: StitchResponse | null;
  finalMarkdown: string;

  // Status
  status: 'idle' | 'busy' | 'error';
  statusText: string;
  sessionExpired: boolean;
  sessionExpiredReason: string;

  // Actions
  setSession: (session: SessionSummary) => void;
  /** Rehydrate the wizard from a live backend session (after page refresh). */
  resumeSession: (session: SessionSummary) => void;
  setStep: (step: WizardStep) => void;
  setGlobalConfig: (cfg: Partial<Pick<VlmIngestState, 'dpi' | 'cropTop' | 'cropBottom' | 'cropLeft' | 'cropRight' | 'systemPrompt'>>) => void;
  setThumbnails: (thumbs: ThumbnailEntry[]) => void;
  setPageEnabled: (pageNum: number, enabled: boolean) => void;
  setPageOverride: (pageNum: number, overrides: Partial<Pick<PageState, 'dpiOverride' | 'cropTopOverride' | 'cropBottomOverride' | 'cropLeftOverride' | 'cropRightOverride'>>) => void;
  setPageResult: (pageNum: number, markdown: string, model: string, status: string) => void;
  /** Merge server-side page statuses (from the polled session summary) into
   *  local wizard state. Keeps bulk (server-side) processing progress live. */
  syncPagesFromServer: (session: SessionSummary) => void;
  setPageError: (pageNum: number, error: string) => void;
  setPageMarkdown: (pageNum: number, markdown: string) => void;
  setProcessing: (isProcessing: boolean, pageNum?: number | null) => void;
  setAutoProcess: (auto: boolean) => void;
  setStitchResult: (result: StitchResponse) => void;
  setFinalMarkdown: (md: string) => void;
  setStatus: (status: VlmIngestState['status'], text: string) => void;
  markSessionExpired: (reason: string) => void;
  setPageAnalysis: (pageNum: number, analysis: { content_class: string; image_ratio: number; image_rects: Array<{ x: number; y: number; w: number; h: number }> }) => void;
  addMaskRegion: (pageNum: number, region: { x: number; y: number; w: number; h: number }) => void;
  removeMaskRegion: (pageNum: number, index: number) => void;
  clearMaskRegions: (pageNum: number) => void;
  setMaskRegions: (pageNum: number, regions: Array<{ x: number; y: number; w: number; h: number }>) => void;
  autoSuggestMasks: (pageNum: number) => void;
  reset: () => void;
}

// ── Defaults ──────────────────────────────────────────────────────

const INITIAL_STATE: Omit<VlmIngestState, 'setSession' | 'resumeSession' | 'setStep' | 'setGlobalConfig' | 'setThumbnails' | 'setPageEnabled' | 'setPageOverride' | 'setPageResult' | 'syncPagesFromServer' | 'setPageError' | 'setPageMarkdown' | 'setProcessing' | 'setAutoProcess' | 'setStitchResult' | 'setFinalMarkdown' | 'setStatus' | 'markSessionExpired' | 'setPageAnalysis' | 'addMaskRegion' | 'removeMaskRegion' | 'clearMaskRegions' | 'setMaskRegions' | 'autoSuggestMasks' | 'reset'> = {
  sessionId: null,
  runId: null,
  filename: '',
  pageCount: 0,
  sessionStatus: '',
  headless: false,
  step: 'start',
  dpi: 200,
  cropTop: 0.04,
  cropBottom: 0.04,
  cropLeft: 0,
  cropRight: 0,
  systemPrompt: '',
  pages: [],
  thumbnails: [],
  currentProcessingPage: null,
  processedCount: 0,
  totalEnabled: 0,
  isProcessing: false,
  autoProcess: true,
  stitchResult: null,
  finalMarkdown: '',
  status: 'idle',
  statusText: 'Ready',
  sessionExpired: false,
  sessionExpiredReason: '',
};

// ── Store ─────────────────────────────────────────────────────────

/** localStorage key holding the live backend session id, so a page refresh
 *  can re-attach to a session the server is still processing. */
export const VLM_SESSION_STORAGE_KEY = 'atlas_vlm_session';

function buildPages(session: SessionSummary): PageState[] {
  return Array.from({ length: session.page_count }, (_, i) => {
    const backendPage = session.pages?.[i];
    return {
      pageNum: i,
      enabled: backendPage?.enabled ?? true,
      status: backendPage?.status ?? 'pending',
      markdown: backendPage?.markdown ?? '',
      model: backendPage?.model ?? '',
      error: undefined,
      dpiOverride: null,
      cropTopOverride: null,
      cropBottomOverride: null,
      cropLeftOverride: null,
      cropRightOverride: null,
      contentClass: null,
      imageRatio: null,
      imageRects: [],
      maskRegions: [],
    };
  });
}

function rememberSession(sessionId: string | null) {
  try {
    if (sessionId) localStorage.setItem(VLM_SESSION_STORAGE_KEY, sessionId);
    else localStorage.removeItem(VLM_SESSION_STORAGE_KEY);
  } catch {
    /* storage unavailable — resume just won't work */
  }
}

export const useVlmIngestStore = create<VlmIngestState>((set) => ({
  ...INITIAL_STATE,

  setSession: (session) => {
    const pages = buildPages(session);
    rememberSession(session.session_id);
    set({
      sessionId: session.session_id,
      runId: session.run_id,
      filename: session.source_filename,
      pageCount: session.page_count,
      sessionStatus: session.status,
      headless: session.headless,
      pages,
      totalEnabled: pages.filter((p) => p.enabled).length,
      step: 'configure',
      sessionExpired: false,
      sessionExpiredReason: '',
    });
  },

  resumeSession: (session) => {
    const pages = buildPages(session);
    // Land on the step matching the server's state. Per-page overrides and
    // mask regions live server-side and are not rehydrated here — results
    // (status/markdown) are, which is what review/commit need.
    const step: WizardStep =
      session.status === 'processing'
        ? 'processing'
        : session.status === 'complete'
          ? 'review'
          : 'configure';
    rememberSession(session.session_id);
    const done = pages.filter((p) => p.status === 'done' || p.status === 'skipped').length;
    set({
      sessionId: session.session_id,
      runId: session.run_id,
      filename: session.source_filename,
      pageCount: session.page_count,
      sessionStatus: session.status,
      headless: session.headless,
      pages,
      totalEnabled: pages.filter((p) => p.enabled).length,
      processedCount: done,
      step,
      status: 'idle',
      statusText: `Re-attached to session (${session.status})`,
      sessionExpired: false,
      sessionExpiredReason: '',
    });
  },

  setStep: (step) => set({ step }),

  setGlobalConfig: (cfg) => set((s) => ({ ...s, ...cfg })),

  setThumbnails: (thumbs) => set({ thumbnails: thumbs }),

  setPageEnabled: (pageNum, enabled) =>
    set((s) => {
      const pages = s.pages.map((p) =>
        p.pageNum === pageNum ? { ...p, enabled } : p,
      );
      return {
        pages,
        totalEnabled: pages.filter((p) => p.enabled).length,
      };
    }),

  setPageOverride: (pageNum, overrides) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum ? { ...p, ...overrides } : p,
      ),
    })),

  setPageResult: (pageNum, markdown, model, status) =>
    set((s) => {
      const pages = s.pages.map((p) =>
        p.pageNum === pageNum ? { ...p, markdown, model, status } : p,
      );
      const processedCount = pages.filter(
        (p) => p.status === 'done' || p.status === 'skipped',
      ).length;
      return { pages, processedCount };
    }),

  syncPagesFromServer: (session) =>
    set((s) => {
      const serverPages = session.pages;
      if (!serverPages?.length || s.sessionId !== session.session_id) {
        return {};
      }
      const byNum = new Map(serverPages.map((sp) => [sp.page_num, sp]));
      let changed = false;
      const pages = s.pages.map((p) => {
        const sp = byNum.get(p.pageNum);
        if (!sp || sp.status === p.status) return p;
        changed = true;
        // Never clobber an operator-edited result: keep local markdown when
        // the page was already done locally and differs from the server's.
        const keepLocalMd =
          p.status === 'done' && p.markdown !== '' && p.markdown !== sp.markdown;
        return {
          ...p,
          status: sp.status,
          model: sp.model || p.model,
          markdown: keepLocalMd ? p.markdown : (sp.markdown ?? p.markdown),
        };
      });
      if (!changed && s.sessionStatus === session.status) return {};
      const processedCount = pages.filter(
        (p) => p.status === 'done' || p.status === 'skipped',
      ).length;
      return { pages, processedCount, sessionStatus: session.status };
    }),

  setPageError: (pageNum, error) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum ? { ...p, status: 'error', error } : p,
      ),
    })),

  setPageMarkdown: (pageNum, markdown) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum ? { ...p, markdown } : p,
      ),
    })),

  setProcessing: (isProcessing, pageNum = null) =>
    set({
      isProcessing,
      currentProcessingPage: pageNum ?? null,
    }),

  setAutoProcess: (autoProcess) => set({ autoProcess }),

  setStitchResult: (result) =>
    set({
      stitchResult: result,
      finalMarkdown: result.markdown,
      step: 'stitch',
    }),

  setFinalMarkdown: (finalMarkdown) => set({ finalMarkdown }),

  setStatus: (status, statusText) => set({ status, statusText }),

  markSessionExpired: (reason) => {
    rememberSession(null);
    set({
      status: 'error',
      statusText: 'Session expired',
      sessionExpired: true,
      sessionExpiredReason: reason,
      isProcessing: false,
      currentProcessingPage: null,
    });
  },

  setPageAnalysis: (pageNum, analysis) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum
          ? {
              ...p,
              contentClass: analysis.content_class,
              imageRatio: analysis.image_ratio,
              imageRects: analysis.image_rects ?? [],
            }
          : p,
      ),
    })),

  addMaskRegion: (pageNum, region) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum
          ? { ...p, maskRegions: [...p.maskRegions, region] }
          : p,
      ),
    })),

  removeMaskRegion: (pageNum, index) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum
          ? { ...p, maskRegions: p.maskRegions.filter((_, i) => i !== index) }
          : p,
      ),
    })),

  clearMaskRegions: (pageNum) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum ? { ...p, maskRegions: [] } : p,
      ),
    })),

  setMaskRegions: (pageNum, regions) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum ? { ...p, maskRegions: regions } : p,
      ),
    })),

  autoSuggestMasks: (pageNum) =>
    set((s) => ({
      pages: s.pages.map((p) =>
        p.pageNum === pageNum
          ? { ...p, maskRegions: [...p.imageRects] }
          : p,
      ),
    })),

  reset: () => {
    rememberSession(null);
    set({ ...INITIAL_STATE });
  },
}));
