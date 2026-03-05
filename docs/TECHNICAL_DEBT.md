# Technical Debt & Code Review: Project Atlas

This repository implements **Project Atlas**, a sophisticated RAG (Retrieval-Augmented Generation) system with a multi-stage ingestion pipeline. The codebase is generally high-quality, modern (Python 3.11+, Pydantic v2, SQLAlchemy 2.0), and well-documented.

However, there are specific areas of technical debt and "foot guns" (hidden dangers) that you should address.

### 1. Technical Debt & Refactoring Targets

**A. The "God Object" Controller (`src/atlas/api_admin.py`)**
This file is over 100KB and violates the Single Responsibility Principle. It mixes API routing, database administration (resetting DBs), configuration management (YAML restore), and business logic for HITL (Human-in-the-Loop) tasks.
*   **Refactor:** Split this into domain-specific routers:
    *   `api/admin/tenants.py` (CRUD for tenants/projects)
    *   `api/admin/maintenance.py` (DB reset, orphans)
    *   `api/admin/config.py` (YAML management)

**B. Ingest Backend Complexity (`src/atlas/pipeline/ingest.py`)**
The `IngestNode` class contains complex branching logic to choose between `Docling`, `LayoutPdfParser`, and `VLM` backends (`auto`, `auto_layout`, `vision`, etc.).
*   **Refactor:** Implement a **Strategy Pattern**. Create an abstract `DocumentParser` interface with concrete implementations (`DoclingParser`, `LayoutParser`, `VisionParser`). The `IngestNode` should just select a strategy and call `parse()`.

**C. Regex-Based LLM Cleaning (`src/atlas/pipeline/refine.py`)**
The `strip_llm_artifacts` function uses a large collection of regexes to remove "Sure, here is the text" chatter from LLMs.
*   **Tech Debt:** This is a perpetual maintenance burden. As models change (e.g., DeepSeek vs. Llama 3), they will invent new ways to be chatty.
*   **Fix:** Move this to a dedicated "Output Guardrail" module rather than hiding it inside the Refine node, or switch to structured output (JSON schema) enforcement if the model supports it, though that often degrades prose quality.

### 2. "Foot Guns" (Potential Dangers)

**A. Sectional Refinement Context Loss**
*   **Location:** `src/atlas/pipeline/refine.py` -> `refine_document_sectional`
*   **Risk:** This splits long documents into chunks based on token counts to fit context windows. It processes these chunks *independently*. If a sentence or logical thought straddles a chunk boundary, the refinement model might "hallucinate" a fix or break the flow because it lacks the adjacent context.
*   **Mitigation:** Implement sliding window overlaps or "smart splitting" that only breaks on markdown headers.

**B. Destructive Cleanup Rules**
*   **Location:** `src/atlas/pipeline/cleanup_rules.py`
*   **Risk:**
    *   `_step_merge_hardwrapped`: Assumes line breaks are formatting errors. In poetry, code blocks (if not detected perfectly), or specialized lists, this will destroy data.
    *   `_step_fix_numbered_headings`: Forces markdown header levels (`#`, `##`) to match the text numbering (e.g., "1.1.2" forces H3). If a document uses "1.1" as a top-level title, this logic will bury it in the hierarchy.

**C. Judge Parsing Fragility**
*   **Location:** `src/atlas/pipeline/judge.py`
*   **Risk:** The code relies on string parsing (`key, _, value = line.partition(":"`) to read LLM scores. If the LLM decides to output `Faithfulness: **5**` (bolded) or `Score (Faithfulness): 5`, the parser breaks.
*   **Mitigation:** Use a robust parser that handles common markdown variations (asterisks, extra whitespace) or enforce a tool-call/JSON response format.

### 3. Summary Recommendation

| Priority | Action | Rationale |
| :--- | :--- | :--- |
| **High** | Split `api_admin.py` | The file is becoming unmaintainable and a merge-conflict magnet. |
| **Medium** | Refactor `IngestNode` | Adding a 4th parser type (e.g., Azure Document Intelligence) will make the current `if/else` logic unreadable. |
| **Medium** | Review `cleanup_rules` | Ensure `merge_hardwrapped` is never enabled globally; it should be an opt-in rule per corpus. |
| **Low** | Strict JSON for Judge | Replace regex parsing with Pydantic validation to prevent "silent failures" where scores default to 3.