from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    ok: bool
    detail: str | None = None


@dataclass(frozen=True)
class RunSummary:
    ok: bool
    results: list[ScenarioResult]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _require_ok(resp: httpx.Response, *, label: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise RuntimeError(f"{label} failed: {resp.status_code} {resp.text}")
    try:
        return resp.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"{label} returned non-JSON: {resp.text}") from e


def wait_for_health(client: httpx.Client, *, api_url: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = client.get(f"{api_url}/health")
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"API not healthy within {timeout_s}s at {api_url}")


def qdrant_collection_dim(client: httpx.Client, *, qdrant_url: str, collection: str) -> int | None:
    r = client.get(f"{qdrant_url}/collections/{collection}")
    if r.status_code == 404:
        return None
    data = _require_ok(r, label="qdrant get collection")
    try:
        return int(data["result"]["config"]["params"]["vectors"]["size"])
    except Exception:
        return None


def qdrant_count_points(client: httpx.Client, *, qdrant_url: str, collection: str, filt: dict[str, Any]) -> int:
    payload = {"filter": filt, "exact": True}
    r = client.post(f"{qdrant_url}/collections/{collection}/points/count", json=payload)
    data = _require_ok(r, label="qdrant count")
    try:
        return int(data["result"]["count"])
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Unexpected qdrant count shape: {data}") from e


def _openai_compat_v1(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def scenario_local_llm_preflight(client: httpx.Client, *, api_url: str) -> None:
    """Verify an OpenAI-compatible server is reachable for the configured models.

    This is intentionally a lightweight smoke check to fail fast with a clear message
    when running `--mode local_llm`.
    """

    base_url = os.environ.get("ATLAS_OPENAI_BASE_URL")
    if not base_url:
        raise RuntimeError("ATLAS_OPENAI_BASE_URL is required for local_llm mode")

    llm_model = os.environ.get("ATLAS_E2E_LLM_MODEL")
    embed_model = os.environ.get("ATLAS_E2E_EMBED_MODEL")
    if not llm_model or not embed_model:
        raise RuntimeError(
            "ATLAS_E2E_LLM_MODEL and ATLAS_E2E_EMBED_MODEL are required for local_llm mode (set them in .env)"
        )

    print(f"[e2e] local_llm preflight base_url={base_url}")

    v1 = _openai_compat_v1(base_url)

    # Optional introspection: helps LM Studio users understand what's loaded.
    try:
        rm = client.get(f"{v1}/models")
        if rm.status_code < 400:
            data = rm.json()
            ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
            if ids:
                print(f"[e2e] local_llm models loaded: {', '.join(ids)}")
                if llm_model not in ids:
                    print(f"[e2e] WARN: ATLAS_E2E_LLM_MODEL not in /v1/models: {llm_model}")
                if embed_model not in ids:
                    print(f"[e2e] WARN: ATLAS_E2E_EMBED_MODEL not in /v1/models: {embed_model}")
            else:
                print("[e2e] local_llm models loaded: <none>")
    except Exception:
        pass

    # Chat preflight
    chat_payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": "You are a test harness."},
            {"role": "user", "content": "Reply with 'ok'"},
        ],
        "temperature": 0.0,
    }
    r1 = client.post(f"{v1}/chat/completions", json=chat_payload)
    if r1.status_code >= 400:
        raise RuntimeError(
            f"OpenAI-compatible chat preflight failed ({r1.status_code}) at {v1} for model={llm_model}: {r1.text}"
        )
    print(f"[e2e] local_llm preflight chat ok model={llm_model}")

    # Embeddings preflight
    emb_payload = {"model": embed_model, "input": ["hello"]}
    r2 = client.post(f"{v1}/embeddings", json=emb_payload)
    if r2.status_code >= 400:
        raise RuntimeError(
            f"OpenAI-compatible embeddings preflight failed ({r2.status_code}) at {v1} for model={embed_model}: {r2.text}"
        )
    print(f"[e2e] local_llm preflight embeddings ok model={embed_model}")


def activate_deterministic_pipeline_models(client: httpx.Client, *, api_url: str, dim: int) -> None:
    payload = {
        "name": f"e2e: deterministic pipeline models (dim {dim})",
        "notes": "E2E scenario runner: deterministic providers for judge/refine/metadata/embeddings.",
        "base": "yaml",
        "patch": {
            "models": {
                "roles": {
                    "embed_model": {
                        "provider": "deterministic",
                        "model_name": "deterministic-embed",
                        "params": {"dim": dim},
                    },
                    "judge_model": {
                        "provider": "deterministic",
                        "model_name": "deterministic-judge",
                        "params": {},
                    },
                    "refine_model": {
                        "provider": "deterministic",
                        "model_name": "deterministic-refine",
                        "params": {},
                    },
                    "metadata_tier1_model": {
                        "provider": "deterministic",
                        "model_name": "deterministic-metadata-t1",
                        "params": {},
                    },
                    "metadata_tier2_model": {
                        "provider": "deterministic",
                        "model_name": "deterministic-metadata-t2",
                        "params": {},
                    },
                }
            }
        },
        "activate": True,
    }
    r = client.post(f"{api_url}/admin/config-versions", json=payload)
    _require_ok(r, label="activate deterministic pipeline models")

    eff = _require_ok(client.get(f"{api_url}/admin/config/effective"), label="effective config")
    roles = eff["models"]["roles"]
    for role_name in ("embed_model", "judge_model", "refine_model", "metadata_tier1_model", "metadata_tier2_model"):
        role = roles.get(role_name) or {}
        if role.get("provider") != "deterministic":
            raise RuntimeError(f"{role_name} provider not deterministic: {role}")


def activate_local_llm_pipeline_guardrails(client: httpx.Client, *, api_url: str) -> None:
    """Apply pipeline config guardrails to reduce flakiness in local-LLM E2E runs.

    - Disable refine loop by setting judge_cutoff_refine=1 (since scores are 1-5)
      so we don't require a vision-capable refine model.
    """

    payload = {
        "name": "e2e: local LLM guardrails",
        "notes": "E2E local_llm: disable refine loop for stability and to avoid vision dependencies.",
        "base": "current",
        "patch": {"pipeline": {"thresholds": {"judge_cutoff_refine": 1}}},
        "activate": True,
    }
    _require_ok(client.post(f"{api_url}/admin/config-versions", json=payload), label="activate local LLM guardrails")


def activate_local_llm_pipeline_models(client: httpx.Client, *, api_url: str) -> None:
    """Activate OpenAI-compatible (local) models for E2E runs.

    The OpenAI-compatible base URL is sourced from ATLAS_OPENAI_BASE_URL by the
    configured provider (see config/models.yaml).
    """

    llm_model = os.environ.get("ATLAS_E2E_LLM_MODEL", "llama3.2:1b")
    embed_model = os.environ.get("ATLAS_E2E_EMBED_MODEL", "nomic-embed-text")

    payload = {
        "name": "e2e: local LLM pipeline models",
        "notes": "E2E local_llm: use OpenAI-compatible provider for judge/metadata + embeddings.",
        "base": "current",
        "patch": {
            "models": {
                "roles": {
                    "embed_model": {"provider": "lmstudio", "model_name": embed_model, "params": {}},
                    "judge_model": {
                        "provider": "lmstudio",
                        "model_name": llm_model,
                        "params": {"temperature": 0.0},
                    },
                    "refine_model": {
                        "provider": "lmstudio",
                        "model_name": llm_model,
                        "params": {"temperature": 0.0},
                    },
                    "metadata_tier1_model": {
                        "provider": "lmstudio",
                        "model_name": llm_model,
                        "params": {"temperature": 0.0},
                    },
                    "metadata_tier2_model": {
                        "provider": "lmstudio",
                        "model_name": llm_model,
                        "params": {"temperature": 0.0},
                    },
                }
            }
        },
        "activate": True,
    }
    _require_ok(client.post(f"{api_url}/admin/config-versions", json=payload), label="activate local LLM models")


def scenario_admin_endpoints(client: httpx.Client, *, api_url: str) -> None:
    health = _require_ok(client.get(f"{api_url}/health"), label="health")
    if health.get("status") != "ok":
        raise RuntimeError(f"Unexpected health: {health}")

    effective = _require_ok(client.get(f"{api_url}/admin/config/effective"), label="effective config")
    if "hash" not in effective or "pipeline" not in effective or "models" not in effective:
        raise RuntimeError(f"Unexpected effective config shape: {effective}")

    reload_res = _require_ok(client.post(f"{api_url}/admin/reload-yaml"), label="reload yaml")
    if reload_res.get("ok") is not True:
        raise RuntimeError(f"reload-yaml not ok: {reload_res}")


def scenario_config_version_activation(client: httpx.Client, *, api_url: str) -> None:
    before = _require_ok(client.get(f"{api_url}/admin/config/effective"), label="effective config")
    before_cutoff = before["pipeline"].get("thresholds", {}).get("judge_cutoff_refine")

    payload = {
        "name": "e2e: tweak judge cutoff",
        "notes": "E2E: create+activate config version and verify effective config changes.",
        "base": "current",
        "patch": {"pipeline": {"thresholds": {"judge_cutoff_refine": 4}}},
        "activate": True,
    }
    _require_ok(client.post(f"{api_url}/admin/config-versions", json=payload), label="create config version")

    after = _require_ok(client.get(f"{api_url}/admin/config/effective"), label="effective config")
    after_cutoff = after["pipeline"].get("thresholds", {}).get("judge_cutoff_refine")
    if after_cutoff != 4:
        raise RuntimeError(
            f"Expected judge_cutoff_refine=4 after activation (was {before_cutoff} -> {after_cutoff})"
        )


def scenario_rag_roundtrip(client: httpx.Client, *, api_url: str) -> None:
    doc_id = f"e2e-doc-{int(time.time())}"
    ingest = {
        "doc_id": doc_id,
        "doc_version": "1",
        "text": "Hello Atlas\n\n# Heading\n\nThis is an E2E test chunk.",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": True,
        "is_sensitive": True,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e"},
    }
    r1 = client.post(f"{api_url}/rag/ingest/text", json=ingest)
    data1 = _require_ok(r1, label="rag ingest")
    if not data1.get("ok"):
        raise RuntimeError(f"rag ingest not ok: {data1}")
    if int(data1.get("chunks_upserted", 0)) < 1:
        raise RuntimeError(f"rag ingest upserted 0 chunks: {data1}")

    search = {
        "query": "E2E test chunk",
        "top_k": 5,
        "tenant_id": "local",
        "project_id": "default",
    }
    r2 = client.post(f"{api_url}/rag/search", json=search)
    data2 = _require_ok(r2, label="rag search")
    hits = data2.get("hits") or []
    if not hits:
        raise RuntimeError(f"rag search returned no hits: {data2}")

    if not any(h.get("doc_id") == doc_id for h in hits):
        raise RuntimeError(
            f"rag search did not return our doc_id={doc_id}: {json.dumps(hits)[:500]}"
        )


def scenario_rag_tenant_isolation(client: httpx.Client, *, api_url: str) -> None:
    doc_a = f"e2e-tenant-a-{int(time.time())}"
    doc_b = f"e2e-tenant-b-{int(time.time())}"
    text = "Tenant isolation sentinel text"

    ingest_a = {
        "doc_id": doc_a,
        "doc_version": "1",
        "text": text,
        "tenant_id": "tenant_a",
        "project_id": "proj_a",
        "is_finalized": True,
        "is_sensitive": True,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e"},
    }
    ingest_b = {
        "doc_id": doc_b,
        "doc_version": "1",
        "text": text,
        "tenant_id": "tenant_b",
        "project_id": "proj_b",
        "is_finalized": True,
        "is_sensitive": True,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e"},
    }
    _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest_a), label="rag ingest tenant_a")
    _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest_b), label="rag ingest tenant_b")

    search_a = {"query": "sentinel", "top_k": 10, "tenant_id": "tenant_a", "project_id": "proj_a"}
    hits_a = (
        _require_ok(client.post(f"{api_url}/rag/search", json=search_a), label="rag search tenant_a").get("hits")
        or []
    )
    if any(h.get("doc_id") == doc_b for h in hits_a):
        raise RuntimeError("Tenant/project isolation failed: tenant_a search returned tenant_b doc")

    search_b = {"query": "sentinel", "top_k": 10, "tenant_id": "tenant_b", "project_id": "proj_b"}
    hits_b = (
        _require_ok(client.post(f"{api_url}/rag/search", json=search_b), label="rag search tenant_b").get("hits")
        or []
    )
    if any(h.get("doc_id") == doc_a for h in hits_b):
        raise RuntimeError("Tenant/project isolation failed: tenant_b search returned tenant_a doc")


def scenario_rag_finalized_filter(client: httpx.Client, *, api_url: str) -> None:
    doc_id = f"e2e-nonfinal-{int(time.time())}"
    ingest = {
        "doc_id": doc_id,
        "doc_version": "1",
        "text": "Non-finalized sentinel",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": False,
        "is_sensitive": True,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e"},
    }
    _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest), label="rag ingest non-finalized")

    search = {"query": "Non-finalized sentinel", "top_k": 10, "tenant_id": "local", "project_id": "default"}
    hits = (
        _require_ok(client.post(f"{api_url}/rag/search", json=search), label="rag search finalized filter").get(
            "hits"
        )
        or []
    )
    if any(h.get("doc_id") == doc_id for h in hits):
        raise RuntimeError("Finalized filter failed: search returned non-finalized doc")


def scenario_rag_idempotent_upsert_count(
    client: httpx.Client, *, api_url: str, qdrant_url: str, collection: str
) -> None:
    doc_id = f"e2e-idempotent-{int(time.time())}"
    payload = {
        "doc_id": doc_id,
        "doc_version": "1",
        "text": "Idempotency sentinel",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": True,
        "is_sensitive": True,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e"},
    }

    _require_ok(client.post(f"{api_url}/rag/ingest/text", json=payload), label="rag ingest 1")
    _require_ok(client.post(f"{api_url}/rag/ingest/text", json=payload), label="rag ingest 2")

    filt = {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}
    count = qdrant_count_points(client, qdrant_url=qdrant_url, collection=collection, filt=filt)
    if count != 1:
        raise RuntimeError(f"Expected 1 point after idempotent upsert, got {count}")


def scenario_pipeline_refine_then_pass(client: httpx.Client, *, api_url: str) -> None:
    doc_id = f"e2e-refine-{int(time.time())}"
    ingest = {
        "doc_id": doc_id,
        "doc_version": "1",
        # Triggers deterministic judge score 3 -> refine -> judge score 5
        "text": "## Ov3rview\n\nThe syst3m c0nsists of components.",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": True,
        "is_sensitive": True,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e"},
    }
    data = _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest), label="pipeline ingest refine")
    if int(data.get("chunks_upserted", 0)) < 1:
        raise RuntimeError(f"Expected commit after refine loop, got: {data}")


def scenario_pipeline_hitl_escalation_and_resume(
    client: httpx.Client, *, api_url: str
) -> None:
    doc_id = f"e2e-hitl-{int(time.time())}"
    ingest = {
        "doc_id": doc_id,
        "doc_version": "1",
        "text": "[UNFIXABLE]\n\n\uFFFD\uFFFD\uFFFD unreadable",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": True,
        "is_sensitive": True,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e"},
    }

    data = _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest), label="pipeline ingest hitl")
    if int(data.get("chunks_upserted", 0)) != 0:
        raise RuntimeError(f"Expected 0 chunks when paused for HITL, got: {data}")

    # Claim next HITL task
    nxt = client.post(f"{api_url}/admin/hitl/tasks/next", params={"assigned_to": "e2e"})
    task = _require_ok(nxt, label="hitl next")
    if task.get("doc_id") != doc_id:
        raise RuntimeError(f"Expected HITL task for {doc_id}, got: {task}")

    # Complete task with an edited markdown that will pass deterministic judge
    complete = client.post(
        f"{api_url}/admin/hitl/tasks/{task['id']}/complete",
        json={"after_md": "# Overview\n\nFixed content for resume.", "reason_for_edit": "e2e"},
    )
    _require_ok(complete, label="hitl complete")

    # Resume pipeline and commit
    resume = client.post(f"{api_url}/admin/hitl/tasks/{task['id']}/resume")
    resumed = _require_ok(resume, label="hitl resume")
    if int(resumed.get("chunks_upserted", 0)) < 1:
        raise RuntimeError(f"Expected chunks after resume, got: {resumed}")

    # Verify searchable
    search = {"query": "Fixed content", "top_k": 5, "tenant_id": "local", "project_id": "default"}
    hits = (
        _require_ok(client.post(f"{api_url}/rag/search", json=search), label="rag search after resume").get("hits")
        or []
    )
    if not any(h.get("doc_id") == doc_id for h in hits):
        raise RuntimeError(f"Expected search to find resumed doc_id={doc_id}")


def scenario_batch_multi_document_ingest(client: httpx.Client, *, api_url: str) -> None:
    """Validate batch ingestion of multiple documents with various characteristics."""
    timestamp = int(time.time())
    doc_ids = []

    # Ingest 5 documents with varying content
    for i in range(5):
        doc_id = f"e2e-batch-{timestamp}-{i}"
        doc_ids.append(doc_id)
        ingest = {
            "doc_id": doc_id,
            "doc_version": "1",
            "text": f"# Document {i}\n\nContent for document {i} about topic_{i}.",
            "tenant_id": "local",
            "project_id": "default",
            "is_finalized": True,
            "is_sensitive": False,
            "source_mime_type": "text/plain",
            "metadata": {"source": "e2e_batch", "batch_index": i},
        }
        data = _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest), label=f"batch ingest doc {i}")
        if int(data.get("chunks_upserted", 0)) < 1:
            raise RuntimeError(f"Batch doc {i} did not ingest: {data}")

    # Verify we can search and find at least some of these documents
    search = {"query": "topic", "top_k": 10, "tenant_id": "local", "project_id": "default"}
    hits = _require_ok(client.post(f"{api_url}/rag/search", json=search), label="batch search").get("hits") or []
    found_count = sum(1 for h in hits if h.get("doc_id") in doc_ids)
    if found_count < 1:
        raise RuntimeError(f"Expected to find at least one batch document, found {found_count}")


def scenario_workflow_orchestration_validation(client: httpx.Client, *, api_url: str) -> None:
    """
    Comprehensive workflow test: ingest → chunk → judge → embed → store → search.
    Validates complete data flow through the entire pipeline.
    """
    doc_id = f"e2e-workflow-{int(time.time())}"

    # Step 1: Ingest with well-formed content
    ingest = {
        "doc_id": doc_id,
        "doc_version": "1",
        "text": "# Technical Overview\n\nThis document describes the system architecture.\n\n## Components\n\nThe system has multiple integrated components.",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": True,
        "is_sensitive": False,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e_workflow"},
    }
    ingest_data = _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest), label="workflow ingest")
    if not ingest_data.get("ok"):
        raise RuntimeError(f"Workflow ingest failed: {ingest_data}")
    chunks = int(ingest_data.get("chunks_upserted", 0))
    if chunks < 1:
        raise RuntimeError(f"Workflow produced no chunks: {ingest_data}")

    # Step 2: Verify the run was recorded in the ledger
    runs = _require_ok(client.get(f"{api_url}/admin/runs"), label="workflow runs list")
    if not runs or not isinstance(runs, list):
        raise RuntimeError("No workflow runs found in ledger")

    # Step 3: Search for the ingested content and validate retrieval
    search = {"query": "system architecture", "top_k": 5, "tenant_id": "local", "project_id": "default"}
    search_data = _require_ok(client.post(f"{api_url}/rag/search", json=search), label="workflow search")
    hits = search_data.get("hits") or []
    if not any(h.get("doc_id") == doc_id for h in hits):
        raise RuntimeError(f"Workflow: search did not find ingested doc_id={doc_id}")


def scenario_error_recovery_validation(client: httpx.Client, *, api_url: str) -> None:
    """
    Test error recovery: HITL skip and reject operations.
    Validates that operators can handle problematic documents appropriately.
    """
    # Create a HITL task for skip test
    doc_id_skip = f"e2e-error-skip-{int(time.time())}"
    ingest_skip = {
        "doc_id": doc_id_skip,
        "doc_version": "1",
        "text": "[UNFIXABLE]\n\nUnrecoverable content for skip",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": True,
        "is_sensitive": False,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e_error_skip"},
    }
    _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest_skip), label="error recovery ingest skip")

    # Get the task and test skip operation
    task_skip = _require_ok(client.post(f"{api_url}/admin/hitl/tasks/next", params={"assigned_to": "e2e"}), label="error recovery get task skip")
    skip_result = _require_ok(client.post(f"{api_url}/admin/hitl/tasks/{task_skip['id']}/skip"), label="error recovery skip")
    if skip_result.get("state") != "skipped":
        raise RuntimeError(f"Skip did not transition to skipped state: {skip_result}")

    # Create a HITL task for reject test
    doc_id_reject = f"e2e-error-reject-{int(time.time())}"
    ingest_reject = {
        "doc_id": doc_id_reject,
        "doc_version": "1",
        "text": "[UNFIXABLE]\n\nUnrecoverable content for reject",
        "tenant_id": "local",
        "project_id": "default",
        "is_finalized": True,
        "is_sensitive": False,
        "source_mime_type": "text/plain",
        "metadata": {"source": "e2e_error_reject"},
    }
    _require_ok(client.post(f"{api_url}/rag/ingest/text", json=ingest_reject), label="error recovery ingest reject")

    # Get the task and test reject operation
    task_reject = _require_ok(client.post(f"{api_url}/admin/hitl/tasks/next", params={"assigned_to": "e2e"}), label="error recovery get task reject")
    reject_result = _require_ok(client.post(f"{api_url}/admin/hitl/tasks/{task_reject['id']}/reject"), label="error recovery reject")
    if reject_result.get("state") != "rejected":
        raise RuntimeError(f"Reject did not transition to rejected state: {reject_result}")


def scenario_looking_glass_endpoints(client: httpx.Client, *, api_url: str) -> None:
    """
    Validate Looking Glass diagnostic endpoints for operational visibility.
    Tests that operators can inspect system state.
    """
    # Check Qdrant status
    qdrant_status = _require_ok(client.get(f"{api_url}/admin/looking-glass/qdrant"), label="looking glass qdrant")
    if "collections" not in qdrant_status:
        raise RuntimeError(f"Looking Glass Qdrant missing collections: {qdrant_status}")

    # Check inventory
    inventory = _require_ok(client.get(f"{api_url}/admin/looking-glass/inventory"), label="looking glass inventory")
    if "docs" not in inventory or "chunks" not in inventory:
        raise RuntimeError(f"Looking Glass inventory incomplete: {inventory}")

    # Check docs list
    docs = _require_ok(client.get(f"{api_url}/admin/looking-glass/docs"), label="looking glass docs")
    if not isinstance(docs, list):
        raise RuntimeError(f"Looking Glass docs should return list: {docs}")


def run_scenarios(
    *,
    api_url: str,
    qdrant_url: str,
    collection: str = "atlas_chunks",
    mode: str = "deterministic",
    timeout_s: float = 20.0,
    admin_token: str | None = None,
) -> RunSummary:
    api = api_url.rstrip("/")
    qdrant = qdrant_url.rstrip("/")

    token = admin_token
    if token is None:
        token = os.environ.get("ATLAS_ADMIN_TOKEN")

    headers: dict[str, str] = {}
    if token:
        headers["X-Atlas-Admin-Token"] = token

    results: list[ScenarioResult] = []

    def _run_one(name: str, fn) -> None:
        try:
            fn()
            results.append(ScenarioResult(name=name, ok=True))
        except Exception as e:  # noqa: BLE001
            results.append(ScenarioResult(name=name, ok=False, detail=str(e)))

    with httpx.Client(timeout=60.0, headers=headers) as client:
        wait_for_health(client, api_url=api, timeout_s=timeout_s)

        _run_one("admin_endpoints", lambda: scenario_admin_endpoints(client, api_url=api))
        if results[-1].ok:
            _run_one(
                "config_version_activation",
                lambda: scenario_config_version_activation(client, api_url=api),
            )

        if results[-1].ok and mode == "local_llm":
            _run_one("local_llm_preflight", lambda: scenario_local_llm_preflight(client, api_url=api))
        if results[-1].ok and mode == "local_llm":
            _run_one(
                "activate_local_llm_pipeline_guardrails",
                lambda: activate_local_llm_pipeline_guardrails(client, api_url=api),
            )
        if results[-1].ok and mode == "local_llm":
            _run_one(
                "activate_local_llm_pipeline_models",
                lambda: activate_local_llm_pipeline_models(client, api_url=api),
            )

        dim = qdrant_collection_dim(client, qdrant_url=qdrant, collection=collection)
        if dim is None:
            dim = 768

        if results[-1].ok and mode == "deterministic":
            _run_one(
                "activate_deterministic_pipeline_models",
                lambda: activate_deterministic_pipeline_models(client, api_url=api, dim=dim),
            )

        if results[-1].ok:
            _run_one("rag_roundtrip", lambda: scenario_rag_roundtrip(client, api_url=api))
        if results[-1].ok:
            _run_one("rag_tenant_isolation", lambda: scenario_rag_tenant_isolation(client, api_url=api))
        if results[-1].ok:
            _run_one("rag_finalized_filter", lambda: scenario_rag_finalized_filter(client, api_url=api))
        if results[-1].ok:
            _run_one(
                "rag_idempotent_upsert_count",
                lambda: scenario_rag_idempotent_upsert_count(
                    client, api_url=api, qdrant_url=qdrant, collection=collection
                ),
            )

        if results[-1].ok and mode == "deterministic":
            _run_one("pipeline_refine_then_pass", lambda: scenario_pipeline_refine_then_pass(client, api_url=api))
        if results[-1].ok and mode == "deterministic":
            _run_one(
                "pipeline_hitl_escalation_and_resume",
                lambda: scenario_pipeline_hitl_escalation_and_resume(client, api_url=api),
            )

        # Comprehensive workflow and orchestration tests
        if results[-1].ok:
            _run_one("batch_multi_document_ingest", lambda: scenario_batch_multi_document_ingest(client, api_url=api))
        if results[-1].ok:
            _run_one(
                "workflow_orchestration_validation",
                lambda: scenario_workflow_orchestration_validation(client, api_url=api),
            )
        if results[-1].ok:
            _run_one("error_recovery_validation", lambda: scenario_error_recovery_validation(client, api_url=api))
        if results[-1].ok:
            _run_one("looking_glass_endpoints", lambda: scenario_looking_glass_endpoints(client, api_url=api))

    ok = all(r.ok for r in results)
    return RunSummary(ok=ok, results=results)
