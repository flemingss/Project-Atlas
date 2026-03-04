# End-to-End Test Guide

This guide documents the comprehensive end-to-end testing strategy for Project Atlas.

## Overview

Project Atlas implements a multi-layered testing approach to ensure system reliability:

1. **Unit Tests** - Fast, isolated tests for individual components
2. **Integration Tests** - Tests that interact with live services (Qdrant, Postgres)
3. **E2E Workflow Tests** - Comprehensive pytest-based workflow validation
4. **E2E Scenario Tests** - Black-box scenario runner against live API

## Test Coverage Matrix

### Unit Tests (pytest)
Located in: `tests/test_*.py`

| Test File | Coverage | Mode |
|-----------|----------|------|
| `test_e2e_workflows.py` | Complete workflow validation | Deterministic |
| `test_rag_endpoints.py` | RAG ingest & search endpoints | Mock Qdrant |
| `test_pipeline_hitl_resume.py` | HITL workflow + resume loop guard | Mock Qdrant |
| `test_pipeline_state.py` | Pipeline state transitions | Deterministic |
| `test_pipeline_nodes.py` | Individual pipeline node logic | Deterministic |
| `test_chunking.py` | Chunking logic | Pure unit |
| `test_chunk_qa.py` | Chunk QA validation | Pure unit |
| `test_hitl.py` | HITL task management | DB-backed |
| `test_hitl_endpoints.py` | HITL API endpoints | Mock |
| `test_config_manager.py` | Config versioning | YAML/DB |
| `test_routing.py` | Pipeline routing decisions | Deterministic |
| `test_routing_layout.py` | Layout-aware routing | Deterministic |
| `test_cleanup.py` | Cleanup rule engine | Pure unit |
| `test_cleanup_rules.py` | Cleanup rule matching | Pure unit |
| `test_cleanup_layout.py` | Layout-specific cleanup | Pure unit |
| `test_cleanup_feedback.py` | Cleanup feedback API | DB-backed |
| `test_cleanup_rules_import_export.py` | Cleanup rules YAML import/export | DB-backed |
| `test_phase_refactors.py` | Phase 8 refactoring (normalize/runner) | Deterministic |
| `test_schemas.py` | Pydantic schema validation | Pure unit |
| `test_admin_endpoints.py` | Admin API endpoints | Mock |
| `test_admin_auth.py` | Admin token auth | Mock |
| `test_admin_db_reset.py` | DB reset endpoint | DB-backed |
| `test_doc_versions_admin.py` | Doc versioning + export | DB-backed |
| `test_corpus_bulk_export_import.py` | Corpus bulk export/import | DB-backed |
| `test_diagnostics.py` | Diagnostics endpoints | Mock |
| `test_model_manager.py` | Model manager lifecycle | Deterministic |
| `test_model_registry.py` | Model registry lookups | Pure unit |
| `test_metrics_aggregation.py` | Metrics aggregation | Pure unit |
| `test_looking_glass_ledger.py` | Looking Glass + run ledger | DB-backed |
| `test_workflow_ledger.py` | Workflow ledger persistence | DB-backed |
| `test_startup_validation.py` | Startup config validation | Pure unit |
| `test_deep_merge.py` | Deep merge utility | Pure unit |
| `test_retry.py` | Retry/backoff logic | Pure unit |
| `test_concurrency.py` | Concurrent ingest safety | Mock |
| `test_docling_ingest.py` | Docling PDF ingest | Mock Docling |
| `test_docling_health.py` | Docling health scoring | Deterministic |
| `test_llm_artifact_stripping.py` | LLM artifact stripping (`<think>`, fences) | Pure unit |
| `test_layout_types.py` | Layout type classification | Pure unit |
| `test_layout_ingest_wiring.py` | Layout parser → IngestNode wiring | Mock |
| `test_postprocess.py` | Post-processing steps | Pure unit |
| `test_rule_suggestion.py` | LLM-assisted rule suggestion | Mock LLM |
| `test_retrieval_eval.py` | Retrieval eval harness | Pure unit |
| `test_page_renderer.py` | PDF→PNG rendering, crop margins, VLM messages | Pure unit |
| `test_vision_plumbing.py` | Multimodal ChatMessage, `<think>` tag stripping | Pure unit |
| `test_integration_qdrant_live.py` | Live Qdrant CRUD | Integration |

**Run unit tests:**
```bash
pytest -q
```

**Run integration tests (requires Docker):**
```bash
docker compose up -d
pytest -m integration -q
```

### E2E Workflow Tests (`test_e2e_workflows.py`)

Comprehensive workflow validation with mocked vector store:

#### Scenarios Covered

1. **Complete Workflow (Ingest → Search)**
   - Test: `test_e2e_complete_workflow_ingest_to_search`
   - Validates: Full pipeline from text ingest through embeddings to search retrieval
   - Mode: Deterministic models

2. **Multi-Document Batch Processing**
   - Test: `test_e2e_multi_document_batch_workflow`
   - Validates: Concurrent document processing and isolation
   - Mode: Deterministic models

3. **Pipeline Refine Loop**
   - Test: `test_e2e_pipeline_refine_loop_workflow`
   - Validates: Automatic refinement when judge score < threshold
   - Mode: Deterministic models (triggers score 3 → refine → score 5)

4. **HITL Escalation Workflow**
   - Test: `test_e2e_hitl_escalation_workflow`
   - Validates: Human-in-the-loop escalation, completion, and resume
   - Mode: Deterministic models

5. **Tenant Isolation (Security)**
   - Test: `test_e2e_tenant_isolation_workflow`
   - Validates: Multi-tenant data isolation
   - Mode: Deterministic models
   - **Critical**: Security boundary test

6. **Finalized Filter**
   - Test: `test_e2e_finalized_filter_workflow`
   - Validates: Draft documents excluded from search
   - Mode: Deterministic models

7. **Config Version Activation**
   - Test: `test_e2e_config_version_activation_affects_pipeline`
   - Validates: Dynamic configuration changes
   - Mode: Deterministic models

8. **Idempotent Ingest**
   - Test: `test_e2e_idempotent_ingest_workflow`
   - Validates: Re-ingesting same content doesn't duplicate
   - Mode: Deterministic models

9. **Admin Health & Diagnostics**
   - Test: `test_e2e_admin_health_and_diagnostics`
   - Validates: Operational monitoring endpoints
   - Mode: N/A (admin endpoints)

**Run E2E workflow tests:**
```bash
pytest tests/test_e2e_workflows.py -v
```

### E2E Scenario Tests (Black-box)

Located in: `src/atlas/e2e/scenarios.py`

Black-box scenario runner that exercises a live Atlas API + Qdrant instance.

#### Deterministic Mode Scenarios

These scenarios use deterministic (mock) LLM providers - safe for CI/CD:

1. **admin_endpoints** - Health, config, reload validation
2. **config_version_activation** - Dynamic config changes
3. **rag_roundtrip** - Ingest text → search retrieval
4. **rag_tenant_isolation** - Multi-tenant isolation verification
5. **rag_finalized_filter** - Non-finalized document filtering
6. **rag_idempotent_upsert_count** - Idempotency validation via Qdrant
7. **pipeline_refine_then_pass** - Refinement loop with deterministic models
8. **pipeline_hitl_escalation_and_resume** - HITL workflow end-to-end
9. **batch_multi_document_ingest** - **NEW** Batch processing validation
10. **workflow_orchestration_validation** - **NEW** Complete pipeline validation
11. **error_recovery_validation** - **NEW** HITL skip/reject operations
12. **looking_glass_endpoints** - **NEW** Operational diagnostics

**Run deterministic scenarios (CI-safe):**
```bash
# Local (requires running API + Qdrant)
python scripts/e2e_scenarios.py --mode deterministic

# Orchestrated (starts services + runs scenarios)
python scripts/e2e_runner.py --mode deterministic

# Dockerized (full stack in containers)
docker compose -f docker-compose.optest.yml --profile deterministic up --abort-on-container-exit
```

#### Local LLM Mode Scenarios

These scenarios use real LLM providers (Ollama, LM Studio) - validates actual AI behavior:

1. **local_llm_preflight** - Verify OpenAI-compatible server connectivity
2. **activate_local_llm_pipeline_guardrails** - Apply stability configs
3. **activate_local_llm_pipeline_models** - Configure real LLM models
4. All deterministic scenarios run with actual embeddings + LLM calls

**Run local LLM scenarios (requires Ollama or LM Studio):**
```bash
# With Ollama (auto-pulls models)
docker compose -f docker-compose.optest.yml --profile local_llm up --abort-on-container-exit

# With LM Studio on host
export ATLAS_OPENAI_BASE_URL=http://host.docker.internal:1234
docker compose -f docker-compose.optest.yml --profile lmstudio up --abort-on-container-exit

# Or locally
export ATLAS_OPENAI_BASE_URL=http://localhost:1234
export ATLAS_E2E_LLM_MODEL=your-model-name
export ATLAS_E2E_EMBED_MODEL=your-embed-model-name
python scripts/e2e_scenarios.py --mode local_llm
```

## Test Execution Strategies

### Fast Feedback (< 30 seconds)
```bash
# Unit tests only
pytest -q --ignore=tests/test_integration_qdrant_live.py
```

### Pre-commit (< 2 minutes)
```bash
# Unit + E2E workflows (mocked)
pytest -q tests/test_e2e_workflows.py
pytest -q --ignore=tests/test_integration_qdrant_live.py
```

### Pre-PR (< 5 minutes)
```bash
# All pytest tests including integration
docker compose up -d
pytest -q
pytest -m integration -q
```

### Full Validation (< 10 minutes)
```bash
# All pytest tests + deterministic scenarios
docker compose up -d
pytest -q
python scripts/e2e_runner.py --mode deterministic
```

### Release Candidate Validation (< 30 minutes)
```bash
# All tests + local LLM scenarios
docker compose up -d
pytest -q
pytest -m integration -q

# Deterministic scenarios
docker compose -f docker-compose.optest.yml --profile deterministic up --abort-on-container-exit

# Local LLM scenarios (with Ollama)
docker compose -f docker-compose.optest.yml --profile local_llm up --abort-on-container-exit
```

## Test Modes Comparison

### Deterministic Mode
- **Purpose**: CI/CD validation, fast feedback
- **LLM Provider**: Mock/deterministic responses
- **Embeddings**: Deterministic vectors (configurable dimension)
- **Speed**: Fast (< 1 minute for all scenarios)
- **Reliability**: 100% reproducible
- **Use When**: CI/CD, development, regression testing

### Local LLM Mode
- **Purpose**: Validate real AI behavior
- **LLM Provider**: Ollama, LM Studio, or any OpenAI-compatible API
- **Embeddings**: Real embeddings from model
- **Speed**: Slower (depends on model size)
- **Reliability**: May vary based on model/temperature
- **Use When**: Pre-release validation, AI quality checks

## Coverage Gaps Addressed

This test suite addresses the following previously identified gaps:

✅ **Complete Workflow Tests**
- Added `test_e2e_workflows.py` with 9 comprehensive workflow tests
- Added 4 new black-box scenarios covering orchestration and error recovery

✅ **Multi-Document Workflows**
- `test_e2e_multi_document_batch_workflow` for batch processing
- `scenario_batch_multi_document_ingest` for API-level batch validation

✅ **LLM Integration Tests**
- Local LLM mode scenarios exercise real embeddings + LLM calls
- Deterministic mode provides fast, reliable baseline

✅ **Error Recovery Scenarios**
- `test_e2e_hitl_escalation_workflow` validates HITL error handling
- `scenario_error_recovery_validation` tests skip/reject operations

✅ **Cross-Component Integration**
- `workflow_orchestration_validation` validates data flow through entire pipeline
- `looking_glass_endpoints` validates operational visibility

## Best Practices

### Writing New E2E Tests

1. **Pytest Workflow Tests** (`tests/test_e2e_workflows.py`):
   - Use for tests that need to validate internal state
   - Mock Qdrant using `_FakeQdrantStore` pattern
   - Keep tests fast and deterministic
   - Focus on workflow logic, not API contracts

2. **Black-box Scenario Tests** (`src/atlas/e2e/scenarios.py`):
   - Use for tests that validate API contracts
   - Exercise live Qdrant when needed
   - Support both deterministic and local_llm modes
   - Include clear error messages with context

### Test Isolation

- Each test should create unique doc_ids (use UUID or timestamp)
- Clean up is NOT required - tests should be additive
- Tenant/project isolation prevents cross-test pollution

### Debugging Failed Tests

1. **Pytest tests**: Use `-v` flag for verbose output
2. **Scenario tests**: Check the detail message in failed results
3. **Looking Glass**: Use `/admin/looking-glass/*` endpoints to inspect state
4. **Logs**: Set `ATLAS_LOG_LEVEL=DEBUG` for detailed logs

## Continuous Integration

### GitHub Actions / CI Pipeline

```yaml
# Fast validation (on every commit)
- run: pytest -q --ignore=tests/test_integration_qdrant_live.py

# Full validation (on PR)
- run: docker compose up -d
- run: pytest -q
- run: docker compose -f docker-compose.optest.yml --profile deterministic up --abort-on-container-exit
```

### Pre-merge Checklist

- [ ] All unit tests pass
- [ ] All E2E workflow tests pass
- [ ] Deterministic scenarios pass
- [ ] Integration tests pass (if touching Qdrant/Postgres)
- [ ] Local LLM scenarios pass (for releases)

## Future Enhancements

Potential areas for additional test coverage:

1. **Performance Tests**: Validate behavior under load
2. **Chaos Tests**: Random failures, network issues, partial outages
3. **Upgrade Tests**: Data migration and backward compatibility
4. **Security Tests**: Penetration testing, fuzzing, auth bypass attempts
5. **UI E2E Tests**: React SPA automation (Playwright)

## Conclusion

This comprehensive test suite ensures Project Atlas maintains high quality through:

- **Fast feedback** via unit tests (< 30s)
- **Workflow validation** via E2E pytest tests (< 2min)
- **Contract validation** via black-box scenarios (< 5min)
- **Real AI validation** via local LLM mode (< 30min)

The multi-layered approach catches issues early while providing confidence in production deployments.
