# Project Atlas - Architecture

## Overview

Project Atlas implements a modular, diagnosable document ingestion + retrieval service with a pipeline scaffold.

Source of truth: `TECHNICAL_DESIGN.md` (current reality, explicit scope decisions, and roadmap). `HLD.md` is historical original intent.

## Core Modules

### Pipeline Module (`atlas.pipeline`)

Pipeline scaffold implementing the intended flow: **Ingest → Judge → Refine → Metadata → Embeddings → Chunking → Commit**.

Status note (Feb 2026): the running RAG MVP uses the API path (`/rag/ingest/text`) + chunking + embeddings provider abstraction + Qdrant commit. The judge/refine/metadata nodes exist as scaffolding and are not yet the default ingest path.

- **`ingest.py`** - Document ingestion node
  - Scaffold for ingest orchestration
  - Docling-based parsing is planned (see `TECHNICAL_DESIGN.md` Phase 4)

- **`judge.py`** - Quality grading node
  - Scaffold for quality grading (provider calls + rubric/versioning planned)

- **`refine.py`** - Document refinement node
  - Scaffold for refinement + retry + HITL escalation

- **`metadata.py`** - Tiered metadata generation
  - Scaffold for tiered metadata generation

- **`orchestrator.py`** - Pipeline coordination
  - Manages state transitions between nodes
  - Handles retry logic and HITL escalation
  - Integrates with diagnostics for traceability

- **`state.py`** - State management
  - Defines pipeline nodes and valid transitions
  - Tracks document processing state
  - Manages results from each node

### Data Models (`atlas.schemas`)

Comprehensive data structures with full traceability:

- **`ChunkMetadata`** - Enhanced chunk metadata
  - Hierarchical structure: parent_header_id, sibling_ids, section_path
  - Quality metrics: judge_score, fidelity_flag, confidence_rationale
  - Traceability: embedding_version, judge_version, parse_profile
  - Metadata tiers: tier 1 (local) vs tier 2 (frontier)

- **`DocumentIngestState`** - Pipeline state
  - Current node and processing status
  - Quality metrics (mean_judge_score, chunks_finalized, etc.)
  - Error tracking with structured codes
  - Docling JSON ground truth storage

- **Pipeline Results** - Structured outputs
  - `JudgeResult`: score, rationale, version, refinement decision
  - `RefineResult`: refined markdown, improvements made, success flag
  - `MetadataResult`: tags, tier used, model info

- **`HITLTask`** - Human-in-the-Loop tasks
  - Priority calculation: (10 - judge_score) * sensitivity_multiplier
  - Before/after markdown for review
  - Status tracking and assignment

### Diagnostics Module (`atlas.diagnostics`)

Structured error handling and performance tracking:

- **Error Codes** - Structured enum for all error types
  - DOC_PARSE_TIMEOUT, VLM_OCR_FAIL, JUDGE_INVALID_SCORE, etc.
  - Enables precise error tracking and debugging

- **Trace Levels** - Configurable logging depth
  - NONE, BASIC, DETAILED, FULL
  - FULL captures intermediate prompts/responses

- **Performance Metrics**
  - Operation duration tracking
  - Success/failure rates
  - Context manager for automatic metrics: `with diagnostics.trace_operation(...)`

- **Event Logging**
  - Structured events with timestamp, component, context
  - Separate channels for errors, warnings, info
  - Aggregated summaries for monitoring

### Concurrency Management (`atlas.concurrency`)

Resource management and task coordination:

- **ConcurrencyGuard** - Heavy task limiting
  - Semaphore for vLLM with concurrency = 1
  - Queue depth tracking
  - Automatic metric logging

- **Resource Monitoring**
  - VRAM threshold checking (default: 92%)
  - Queue depth threshold (default: 2)
  - Frontier fallback decision logic

- **Privacy Guard**
  - Default is_sensitive: true
  - API routing requires explicit override
  - Enforced per-tenant

### HITL Management (`atlas.hitl`)

Human-in-the-Loop workflow:

- **Priority Queue**
  - Formula: (10 - judge_score) * sensitivity_multiplier
  - High-sensitivity + Low Score = Top priority
  - Automatic sorting on task creation

- **Task Management**
  - Create, assign, complete, skip operations
  - Before/after markdown tracking
  - Reason for edit documentation

- **Integration surface** (scaffold)
  - Current: in-memory queue semantics
  - Dify integration remains optional/experimental
  - Primary direction is a purpose-built console (“Control Center”) per `TECHNICAL_DESIGN.md`

### Enhanced Chunking (`atlas.rag.chunking`)

Heading-aware hierarchical chunking:

- **Hierarchical Structure**
  - Extract markdown headings (# through ######)
  - Build section hierarchy
  - Track section_path: ["Chapter 1", "Thermal Dynamics"]

- **Relationships**
  - parent_header_id: Links to parent section
  - sibling_ids: Related chunks in same section
  - Enables context-aware retrieval

- **Smart Splitting**
  - Respect heading boundaries
  - Preserve section context
  - Handle oversized paragraphs

## Design Principles

### Modularity
- Clear separation of concerns
- Each module has single responsibility
- Pluggable components (e.g., swap LLM providers)

### Traceability
- Every chunk stores: judge_version, embedding_version, parse_profile
- Pipeline state fully tracked
- Structured error codes with context

### Diagnosability
- Comprehensive logging at multiple levels
- Performance metrics for all operations
- Structured events for monitoring

### Best Practices
- Type hints throughout
- Dataclasses for immutable data
- Context managers for resource cleanup
- Comprehensive docstrings
- Unit tests for all modules

## Testing

Current automated coverage:
- Schema creation and validation
- Diagnostics and error handling
- Pipeline state transitions
- HITL priority calculations
- Admin and RAG endpoints
- Config management
- Qdrant integration

Status (as of Feb 2026):
- Unit/breadcrumb tests: 49 passing
- Integration tests: 1 passing (requires Docker Qdrant)

Run tests:
```bash
pytest -q                    # All tests
pytest -m integration        # Integration tests only
```

## Next Steps

The current implementation provides a solid scaffold with:
- ✅ All core data models
- ✅ Pipeline node structure
- ✅ Diagnostics and concurrency management
- ✅ HITL workflow
- ✅ Enhanced chunking

Still needed for full HLD implementation:
- 🔄 Docling integration for PDF/Office parsing
- 🔄 Actual LLM calls in judge/refine nodes
- 🔄 Embeddings node implementation
- 🔄 Commit node with vector store integration
- 🔄 Durable HITL model + operator UX (Dify optional; purpose-built console planned)
- 🔄 Repo “Looking Glass” (corpus inspection) API/UX per `TECHNICAL_DESIGN.md`
- 🔄 Retrieval upgrades (hybrid/rerank) are explicitly deferred unless a measured failure mode requires them
- 🔄 Semantic cache implementation
