"""VLM ingest session — in-memory state for interactive + headless workflows.

A *session* tracks a single PDF being processed through the VLM-first ingest
pipeline.  The operator creates a session (upload or reference an existing run),
configures global + per-page rendering settings, processes pages one-at-a-time,
reviews results, and finally stitches + commits the output.

The session registry is an in-memory dict keyed by session ID.  This is
intentionally simple — sessions are short-lived (minutes to an hour) and
losing them on server restart is acceptable.  Scaling to multi-process would
require moving to Redis or Postgres; that is a future concern.

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

    def set_page_result(self, page_num: int, result: PageResult) -> None:
        """Store VLM output for a page."""
        self.page_results[page_num] = result
        self.page_statuses[page_num] = PageStatus.DONE

    def set_page_error(self, page_num: int, error: str) -> None:
        """Record a page-level error."""
        self.page_errors[page_num] = error
        self.page_statuses[page_num] = PageStatus.ERROR

    def skip_page(self, page_num: int) -> None:
        """Mark page as skipped (operator decision)."""
        self.page_statuses[page_num] = PageStatus.SKIPPED

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
    """Thread-safe in-memory registry of active VLM ingest sessions."""

    def __init__(self, max_sessions: int = 50, ttl_seconds: float = 3600.0):
        self._sessions: dict[str, VlmIngestSession] = {}
        self._lock = Lock()
        self._max = max_sessions
        self._ttl = ttl_seconds

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
        self._evict_expired()
        with self._lock:
            if len(self._sessions) >= self._max:
                raise RuntimeError(
                    f"Session limit reached ({self._max}). "
                    "Complete or discard existing sessions."
                )
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
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.summary() for s in self._sessions.values()]

    def _evict_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if now - s.created_at > self._ttl
            ]
            for sid in expired:
                log.info("Evicting expired VLM ingest session: %s", sid)
                del self._sessions[sid]
