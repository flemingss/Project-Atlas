"""VLM ingest session — in-memory state for interactive + headless workflows.

A *session* tracks a single PDF being processed through the VLM-first ingest
pipeline.  The operator creates a session (upload or reference an existing run),
configures global + per-page rendering settings, processes pages one-at-a-time,
reviews results, and finally stitches + commits the output.

This module holds the *in-memory shape* of a session and nothing else.  The
system of record is ``atlas.vlm_ingest.store`` (Postgres); the registry below
is a cache in front of it, so dropping an entry costs a rehydrate rather than
the operator's work.  Persistence is delegated through the ``writer`` hook so
that this module stays free of any database dependency.

Headless mode: a session can be created with a saved ``VlmIngestConfig``
(global settings + per-page overrides) and processed non-interactively by
iterating through ``process_page()`` and then ``stitch()``.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any

from atlas.vlm_ingest.stitcher import PageResult, StitchResult, stitch_pages

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------

class PageStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    SKIPPED = "skipped"
    ERROR = "error"


class SessionStatus(str, Enum):
    CONFIGURING = "configuring"  # operator is setting DPI/crop/page selection
    PROCESSING = "processing"    # pages are being sent to VLM
    STITCHING = "stitching"      # all pages done, stitching requested
    COMPLETE = "complete"        # stitched, markdown available
    COMMITTED = "committed"      # saved as artifact / fed into pipeline
    FAILED = "failed"


@dataclass
class PageSettings:
    """Per-page rendering configuration."""

    page_num: int        # 0-indexed
    enabled: bool = True
    dpi: int = 200
    crop_top: float = 0.04
    crop_bottom: float = 0.04
    crop_left: float = 0.0
    crop_right: float = 0.0
    mask_regions: list[dict] = field(default_factory=list)


@dataclass
class VlmIngestConfig:
    """Reusable ingest configuration (global defaults + per-page overrides).

    Can be serialised to/from dict for headless reuse across documents.
    """

    dpi: int = 200
    crop_top: float = 0.04
    crop_bottom: float = 0.04
    crop_left: float = 0.0
    crop_right: float = 0.0
    system_prompt: str | None = None
    page_overrides: dict[int, dict[str, Any]] = field(default_factory=dict)

    def settings_for_page(self, page_num: int) -> PageSettings:
        """Return effective settings for *page_num*, merging overrides."""
        base = PageSettings(
            page_num=page_num,
            dpi=self.dpi,
            crop_top=self.crop_top,
            crop_bottom=self.crop_bottom,
            crop_left=self.crop_left,
            crop_right=self.crop_right,
        )
        overrides = self.page_overrides.get(page_num, {})
        for k, v in overrides.items():
            if hasattr(base, k):
                object.__setattr__(base, k, v)
        return base

    def to_dict(self) -> dict[str, Any]:
        return {
            "dpi": self.dpi,
            "crop_top": self.crop_top,
            "crop_bottom": self.crop_bottom,
            "crop_left": self.crop_left,
            "crop_right": self.crop_right,
            "system_prompt": self.system_prompt,
            "page_overrides": self.page_overrides,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VlmIngestConfig:
        return cls(
            dpi=d.get("dpi", 200),
            crop_top=d.get("crop_top", 0.04),
            crop_bottom=d.get("crop_bottom", 0.04),
            crop_left=d.get("crop_left", 0.0),
            crop_right=d.get("crop_right", 0.0),
            system_prompt=d.get("system_prompt"),
            page_overrides=d.get("page_overrides", {}),
        )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class VlmIngestSession:
    """State for one VLM ingest workflow."""

    session_id: str
    pdf_bytes: bytes
    page_count: int
    source_filename: str = ""
    run_id: int | None = None  # if created from an existing run
    # Content address of the source PDF. Prefix of every page cache key, so an
    # empty value simply disables caching rather than risking a wrong hit.
    source_sha256: str = ""

    # Config
    config: VlmIngestConfig = field(default_factory=VlmIngestConfig)

    # Per-page state
    page_statuses: dict[int, PageStatus] = field(default_factory=dict)
    page_results: dict[int, PageResult] = field(default_factory=dict)
    page_errors: dict[int, str] = field(default_factory=dict)
    page_analysis: dict[int, dict] = field(default_factory=dict)

    # Session-level state
    status: SessionStatus = SessionStatus.CONFIGURING
    stitched: StitchResult | None = None
    created_at: float = field(default_factory=time.time)
    # Recency for LRU ordering in the session cache. Deliberately *not* a
    # liveness signal: whether work survives is decided by the ledger, never
    # by how recently someone looked at this object.
    last_activity: float = field(default_factory=time.time)

    # Directory holding the source PDF and a human-readable ``page_XXXX.md``
    # per completed page. Redundant with the ledger by design — it costs
    # nothing and gives the operator something to salvage by hand.
    checkpoint_dir: str | None = None

    # Write-through durability adapter (atlas.vlm_ingest.store.LedgerSessionWriter).
    # Untyped to keep this module free of any database dependency — session
    # state is a plain data structure, persistence is somebody else's job.
    writer: Any = None

    # Headless flag
    headless: bool = False

    def __post_init__(self) -> None:
        # Initialise all pages as PENDING and enabled
        for p in range(self.page_count):
            self.page_statuses.setdefault(p, PageStatus.PENDING)

    # -- queries --

    def enabled_pages(self) -> list[int]:
        """Return sorted list of enabled page numbers."""
        result = []
        for p in range(self.page_count):
            settings = self.config.settings_for_page(p)
            if settings.enabled:
                result.append(p)
        return result

    def next_pending_page(self) -> int | None:
        """Return the next enabled page that has not been processed."""
        for p in self.enabled_pages():
            if self.page_statuses.get(p) == PageStatus.PENDING:
                return p
        return None

    def all_done(self) -> bool:
        """True if all enabled pages are DONE or SKIPPED."""
        for p in self.enabled_pages():
            st = self.page_statuses.get(p, PageStatus.PENDING)
            if st not in (PageStatus.DONE, PageStatus.SKIPPED):
                return False
        return True

    def progress(self) -> dict[str, int]:
        """Return progress statistics."""
        enabled = self.enabled_pages()
        done = sum(1 for p in enabled if self.page_statuses.get(p) == PageStatus.DONE)
        skipped = sum(1 for p in enabled if self.page_statuses.get(p) == PageStatus.SKIPPED)
        errors = sum(1 for p in enabled if self.page_statuses.get(p) == PageStatus.ERROR)
        pending = sum(1 for p in enabled if self.page_statuses.get(p) == PageStatus.PENDING)
        return {
            "total": self.page_count,
            "enabled": len(enabled),
            "done": done,
            "skipped": skipped,
            "errors": errors,
            "pending": pending,
        }

    # -- mutations --

    def touch(self) -> None:
        """Record recency for LRU ordering in the session cache."""
        self.last_activity = time.time()

    def set_status(self, status: SessionStatus) -> None:
        """Transition the session and persist the new status immediately.

        Status is durable state: it decides whether a resumed session lands on
        the review step or the configure step, and it gates the double-start
        guard. Assigning ``.status`` directly skips persistence — go through
        this instead.
        """
        self.status = status
        self.touch()
        self._notify("status_changed")

    def _checkpoint_page(self, page_num: int, markdown: str) -> None:
        """Best-effort write-through of a completed page's markdown."""
        if not self.checkpoint_dir:
            return
        try:
            from pathlib import Path

            d = Path(self.checkpoint_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"page_{page_num:04d}.md").write_text(markdown, encoding="utf-8")
        except OSError:
            log.warning(
                "Checkpoint write failed: sid=%s page=%d", self.session_id, page_num,
                exc_info=True,
            )

    def _notify(self, hook: str, *args: Any) -> None:
        """Fire a write-through hook. Never lets persistence break the job."""
        if self.writer is None:
            return
        fn = getattr(self.writer, hook, None)
        if fn is None:
            return
        try:
            fn(self, *args)
        except Exception:  # durability is best-effort by design
            log.warning("Session writer hook %s failed: sid=%s", hook, self.session_id, exc_info=True)

    def set_page_result(self, page_num: int, result: PageResult) -> None:
        """Store VLM output for a page."""
        self.page_results[page_num] = result
        self.page_statuses[page_num] = PageStatus.DONE
        self.touch()
        self._checkpoint_page(page_num, result.markdown)
        self._notify("page_completed", page_num, result)

    def set_page_error(self, page_num: int, error: str) -> None:
        """Record a page-level error."""
        self.page_errors[page_num] = error
        self.page_statuses[page_num] = PageStatus.ERROR
        self.touch()
        self._notify("page_failed", page_num, error)

    def skip_page(self, page_num: int) -> None:
        """Mark page as skipped (operator decision)."""
        self.page_statuses[page_num] = PageStatus.SKIPPED
        self.touch()
        self._notify("page_skipped", page_num)

    def update_page_settings(self, page_num: int, **overrides: Any) -> None:
        """Apply per-page overrides."""
        self.config.page_overrides.setdefault(page_num, {}).update(overrides)

    def stitch(self, **kwargs: Any) -> StitchResult:
        """Stitch all completed pages into a single markdown document."""
        pages = [
            self.page_results[p]
            for p in sorted(self.page_results)
            if self.page_statuses.get(p) == PageStatus.DONE
        ]
        result = stitch_pages(pages, **kwargs)
        self.stitched = result
        self.status = SessionStatus.COMPLETE
        self.touch()
        self._notify("status_changed")
        return result

    def summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the session."""
        pages = []
        for p in range(self.page_count):
            settings = self.config.settings_for_page(p)
            result = self.page_results.get(p)
            pages.append({
                "page_num": p,
                "status": self.page_statuses.get(p, PageStatus.PENDING).value,
                "enabled": settings.enabled,
                "markdown": result.markdown if result else "",
                "model": result.model if result else "",
            })
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "source_filename": self.source_filename,
            "run_id": self.run_id,
            "page_count": self.page_count,
            "headless": self.headless,
            "progress": self.progress(),
            "config": self.config.to_dict(),
            "created_at": self.created_at,
            "pages": pages,
        }


# ---------------------------------------------------------------------------
# Session registry (in-memory)
# ---------------------------------------------------------------------------

class SessionRegistry:
    """Thread-safe in-memory *cache* of hydrated VLM ingest sessions.

    This is deliberately not the system of record — ``atlas.vlm_ingest.store``
    is.  Everything held here is reconstructible, so evicting an entry costs at
    most the work of re-reading Postgres and the source PDF.  That is the whole
    point: a memory-management policy must never be able to destroy hours of
    paid VLM output.

    A ``loader`` bridges the two.  On a cache miss ``get()`` asks it to
    rehydrate from durable state; only if that also comes back empty is the
    session genuinely gone.
    """

    def __init__(
        self,
        max_sessions: int = 50,
        ttl_seconds: float = 3600.0,
        loader: Any = None,
    ):
        self._sessions: dict[str, VlmIngestSession] = {}
        self._lock = Lock()
        self._max = max_sessions
        self._ttl = ttl_seconds
        # Callable[[str], VlmIngestSession | None] — rehydrates from the ledger.
        self._loader = loader

    def set_loader(self, loader: Any) -> None:
        """Install the rehydration callback (wired after router construction)."""
        self._loader = loader

    def put(self, session: VlmIngestSession) -> None:
        """Insert an already-built session (used by the rehydration path)."""
        with self._lock:
            self._sessions[session.session_id] = session

    def create(
        self,
        *,
        pdf_bytes: bytes,
        page_count: int,
        source_filename: str = "",
        run_id: int | None = None,
        config: VlmIngestConfig | None = None,
        headless: bool = False,
    ) -> VlmIngestSession:
        """Create a new session and return it."""
        self.release_cold_sessions()
        with self._lock:
            # Over capacity, drop the least-recently-used entry rather than
            # refusing the operator. Safe now that eviction is cache-only:
            # the displaced session rehydrates on its next access.
            #
            # Sessions mid-run are never candidates. Their bulk loop holds a
            # direct reference, so evicting one would let a later request
            # rehydrate a *second* object for the same id and the two would
            # write over each other's progress.
            while len(self._sessions) >= self._max:
                candidates = [
                    s for s in self._sessions.values()
                    if s.status is not SessionStatus.PROCESSING
                ]
                if not candidates:
                    raise RuntimeError(
                        f"All {self._max} cached sessions are actively processing. "
                        "Wait for one to finish, or discard it."
                    )
                lru = min(candidates, key=lambda s: s.last_activity)
                log.info(
                    "Session cache full (%d) — releasing LRU sid=%s (durable state retained)",
                    self._max, lru.session_id,
                )
                del self._sessions[lru.session_id]
            sid = uuid.uuid4().hex[:12]
            session = VlmIngestSession(
                session_id=sid,
                pdf_bytes=pdf_bytes,
                page_count=page_count,
                source_filename=source_filename,
                run_id=run_id,
                config=config or VlmIngestConfig(),
                headless=headless,
            )
            self._sessions[sid] = session
            log.info(
                "VLM ingest session created: sid=%s pages=%d headless=%s",
                sid, page_count, headless,
            )
            return session

    def get(self, session_id: str) -> VlmIngestSession | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None:
                # Recency for LRU ordering only. Liveness is no longer a
                # function of client traffic — an idle session is merely cold,
                # not dead, so nothing here decides whether work survives.
                s.touch()
                return s

        # Cache miss — rehydrate from the ledger outside the lock, since the
        # loader does database and filesystem I/O.
        if self._loader is None:
            return None
        try:
            restored = self._loader(session_id)
        except Exception:  # a failed rehydrate is a miss, not a 500
            log.warning("Rehydrate failed for sid=%s", session_id, exc_info=True)
            return None
        if restored is None:
            return None

        with self._lock:
            # Another request may have rehydrated concurrently; first one wins
            # so the two callers cannot diverge into separate session objects.
            existing = self._sessions.get(session_id)
            if existing is not None:
                existing.touch()
                return existing
            self._sessions[session_id] = restored
        log.info("Rehydrated VLM session from ledger: sid=%s", session_id)
        return restored

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.summary() for s in self._sessions.values()]

    def release_cold_sessions(self) -> None:
        """Release cold sessions from RAM. Purely a memory reclaim.

        Nothing is destroyed: the ledger keeps page results, config and status,
        and the source PDF stays on disk, so an evicted session is rebuilt in
        full on its next request. Sessions actively processing are held back
        anyway — their in-flight loop still needs the PDF bytes in hand.
        """
        now = time.time()
        with self._lock:
            cold = [
                sid for sid, s in self._sessions.items()
                if now - s.last_activity > self._ttl
                and s.status is not SessionStatus.PROCESSING
            ]
            for sid in cold:
                log.info(
                    "Releasing cold VLM session from cache: %s "
                    "(durable state retained; rehydrates on next access)", sid,
                )
                del self._sessions[sid]
