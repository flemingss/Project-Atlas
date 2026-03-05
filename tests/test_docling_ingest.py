from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import atlas.pipeline.parsers as pipeline_parsers
from atlas.api_rag import make_rag_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.ingest.docling_adapter import DoclingParseResult
from atlas.schemas import ParseProfile

from tests.helpers import FakeQdrantStore, make_test_app


def _fake_parse_document_path(*, doc_path: Path, source_mime_type: str) -> DoclingParseResult:  # noqa: ARG001
    if source_mime_type.endswith("wordprocessingml.document"):
        return DoclingParseResult(
            markdown_projection="# Sample DOCX\n\nHello from DOCX.\n",
            docling_json={"schema_version": "test", "pages": 1, "text": "Hello from DOCX."},
            parse_profile=ParseProfile.TEXT,
            docling_schema_version="test",
            meta={"fake": True, "source_mime_type": source_mime_type},
        )
    return DoclingParseResult(
        markdown_projection="# Sample PDF\n\nHello from PDF.\n",
        docling_json={"schema_version": "test", "pages": 1, "text": "Hello from PDF."},
        parse_profile=ParseProfile.PDF_TEXT,
        docling_schema_version="test",
        meta={"fake": True, "source_mime_type": source_mime_type},
    )


def _make_test_app(tmp_root: Path, monkeypatch: Any) -> tuple[Any, Any]:
    app, session_factory = make_test_app(tmp_root, monkeypatch, include_admin=False)
    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _fake_parse_document_path)
    return app, session_factory


def test_rag_ingest_file_persists_docling_artifacts(tmp_path: Path, monkeypatch: Any) -> None:
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "pdf1", "doc_version": "v1"},
        files={"file": ("sample.pdf", b"%PDF-1.4\n%fake\n", "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] >= 1

    # Confirm we wrote artifact refs for the run.
    from sqlalchemy import select

    from atlas.models import ArtifactRef, WorkflowRun

    with session_factory() as session:
        run = session.execute(select(WorkflowRun).order_by(WorkflowRun.id.desc())).scalars().first()
        assert run is not None
        run_id = int(run.id)

        artifacts = (
            session.execute(select(ArtifactRef).where(ArtifactRef.run_id == run_id).order_by(ArtifactRef.id.asc()))
            .scalars()
            .all()
        )
        kinds = {a.kind for a in artifacts}
        assert "source" in kinds
        assert "docling_json" in kinds
        assert "markdown_projection" in kinds

        artifacts_dir = tmp_path / "artifacts"
        for a in artifacts:
            assert (artifacts_dir / a.path).exists()


def test_rag_ingest_office_docx_persists_artifacts(tmp_path: Path, monkeypatch: Any) -> None:
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # A real DOCX is a ZIP; the parser is patched in this unit test.
    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "docx1", "doc_version": "v1"},
        files={
            "file": (
                "sample.docx",
                b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] >= 1

    from sqlalchemy import select

    from atlas.models import ArtifactRef, WorkflowRun

    with session_factory() as session:
        run = session.execute(select(WorkflowRun).order_by(WorkflowRun.id.desc())).scalars().first()
        assert run is not None
        run_id = int(run.id)

        artifacts = (
            session.execute(select(ArtifactRef).where(ArtifactRef.run_id == run_id).order_by(ArtifactRef.id.asc()))
            .scalars()
            .all()
        )
        kinds = {a.kind for a in artifacts}
        assert "source" in kinds
        assert "docling_json" in kinds
        assert "markdown_projection" in kinds


def test_rag_ingest_markdown_file_succeeds(tmp_path: Path, monkeypatch: Any) -> None:
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "md1", "doc_version": "v1"},
        files={"file": ("sample.md", b"# Hello\n\nThis is markdown.\n", "text/markdown")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] >= 1

    # Confirm we wrote artifact refs for the run.
    from sqlalchemy import select

    from atlas.models import ArtifactRef, WorkflowRun

    with session_factory() as session:
        run = session.execute(select(WorkflowRun).order_by(WorkflowRun.id.desc())).scalars().first()
        assert run is not None
        run_id = int(run.id)

        artifacts = (
            session.execute(select(ArtifactRef).where(ArtifactRef.run_id == run_id).order_by(ArtifactRef.id.asc()))
            .scalars()
            .all()
        )
        kinds = {a.kind for a in artifacts}
        assert "source" in kinds
        assert "markdown_projection" in kinds


def test_rag_ingest_html_file_succeeds(tmp_path: Path, monkeypatch: Any) -> None:
    app, _session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    html = b"<html><body><h1>Title</h1><p>Hello <b>world</b>.</p></body></html>"
    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "html1", "doc_version": "v1"},
        files={"file": ("sample.html", html, "text/html")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] >= 1


def test_rag_ingest_pdf_ocr_empty_returns_error_code(tmp_path: Path, monkeypatch: Any) -> None:
    app, _session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Force backend=docling so auto-fallback to layout parser doesn't mask the error.
    monkeypatch.setenv("ATLAS_PDF_PARSER_BACKEND", "docling")

    # Override the patched parser to simulate an OCR-empty PDF.
    def _fake_parse_document_path_empty(*, doc_path: Path, source_mime_type: str) -> DoclingParseResult:  # noqa: ARG001
        return DoclingParseResult(
            markdown_projection="\n\n   \n",
            docling_json={"schema_version": "test", "pages": 1, "text": ""},
            parse_profile=ParseProfile.PDF_TEXT,
            docling_schema_version="test",
            meta={"fake": True, "source_mime_type": source_mime_type},
        )

    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _fake_parse_document_path_empty)

    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "pdf-empty", "doc_version": "v1"},
        files={"file": ("empty.pdf", b"%PDF-1.4\n%fake\n", "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["chunks_upserted"] == 0
    assert data["error_code"] == "DOC_OCR_EMPTY"
    assert data["error_message"]


def test_rag_ingest_pdf_low_quality_returns_error_code(tmp_path: Path, monkeypatch: Any) -> None:
    app, _session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Make the quality gates strict for this test.
    monkeypatch.setenv("ATLAS_PDF_QUALITY_MIN_CHARS", "10")
    monkeypatch.setenv("ATLAS_PDF_QUALITY_MIN_WORDS", "5")
    monkeypatch.setenv("ATLAS_PDF_QUALITY_ALPHA_RATIO_MIN", "0.90")

    def _fake_parse_document_path_low_quality(*, doc_path: Path, source_mime_type: str) -> DoclingParseResult:  # noqa: ARG001
        return DoclingParseResult(
            markdown_projection="@@@@@@ !!!! $$$$$ ######",  # mostly symbols
            docling_json={"schema_version": "test", "pages": 1, "text": ""},
            parse_profile=ParseProfile.PDF_TEXT,
            docling_schema_version="test",
            meta={"fake": True, "source_mime_type": source_mime_type, "extraction_method": "embedded_text"},
        )

    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _fake_parse_document_path_low_quality)

    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "pdf-lowq", "doc_version": "v1"},
        files={"file": ("lowq.pdf", b"%PDF-1.4\n%fake\n", "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["chunks_upserted"] == 0
    assert data["error_code"] == "DOC_EXTRACT_LOW_QUALITY"
    assert data["error_message"]


def test_rag_ingest_docling_unavailable_returns_error_code(tmp_path: Path, monkeypatch: Any) -> None:
    """DoclingUnavailableError must surface as DOC_PARSE_DEPENDENCY_MISSING, not 502."""
    from atlas.ingest.docling_adapter import DoclingUnavailableError

    app, _session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Force backend=docling so auto-fallback to layout parser doesn't mask the error.
    monkeypatch.setenv("ATLAS_PDF_PARSER_BACKEND", "docling")

    def _raise_unavailable(*, doc_path: Path, source_mime_type: str) -> None:  # noqa: ARG001
        raise DoclingUnavailableError()

    monkeypatch.setattr(pipeline_parsers, "parse_document_path", _raise_unavailable)

    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "pdf-nodocling", "doc_version": "v1"},
        files={"file": ("no_docling.pdf", b"%PDF-1.4\n%fake\n", "application/pdf")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["chunks_upserted"] == 0
    assert data["error_code"] == "DOC_PARSE_DEPENDENCY_MISSING"
    assert data["error_message"]


def test_rag_ingest_file_fidelity_flag_in_payload(tmp_path: Path, monkeypatch: Any) -> None:
    """Committed chunks must include fidelity_flag in their Qdrant payload."""
    app, _session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    res = client.post(
        "/rag/ingest/file",
        data={"doc_id": "pdf-fid", "doc_version": "v1"},
        files={"file": ("sample.pdf", b"%PDF-1.4\n%fake\n", "application/pdf")},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True

    for pt in FakeQdrantStore.last_points:
        assert "fidelity_flag" in pt.payload, "fidelity_flag missing from chunk payload"


def test_rag_ingest_hierarchical_strategy(tmp_path: Path, monkeypatch: Any) -> None:
    """Using chunking.strategy=hierarchical must succeed and produce structured chunks."""
    root = tmp_path / "hier"
    root.mkdir()
    (root / "config").mkdir()
    (root / "config" / "pipeline.yaml").write_text(
        "version: 1\n"
        "limits: { chunk_max_chars: 500 }\n"
        "chunking: { strategy: hierarchical }\n",
        encoding="utf-8",
    )
    (root / "config" / "models.yaml").write_text(
        "version: 1\n"
        "providers: { deterministic: { type: deterministic } }\n"
        "roles: {\n"
        "  embed_model: { provider: deterministic, model_name: deterministic-embed, params: { dim: 8 } },\n"
        "  judge_model: { provider: deterministic, model_name: deterministic-judge, params: {} },\n"
        "  refine_model: { provider: deterministic, model_name: deterministic-refine, params: {} },\n"
        "  metadata_tier1_model: { provider: deterministic, model_name: deterministic-meta1, params: {} },\n"
        "  metadata_tier2_model: { provider: deterministic, model_name: deterministic-meta2, params: {} }\n"
        "}\n",
        encoding="utf-8",
    )

    config_manager = ConfigManager(root_dir=root)
    db_path = root / "test.sqlite"

    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    sf = make_sessionmaker(engine)

    FakeQdrantStore.reset()
    monkeypatch.setattr("atlas.pipeline.runner.QdrantStore", FakeQdrantStore)
    monkeypatch.setenv("ATLAS_ARTIFACTS_DIR", str(root / "artifacts"))

    # Use a markdown file so no Docling call is needed.
    md = b"# Chapter 1\n\nIntro text.\n\n## Section 1.1\n\nDetailed content here.\n"
    app = FastAPI()
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=sf))

    c = TestClient(app)
    res = c.post(
        "/rag/ingest/file",
        data={"doc_id": "hier1", "doc_version": "v1"},
        files={"file": ("doc.md", md, "text/markdown")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] >= 1

    # Verify section_path is populated for at least one chunk (hierarchical feature).
    chunks_with_path = [pt for pt in FakeQdrantStore.last_points if pt.payload.get("section_path")]
    assert chunks_with_path, "hierarchical strategy should produce chunks with section_path"
