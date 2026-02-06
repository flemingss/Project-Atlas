# Project Atlas - Architecture

## Overview

Project Atlas implements a modular, diagnosable agentic pipeline for document processing following the High-Level Design (HLD.md).

## Core Modules

### Pipeline Module (`atlas.pipeline`)

Agentic processing pipeline implementing the flow: **Ingest → Judge → Refine → Metadata → Embeddings → Chunking → Commit**

- **`ingest.py`** - Document ingestion node
  - Converts documents to DoclingDocument format
  - Stores full JSON as ground truth
  - Generates Markdown projection for LLM consumption
  - Tracks source_mime_type and parse_profile

- **`judge.py`** - Quality grading node
  - Grades Markdown on 1-5 scale using Llama 3.2 3B
  - Uses explicit few-shot rubric
  - Outputs confidence_rationale
  - Persists judge_version for traceability

- **`refine.py`** - Document refinement node
  - Triggered when Judge Score < 4
  - Uses Llama 3.2 Vision for improvements
  - Max 2 retries before HITL escalation
  - Tags problematic chunks with fidelity_flag

- **`metadata.py`** - Tiered metadata generation
  - Tier 1: Small local model for 90% of chunks
  - Tier 2: Frontier/70B model for technical density or borderline scores (3-4)
  - Cost guardrail: Configurable cap per document

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

- **Dify Integration** (scaffold)
  - Push tasks to Dify API
  - Callback handling for completed reviews
  - Priority-based task routing

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

43 passing tests covering:
- Schema creation and validation
- Diagnostics and error handling
- Pipeline state transitions
- HITL priority calculations
- Admin and RAG endpoints
- Config management
- Qdrant integration

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
- 🔄 Dify API integration
- 🔄 Hybrid search (BM25 + vector)
- 🔄 Semantic cache implementation
