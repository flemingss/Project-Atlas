from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from atlas.export_package import build_frontmatter, build_index_md
from tests.helpers import FakeQdrantStore, make_test_app


def test_corpus_export_then_import_roundtrip(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = make_test_app(tmp_path, monkeypatch)
    client = TestClient(app)

    # Ingest a doc into corpus A.
    ingest = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": "doc1",
            "doc_version": "v1",
            "text": "# Title\n\nHello world",
            "corpus_id": "corpA",
        },
    )
    assert ingest.status_code == 200
    assert ingest.json()["ok"] is True

    # Export corpus A.
    exp = client.get("/admin/corpora/corpA/export")
    assert exp.status_code == 200
    assert exp.headers["content-type"].startswith("application/zip")

    outer = zipfile.ZipFile(io.BytesIO(exp.content))
    names = set(outer.namelist())
    assert "corpus_manifest.json" in names
    doc_zips = [n for n in names if n.startswith("docs/") and n.endswith(".zip")]
    assert doc_zips

    # Validate one inner doc package.
    inner_bytes = outer.read(doc_zips[0])
    inner = zipfile.ZipFile(io.BytesIO(inner_bytes))
    inner_names = set(inner.namelist())
    assert "manifest.json" in inner_names
    assert "document.md" in inner_names

    # Import into corpus B.
    files = {"file": ("corp.zip", exp.content, "application/zip")}
    imp = client.post("/admin/corpora/corpB/import", files=files)
    assert imp.status_code == 200
    data = imp.json()
    assert data["ok"] is True
    assert data["docs_imported"] >= 1

    # Ensure points exist for the imported corpus and are searchable.
    has_corp_b = any(
        pt.payload.get("corpus_id") == "corpB"
        and pt.payload.get("is_finalized") is True
        and pt.payload.get("is_active_version") is True
        for pt in FakeQdrantStore.last_points
    )
    assert has_corp_b

    # Verify search is corpus-scoped.
    s_a = client.post("/rag/search", json={"query": "hello", "top_k": 5, "corpus_id": "corpA"}).json()
    s_b = client.post("/rag/search", json={"query": "hello", "top_k": 5, "corpus_id": "corpB"}).json()
    assert s_a["hits"]
    assert s_b["hits"]


def test_corpus_full_export_contains_index_md(tmp_path: Path, monkeypatch: Any) -> None:
    """Full corpus export should include an INDEX.md inventory file."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    client = TestClient(app)

    client.post("/rag/ingest/text", json={
        "doc_id": "idx_doc", "doc_version": "v1",
        "text": "# Index test\n\nContent here", "corpus_id": "corpIdx",
    })
    res = client.get("/admin/corpora/corpIdx/export")
    assert res.status_code == 200

    outer = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(outer.namelist())
    assert "INDEX.md" in names

    index_md = outer.read("INDEX.md").decode("utf-8")
    assert "# Export Inventory" in index_md
    assert "idx_doc" in index_md
    assert "corpIdx" in index_md
    assert "| # |" in index_md  # table header


def test_corpus_lean_export_contains_index_md_and_frontmatter(tmp_path: Path, monkeypatch: Any) -> None:
    """Lean corpus export should include INDEX.md and YAML frontmatter on each MD."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    client = TestClient(app)

    client.post("/rag/ingest/text", json={
        "doc_id": "lean_doc", "doc_version": "v1",
        "text": "# Lean test\n\nSome content", "corpus_id": "corpLean",
    })
    res = client.get("/admin/corpora/corpLean/export?format=lean")
    assert res.status_code == 200

    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(z.namelist())
    assert "INDEX.md" in names

    # Each MD file should have YAML frontmatter
    md_files = [n for n in names if n.endswith(".md") and n != "INDEX.md"]
    assert md_files
    for md_file in md_files:
        content = z.read(md_file).decode("utf-8")
        assert content.startswith("---\n"), f"{md_file} missing frontmatter"
        assert "corpus_id:" in content
        assert "doc_id:" in content


def test_single_doc_lean_export_has_frontmatter(tmp_path: Path, monkeypatch: Any) -> None:
    """Single doc lean export should include YAML frontmatter."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    client = TestClient(app)

    client.post("/rag/ingest/text", json={
        "doc_id": "solo_doc", "doc_version": "v1",
        "text": "# Solo\n\nJust one doc", "corpus_id": "corpSolo",
    })
    res = client.get("/admin/docs/solo_doc/export?format=lean")
    assert res.status_code == 200

    z = zipfile.ZipFile(io.BytesIO(res.content))
    content = z.read("document.md").decode("utf-8")
    assert content.startswith("---\n")
    assert "doc_id:" in content
    assert "exported_at:" in content


# ── Unit tests for helpers ──


def test_build_frontmatter_basic() -> None:
    fm = build_frontmatter({"doc_id": "test", "version": "v1"})
    assert fm.startswith("---\n")
    assert fm.endswith("---\n\n")
    assert 'doc_id: "test"' in fm
    assert 'version: "v1"' in fm


def test_build_frontmatter_skips_none() -> None:
    fm = build_frontmatter({"a": "yes", "b": None, "c": 42})
    assert "a:" in fm
    assert "b:" not in fm
    assert "c: 42" in fm


def test_build_frontmatter_flexible_fields() -> None:
    """Future-proofing: arbitrary keys are accepted without code changes."""
    fm = build_frontmatter({"custom_tag": "important", "priority": 1, "labels": ["a", "b"]})
    assert 'custom_tag: "important"' in fm
    assert "priority: 1" in fm
    assert '["a", "b"]' in fm


def test_build_index_md_basic() -> None:
    docs = [
        {"doc_id": "d1", "doc_version": "v1", "workspace": "w", "project": "p", "collection": "c", "chunks": 5, "file": "docs/d1.md"},
        {"doc_id": "d2", "doc_version": "v2", "workspace": "w", "project": "p", "collection": "c", "chunks": 3, "file": "docs/d2.md"},
    ]
    md = build_index_md(docs, exported_at="2026-03-04T00:00:00Z")
    assert "# Export Inventory" in md
    assert "Exported: 2026-03-04T00:00:00Z" in md
    assert "Total documents: 2" in md
    assert "| 1 |" in md  # first row
    assert "| 2 |" in md  # second row
    assert "d1" in md
    assert "d2" in md


def test_build_index_md_empty() -> None:
    md = build_index_md([])
    assert "Total documents: 0" in md
    assert "| # |" in md  # header still present
