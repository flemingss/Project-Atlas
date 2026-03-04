from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from qdrant_client.http import models as qm

from atlas.artifacts import write_text
from atlas.workflow_ledger import ArtifactRefCreateRequest, WorkflowRunCreateRequest, add_artifact_ref, create_workflow_run
from tests.helpers import FakeQdrantStore, make_test_app


def _make_test_app(*, tmp_root: Path, monkeypatch: Any) -> tuple[Any, Any]:
    app, session_factory = make_test_app(tmp_root, monkeypatch, include_rag=False)
    return app, session_factory


def test_admin_set_active_doc_version_updates_qdrant_payload(tmp_path: Path, monkeypatch: Any) -> None:
    app, _ = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    res = client.post("/admin/docs/demo/active-version", json={"doc_version": "v2"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["active_doc_version"] == "v2"

    # Should set all inactive then selected active.
    assert len(FakeQdrantStore.last_set_payload_calls) == 2
    assert FakeQdrantStore.last_set_payload_calls[0]["payload"] == {"is_active_version": False}
    assert FakeQdrantStore.last_set_payload_calls[1]["payload"] == {"is_active_version": True}


def test_admin_export_doc_returns_zip_with_manifest_and_markdown(tmp_path: Path, monkeypatch: Any) -> None:
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)

    # Seed a completed run + markdown artifact.
    with session_factory() as session:
        run = create_workflow_run(
            session,
            req=WorkflowRunCreateRequest(
                tenant_id="local",
                project_id="default",
                doc_id="demo",
                doc_version="v1",
                status="completed",
                current_node="completed",
                meta={},
            ),
        )
        run_id = int(run.id)

    artifacts_dir = Path(tmp_path / "artifacts")
    md = write_text(
        artifacts_dir=artifacts_dir,
        rel_path=f"runs/{run_id}/ingest/markdown.md",
        text="# Title\n\nHello",
        mime_type="text/markdown",
    )
    with session_factory() as session:
        add_artifact_ref(
            session,
            run_id=run_id,
            req=ArtifactRefCreateRequest(
                kind="markdown_projection",
                path=md.rel_path,
                sha256=md.sha256,
                mime_type=md.mime_type,
                meta={},
            ),
        )

    client = TestClient(app)
    res = client.get("/admin/docs/demo/export")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/zip")

    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(z.namelist())
    assert "manifest.json" in names
    assert "document.md" in names
    assert "index.json" in names

    manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    assert manifest["doc_id"] == "demo"
    assert manifest["doc_version"] == "v1"

    doc_md = z.read("document.md").decode("utf-8")
    assert doc_md.startswith("---\n")
    assert "# Title" in doc_md


def test_admin_reassociate_run_scope_updates_db_and_qdrant(tmp_path: Path, monkeypatch: Any) -> None:
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)

    with session_factory() as session:
        run = create_workflow_run(
            session,
            req=WorkflowRunCreateRequest(
                tenant_id="default",
                project_id="default",
                doc_id="mis_scoped_doc",
                doc_version="1",
                status="completed",
                current_node="completed",
                meta={"corpus_id": "default"},
            ),
        )
        run_id = int(run.id)

    fake_store = FakeQdrantStore(url="http://fake", api_key=None, collection="atlas_chunks")
    fake_store.upsert_points(
        points=[
            qm.PointStruct(
                id="pt-1",
                vector=[0.1] * 8,
                payload={
                    "tenant_id": "default",
                    "project_id": "default",
                    "corpus_id": "default",
                    "doc_id": "mis_scoped_doc",
                    "doc_version": "1",
                    "chunk_index": 0,
                    "text": "hello",
                },
            )
        ]
    )

    client = TestClient(app)
    res = client.post(
        f"/admin/runs/{run_id}/reassociate-scope",
        json={"tenant_id": "local", "project_id": "alpha", "corpus_id": "main"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["from"]["tenant_id"] == "default"
    assert data["to"]["tenant_id"] == "local"
    assert data["qdrant_payload_updated"] is True

    with session_factory() as session:
        from atlas.workflow_ledger import get_workflow_run

        updated = get_workflow_run(session, run_id=run_id)
        assert updated is not None
        assert updated.tenant_id == "local"
        assert updated.project_id == "alpha"
        assert (updated.meta or {}).get("corpus_id") == "main"

    points = fake_store.scroll_points(
        must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value="mis_scoped_doc"))],
        limit=10,
    )
    assert points
    payload = points[0]["payload"]
    assert payload["tenant_id"] == "local"
    assert payload["project_id"] == "alpha"
    assert payload["corpus_id"] == "main"


def test_admin_cleanup_orphan_chunks_dry_run_and_delete(tmp_path: Path, monkeypatch: Any) -> None:
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)

    with session_factory() as session:
        create_workflow_run(
            session,
            req=WorkflowRunCreateRequest(
                tenant_id="local",
                project_id="alpha",
                doc_id="kept_doc",
                doc_version="1",
                status="completed",
                current_node="completed",
                meta={"corpus_id": "main"},
            ),
        )

    fake_store = FakeQdrantStore(url="http://fake", api_key=None, collection="atlas_chunks")
    fake_store.upsert_points(
        points=[
            qm.PointStruct(
                id="keep-1",
                vector=[0.1] * 8,
                payload={
                    "tenant_id": "local",
                    "project_id": "alpha",
                    "corpus_id": "main",
                    "doc_id": "kept_doc",
                    "doc_version": "1",
                    "chunk_index": 0,
                    "text": "keep",
                },
            ),
            qm.PointStruct(
                id="orph-1",
                vector=[0.1] * 8,
                payload={
                    "tenant_id": "local",
                    "project_id": "alpha",
                    "corpus_id": "main",
                    "doc_id": "orphan_doc",
                    "doc_version": "9",
                    "chunk_index": 0,
                    "text": "orphan",
                },
            ),
        ]
    )

    client = TestClient(app)

    dry = client.post(
        "/admin/maintenance/cleanup-orphan-chunks",
        json={"dry_run": True, "max_points": 1000, "tenant_id": "local", "project_id": "alpha"},
    )
    assert dry.status_code == 200
    dry_data = dry.json()
    assert dry_data["dry_run"] is True
    assert dry_data["orphan_groups"] >= 1

    do_delete = client.post(
        "/admin/maintenance/cleanup-orphan-chunks",
        json={"dry_run": False, "max_points": 1000, "tenant_id": "local", "project_id": "alpha"},
    )
    assert do_delete.status_code == 200
    delete_data = do_delete.json()
    assert delete_data["dry_run"] is False
    assert delete_data["deleted_groups"] >= 1

    remaining_orphans = fake_store.scroll_points(
        must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value="orphan_doc"))],
        limit=10,
    )
    assert remaining_orphans == []


def test_admin_adopt_orphan_group_creates_run_and_updates_qdrant(tmp_path: Path, monkeypatch: Any) -> None:
    """Adopt an orphan group: verify synthetic run created + Qdrant scope moved."""
    app, session_factory = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)

    # Create an orphaned Qdrant group (no matching WorkflowRun).
    fake_store = FakeQdrantStore(url="http://fake", api_key=None, collection="atlas_chunks")
    fake_store.upsert_points(
        points=[
            qm.PointStruct(
                id="orph-adopt-1",
                vector=[0.1] * 8,
                payload={
                    "tenant_id": "stale",
                    "project_id": "old",
                    "corpus_id": "gone",
                    "doc_id": "adoptme.pdf",
                    "doc_version": "1",
                    "chunk_index": 0,
                    "text": "some text",
                },
            ),
            qm.PointStruct(
                id="orph-adopt-2",
                vector=[0.1] * 8,
                payload={
                    "tenant_id": "stale",
                    "project_id": "old",
                    "corpus_id": "gone",
                    "doc_id": "adoptme.pdf",
                    "doc_version": "1",
                    "chunk_index": 1,
                    "text": "more text",
                },
            ),
        ]
    )

    client = TestClient(app)
    res = client.post(
        "/admin/maintenance/adopt-orphan-group",
        json={
            "old_tenant_id": "stale",
            "old_project_id": "old",
            "old_doc_id": "adoptme.pdf",
            "old_doc_version": "1",
            "tenant_id": "local",
            "project_id": "default",
            "corpus_id": "main",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["doc_id"] == "adoptme.pdf"
    assert data["qdrant_payload_updated"] is True
    assert data["run_id"] > 0
    assert data["to"]["tenant_id"] == "local"
    assert data["to"]["corpus_id"] == "main"

    # Verify a WorkflowRun was created.
    from atlas.workflow_ledger import get_workflow_run
    with session_factory() as session:
        run = get_workflow_run(session, run_id=data["run_id"])
        assert run is not None
        assert run.tenant_id == "local"
        assert run.project_id == "default"
        assert run.doc_id == "adoptme.pdf"
        assert (run.meta or {}).get("source") == "orphan_adoption"

    # Verify Qdrant payload was updated.
    moved = fake_store.scroll_points(
        must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value="adoptme.pdf")),
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value="local")),
        ],
        limit=10,
    )
    assert len(moved) == 2
    assert moved[0]["payload"]["corpus_id"] == "main"

    # Old scope should have 0 points.
    old = fake_store.scroll_points(
        must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value="adoptme.pdf")),
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value="stale")),
        ],
        limit=10,
    )
    assert old == []
