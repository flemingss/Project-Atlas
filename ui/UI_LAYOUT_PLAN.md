# Atlas Knowledge-Base Console — UI Layout & Information Architecture Plan

> **Design north-star:** A domain expert who has never seen the Atlas backend
> should be able to create a knowledge base, upload documents, fix flagged
> content, and export — all without training.

This document defines **how the UI is arranged**, what each area is
responsible for, and how we evolve it into a cohesive, low-cognitive-load
operator experience.

It complements `STYLE_GUIDE.md` (visual/style rules) by covering
**structure, navigation, workflow, progressive disclosure, inline guidance,
and presentation hierarchy**.

---

## 1) Design Principles

| # | Principle | Implication |
|---|-----------|-------------|
| 1 | **Knowledge-base mental model** | The user is *building and maintaining a knowledge base*, not operating infrastructure. Language, ordering, and defaults reflect this. |
| 2 | **Front-load guidance** | Every screen tells the user what it is for (one-sentence explainer) and what to do next (primary action). |
| 3 | **Progressive disclosure** | Show only what is needed for the main task; tuck power-user and admin controls into clearly labelled expanders. |
| 4 | **Translate, don't expose** | Backend concepts (tenant, corpus, finalized, HITL) are renamed or wrapped with plain-language labels and help text. |
| 5 | **One thing at a time** | Review tasks are presented as an inbox, not a query builder. Export is a single guided card, not multiple sections. |
| 6 | **Safe by default** | Destructive and admin-only controls live behind explicit boundaries with warning copy. |

---

## 2) Terminology Map (Backend → UI)

| Backend term | User-facing label | Notes |
|-------------|-------------------|-------|
| Tenant | **Workspace** | Top-level organisational unit. |
| Project | **Project** | Kept as-is; already friendly. |
| Corpus | **Collection** | Secondary label in code: `corpus_id`. Shown as "Collection" to the user. |
| `is_finalized` | **Searchable** | "Only searchable documents are used for answering questions." |
| `is_sensitive` | **Sensitive** | "Sensitive documents are excluded from bulk exports." |
| HITL task | **Review task** or "Flagged document" | Avoid "HITL" in any user-facing copy. |
| `doc_version` | **Version** | "Use this when replacing an existing document with a new copy." |
| `top_k` | **Max results** | Plain number. |

All user-facing labels live in `theme.py` constants so a single change propagates everywhere.

---

## 3) Primary User Journeys

### A. First-time setup (guided)
1. Land on **Home** tab → see checklist.
2. Connect to Atlas (API URL + token) → green check.
3. Create a **Workspace** (or select existing).
4. Create a **Collection** inside the workspace.
5. Upload first documents.
6. Check that they're searchable.

### B. Day-to-day content management
1. Select workspace & collection in sidebar.
2. **Upload** new documents.
3. Open **Library** → verify docs appear, preview chunks.
4. Open **Review** when badge shows flagged items.

### C. Content review
1. See "You have N documents that need your review" on **Review** tab.
2. Step through one-at-a-time inbox: view original vs. proposed, accept/edit/skip.
3. Approved docs become searchable automatically.

### D. Export & delivery
1. Open **Export** tab.
2. Pick scope (document / collection / project / workspace), format (full / lean).
3. Download.

---

## 4) Global UI Structure

### 4.1 Sidebar

The sidebar answers three questions:

1. **Am I connected?** — Connection section.
2. **Where am I working?** — Scope section.
3. **Is Atlas healthy?** — Status indicator.

Everything else (diagnostics, DB reset, group management, logs) is hidden
behind an **"Admin tools (advanced)"** expander with a warning banner.

```
┌─────────────────────────────────────┐
│  Connection                         │
│  ─────────                          │
│  Atlas URL              [text input]│
│  Token                [password inp]│
│                                     │
│  Workspace        [selectbox / inp] │
│  Collection       [selectbox / inp] │
│                                     │
│  Status                             │
│  ─────────                          │
│  ● Connected          ● Admin OK    │
│                                     │
│  ▸ Admin tools (advanced)           │
│  ┌─────────────────────────────────┐│
│  │ ⚠ These tools are for Atlas    ││
│  │ administrators. Using them may  ││
│  │ delete data or interrupt        ││
│  │ processing.                     ││
│  │                                 ││
│  │ Project       [selectbox / inp] ││
│  │ Refresh groups        [button]  ││
│  │ Create workspace / project /    ││
│  │   collection          [expand]  ││
│  │ Diagnostics controls  [buttons] ││
│  │ Download logs     [dl button]   ││
│  │ ▸ Danger zone (DB reset)        ││
│  │   [checkboxes + CONFIRM + btn]  ││
│  └─────────────────────────────────┘│
└─────────────────────────────────────┘
```

**Key changes from v1:**
- "Workspace" and "Collection" are **top-level** (not buried under "Advanced Scope").
- "Project" moves into admin expander (most users work within one project).
- All diagnostics / DB ops are inside the admin boundary.
- Warning copy guards the admin expander.

---

### 4.2 Top-level Tabs

```
[Home] [Upload] [Library] [Search] [Review] [Export] [History]
```

| Tab | Purpose (one sentence, shown as caption) |
|-----|------------------------------------------|
| **Home** | "Get started with your knowledge base — connect, create, upload." |
| **Upload** | "Add new documents into this collection so Atlas can prepare them for search." |
| **Library** | "Browse, inspect, and manage documents in your collection." |
| **Search** | "Test whether Atlas can find the right answers from your content." |
| **Review** | "Review and fix documents where automation wasn't confident." |
| **Export** | "Download documents and collections in portable formats." |
| **History** | "Inspect processing runs and pipeline details." |

**Changes from v1:**
- **Home** added as first tab (guided onboarding).
- Tab order follows the user journey: set up → add content → manage → test → review → export → (advanced) history.
- "Versions & Export" split: version management absorbed into Library; export gets its own simple tab.
- "Review" caption explains it in plain English.

---

## 5) Tab Specifications

### 5.0 Home (Getting Started)

**Explainer:** *"Get started with your knowledge base — connect, create, upload."*

This tab is a **guided checklist**. Each step shows its current state
(done / not done) and a single action button.

```
┌──────────────────────────────────────────────────┐
│  Getting Started                                 │
│  ────────────────                                │
│                                                  │
│  ✅ Step 1 — Connect to Atlas                    │
│     Atlas URL and token are set. Connected.      │
│                                                  │
│  ✅ Step 2 — Choose or create a workspace        │
│     Active workspace: "acme-corp"                │
│                                                  │
│  ⬜ Step 3 — Choose or create a collection       │
│     No collection selected.                      │
│     [Create a collection]                        │
│                                                  │
│  ⬜ Step 4 — Upload your first documents         │
│     [Go to Upload →]                             │
│                                                  │
│  ⬜ Step 5 — Review flagged content              │
│     No review tasks yet.                         │
│                                                  │
└──────────────────────────────────────────────────┘
```

Each button pre-fills relevant context and navigates to the correct tab.

Success criteria:
- A new user can complete all five steps without reading any other documentation.
- Returning users see green checks and skip straight to daily tasks.

---

### 5.1 Upload

**Explainer:** *"Add new documents into this collection so Atlas can prepare them for search."*

#### Default view (what everyone sees)

```
┌──────────────────────────────────────────────────┐
│  Upload Documents                                │
│  "Add new documents into this collection so      │
│   Atlas can prepare them for search."            │
│                                                  │
│  ○ File  ○ Paste text         (radio)            │
│                                                  │
│  Document name         [text input]              │
│  [📎 Choose file]                                │
│  ☑ Searchable (recommended)                      │
│                                                  │
│  [Ingest and index for search]     ← button      │
│                                                  │
│  ▸ Advanced options                              │
│  ┌────────────────────────────────────────────┐  │
│  │ Version              [text input]          │  │
│  │ Custom document ID   [checkbox + input]    │  │
│  │ MIME type override    [text input]         │  │
│  │ ☐ Sensitive           [checkbox]           │  │
│  │   ? "Sensitive docs are excluded from      │  │
│  │      bulk exports."                        │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ── After ingest ──                              │
│  ✅ "Document uploaded and is being processed.   │
│      It will appear in Library once chunks are   │
│      ready."                                     │
│  ▸ Details (raw response)                        │
└──────────────────────────────────────────────────┘
```

**Key changes:**
- Only 3 controls visible by default: name, file, searchable.
- Version, custom ID, MIME override, and sensitive flag are under "Advanced options".
- Button label is action-oriented: "Ingest and index for search".
- Post-action summary is plain-language, not raw JSON.

---

### 5.2 Library

**Explainer:** *"Browse, inspect, and manage documents in your collection."*

```
┌──────────────────────────────────────────────────┐
│  Library                                         │
│  "Browse, inspect, and manage documents in your  │
│   collection."                                   │
│                                                  │
│  Filter [text input]  ☐ Searchable only          │
│                                                  │
│  ┌─ Document table ─────────────────────────┐    │
│  │ ☐  Name        Version  Status  Chunks   │    │
│  │ ☑  intro.pdf   v1       ✅      12       │    │
│  │ ☐  faq.md      v2       ✅      8        │    │
│  └──────────────────────────────────────────┘    │
│                                                  │
│  With selected (2):                              │
│    [Export selected]  [Delete selected]           │
│                                                  │
│  ── Chunk preview ──                             │
│  Document to inspect  [text input / select]      │
│  (shows chunk cards)                             │
│                                                  │
│  ── Version management ──                        │
│  ▸ Advanced: set active version                  │
│  ┌────────────────────────────────────────────┐  │
│  │ Document ID   [input]                      │  │
│  │ Set searchable version  [input] [Apply]    │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ── Empty state ──                               │
│  "No documents in this collection yet.           │
│   Go to Upload to add some."                     │
│   [Go to Upload →]                               │
└──────────────────────────────────────────────────┘
```

**Key changes:**
- Version management (was in Versions & Export) is now an advanced section here.
- Empty state includes guidance and a link to Upload.
- "Finalized only" renamed to "Searchable only".

---

### 5.3 Search

**Explainer:** *"Test whether Atlas can find the right answers from your content."*

```
┌──────────────────────────────────────────────────┐
│  Search                                          │
│  "Test whether Atlas can find the right answers  │
│   from your content."                            │
│                                                  │
│  Ask a question      [text input]                │
│  [Search]                                        │
│                                                  │
│  ▸ Advanced options                              │
│  ┌────────────────────────────────────────────┐  │
│  │ Max results    [number input, default 5]   │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ── Results ──                                   │
│  Card 1: chunk text + score + source doc         │
│  Card 2: …                                       │
│                                                  │
│  ── Empty state ──                               │
│  "No results. Try a different question, or check │
│   that you're in the right collection."          │
└──────────────────────────────────────────────────┘
```

**Key changes:**
- "Max results" is advanced (most users want the default).
- Empty-state copy guides the user.

---

### 5.4 Review (Inbox-style)

**Explainer:** *"Review and fix documents where automation wasn't confident."*

The Review tab is redesigned as a **task inbox**, not a filter/query view.

#### Default view: one task at a time

```
┌──────────────────────────────────────────────────┐
│  Review                                          │
│  "Review and fix documents where automation      │
│   wasn't confident."                             │
│                                                  │
│  ┌─ Queue summary ─────────────────────────────┐ │
│  │  You have 3 documents that need your review.│ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  ┌─ Current task ──────────────────────────────┐ │
│  │                                             │ │
│  │  📄 "intro.pdf" in Collection "kb-main"     │ │
│  │     Flagged because: <reason from backend>  │ │
│  │                                             │ │
│  │  ┌─ Original ─────┐  ┌─ Proposed ────────┐ │ │
│  │  │ (original text) │  │ (refined text,    │ │ │
│  │  │                 │  │  editable)        │ │ │
│  │  └─────────────────┘  └──────────────────┘ │ │
│  │                                             │ │
│  │  [Accept and continue]                      │ │
│  │  [Edit and accept]                          │ │
│  │  [Skip for now]                             │ │
│  │                                             │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  ── After action ──                              │
│  ✅ "Your changes were applied. The document is  │
│      now ready for search."                      │
│  Next task loads automatically.                  │
│                                                  │
│  ▸ Show full queue / filters (advanced)          │
│  ┌────────────────────────────────────────────┐  │
│  │ Status filter   [selectbox]                │  │
│  │ Row limit       [number input]             │  │
│  │ Assign to       [text input]               │  │
│  │ (full table view with all tasks)           │  │
│  │ Reason          [text input]               │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ── Empty state ──                               │
│  "No documents need review right now.            │
│   Everything looks good! ✓"                      │
└──────────────────────────────────────────────────┘
```

**Key changes from v1:**
- Top-level is a **count badge** ("You have 3 documents…"), not a filter panel.
- Default flow is one-at-a-time: original vs. proposed, three clear actions.
- Full queue/filters are available but hidden under "Show full queue / filters".
- Post-action summary is plain-language: "Your changes were applied…"
- Button labels are verb-first and self-explanatory.

---

### 5.5 Export

**Explainer:** *"Download documents and collections in portable formats."*

Replaces the old "Versions & Export" tab. Version management moved to Library.

#### Default view: one export card

```
┌──────────────────────────────────────────────────┐
│  Export                                          │
│  "Download documents and collections in          │
│   portable formats."                             │
│                                                  │
│  What do you want to export?                     │
│    ○ Current document                            │
│    ○ Collection                                  │
│    ○ Project                                     │
│    ○ Workspace               (radio)             │
│                                                  │
│  Format                                          │
│    ○ Full (all metadata + chunks)                │
│    ○ Lean (text only)        (radio)             │
│                                                  │
│  [Export and download]       ← button            │
│                                                  │
│  ▸ Advanced filters                              │
│  ┌────────────────────────────────────────────┐  │
│  │ Max documents     [number input]           │  │
│  │ Document ID       [text input]             │  │
│  │ Version           [text input]             │  │
│  │ Import collection ZIP  [file uploader]     │  │
│  │   ? "Upload a previously exported          │  │
│  │      collection package to restore it."    │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ── After export ──                              │
│  ✅ "Exported 14 documents from collection       │
│      'kb-main' in lean format."                  │
│  ▸ Details (raw response)                        │
└──────────────────────────────────────────────────┘
```

**Key changes:**
- Single mental model: scope → format → go.
- Max docs, explicit IDs, version, and import are behind "Advanced filters".
- Post-action summary counts what was exported.

---

### 5.6 History

**Explainer:** *"Inspect processing runs and pipeline details."*

This tab is for power users and is intentionally last. Minimal changes from
v1 — it already serves its audience.

```
┌──────────────────────────────────────────────────┐
│  History                                         │
│  "Inspect processing runs and pipeline details." │
│                                                  │
│  Row limit       [number input]                  │
│  [Load runs]                                     │
│                                                  │
│  ┌─ Runs table ────────────────────────────────┐ │
│  │ Run ID  Started   Status  Nodes  Duration   │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  Select run      [selectbox]                     │
│  (run detail cards, node details, artifacts)     │
│                                                  │
│  ▸ Details (raw)                                 │
└──────────────────────────────────────────────────┘
```

---

## 6) Information Hierarchy & Presentation Rules

1. **One-sentence explainer** at the top of every tab — always visible, conversational tone.
2. **Scope context always visible** in sidebar before any action.
3. **Primary action first** — the main button is visible without scrolling.
4. **Progressive disclosure** — advanced controls in expanders, never inline.
5. **Human-readable summaries first**, raw payloads in collapsed expanders.
6. **Destructive actions isolated** behind explicit confirm gates.
7. **Empty states are helpful** — they explain what's missing and link to the fix.
8. **Spinners + completion feedback** wrapping all long-running operations.

---

## 7) Progressive Disclosure Rules

Every tab splits its controls into two tiers:

| Tier | Visibility | Contains |
|------|-----------|----------|
| **Default** | Always visible | The minimum controls needed to complete the tab's main task. |
| **Advanced** | Inside `▸ Advanced options` expander | Power-user knobs, overrides, filters, imports. |

### Per-tab breakdown

| Tab | Default controls | Advanced controls |
|-----|-----------------|-------------------|
| Upload | Document name, file/paste, Searchable checkbox, Ingest button | Version, Custom ID, MIME override, Sensitive flag |
| Library | Filter, Searchable-only toggle, doc table, chunk preview, export/delete actions | Version management (set active version) |
| Search | Question input, Search button | Max results |
| Review | Queue count, current-task card (original vs. proposed), Accept/Edit/Skip | Status filter, row limit, assign-to, full table view, reason field |
| Export | Scope radio, Format radio, Export button | Max docs, Document ID, Version, Import ZIP |
| History | Row limit, Load runs, runs table, run detail | *(none — already a power-user tab)* |

---

## 8) Inline Guidance Specification

### 8.1 Tooltips / help text

Small "?" icons or `help=` parameter on Streamlit widgets for non-obvious fields:

| Field | Help text |
|-------|-----------|
| Searchable | "Only searchable documents are used when answering questions." |
| Sensitive | "Sensitive documents are excluded from bulk exports." |
| Version | "Use this if you are replacing an existing document with a new copy." |
| Max results | "How many matching chunks to return (default: 5)." |
| Import ZIP | "Upload a previously exported collection package to restore it." |
| Collection | "A collection groups related documents together inside a project." |
| Workspace | "A workspace is the top-level organisational boundary (e.g., a team or department)." |

### 8.2 Empty states

Every data-displaying area has a friendly empty state:

| Area | Empty-state copy |
|------|-----------------|
| Library (no docs) | "No documents in this collection yet. Go to Upload to add some." + [Go to Upload →] button |
| Search (no results) | "No results. Try a different question, or check that you're in the right collection." |
| Review (no tasks) | "No documents need review right now. Everything looks good! ✓" |
| History (no runs) | "No processing runs found. Upload a document to trigger the first one." |
| Home (all done) | "You're all set! Your knowledge base is connected and has content." |

### 8.3 Microcopy on buttons

Buttons use verb-first labels that explain the effect:

| Current label | New label |
|--------------|-----------|
| Submit | **Ingest and index for search** |
| Export | **Export and download** |
| Save | **Accept and continue** |
| Resume | **Edit and accept** |
| Delete | **Delete selected documents** |
| Reset | **Reset database — this cannot be undone** |

### 8.4 Post-action summaries

After every key action, show a plain-language "what happened" message:

| Action | Summary |
|--------|---------|
| Upload | "Document uploaded and is being processed. It will appear in Library once chunks are ready." |
| Review accept | "Your changes were applied. The document is now ready for search." |
| Review skip | "Skipped for now. You can come back to this document later." |
| Export | "Exported N documents from collection 'X' in Y format." |
| Delete | "N documents deleted from collection 'X'." |
| DB reset | "Database has been reset. All data has been removed." |

---

## 9) State Model (UI)

Session state is organised into four namespaced buckets:

| Bucket | Keys (prefix) | Examples |
|--------|--------------|----------|
| **Connection** | `api_*` | `api_url`, `admin_token` |
| **Scope** | `scope_*` | `scope_tenant_id`, `scope_project_id`, `scope_corpus_id` |
| **Workflow** | `upload_*`, `lib_*`, `search_*`, `review_*`, `export_*`, `history_*` | `upload_file_doc_name`, `lib_filter`, `review_current_task` |
| **Admin** | `diag_*`, `admin_*` | `diag_event_log_visible`, `admin_db_reset_confirm` |

Planning rule: never share keys across buckets; keep naming predictable.

---

## 10) Admin Boundary Design

Controls are separated into two zones:

### User zone (always visible)
- Connection (URL + token)
- Workspace selector
- Collection selector
- Status indicators
- All tab content (default tier)

### Admin zone (behind expander with warning)

**Trigger:** `▸ Admin tools (advanced)` expander in sidebar.

**Warning banner inside expander:**
> ⚠ These tools are for Atlas administrators. Using them may delete data or interrupt processing.

**Contents:**
- Project selector (most users don't switch projects)
- Refresh groups button
- Create workspace/project/collection form
- Diagnostics controls
- Download logs
- Danger zone (DB reset): nested expander with checkboxes + typed confirmation + button

**Label rule:** Any button that can destroy data must include the consequence in its label:
- "Reset database — this cannot be undone"
- "Delete selected documents"

---

## 11) Visual Text Blueprint — Sidebar

```
┌─────────────────────────────────────┐
│  Connection                         │
│  ─────────                          │
│  Atlas URL              [text input]│
│  Token                [password inp]│
│                                     │
│  Workspace        [selectbox / inp] │
│  Collection       [selectbox / inp] │
│                                     │
│  Status                             │
│  ─────────                          │
│  ● Connected          ● Admin OK    │
│                                     │
│  ▸ Admin tools (advanced)           │
│    ⚠ Warning banner                 │
│    Project        [selectbox / inp] │
│    Refresh groups        [button]   │
│    ▸ Create new…         [expand]   │
│      Type [selectbox] ID [input]    │
│      Name (optional)     [input]    │
│      [Create]                       │
│    Diagnostics           [buttons]  │
│    Download logs     [dl button]    │
│    ▸ Danger zone                    │
│      [checkboxes + CONFIRM + btn]   │
└─────────────────────────────────────┘
```

---

## 12) Visual Text Blueprint — Tabs

```
[Home] [Upload] [Library] [Search] [Review] [Export] [History]
```

Each tab wireframe is in the corresponding section 5.0–5.6 above.

---

## 13) Field Inventory (Name + Type)

> All user-facing labels use the terminology from Section 2.

| Area | Field Label | State Key / Param | Control | Tier |
|------|-------------|-------------------|---------|------|
| **Sidebar** | | | | |
| Sidebar/Connection | Atlas URL | `api_url` | text input | default |
| Sidebar/Connection | Token | `admin_token` | password input | default |
| Sidebar/Scope | Workspace | `scope_tenant_id` | selectbox / text input | default |
| Sidebar/Scope | Collection | `scope_corpus_id` | selectbox / text input | default |
| Sidebar/Admin | Project | `scope_project_id` | selectbox / text input | admin |
| Sidebar/Admin | Type (create) | `scope_create_kind` | selectbox | admin |
| Sidebar/Admin | ID (create) | `scope_create_id` | text input | admin |
| Sidebar/Admin | Display name | `scope_create_name` | text input | admin |
| **Home** | | | | |
| Home | (checklist steps) | derived from connection + scope state | status indicators + buttons | default |
| **Upload** | | | | |
| Upload | Source | `upload_mode` | radio (File / Paste text) | default |
| Upload | Document name | `upload_file_doc_name` | text input | default |
| Upload | File | (file uploader) | file uploader | default |
| Upload | Searchable | `upload_file_is_finalized` | checkbox (default: checked) | default |
| Upload | Version | `upload_file_doc_version` | text input | advanced |
| Upload | Use custom document ID | `upload_file_use_custom_id` | checkbox | advanced |
| Upload | Document ID | `upload_file_doc_id` | text input | advanced |
| Upload | MIME type override | `upload_file_mime_override` | text input | advanced |
| Upload | Sensitive | `upload_file_is_sensitive` | checkbox | advanced |
| Upload | Content (text mode) | `upload_text_body` | text area | default |
| Upload | Format (text mode) | `upload_text_mime` | selectbox | default |
| **Library** | | | | |
| Library | Filter | `lib_filter` | text input | default |
| Library | Searchable only | `lib_finalized` | checkbox | default |
| Library | Document table | `lib_table` | data editor | default |
| Library | Document to inspect | `lib_chunk_view_id` | text input / select | default |
| Library | Export format (selected) | `lib_sel_fmt` | radio | default |
| Library | Type CONFIRM to delete | `lib_del_confirm` | text input | default |
| Library | Document ID (version mgmt) | `lib_ver_doc_id` | text input | advanced |
| Library | Set searchable version | `lib_ver_new_version` | text input | advanced |
| **Search** | | | | |
| Search | Ask a question | `last_query` | text input | default |
| Search | Max results | `top_k` | number input | advanced |
| **Review** | | | | |
| Review | Queue count | derived | info banner | default |
| Review | Current task card | `review_current_task` | composite card | default |
| Review | Proposed text (editable) | `review_after_md` | text area | default |
| Review | Status filter | `review_status` | selectbox | advanced |
| Review | Row limit | `review_limit` | number input | advanced |
| Review | Assign to | `review_assigned_to` | text input | advanced |
| Review | Reason | `review_reason` | text input | advanced |
| **Export** | | | | |
| Export | What to export | `export_scope` | radio (Document / Collection / Project / Workspace) | default |
| Export | Format | `export_format` | radio (Full / Lean) | default |
| Export | Max documents | `export_max_docs` | number input | advanced |
| Export | Document ID | `export_doc_id` | text input | advanced |
| Export | Version | `export_version` | text input | advanced |
| Export | Import collection ZIP | `export_import_zip` | file uploader | advanced |
| **History** | | | | |
| History | Row limit | `history_runs_limit` | number input | default |
| History | Select run | `history_selected_run_id` | selectbox | default |

---

## 14) Presentation Pattern (Per-tab structure)

Every tab follows this exact visual order:

1. **Section header** — title + one-sentence explainer (always visible).
2. **Default input controls** — the minimum fields for the main task.
3. **Primary action button** — verb-first label, visible without scrolling.
4. **Outcome summary** — plain-language `success` / `warning` / `error`.
5. **Advanced options** — expander, closed by default.
6. **Details** — collapsed expander with raw JSON/response (for debugging).
7. **Empty state** — shown when there's nothing to display; includes guidance copy.

---

## 15) Phased Implementation Roadmap

### Phase 1 — Guided surface (highest priority)
1. Add **Home / Getting Started** tab with checklist + navigation buttons.
2. Rename and soften language: Workspace / Collection / Searchable everywhere.
3. Apply progressive disclosure on Upload, Export, and Review (default vs. advanced).

### Phase 2 — Inbox review
4. Rework **Review** into one-task-at-a-time inbox layout.
5. Push admin/diagnostics/DB reset fully behind "Admin tools (advanced)" with warning.

### Phase 3 — Guidance layer
6. Add one-sentence explainers at top of every tab.
7. Add empty states with guidance copy and navigation links.
8. Add tooltip help text for non-obvious fields.
9. Update button labels to verb-first microcopy.

### Phase 4 — Post-action intelligence
10. Add post-action plain-language summaries.
11. Add review-task badge count in tab label or sidebar.
12. Cross-link tabs (Library → Upload, Home → Review, etc.).

### Phase 5 — Validation
13. Run a hallway usability test: 3 tasks, non-technical user, observe friction.
14. Fix the top 3 friction points found.

---

## 16) Non-goals (for now)

- No additional pages/routes outside current Streamlit single-page app.
- No role-based access control in the UI (admin expander is honor-system).
- No analytics dashboard expansion.
- No visual redesign beyond existing `STYLE_GUIDE.md` design system.

---

## 17) Source of Truth Relationship

| Document | Governs |
|----------|---------|
| `STYLE_GUIDE.md` | Component usage, colour palette, CSS rules, hard coding constraints. |
| `UI_LAYOUT_PLAN.md` (this file) | Layout, IA, workflow architecture, terminology, progressive disclosure, guidance copy. |
| `theme.py` | Tab names, colour constants, layout constants — single source for all labels. |
| `app.py` | Implementation of both documents. |

If conflict exists: this document governs **arrangement, workflow, and language**; `STYLE_GUIDE.md` governs **visual presentation constraints**.
