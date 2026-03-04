/**
 * Persistent VLM ingest defaults store (Zustand + localStorage).
 *
 * Stores user's preferred VLM configuration so new sessions can be
 * pre-populated without re-entering DPI, crop, system prompt, etc.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface IngestDefaults {
  // VLM defaults
  dpi: number;
  cropTop: number;
  cropBottom: number;
  cropLeft: number;
  cropRight: number;
  systemPrompt: string;

  // Docling/Layout defaults
  parserBackend: 'auto' | 'auto_layout' | 'layout' | 'docling';

  // Actions
  setVlmDefaults: (d: Partial<Omit<IngestDefaults, 'parserBackend' | 'setVlmDefaults' | 'setParserBackend' | 'resetAll'>>) => void;
  setParserBackend: (b: IngestDefaults['parserBackend']) => void;
  resetAll: () => void;
}

const DEFAULTS = {
  dpi: 200,
  cropTop: 0.04,
  cropBottom: 0.04,
  cropLeft: 0,
  cropRight: 0,
  systemPrompt: '',
  parserBackend: 'auto' as const,
};

export const useIngestDefaultsStore = create<IngestDefaults>()(
  persist(
    (set) => ({
      ...DEFAULTS,

      setVlmDefaults: (d) => set((s) => ({ ...s, ...d })),
      setParserBackend: (parserBackend) => set({ parserBackend }),
      resetAll: () => set({ ...DEFAULTS }),
    }),
    {
      name: 'atlas-ingest-defaults',
    },
  ),
);
