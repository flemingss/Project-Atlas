from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.api_admin import make_admin_router
from atlas.api_rag import make_rag_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema
from atlas.vectorstore.qdrant_store import QdrantHit


# Test markers for triggering pipeline behaviors
HITL_ESCALATION_MARKER = "[UNFIXABLE]"
HITL_ESCALATION_CONTENT = "\uFFFD\uFFFD\uFFFD"  # Replacement characters that trigger HITL
REFINE_TRIGGER_TEXT = "## Ov3rview\n\nThe syst3m c0nsists of components."  # Typos trigger judge score 3 → refine


def _write_minimal_yaml_config(root_dir: Path) -> None:
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "pipeline.yaml").write_text(
        "version: 1\nthresholds: { judge_cutoff_refine: 4, refine_max_retries: 2 }\nlimits: { chunk_max_chars: 1000 }\n",
        encoding="utf-8",
    )
    (root_dir / "config" / "models.yaml").write_text(
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


class _FakeQdrantStore:
    """Mock QdrantStore that tracks all operations for validation.
    
    Note: Uses class-level state for simplicity. Tests using this mock should not
    run in parallel (pytest-xdist) as they share state. The reset() method ensures
    test isolation when run sequentially.
    """

    all_points: list[Any] = []
    search_calls: list[dict[str, Any]] = []
    upsert_count: int = 0

    def __init__(self, *, url: str, api_key: str | None, collection: str):
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self, *, vector_size: int) -> None:
        assert vector_size > 0

    def upsert_points(self, *, points: list[Any]) -> None:
        # Simulate Qdrant's upsert behavior: replace by ID
        # Build a dictionary for O(n) lookup
        existing_by_id = {pt.id: pt for pt in _FakeQdrantStore.all_points}
        
        # Update with new points (replaces existing by ID)
        for pt in points:
            existing_by_id[pt.id] = pt
        
        # Reconstruct the list
        _FakeQdrantStore.all_points = list(existing_by_id.values())
        _FakeQdrantStore.upsert_count += len(points)

    def _extract_condition_value(self, condition: Any) -> tuple[str, Any] | None:
        """Extract key and expected value from a filter condition.
        
        Returns (key, expected_value) tuple or None if condition cannot be parsed.
        """
        # Handle FieldCondition objects from qdrant_client
        if hasattr(condition, 'key') and hasattr(condition, 'match'):
            key = condition.key
            expected = condition.match.value if hasattr(condition.match, 'value') else condition.match
            return (key, expected)
        # Handle dict format (for backward compatibility)
        elif isinstance(condition, dict) and "key" in condition and "match" in condition:
            key = condition["key"]
            expected = condition["match"]["value"] if isinstance(condition["match"], dict) else condition["match"]
            return (key, expected)
        return None

    def search(self, *, query_vector: list[float], limit: int, must: list[Any]) -> list[QdrantHit]:
        _FakeQdrantStore.search_calls.append({"query_vector": query_vector, "limit": limit, "must": must})
        # Return relevant points that match the filters
        hits = []
        for pt in _FakeQdrantStore.all_points:
            payload = pt.payload
            # Check filter matching
            matches = True
            for condition in must:
                parsed = self._extract_condition_value(condition)
                if parsed is None:
                    continue
                key, expected = parsed
                if payload.get(key) != expected:
                    matches = False
                    break
            if matches and len(hits) < limit:
                hits.append(
                    QdrantHit(
                        id=pt.id,
                        score=0.9,
                        payload=payload,
                    )
                )
        return hits

    @classmethod
    def reset(cls) -> None:
        cls.all_points = []
        cls.search_calls = []
        cls.upsert_count = 0


def _make_test_app(tmp_root: Path, monkeypatch: Any) -> FastAPI:
    _write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    # Patch the store used by the pipeline runner and rag router.
    _FakeQdrantStore.reset()
    monkeypatch.setattr("atlas.pipeline.runner.QdrantStore", _FakeQdrantStore)
    monkeypatch.setattr("atlas.api_rag.QdrantStore", _FakeQdrantStore)

    app = FastAPI()
    
    # Add health endpoint like the production app
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}
    
    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    app.include_router(make_rag_router(config_manager=config_manager, session_factory=session_factory))
    return app


def test_e2e_complete_workflow_ingest_to_search(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end workflow test: Ingest → Chunk → Judge → Embed → Store → Search
    
    This validates the complete happy path through the system with deterministic models.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Step 1: Ingest a document
    doc_id = f"e2e-workflow-{uuid.uuid4()}"
    ingest_response = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": doc_id,
            "doc_version": "1",
            "text": "# Product Overview\n\nOur product is a comprehensive solution.\n\n## Features\n\nIt has many features.",
            "tenant_id": "test_tenant",
            "project_id": "test_project",
            "is_finalized": True,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {"source": "e2e_test"},
        },
    )
    assert ingest_response.status_code == 200
    ingest_data = ingest_response.json()
    assert ingest_data["ok"] is True
    assert ingest_data["chunks_upserted"] >= 1

    # Verify chunks were stored
    assert _FakeQdrantStore.upsert_count >= 1
    stored_chunk = _FakeQdrantStore.all_points[0]
    assert stored_chunk.payload["doc_id"] == doc_id
    assert stored_chunk.payload["tenant_id"] == "test_tenant"
    assert stored_chunk.payload["project_id"] == "test_project"
    assert stored_chunk.payload["is_finalized"] is True

    # Step 2: Search for the ingested content
    search_response = client.post(
        "/rag/search",
        json={
            "query": "product features",
            "top_k": 5,
            "tenant_id": "test_tenant",
            "project_id": "test_project",
        },
    )
    assert search_response.status_code == 200
    search_data = search_response.json()
    assert search_data["ok"] is True
    assert len(search_data["hits"]) >= 1

    # Verify the search found our document
    found = any(hit["doc_id"] == doc_id for hit in search_data["hits"])
    assert found, f"Search did not return doc_id={doc_id}"


def test_e2e_multi_document_batch_workflow(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: Batch ingest multiple documents and verify isolation and retrieval.
    
    Tests that multiple documents can be ingested and each maintains proper isolation.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    documents = [
        {
            "doc_id": f"batch-doc-{i}",
            "doc_version": "1",
            "text": f"# Document {i}\n\nThis is document number {i} with unique content about topic_{i}.",
            "tenant_id": "batch_tenant",
            "project_id": "batch_project",
            "is_finalized": True,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {"source": "batch_test", "batch_index": i},
        }
        for i in range(5)
    ]

    # Ingest all documents
    for doc in documents:
        response = client.post("/rag/ingest/text", json=doc)
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["chunks_upserted"] >= 1

    # Verify all documents were stored
    stored_doc_ids = {pt.payload["doc_id"] for pt in _FakeQdrantStore.all_points}
    expected_doc_ids = {doc["doc_id"] for doc in documents}
    assert expected_doc_ids.issubset(stored_doc_ids)

    # Search and verify we can find different documents
    search_response = client.post(
        "/rag/search",
        json={
            "query": "topic",
            "top_k": 10,
            "tenant_id": "batch_tenant",
            "project_id": "batch_project",
        },
    )
    assert search_response.status_code == 200
    hits = search_response.json()["hits"]
    assert len(hits) >= 1  # Should find at least some of our documents


def test_e2e_pipeline_refine_loop_workflow(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: Pipeline with refine loop (judge score < threshold → refine → re-judge)
    
    Tests the automatic refinement workflow using deterministic models that trigger refinement.
    The text with intentional typos (Ov3rview, syst3m, c0nsists) triggers deterministic judge
    score 3, which is below the threshold of 4, triggering the refine loop. After refinement,
    the judge scores 5 and the content is committed.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    doc_id = f"refine-test-{uuid.uuid4()}"
    response = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": doc_id,
            "doc_version": "1",
            "text": REFINE_TRIGGER_TEXT,
            "tenant_id": "refine_tenant",
            "project_id": "refine_project",
            "is_finalized": True,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {"source": "refine_test"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["chunks_upserted"] >= 1

    # Verify the refined content was stored
    refined_chunks = [pt for pt in _FakeQdrantStore.all_points if pt.payload.get("doc_id") == doc_id]
    assert len(refined_chunks) >= 1
    
    # Verify refinement occurred by checking for the [REFINED] marker added by deterministic refine
    # The deterministic refine model adds "[REFINED]" prefix to refined content
    chunk_text = refined_chunks[0].payload.get("text", "")
    assert "[REFINED]" in chunk_text, "Refinement did not occur - [REFINED] marker not found in chunk text"


def test_e2e_hitl_escalation_workflow(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: HITL escalation workflow (unfixable content → HITL → human fix → resume)
    
    Tests the complete human-in-the-loop workflow from escalation through resolution.
    Content containing HITL_ESCALATION_MARKER and replacement characters triggers
    automatic escalation to human review.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    doc_id = f"hitl-test-{uuid.uuid4()}"
    ingest_response = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": doc_id,
            "doc_version": "1",
            "text": f"{HITL_ESCALATION_MARKER}\n\n{HITL_ESCALATION_CONTENT} corrupted data",
            "tenant_id": "hitl_tenant",
            "project_id": "hitl_project",
            "is_finalized": True,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {"source": "hitl_test"},
        },
    )
    assert ingest_response.status_code == 200
    assert ingest_response.json()["chunks_upserted"] == 0  # Should be 0 because paused for HITL

    # Get the HITL task
    task_response = client.post("/admin/hitl/tasks/next", params={"assigned_to": "test_operator"})
    assert task_response.status_code == 200
    task = task_response.json()
    assert task["doc_id"] == doc_id
    assert task["status"] == "in_progress"

    # Complete the HITL task with fixed content
    complete_response = client.post(
        f"/admin/hitl/tasks/{task['id']}/complete",
        json={
            "after_md": "# Clean Overview\n\nThis is the corrected content.",
            "reason_for_edit": "Fixed corrupted data",
        },
    )
    assert complete_response.status_code == 200

    # Resume the pipeline
    resume_response = client.post(f"/admin/hitl/tasks/{task['id']}/resume")
    assert resume_response.status_code == 200
    resume_data = resume_response.json()
    assert resume_data["ok"] is True
    assert resume_data["chunks_upserted"] >= 1

    # Verify the fixed content is now searchable
    search_response = client.post(
        "/rag/search",
        json={
            "query": "corrected content",
            "top_k": 5,
            "tenant_id": "hitl_tenant",
            "project_id": "hitl_project",
        },
    )
    assert search_response.status_code == 200
    hits = search_response.json()["hits"]
    found = any(hit["doc_id"] == doc_id for hit in hits)
    assert found, f"Search did not return HITL-resumed doc_id={doc_id}"


def test_e2e_tenant_isolation_workflow(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: Multi-tenant isolation (verify tenant A cannot see tenant B's data)
    
    Critical security test ensuring tenant isolation in the RAG system.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Ingest document for tenant A
    doc_a = f"tenant-a-{uuid.uuid4()}"
    response_a = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": doc_a,
            "doc_version": "1",
            "text": "Confidential information for Tenant A",
            "tenant_id": "tenant_a",
            "project_id": "project_a",
            "is_finalized": True,
            "is_sensitive": True,
            "source_mime_type": "text/plain",
            "metadata": {"source": "tenant_a_data"},
        },
    )
    assert response_a.status_code == 200
    assert response_a.json()["ok"] is True
    assert response_a.json()["chunks_upserted"] >= 1

    # Ingest document for tenant B
    doc_b = f"tenant-b-{uuid.uuid4()}"
    response_b = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": doc_b,
            "doc_version": "1",
            "text": "Confidential information for Tenant B",
            "tenant_id": "tenant_b",
            "project_id": "project_b",
            "is_finalized": True,
            "is_sensitive": True,
            "source_mime_type": "text/plain",
            "metadata": {"source": "tenant_b_data"},
        },
    )
    assert response_b.status_code == 200
    assert response_b.json()["ok"] is True
    assert response_b.json()["chunks_upserted"] >= 1

    # Search as tenant A - should see tenant A doc but NOT tenant B's data
    search_a = client.post(
        "/rag/search",
        json={
            "query": "Confidential",
            "top_k": 10,
            "tenant_id": "tenant_a",
            "project_id": "project_a",
        },
    )
    assert search_a.status_code == 200
    hits_a = search_a.json()["hits"]
    
    # Tenant A should see their own doc
    tenant_a_found = any(hit["doc_id"] == doc_a for hit in hits_a)
    assert tenant_a_found, "Tenant A cannot see their own document!"
    
    # Tenant A should NOT see tenant B's doc
    tenant_b_leaked = any(hit["doc_id"] == doc_b for hit in hits_a)
    assert not tenant_b_leaked, "SECURITY: Tenant A can see Tenant B's data!"

    # Search as tenant B - should see tenant B doc but NOT tenant A's data
    search_b = client.post(
        "/rag/search",
        json={
            "query": "Confidential",
            "top_k": 10,
            "tenant_id": "tenant_b",
            "project_id": "project_b",
        },
    )
    assert search_b.status_code == 200
    hits_b = search_b.json()["hits"]
    
    # Tenant B should see their own doc
    tenant_b_found = any(hit["doc_id"] == doc_b for hit in hits_b)
    assert tenant_b_found, "Tenant B cannot see their own document!"
    
    # Tenant B should NOT see tenant A's doc
    tenant_a_leaked = any(hit["doc_id"] == doc_a for hit in hits_b)
    assert not tenant_a_leaked, "SECURITY: Tenant B can see Tenant A's data!"


def test_e2e_finalized_filter_workflow(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: Non-finalized documents are not returned in searches.
    
    Tests that the is_finalized filter correctly excludes draft documents.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Ingest finalized document
    doc_final = f"final-{uuid.uuid4()}"
    final_response = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": doc_final,
            "doc_version": "1",
            "text": "Production ready content",
            "tenant_id": "test",
            "project_id": "test",
            "is_finalized": True,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {},
        },
    )
    assert final_response.status_code == 200
    assert final_response.json()["ok"] is True
    assert final_response.json()["chunks_upserted"] >= 1

    # Ingest non-finalized (draft) document
    doc_draft = f"draft-{uuid.uuid4()}"
    draft_response = client.post(
        "/rag/ingest/text",
        json={
            "doc_id": doc_draft,
            "doc_version": "1",
            "text": "Draft content not ready for production",
            "tenant_id": "test",
            "project_id": "test",
            "is_finalized": False,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {},
        },
    )
    assert draft_response.status_code == 200
    assert draft_response.json()["ok"] is True

    # Search should only return finalized content
    search_response = client.post(
        "/rag/search",
        json={
            "query": "content",
            "top_k": 10,
            "tenant_id": "test",
            "project_id": "test",
        },
    )
    assert search_response.status_code == 200
    hits = search_response.json()["hits"]

    # Finalized document SHOULD appear in results
    final_in_results = any(hit["doc_id"] == doc_final for hit in hits)
    assert final_in_results, "Finalized document not found in search results!"
    
    # Draft document should NOT appear in results
    draft_in_results = any(hit["doc_id"] == doc_draft for hit in hits)
    assert not draft_in_results, "Non-finalized document appeared in search results!"


def test_e2e_config_version_activation_affects_pipeline(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: Changing config versions affects pipeline behavior.
    
    Tests that the dynamic configuration system correctly applies to the pipeline.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Get current effective config
    effective_before = client.get("/admin/config/effective")
    assert effective_before.status_code == 200
    before_threshold = effective_before.json()["pipeline"]["thresholds"]["judge_cutoff_refine"]

    # Create and activate a new config version
    new_config = client.post(
        "/admin/config-versions",
        json={
            "name": "e2e test config",
            "notes": "Testing config version activation",
            "base": "current",
            "patch": {"pipeline": {"thresholds": {"judge_cutoff_refine": 5}}},
            "activate": True,
        },
    )
    assert new_config.status_code == 200

    # Verify the config changed
    effective_after = client.get("/admin/config/effective")
    assert effective_after.status_code == 200
    after_threshold = effective_after.json()["pipeline"]["thresholds"]["judge_cutoff_refine"]
    assert after_threshold == 5
    assert after_threshold != before_threshold


def test_e2e_idempotent_ingest_workflow(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: Ingesting the same document multiple times is idempotent.
    
    Tests that re-ingesting identical content doesn't create duplicates.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    doc_payload = {
        "doc_id": "idempotent-test",
        "doc_version": "1",
        "text": "This content should only appear once",
        "tenant_id": "test",
        "project_id": "test",
        "is_finalized": True,
        "is_sensitive": False,
        "source_mime_type": "text/plain",
        "metadata": {},
    }

    # First ingest
    response1 = client.post("/rag/ingest/text", json=doc_payload)
    assert response1.status_code == 200
    
    # Count points for this doc after first ingest
    points_after_first = [pt for pt in _FakeQdrantStore.all_points if pt.payload.get("doc_id") == "idempotent-test"]
    count_after_first = len(points_after_first)
    assert count_after_first >= 1, "First ingest produced no points"

    # Second ingest (identical)
    response2 = client.post("/rag/ingest/text", json=doc_payload)
    assert response2.status_code == 200
    
    # Count points for this doc after second ingest
    points_after_second = [pt for pt in _FakeQdrantStore.all_points if pt.payload.get("doc_id") == "idempotent-test"]
    count_after_second = len(points_after_second)
    
    # Idempotency: the number of stored points should remain the same (upsert replaces by ID)
    assert count_after_second == count_after_first, f"Idempotency violation: {count_after_first} points after first ingest, {count_after_second} after second"


def test_e2e_admin_health_and_diagnostics(tmp_path: Path, monkeypatch: Any) -> None:
    """
    End-to-end test: Admin endpoints for health and diagnostics work correctly.
    
    Tests that operational endpoints are functional for monitoring.
    """
    app = _make_test_app(tmp_root=tmp_path, monkeypatch=monkeypatch)
    client = TestClient(app)

    # Health check
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # Effective config
    config = client.get("/admin/config/effective")
    assert config.status_code == 200
    data = config.json()
    assert "hash" in data
    assert "pipeline" in data
    assert "models" in data

    # Reload YAML
    reload = client.post("/admin/reload-yaml")
    assert reload.status_code == 200
    assert reload.json()["ok"] is True
