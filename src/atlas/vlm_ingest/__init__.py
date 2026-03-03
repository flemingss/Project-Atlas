"""VLM-first document ingest — interactive & headless.

Provides a session-based workflow where operators configure per-page
rendering settings, run VLM page-by-page, and deterministically stitch
the results into a single markdown document.

Modules:
    session   – VlmIngestSession model + in-memory registry
    stitcher  – Deterministic per-page → full-document stitching
"""
