"""Durability guarantees for VLM ingest.

These tests pin the invariant that the original design violated: **a cache
eviction policy must never be able to destroy business state.**  Sessions used
to live only in an in-memory registry, so a TTL sweep — or a restart, or memory
pressure — silently discarded hours of paid VLM output.

The properties under test:

* the ledger, not the registry, decides whether work survives;
* releasing a session from the cache is transparent to the caller;
* the content-addressed page cache never serves a result for different inputs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.vlm_ingest import store as vlm_store
from atlas.vlm_ingest.session import (
    PageStatus,
    SessionRegistry,
    SessionStatus,
    VlmIngestConfig,
    VlmIngestSession,
)
from atlas.vlm_ingest.stitcher import PageResult


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'durability.db'}")
    ensure_schema(engine)
    return make_sessionmaker(engine)


def _make_session(sid: str = "sess0001", pages: int = 5) -> VlmIngestSession:
    return VlmIngestSession(
        session_id=sid,
        pdf_bytes=b"%PDF-1.4 fake",
        page_count=pages,
        source_filename="manual.pdf",
        config=VlmIngestConfig(dpi=200),
    )


# ---------------------------------------------------------------------------
# Content-addressed page cache
# ---------------------------------------------------------------------------

BASE_KEY_ARGS = {
    "source_sha256": "a" * 64,
    "page_num": 3,
    "dpi": 200,
    "crop": (0.04, 0.04, 0.0, 0.0),
    "mask_regions": None,
    "system_prompt": None,
    "model": "vision-1",
}


@pytest.mark.parametrize(
    ("label", "override"),
    [
        ("document", {"source_sha256": "b" * 64}),
        ("page", {"page_num": 4}),
        ("dpi", {"dpi": 300}),
        ("crop", {"crop": (0.05, 0.04, 0.0, 0.0)}),
        ("mask", {"mask_regions": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}]}),
        ("prompt", {"system_prompt": "be terse"}),
        ("model", {"model": "vision-2"}),
    ],
)
def test_cache_key_covers_every_output_determining_input(label: str, override: dict) -> None:
    """A change to any of these must miss the cache.

    If one of them were omitted from the key, the cache would confidently serve
    the wrong extraction — a correctness bug, not a performance one.
    """
    base = vlm_store.compute_cache_key(**BASE_KEY_ARGS)
    assert vlm_store.compute_cache_key(**{**BASE_KEY_ARGS, **override}) != base, label


def test_cache_key_is_stable_across_float_noise() -> None:
    """Slider jitter must not fragment the cache into useless one-off entries."""
    base = vlm_store.compute_cache_key(**BASE_KEY_ARGS)
    jittered = vlm_store.compute_cache_key(
        **{**BASE_KEY_ARGS, "crop": (0.04000000001, 0.04, 0.0, 0.0)}
    )
    assert jittered == base


def test_cache_round_trip_and_idempotent_store(session_factory) -> None:
    key = vlm_store.compute_cache_key(**BASE_KEY_ARGS)
    with session_factory() as db:
        assert vlm_store.cache_lookup(db, key) is None
        vlm_store.cache_store(
            db, cache_key=key, source_sha256="a" * 64, page_num=3,
            markdown="# Page", model="vision-1",
        )
        db.commit()

    with session_factory() as db:
        hit = vlm_store.cache_lookup(db, key)
        assert hit is not None
        assert hit.markdown == "# Page"
        # A second store for the same key is a no-op, not an integrity error.
        vlm_store.cache_store(
            db, cache_key=key, source_sha256="a" * 64, page_num=3,
            markdown="DIFFERENT", model="vision-1",
        )
        db.commit()

    with session_factory() as db:
        assert vlm_store.cache_lookup(db, key).markdown == "# Page"


# ---------------------------------------------------------------------------
# Session persistence + rehydration
# ---------------------------------------------------------------------------

def test_session_survives_round_trip_through_ledger(session_factory) -> None:
    s = _make_session()
    vlm_store.save_session(session_factory, s, source_sha256="c" * 64, source_path="/tmp/x.pdf")
    vlm_store.save_page(
        session_factory, s.session_id, 0,
        status="done", markdown="# One", model="vision-1", cache_key="k0",
    )
    vlm_store.save_page(session_factory, s.session_id, 1, status="skipped")
    vlm_store.save_page(session_factory, s.session_id, 2, status="error", error="boom")

    state = vlm_store.load_session(session_factory, s.session_id)
    assert state is not None
    assert state["source_filename"] == "manual.pdf"
    assert state["source_sha256"] == "c" * 64

    restored = vlm_store.rehydrate(state, b"%PDF-1.4 fake")
    assert restored.page_statuses[0] is PageStatus.DONE
    assert restored.page_results[0].markdown == "# One"
    assert restored.page_statuses[1] is PageStatus.SKIPPED
    assert restored.page_statuses[2] is PageStatus.ERROR
    assert restored.page_errors[2] == "boom"
    # Untouched pages come back pending, ready to be picked up.
    assert restored.page_statuses[3] is PageStatus.PENDING


def test_rehydrate_returns_in_flight_page_to_pending(session_factory) -> None:
    """A page interrupted mid-call must be retryable, not wedged forever."""
    s = _make_session()
    vlm_store.save_session(session_factory, s)
    vlm_store.save_page(session_factory, s.session_id, 0, status="processing")

    state = vlm_store.load_session(session_factory, s.session_id)
    restored = vlm_store.rehydrate(state, b"pdf")
    assert restored.page_statuses[0] is PageStatus.PENDING
    assert restored.next_pending_page() == 0


def test_load_session_returns_none_for_unknown_id(session_factory) -> None:
    assert vlm_store.load_session(session_factory, "nope") is None


def test_delete_removes_session_but_keeps_page_cache(session_factory) -> None:
    """Discarding a job must not throw away extractions other jobs can reuse."""
    key = vlm_store.compute_cache_key(**BASE_KEY_ARGS)
    with session_factory() as db:
        vlm_store.cache_store(
            db, cache_key=key, source_sha256="a" * 64, page_num=3,
            markdown="# keep me", model="vision-1",
        )
        db.commit()

    s = _make_session()
    vlm_store.save_session(session_factory, s)
    vlm_store.save_page(session_factory, s.session_id, 0, status="done", markdown="x")

    assert vlm_store.delete_session(session_factory, s.session_id) is True
    assert vlm_store.load_session(session_factory, s.session_id) is None
    assert vlm_store.delete_session(session_factory, s.session_id) is False

    with session_factory() as db:
        assert vlm_store.cache_lookup(db, key).markdown == "# keep me"


def test_list_sessions_reports_progress(session_factory) -> None:
    s = _make_session()
    vlm_store.save_session(session_factory, s)
    vlm_store.save_page(session_factory, s.session_id, 0, status="done", markdown="a")
    vlm_store.save_page(session_factory, s.session_id, 1, status="skipped")
    vlm_store.save_page(session_factory, s.session_id, 2, status="error", error="e")

    listed = vlm_store.list_sessions(session_factory)
    assert len(listed) == 1
    # done + skipped count as settled; an errored page still needs attention.
    assert listed[0]["pages_done"] == 2
    assert listed[0]["page_count"] == 5


# ---------------------------------------------------------------------------
# The registry is a cache, not the system of record
# ---------------------------------------------------------------------------

def test_get_rehydrates_through_loader_on_miss() -> None:
    """The regression test for the original bug.

    An id the registry has never seen must still resolve, because the ledger
    knows about it.
    """
    restored = _make_session("cold0001")
    registry = SessionRegistry(loader=lambda sid: restored if sid == "cold0001" else None)

    assert registry.get("cold0001") is restored
    assert registry.get("never-existed") is None


def test_releasing_a_cold_session_does_not_lose_work() -> None:
    """Eviction is transparent: the caller cannot tell it happened."""
    s = _make_session("warm0001")
    s.set_page_result(0, PageResult(page_num=0, markdown="# expensive", model="vision-1"))

    registry = SessionRegistry(ttl_seconds=0.0, loader=lambda sid: s)
    registry.put(s)

    registry.release_cold_sessions()
    assert registry._sessions == {}

    recovered = registry.get("warm0001")
    assert recovered is not None
    assert recovered.page_results[0].markdown == "# expensive"


def test_a_processing_session_is_never_released() -> None:
    """Its bulk loop holds a live reference; releasing it would fork state."""
    s = _make_session("busy0001")
    s.status = SessionStatus.PROCESSING

    registry = SessionRegistry(ttl_seconds=0.0)
    registry.put(s)
    registry.release_cold_sessions()

    assert "busy0001" in registry._sessions


def test_capacity_pressure_releases_lru_and_spares_processing() -> None:
    registry = SessionRegistry(max_sessions=2)

    busy = _make_session("busy0001")
    busy.status = SessionStatus.PROCESSING
    busy.last_activity = 0.0  # oldest, but must be spared
    registry.put(busy)

    idle = _make_session("idle0001")
    idle.last_activity = 1.0
    registry.put(idle)

    registry.create(pdf_bytes=b"pdf", page_count=1)

    assert "busy0001" in registry._sessions
    assert "idle0001" not in registry._sessions


def test_capacity_pressure_refuses_rather_than_disturbing_active_jobs() -> None:
    registry = SessionRegistry(max_sessions=1)
    busy = _make_session("busy0001")
    busy.status = SessionStatus.PROCESSING
    registry.put(busy)

    with pytest.raises(RuntimeError, match="actively processing"):
        registry.create(pdf_bytes=b"pdf", page_count=1)


# ---------------------------------------------------------------------------
# Write-through hooks
# ---------------------------------------------------------------------------

class _RecordingWriter:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def page_completed(self, s, page_num, result):
        self.events.append(("done", page_num, result.markdown))

    def page_failed(self, s, page_num, error):
        self.events.append(("error", page_num, error))

    def page_skipped(self, s, page_num):
        self.events.append(("skipped", page_num))

    def status_changed(self, s):
        self.events.append(("status", s.status.value))


def test_every_page_outcome_is_written_through_immediately() -> None:
    s = _make_session()
    w = _RecordingWriter()
    s.writer = w

    s.set_page_result(0, PageResult(page_num=0, markdown="# md", model="m"))
    s.set_page_error(1, "boom")
    s.skip_page(2)
    s.set_status(SessionStatus.COMPLETE)

    assert w.events == [
        ("done", 0, "# md"),
        ("error", 1, "boom"),
        ("skipped", 2),
        ("status", "complete"),
    ]


def test_a_failing_writer_never_breaks_the_job() -> None:
    """Durability is best-effort: a database blip must not kill a running job."""

    class _Exploding:
        def page_completed(self, *a):
            raise RuntimeError("postgres is down")

    s = _make_session()
    s.writer = _Exploding()

    s.set_page_result(0, PageResult(page_num=0, markdown="# md", model="m"))

    # The in-memory result still landed, which is what the operator sees.
    assert s.page_results[0].markdown == "# md"
    assert s.page_statuses[0] is PageStatus.DONE
