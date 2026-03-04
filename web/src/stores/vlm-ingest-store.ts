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
}

export interface VlmIngestState {
  // Session
  sessionId: string | null;
  runId: number | null;
  filename: string;
  pageCount: number;
  sessionStatus: string;

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
  setStep: (step: WizardStep) => void;
  setGlobalConfig: (cfg: Partial<Pick<VlmIngestState, 'dpi' | 'cropTop' | 'cropBottom' | 'cropLeft' | 'cropRight' | 'systemPrompt'>>) => void;
  setThumbnails: (thumbs: ThumbnailEntry[]) => void;
  setPageEnabled: (pageNum: number, enabled: boolean) => void;
  setPageOverride: (pageNum: number, overrides: Partial<Pick<PageState, 'dpiOverride' | 'cropTopOverride' | 'cropBottomOverride' | 'cropLeftOverride' | 'cropRightOverride'>>) => void;
  setPageResult: (pageNum: number, markdown: string, model: string, status: string) => void;
  setPageError: (pageNum: number, error: string) => void;
  setPageMarkdown: (pageNum: number, markdown: string) => void;
  setProcessing: (isProcessing: boolean, pageNum?: number | null) => void;
  setAutoProcess: (auto: boolean) => void;
  setStitchResult: (result: StitchResponse) => void;
  setFinalMarkdown: (md: string) => void;
  setStatus: (status: VlmIngestState['status'], text: string) => void;
  markSessionExpired: (reason: string) => void;
  reset: () => void;
}

// ── Defaults ──────────────────────────────────────────────────────

const INITIAL_STATE: Omit<VlmIngestState, 'setSession' | 'setStep' | 'setGlobalConfig' | 'setThumbnails' | 'setPageEnabled' | 'setPageOverride' | 'setPageResult' | 'setPageError' | 'setPageMarkdown' | 'setProcessing' | 'setAutoProcess' | 'setStitchResult' | 'setFinalMarkdown' | 'setStatus' | 'markSessionExpired' | 'reset'> = {
  sessionId: null,
  runId: null,
  filename: '',
  pageCount: 0,
  sessionStatus: '',
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

export const useVlmIngestStore = create<VlmIngestState>((set) => ({
  ...INITIAL_STATE,

  setSession: (session) =>
    set({
      sessionId: session.session_id,
      runId: session.run_id,
      filename: session.source_filename,
      pageCount: session.page_count,
      sessionStatus: session.status,
      pages: Array.from({ length: session.page_count }, (_, i) => ({
        pageNum: i,
        enabled: true,
        status: 'pending',
        markdown: '',
        model: '',
        dpiOverride: null,
        cropTopOverride: null,
        cropBottomOverride: null,
        cropLeftOverride: null,
        cropRightOverride: null,
      })),
      totalEnabled: session.page_count,
      step: 'configure',
      sessionExpired: false,
      sessionExpiredReason: '',
    }),

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

  markSessionExpired: (reason) =>
    set({
      status: 'error',
      statusText: 'Session expired',
      sessionExpired: true,
      sessionExpiredReason: reason,
      isProcessing: false,
      currentProcessingPage: null,
    }),

  reset: () => set({ ...INITIAL_STATE }),
}));
