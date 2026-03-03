/**
 * Editor state store (Zustand).
 *
 * Holds the "open document" state that multiple components need:
 * run ID, page count, current page, VLM settings, etc.
 */
import { create } from 'zustand';

export interface VlmSettings {
  dpi: number;
  cropTop: number;
  cropBottom: number;
  showCropOverlay: boolean;
}

export interface EditorState {
  // Document
  runId: number | null;
  docId: string | null;
  filename: string;
  docVersion: string;
  totalPages: number;
  currentPage: number;

  // Zoom
  zoomScale: number;

  // VLM
  vlm: VlmSettings;

  // Status
  status: 'idle' | 'busy' | 'error';
  statusText: string;
  lastModel: string;

  // Actions
  setDocument: (opts: {
    runId: number;
    docId: string;
    filename: string;
    docVersion: string;
    totalPages: number;
  }) => void;
  setCurrentPage: (page: number) => void;
  setZoomScale: (scale: number) => void;
  setVlm: (partial: Partial<VlmSettings>) => void;
  setStatus: (status: EditorState['status'], text: string) => void;
  setLastModel: (model: string) => void;
  reset: () => void;
}

const DEFAULT_VLM: VlmSettings = {
  dpi: 200,
  cropTop: 0.04,
  cropBottom: 0.04,
  showCropOverlay: true,
};

const INITIAL: Pick<
  EditorState,
  | 'runId' | 'docId' | 'filename' | 'docVersion'
  | 'totalPages' | 'currentPage' | 'zoomScale' | 'vlm'
  | 'status' | 'statusText' | 'lastModel'
> = {
  runId: null,
  docId: null,
  filename: '',
  docVersion: '',
  totalPages: 0,
  currentPage: 0,
  zoomScale: 1.0,
  vlm: { ...DEFAULT_VLM },
  status: 'idle',
  statusText: 'Ready',
  lastModel: '',
};

export const useEditorStore = create<EditorState>((set) => ({
  ...INITIAL,

  setDocument: ({ runId, docId, filename, docVersion, totalPages }) =>
    set({
      runId,
      docId,
      filename,
      docVersion,
      totalPages,
      currentPage: 0,
      status: 'idle',
      statusText: `Loaded ${filename} (${totalPages} pages, run #${runId})`,
    }),

  setCurrentPage: (page) => set({ currentPage: page }),

  setZoomScale: (scale) =>
    set({ zoomScale: Math.max(0.25, Math.min(4, scale)) }),

  setVlm: (partial) =>
    set((s) => ({ vlm: { ...s.vlm, ...partial } })),

  setStatus: (status, statusText) => set({ status, statusText }),

  setLastModel: (lastModel) => set({ lastModel }),

  reset: () => set({ ...INITIAL, vlm: { ...DEFAULT_VLM } }),
}));
