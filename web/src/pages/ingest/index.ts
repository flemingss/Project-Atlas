/**
 * Unified Ingest page — wizard-style entry point for all ingest methods.
 *
 * Methods:
 *   - Docling / Layout  — deterministic PDF parsing via backend pipeline
 *   - VLM               — interactive VLM page-by-page processing
 *   - Import             — pre-processed markdown/JSON → chunk & embed
 *   - Write / Paste      — free-text → chunk & embed
 *
 * The wizard adapts its steps based on the chosen method:
 *   1. Method + Upload  — choose method, upload file(s), set doc name
 *   2. Configure        — method-specific settings (VLM: DPI/crop/prompt, Docling: backend)
 *   3. Pages (VLM only) — page grid with enable/disable, per-page overrides
 *   4. Process          — run the pipeline / VLM
 *   5. Review (VLM)     — per-page corrections + stitch
 *   6. Commit           — summary + save
 */
export { IngestPage } from './ingest-page';
