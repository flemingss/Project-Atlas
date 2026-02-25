from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

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
