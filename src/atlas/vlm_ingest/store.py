"""Durable store for VLM ingest sessions.

This module is the *system of record* for VLM ingest.  The in-memory
``SessionRegistry`` is a hydration cache in front of it: evicting a session
from RAM is free, because every durable fact — page results, config, status,
and the source PDF's location — lives here.

Two distinct concerns live side by side:

``VlmSession`` / ``VlmPageResult``
    Session-scoped state.  Answers "what happened in *this* job?"

``VlmPageCache``
    Content-addressed memo of page extractions, keyed on everything that
    determines the output.  Answers "have we *ever* extracted this exact page
    under these exact settings?"  This is what makes re-running a document
    nearly free and caps the loss from any failure at the in-flight page.

``session.py`` stays deliberately free of SQLAlchemy; the write-through
adapter that bridges the two is :class:`LedgerSessionWriter` at the bottom.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from atlas.models import VlmPageCache, VlmPageResult, VlmSession
from atlas.vlm_ingest.session import (
    PageStatus,
    SessionStatus,
    VlmIngestConfig,
    VlmIngestSession,
)
from atlas.vlm_ingest.stitcher import PageResult, StitchResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Content addressing
# ---------------------------------------------------------------------------

# Bump when a change to rendering or prompting invalidates previously cached
# extractions. Old entries stay in the table but stop being consulted.
CACHE_VERSION = "v1"


def compute_cache_key(
    *,
    source_sha256: str,
    page_num: int,
    dpi: int,
    crop: tuple[float, float, float, float],
    mask_regions: list[dict] | None,
    system_prompt: str | None,
    model: str,
) -> str:
    """Return the content address of one page extraction.

    Every input that can change the VLM's output participates in the key, so a
    hit is always safe to serve. Anything omitted here would be a correctness
    bug, not merely a cache-efficiency one.
    """
    payload = {
        "v": CACHE_VERSION,
        "src": source_sha256,
        "page": page_num,
        "dpi": dpi,
        # Rounded: float noise from the UI's sliders must not fragment the cache.
        "crop": [round(float(c), 6) for c in crop],
        "masks": sorted(
            (
                round(float(m.get("x", 0)), 6),
                round(float(m.get("y", 0)), 6),
                round(float(m.get("w", 0)), 6),
                round(float(m.get("h", 0)), 6),
            )
            for m in (mask_regions or [])
        ),
        "prompt": system_prompt or "",
        "model": model,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_lookup(session: Session, cache_key: str) -> VlmPageCache | None:
    """Return a memoised extraction, or ``None`` on a miss."""
    return session.get(VlmPageCache, cache_key)


def cache_store(
    session: Session,
    *,
    cache_key: str,
    source_sha256: str,
    page_num: int,
    markdown: str,
    model: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """Memoise an extraction. Idempotent — a concurrent writer wins harmlessly."""
    existing = session.get(VlmPageCache, cache_key)
    if existing is not None:
        return
    session.add(
        VlmPageCache(
            cache_key=cache_key,
            source_sha256=source_sha256,
            page_num=page_num,
            markdown=markdown,
            model=model,
            meta=meta or {},
        )
    )


def cache_stats(session: Session) -> dict[str, int]:
    """Return coarse cache counters for the health/admin surface."""
    entries = session.scalar(select(func.count()).select_from(VlmPageCache)) or 0
    docs = session.scalar(select(func.count(func.distinct(VlmPageCache.source_sha256)))) or 0
    return {"entries": int(entries), "documents": int(docs)}


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

def save_session(
    session_factory: sessionmaker,
    s: VlmIngestSession,
    *,
    source_sha256: str = "",
    source_path: str = "",
) -> None:
    """Upsert the session-level row (config, status, source pointers)."""
    with session_factory() as db:
        row = db.get(VlmSession, s.session_id)
        if row is None:
            row = VlmSession(session_id=s.session_id)
            db.add(row)
        row.run_id = s.run_id
        row.source_filename = s.source_filename
        row.page_count = s.page_count
        # Never persist an in-flight status. PROCESSING/STITCHING describe an
        # activity belonging to a process that will not exist after a restart;
        # writing one down produced sessions that came back claiming to be busy
        # and could never be restarted.
        row.status = s.resting_status().value
        row.headless = s.headless
        row.config = s.config.to_dict()
        if source_sha256:
            row.source_sha256 = source_sha256
        if source_path:
            row.source_path = source_path
        if s.stitched is not None:
            row.stitched_markdown = s.stitched.markdown
        db.commit()


def save_page(
    session_factory: sessionmaker,
    session_id: str,
    page_num: int,
    *,
    status: str,
    markdown: str = "",
    model: str = "",
    error: str = "",
    cache_key: str = "",
) -> None:
    """Upsert one page's outcome. Called the moment a page settles."""
    with session_factory() as db:
        row = db.scalar(
            select(VlmPageResult).where(
                VlmPageResult.session_id == session_id,
                VlmPageResult.page_num == page_num,
            )
        )
        if row is None:
            row = VlmPageResult(session_id=session_id, page_num=page_num)
            db.add(row)
        row.status = status
        row.markdown = markdown
        row.model = model
        row.error = error
        row.cache_key = cache_key
        db.commit()


def load_session(session_factory: sessionmaker, session_id: str) -> dict[str, Any] | None:
    """Return the durable state needed to rehydrate a session, or ``None``.

    The caller is responsible for re-reading the source PDF from
    ``source_path`` — this module does not touch the filesystem.
    """
    with session_factory() as db:
        row = db.get(VlmSession, session_id)
        if row is None:
            return None
        pages = db.execute(
            select(VlmPageResult).where(VlmPageResult.session_id == session_id)
        ).scalars().all()
        return {
            "session_id": row.session_id,
            "run_id": row.run_id,
            "source_filename": row.source_filename,
            "source_sha256": row.source_sha256,
            "source_path": row.source_path,
            "page_count": row.page_count,
            "status": row.status,
            "headless": row.headless,
            "config": row.config or {},
            "stitched_markdown": row.stitched_markdown or "",
            "pages": [
                {
                    "page_num": p.page_num,
                    "status": p.status,
                    "markdown": p.markdown,
                    "model": p.model,
                    "error": p.error,
                }
                for p in pages
            ],
        }


def list_sessions(session_factory: sessionmaker, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return durable session headers, newest first — powers the resume list."""
    with session_factory() as db:
        rows = db.execute(
            select(VlmSession).order_by(VlmSession.updated_at.desc()).limit(limit)
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for row in rows:
            done = len(
                db.execute(
                    select(VlmPageResult.page_num).where(
                        VlmPageResult.session_id == row.session_id,
                        VlmPageResult.status.in_(("done", "skipped")),
                    )
                ).scalars().all()
            )
            out.append(
                {
                    "session_id": row.session_id,
                    "source_filename": row.source_filename,
                    "page_count": row.page_count,
                    "status": row.status,
                    "run_id": row.run_id,
                    "pages_done": done,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else "",
                }
            )
        return out


def delete_session(session_factory: sessionmaker, session_id: str) -> bool:
    """Remove a session and its page rows. The page *cache* is untouched."""
    with session_factory() as db:
        row = db.get(VlmSession, session_id)
        if row is None:
            return False
        db.execute(delete(VlmPageResult).where(VlmPageResult.session_id == session_id))
        db.delete(row)
        db.commit()
        return True


def rehydrate(
    state: dict[str, Any],
    pdf_bytes: bytes,
) -> VlmIngestSession:
    """Rebuild an in-memory session from :func:`load_session` output."""
    s = VlmIngestSession(
        session_id=state["session_id"],
        pdf_bytes=pdf_bytes,
        page_count=int(state["page_count"]),
        source_filename=state.get("source_filename", ""),
        run_id=state.get("run_id"),
        config=VlmIngestConfig.from_dict(state.get("config") or {}),
        headless=bool(state.get("headless", False)),
    )
    try:
        s.status = SessionStatus(state.get("status", "configuring"))
    except ValueError:
        s.status = SessionStatus.CONFIGURING
    # Defence in depth: save_session should never have written one of these,
    # but a row from an older build might carry one. Nothing is running now.
    if s.status in (SessionStatus.PROCESSING, SessionStatus.STITCHING):
        s.status = SessionStatus.CONFIGURING

    # Restore the stitched document. Without this a resumed session cannot be
    # committed — commit falls back to s.stitched when the caller sends no
    # markdown of its own.
    stitched_md = state.get("stitched_markdown") or ""
    if stitched_md:
        done_pages = sum(
            1 for p in state.get("pages", []) if p.get("status") == "done"
        )
        s.stitched = StitchResult(
            markdown=stitched_md,
            page_count=int(state["page_count"]),
            pages_processed=done_pages,
        )
        s.status = SessionStatus.COMPLETE

    for p in state.get("pages", []):
        num = int(p["page_num"])
        try:
            status = PageStatus(p.get("status", "pending"))
        except ValueError:
            status = PageStatus.PENDING
        # A page caught mid-flight by a restart is pending again, not stuck.
        if status is PageStatus.PROCESSING:
            status = PageStatus.PENDING
        s.page_statuses[num] = status
        if status is PageStatus.DONE:
            s.page_results[num] = PageResult(
                page_num=num,
                markdown=p.get("markdown", ""),
                model=p.get("model", ""),
                dpi=s.config.settings_for_page(num).dpi,
                crop_top=s.config.settings_for_page(num).crop_top,
                crop_bottom=s.config.settings_for_page(num).crop_bottom,
            )
        elif status is PageStatus.ERROR:
            s.page_errors[num] = p.get("error", "")
    return s


# ---------------------------------------------------------------------------
# Write-through adapter
# ---------------------------------------------------------------------------

class LedgerSessionWriter:
    """Persists page outcomes the instant they settle.

    Installed on a session as ``session.writer``. Failures here are logged and
    swallowed: losing durability is bad, but crashing an in-flight multi-hour
    VLM job because Postgres blipped would be worse.
    """

    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory

    def page_completed(self, s: VlmIngestSession, page_num: int, result: PageResult) -> None:
        self._safe(
            save_page,
            self._sf,
            s.session_id,
            page_num,
            status="done",
            markdown=result.markdown,
            model=result.model,
            cache_key=getattr(result, "cache_key", "") or "",
        )

    def page_failed(self, s: VlmIngestSession, page_num: int, error: str) -> None:
        self._safe(save_page, self._sf, s.session_id, page_num, status="error", error=error)

    def page_skipped(self, s: VlmIngestSession, page_num: int) -> None:
        self._safe(save_page, self._sf, s.session_id, page_num, status="skipped")

    def status_changed(self, s: VlmIngestSession) -> None:
        self._safe(save_session, self._sf, s)

    @staticmethod
    def _safe(fn: Any, *args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # durability must never break the job
            log.warning("VLM ledger write failed", exc_info=True)
