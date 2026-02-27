# Atlas Knowledge-Base Console — UI Layout & Information Architecture

> **Design north-star:** A domain expert who has never seen the Atlas backend
> should be able to create a knowledge base, upload documents, fix flagged
> content, and export — all without training.

This document describes **the current implemented state** of the UI: how it
is arranged, what each area is responsible for, which controls exist, their
field types, and their session-state keys. It is kept in sync with
`ui/app.py`, `ui/theme.py`, `ui/components.py`, and `ui/styles.py`.

---

## 1) Design Principles

| # | Principle | Implication |
|---|-----------|-------------|
| 1 | **Knowledge-base mental model** | The user is *building and maintaining a knowledge base*, not operating infrastructure. Language, ordering, and defaults reflect this. |
| 2 | **Front-load guidance** | Every screen tells the user what it is for (one-sentence explainer) and what to do next (primary action). |
| 3 | **Progressive disclosure** | Show only what is needed for the main task; tuck power-user and admin controls into clearly labelled expanders. |
| 4 | **Translate, don't expose** | Backend concepts (tenant, corpus, finalized, HITL) are renamed or wrapped with plain-language labels and help text. |
| 5 | **One thing at a time** | Review tasks are presented as an inbox, not a query builder. Export is a single guided card. |
| 6 | **Safe by default** | Destructive and admin-only controls live behind explicit boundaries with warning copy. |
| 7 | **One primary action per screen** | Only one primary (filled blue) button per visible screen. All others are secondary (outline) or danger (red). |
| 8 | **Friendly errors** | User-facing errors show plain-language messages with raw HTTP details tucked into an expander. |

---

## 2) Terminology Map (Backend → UI)

| Backend term | User-facing label | `theme.py` constant |
|-------------|-------------------|----------------------|
| Tenant | **Workspace** | `LABEL_WORKSPACE` |
| Project | **Project** | `LABEL_PROJECT` |
| Corpus | **Collection** | `LABEL_COLLECTION` |
| `is_finalized` | **Searchable** / **Include in search results** | `LABEL_SEARCHABLE`, `LABEL_MAKE_SEARCH` |
| `is_sensitive` | **Sensitive** | `LABEL_SENSITIVE` |
| HITL task | **Review task** / "Flagged document" | — |
| `doc_version` | **Version** | — |
| `top_k` | **Max results** | — |
| Active doc version | **Version used for answers** | `LABEL_VERSION_ACTIVE` |
| `fidelity_mode` | **Result quality** (Verified only / Include partially verified / Show everything) | — |

---

## 3) Global UI Structure

### 3.1 Page shell

- Page title: **"Atlas Operator Console"**, layout `wide`.
- Page header: `components.page_header("Project Atlas", subtitle="Knowledge base management console")`.
- Sidebar always visible; tabs rendered in the main content area.

### 3.2 Tab Strip (6 tabs)

```
[Home] [Upload] [My Collection] [Search] [Review]
```

Plus a token-gated tab appended when an admin token is present:

```
[Home] [Upload] [My Collection] [Search] [Review] [Admin]
```

Tab labels come from `theme.py` constants: `TAB_HOME`, `TAB_UPLOAD`,
`TAB_LIBRARY` (which is "My Collection"), `TAB_SEARCH`, `TAB_REVIEW`, `TAB_ADMIN`.

Tab index mapping:

| Index | Label | Requires admin token |
|-------|-------|---------------------|
| 0 | Home | No |
| 1 | Upload | No (Processing history section requires admin) |
| 2 | My Collection | Yes (auth gate shown otherwise) |
| 3 | Search | No |
| 4 | Review | Yes (auth gate shown otherwise) |
| 5 | Admin | Only visible when token is set |

### 3.3 Tab Consolidation History

The following consolidations were made to reduce cognitive load:

- **Library + Versions & Export → My Collection (index 2):** Document browsing, version control, per-document export, and collection-wide export all live in one place.
- **History → Upload (index 1):** Processing history is now an expandable section at the bottom of Upload, since users check run status right after uploading.
- **Tab count reduced** from 8 (Home, Upload, Library, Search, Review, Versions & Export, History, Admin) to 6.

---

## 4) Sidebar — Context Rail

The sidebar is a **light context rail**. All destructive / group-management
admin controls live in the dedicated Admin tab. The sidebar only handles
connection, scope selection, and status.

```
┌──────────────────────────────────────────┐
│  ▸ Connection  (expander, auto-collapses) │
│    Atlas URL              [text input]    │
│    Token                [password input]  │
│                                           │
│  Workspace      [selectbox OR text input] │
│  Project        [selectbox OR text input] │
│  Collection     [selectbox OR text input] │
│    workspace-banner (bold ws / proj / col) │
│                                           │
│  [Test connection]        [button]        │
│                                           │
│  ● Connected to Atlas     (status pill)   │
│  ● Admin access           (status pill)   │
│                                           │
│  📖 Viewer mode notice (when no token)    │
└──────────────────────────────────────────┘
```

**Scope cascade:** Workspace → Project → Collection. Changing a parent
scope resets child dropdowns and clears cached data (`runs_cache`,
`hitl_tasks`, `hitl_current`, `lib_docs`) to prevent cross-scope data leaks.

#### Auto-connect (A1)

When the page first loads and an admin token is present (env var
`ATLAS_ADMIN_TOKEN`), the sidebar automatically fires a health check and
admin check, stores the results, and collapses the Connection expander.
This removes the need for the user to click "Test connection" on every reload.

#### Session-state keys

| Key | Type | Purpose |
|-----|------|---------|
| `health_status` | tuple | `(status_code, json_body, raw_text)` from `/health` |
| `admin_status` | tuple | same shape, from `/admin/ping` |
| `api_url` | str | Persisted Atlas URL |
| `admin_token` | str | Persisted admin token |
| `_auto_connected` | bool | Guard — auto-connect fires once |
| `_prev_scope` | tuple | `(tenant_id, project_id, corpus_id)` — used to detect scope changes and invalidate cached data |

---

## 5) Tab 0 — Home

**Purpose:** Onboarding checklist for first-time and returning users.

```
┌─ hero card ──────────────────────────────────────┐
│  Getting started                                  │
│  Complete these steps to set up your collection.  │
│                                                   │
│  ✓ 1. Connect to Atlas                           │
│       [guidance if not done]                      │
│  ✓ 2. Choose a workspace                         │
│  ○ 3. Upload your first document                 │
│       → Navigate to the Upload tab                │
│  ○ 4. Search your collection                     │
│       → Navigate to the Search tab                │
│  ○ 5. Review flagged content                     │
│       → Navigate to the Review tab                │
└──────────────────────────────────────────────────┘
```

Each unchecked item shows a contextual `st.info()` or `st.caption()` tip
pointing the user to the correct tab. Checked items are derived from
session-state flags (`health_status`, `last_doc_id`, `last_query`,
`hitl_last_action`).

---

## 6) Tab 1 — Upload

**Purpose:** Add documents into the collection via file upload or text paste.

### 6.1 Source radio

```
(•) Upload file   ( ) Write or paste content
```

The label "Paste text" was renamed to "Write or paste content" for clarity.

### 6.2 File upload mode

```
┌─ hero card ───────────────────────────────────────┐
│  Add a document                                    │
│  [file uploader]                                   │
│  Document name         [text input]                │
│  ☐ Include in search results   (checkbox)          │
│  ☐ Sensitive                   (checkbox)          │
│                                                    │
│  ▸ Advanced options (expander)                     │
│    Document ID         [text input]                │
│    Document version    [text input, default "1"]   │
│    MIME type override  [text input, optional]       │
│                                                    │
│  [Upload and index]   (PRIMARY button)             │
└───────────────────────────────────────────────────┘
```

### 6.3 Text upload mode

Same card structure but with a text area (height `TEXT_AREA_LG`) instead of
file uploader. Includes the same **☐ Include in search results** and
**☐ Sensitive** checkboxes as file upload mode.

### 6.4 Upload result card (A2)

On success, a **structured result card** replaces the raw JSON dump:

```
┌─ success/warning/error ──────────────────────────┐
│  📄 document-name                                 │
│  ID: abc123   │   5 chunks                        │
│  Searchable: Yes / Will be after review / No      │
│  Status: Ready / Sent for review / Processing     │
│  [error message if any]                           │
│                                                   │
│  ▸ Full response (JSON)                           │
└──────────────────────────────────────────────────┘
```

Component: `components.ingest_result_card(doc_name, doc_id, chunks,
searchable, paused_for_review, error_message)`.

### 6.5 Processing history (B2 — absorbed from History tab)

Below the upload form, a collapsible expander shows recent pipeline runs.
Requires admin token.

```
─────────────────────────────────────────────────
▸ Processing history  (expander, collapsed by default)
  Processing runs — Recent ingest and pipeline runs
  Max rows  [number_input]   [Refresh]

  ┌─ runs table ─────────────────────────────────┐
  │ run_id | status | doc_id | version | updated │
  └──────────────────────────────────────────────┘

  Run details — select a run to inspect
  Select run  [selectbox]
  [Load details]  (PRIMARY button)
  → run_detail_card (steps, artifacts)
```

---

## 7) Tab 2 — My Collection

**Purpose:** Browse, manage, version-control, and export documents.
Requires admin token (auth gate shown otherwise).

Combines the former **Library**, **Versions & Export** tabs.

### 7.1 Collection overview

```
┌─ card ── Collection overview ──────────────────┐
│  Documents in {corpus_id}                       │
│  [Filter]          ☐ Searchable only  [Refresh] │
│                                                  │
│  ┌ stats strip ─────────────────────────────┐   │
│  │ Documents: 42  Searchable: 38  Pending: 4│   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### 7.2 Documents table (data_editor)

Columns: Select (checkbox), Document ID, Collection, Ver, Searchable,
Sensitive, Type (MIME), Created. All columns read-only except Select.

### 7.3 Chunk viewer (expander)

```
▸ View chunks
  Document ID to inspect  [text input]
  [Load chunks]
  → per-chunk display: index, version, finalized, text preview
```

### 7.4 Selected actions

When rows are checked:

```
┌─ card ── N document(s) selected ──────────────┐
│  ▾ Export selected            ▸ Delete selected │
│  Format: (•) Full (•) Lean   ⚠ Type CONFIRM   │
│  [Generate export]            [Delete N docs]   │
│  → download button            → rerun           │
└────────────────────────────────────────────────┘
```

### 7.5 Version control (B1 — absorbed from Versions tab)

```
┌─ card ── Version used for answers ─────────────┐
│  Document ID    [text input]                    │
│  Set version    [text input, default "1"]       │
│                                                  │
│  [Show current version] (secondary)              │
│  [Set version]          (secondary)              │
│  → JSON response or success message              │
└─────────────────────────────────────────────────┘
```

### 7.6 Collection export (B1 — absorbed from Versions tab)

```
┌─ card ── Collection export ────────────────────┐
│  Format: (•) Full package  (•) Markdown only   │
│  ▸ Advanced export options                      │
│    Max documents  [number_input, default 2000]  │
│                                                  │
│  [Generate collection export]  (PRIMARY)         │
│  → download button                               │
└─────────────────────────────────────────────────┘
```

---

## 8) Tab 3 — Search

**Purpose:** Query the collection and inspect search results.

### 8.1 Query area

```
┌─ hero card ── Ask a question ──────────────────┐
│  [text input: "What are you looking for?"]      │
│  help: "Enter a natural language question..."   │
│                                                  │
│  Max results [number_input]                      │
│  Result quality [selectbox]:                     │
│    - Verified only                               │
│    - Include partially verified                  │
│    - Show everything                             │
│                                                  │
│  [Search]  (PRIMARY button)                      │
└─────────────────────────────────────────────────┘
```

The **Result quality** dropdown maps to the backend `fidelity_mode`:
- "Verified only" → `"verified"`
- "Include partially verified" → `"verified+partial"`
- "Show everything" → `"all"`

### 8.2 Results

Each result is rendered as a `search_hit_card`:

```
▸ #1 — source-filename.pdf
  "Relevant text snippet..."
  Source: file.pdf   Version: 1   Chunk: 3   Score: 0.847   Quality: Verified
  ▸ Full payload (JSON)
```

Metrics shown: **Source** (filename or doc_id), **Version**, **Chunk** index,
**Score** (float to 3 decimal places), **Quality** (human-readable fidelity
flag: Verified, Partially verified, Needs review).

### 8.3 Error handling

Search errors use `components.friendly_error()` — a plain-language message
with the HTTP status code and raw response tucked into a "Technical details"
expander.

---

## 9) Tab 4 — Review

**Purpose:** Human-in-the-loop inbox for reviewing documents flagged by the
pipeline. Requires admin token.

### 9.1 Inbox

```
┌─ card ───────────────────────────────────────────┐
│  Review queue                                     │
│  N tasks pending for this workspace               │
│  [Start reviewing]  (PRIMARY button)              │
└──────────────────────────────────────────────────┘
```

### 9.2 Current review task (elevated card)

```
┌─ elevated card ── Review task #{id} ─────────────┐
│  Doc: {doc_id}  Version: {doc_version}            │
│  Chunk: {chunk_id}                                │
│                                                    │
│  ⚠ Flagged for review: low quality score 2/5     │
│    on a sensitive document.                        │
│  (derived by _hitl_flag_reason() from judge_score, │
│   is_sensitive, meta.source)                       │
│                                                    │
│  ▸ Judge & refine context (expander)              │
│    Per-dimension scores (colour-coded):            │
│      Faithfulness: 5 🟢  Formatting: 2 🔴         │
│      Cohesion: 4 🟢  Hallucination Risk: 5 🟢     │
│    Rationale: [full text]                          │
│    Score history: [3, 2, 3]                        │
│    Refine attempts: 2 (of 3 max)                  │
│    Last improvements: [list]                       │
│                                                    │
│  ┌─ Before (rendered markdown) ─┬─ After (edit) ─┐│
│  │  st.markdown(before_md)      │ st.text_area   ││
│  │  (read-only, formatted)      │ (editable)     ││
│  └──────────────────────────────┴────────────────┘│
│                                                    │
│  Reason for edit   [text_input]                   │
│                                                    │
│  [Approve and continue]  (PRIMARY)                │
│  Skip reason  [selectbox]   [Skip]  (secondary)  │
│                                                    │
│  ▸ Full task payload (JSON)                       │
└──────────────────────────────────────────────────┘
```

#### Rich HITL context (v0.7.0)

When the HITL task contains `meta.judge_sub_scores`, a collapsible panel
shows:
- **Per-dimension scores** with colour coding (🟢 ≥ 4, 🟡 = 3, 🔴 ≤ 2)
- **Judge rationale** (full text from per-dimension feedback)
- **Score history** (list of all judge scores across refine iterations)
- **Refine attempt counts** (successful retries + total attempts out of max)
- **Last improvements** made by the refine node

This gives operators full context on *why* the document was escalated and
*what the pipeline already tried*.

#### Approve and continue (A4)

A single primary button that:
1. POSTs `complete` with `after_md` and reason
2. Auto-resumes the pipeline (`POST .../resume`, best-effort)
3. **Resume status check** — if the resume returns a non-success status
   (e.g., max resumes reached), a warning is shown instead of a false
   success message
4. Claims the next task (`POST .../tasks/next`)
5. Reruns the page

Replaces the old "Save review" + "Save and resume indexing" two-button pattern.

#### Skip with reason

Skip dropdown options: "Not sure", "Looks fine to me", "Needs someone else",
"Other". POSTs to `/admin/hitl/tasks/{id}/skip` with `{"reason": ...}`.

#### Flagging reason (A3)

`_hitl_flag_reason(task)` produces a human-readable string:
- `judge_score ≤ 2` → "low quality score N/5"
- `judge_score ≤ 3` → "borderline quality score N/5"
- `is_sensitive` → "on a sensitive document"
- `meta.source` → "via {source}"
- Falls back to "This chunk was flagged for human review."

### 9.3 Full queue (expander)

Shows all tasks with status filter (Pending, Completed, Skipped, All).

---

## 10) Tab 5 — Admin

**Purpose:** Advanced admin operations. Only visible when admin token is set.
Uses nested sub-tabs to eliminate deep scrolling and separate destructive
operations from day-to-day tools.

### 10.0 Sub-tab structure

```
Admin tab
├── [Health & Metrics]     ← pipeline metrics + diagnostics log
├── [Cleanup & Feedback]   ← rules viewer + feedback form + AI suggestion
├── [Groups]               ← workspace/project/collection management
└── [Danger Zone]          ← DB reset, config restore, corpus import
```

The `tab_header()` with scope strip renders once above the sub-tabs, not
repeated inside each sub-tab.

### 10.1 Health & Metrics (sub-tab 0)

**Card 1 — Pipeline health** (hero)
- Load metrics button (`primary_button`)
- 4-column metric strip: Runs, Success rate, HITL tasks, Feedback items
- Detail expander with full metrics JSON

**Card 2 — Session diagnostics**
- Event count metric
- Clear log + Download JSON buttons
- Inline event table (replaces the former global diagnostics panel)
- Table columns: ts, type, label, method, status, elapsed_ms, url, error

### 10.2 Cleanup & Feedback (sub-tab 1)

**Card 1 — Active cleanup rules**
- Rules loaded from `/admin/config/effective` → `pipeline.cleanup_rules`
- Each rule shown in a collapsed expander with a **Remove** `danger_button`
- Source indicator: "DB config version" or "pipeline.yaml (no DB override)"
- Remove calls `DELETE /admin/cleanup-rules/{name}` → new config version

**Card 2 — Quality feedback**
- Document ID, Category selectbox, Comment text area
- Submit button (`secondary_button`)
- Post-submit copy explains feedback is tracked for pattern analysis and does
  not automatically change processing
- Feedback overview sub-section (`card_section`): load category counts

**Card 3 — Suggest a cleanup rule**
- Sample markdown text area, Observed issues text area
- Suggest button (`primary_button`)
- Displays suggested YAML + rationale + validation warnings
- **Apply rule to live config** button (`primary_button`) — calls
  `POST /admin/cleanup-rules/apply` to push into DB config version
- Copy-to-file caption for operators who prefer YAML-based config

### 10.3 Groups (sub-tab 2)

**Card 1 — Group management**
- Refresh button, 3-column metric strip (Workspaces, Projects, Collections)
- "View all groups" expander with nested Workspaces/Projects/Collections tabs
- "Create new" sub-section (`card_section`): Type selectbox, ID, Display name,
  Create button (`secondary_button`)
- **Hierarchy guidance**: when creating a Project or Collection, if the
  required parent entity (workspace for project, workspace+project for
  collection) does not exist, an info message explains what to create first
  and the Create button is disabled

### 10.4 Danger Zone (sub-tab 3)

**Card 1 — Database reset**
- Warning banner explaining destructive consequences
- Type RESET confirmation field
- 3 checkboxes: Reset Postgres, Clear Qdrant, Clear artifacts
- Reset button (`danger_button`, disabled until confirmation matches)
- No `primary_button` — Danger Zone uses only `danger_button`

**Card 2 — Restore stock config**
- Info banner explaining the restore action
- 2 checkboxes: Restore pipeline.yaml, Restore models.yaml
- Type RESTORE confirmation field
- Restore button (`danger_button`, disabled until confirmation matches)
- Calls `POST /admin/config/restore-stock`

**Card 3 — Collection import**
- ZIP file uploader
- Import button (`secondary_button`)

### 10.5 Sub-tab design rules

- Each sub-tab independently follows the card pattern with `card_header()` on
  every card.
- The one-primary-button-per-tab rule applies **per sub-tab**.
- Sub-tabs whose sole purpose is destructive operations have **zero**
  `primary_button()` calls.

---

## 11) Component Library (`components.py`)

All reusable components:

| Component | Description |
|-----------|-------------|
| `card(hero, elevated)` | Context-managed div with CSS class |
| `card_header(title, caption)` | Styled h3 + caption inside a card |
| `tab_header(title, copy, workspace, collection, project)` | Page-level heading + scope strip |
| `page_header(title, subtitle)` | Top-level page title |
| `detail_expander(label, data)` | Collapsed JSON viewer |
| `friendly_error(msg, status_code, raw_text)` | User-friendly error + technical details expander |
| `ingest_result(title, detail)` | Success notification |
| `ingest_result_card(...)` | Structured upload result card (A2) |
| `primary_button(label, disabled, key)` | Filled blue action button |
| `secondary_button(label, disabled, key)` | Outline/ghost button |
| `danger_button(label, disabled, key)` | Red destructive button |
| `status_pill(label, ok, detail)` | Green/red status indicator |
| `stats_strip(**kv)` | Inline stat bar (key-value pairs) |
| `data_table(rows)` | Simple table from list of dicts |
| `search_hit_card(index, title, snippet, metrics, raw)` | Search result card |
| `run_detail_card(run, node_runs, artifacts)` | Pipeline run viewer |
| `checklist_item(done, prefix, label, help)` | Home checklist row |
| `auth_gate(message)` | "Admin required" placeholder |
| `empty_state(message, *, button_label, button_key)` | Empty content placeholder (optional nav button) |
| `card_section(title, caption, *, help)` | Sub-section heading inside a card |
| `danger_zone(...)` | Destructive-action expander |
| `scope_strip(workspace, collection, project)` | Breadcrumb-style context line |

---

## 12) Design Tokens (`theme.py`)

### Colours

| Token | Value | Usage |
|-------|-------|-------|
| `PRIMARY` | `#0068C9` | Primary actions, active tab, links |
| `PRIMARY_DARK` | `#0054A3` | Primary hover state |
| `DANGER` | `#D9534F` | Destructive actions |
| `SUCCESS` | `#28A745` | Positive states, verified quality |
| `MUTED` | `#6C757D` | Labels, secondary text |
| `MUTED_LIGHT` | `#ADB5BD` | Borders, disabled text |
| `BORDER` | `#E8EAED` | Card/input borders |
| `BG_SURFACE` | `#FAFBFC` | Card backgrounds |
| `BG_ALT` | `#F5F7FA` | Alternating backgrounds |
| `BG_PRIMARY_TINT` | `#F0F6FF` | Hero card gradient end |
| `TEXT_PRIMARY` | `#1A1D23` | Headings, body text |
| `TEXT_SECONDARY` | `#4A5568` | Card captions, sidebar |

### Layout

| Token | Value |
|-------|-------|
| `COL_HALF` | `[1, 1]` |
| `COL_THIRDS` | `[1, 1, 1]` |
| `COL_QUARTERS` | `[1, 1, 1, 1]` |
| `COL_MAIN_ASIDE` | `[3, 1]` |
| `MAX_SNIPPET_CHARS` | `280` |
| `MAX_DIAG_ROWS` | `50` |

---

## 13) CSS Architecture (`styles.py`)

Single injection via `inject_styles()` at app startup. Key layers:

1. **Global layout:** `max-width: 1080px`, tightened padding
2. **Typography:** h1 1.5rem / h2-h3 1.1rem / h4 0.88rem / captions 0.8rem
3. **Tab bar:** 2px bottom border, active tab in primary blue
4. **Cards:** 3 variants — `.atlas-card`, `.atlas-card-elevated`, `.atlas-card-hero`
5. **Buttons:** 4 styles — default, `.atlas-primary-action`, `.atlas-secondary-btn`, `.atlas-danger-btn`
6. **Inputs:** Rounded 8px, focus glow in primary blue
7. **Data frames:** Rounded 10px with subtle shadow
8. **Search hits:** `.atlas-search-hit` with elevated expander
9. **Card sections:** `.atlas-card-section` sub-heading with top border
10. **Quality badges:** `.atlas-quality-verified` (green), `.atlas-quality-partial` (amber), `.atlas-quality-review` (red)
11. **Animations:** Tab panel fade-in (`atlas-fade-in`, 0.18s)
12. **Responsive:** Tighter padding, flex-wrap at `max-width: 768px`
13. **Download buttons:** Primary-outline style with hover fill

---

## 14) Error Handling Pattern

All user-facing errors follow the `components.friendly_error()` pattern:

```python
components.friendly_error(
    "The file could not be uploaded. Check that ...",
    status_code=resp.status_code,
    raw_text=resp.text,
)
```

This shows:
1. A plain-language `st.error()` message
2. A collapsed "Technical details" expander with HTTP status + raw body

Applied in: file upload, text upload, search, review save, review claim.

---

## 15) Session-State Key Index

| Key | Type | Set by | Purpose |
|-----|------|--------|---------|
| `health_status` | tuple | sidebar | `(code, json, raw)` from `/health` |
| `admin_status` | tuple | sidebar | admin ping result |
| `api_url` | str | sidebar | Base API URL |
| `admin_token` | str | sidebar | Admin bearer token |
| `_auto_connected` | bool | sidebar | Auto-connect guard |
| `group_registry` | dict | sidebar | Loaded workspace/project/collection options |
| `last_query` | str | Search tab | Persisted query text |
| `last_doc_id` | str | Upload tab | Last uploaded doc ID |
| `last_doc_name` | str | Upload tab | Last uploaded doc name |
| `last_run_id` | int | Upload > History | Last inspected run ID |
| `lib_docs` | list | My Collection | Cached document list |
| `runs_cache` | tuple | Upload > History | `(code, data, raw)` from runs endpoint |
| `hitl_current` | dict | Review tab | Current HITL task, fetched via `.../tasks/next` |

---

## Appendix: UX Improvements Applied

### Phase A — High-Impact / Low-Risk
- **A1:** Auto-connect on page load when token is present
- **A2:** Structured upload result card (replaces raw JSON)
- **A3:** Plain-language flagging reason in Review
- **A4:** Merged Save/Resume into single "Approve and continue"
- **A5:** Before panel rendered as markdown (not disabled text area)
- **A6:** Actionable Home checklist with tab navigation tips
- **A7:** Primary button audit — demoted 3 secondary actions
- **A8:** Microcopy refinements, fidelity mode dropdown, friendly errors

### Phase B — Structural
- **B1:** Merged Library + Versions & Export → My Collection
- **B2:** Absorbed History into Upload as collapsible section
- **B3:** Tab count reduced from 8 to 6

### Phase C — Polish
- CSS animations (tab fade-in)
- Quality badge colouring
- Responsive breakpoints
- Download button emphasis
- Input focus consistency
- Help tooltips on key inputs

### Phase D — Admin Restructure
- **D1:** Admin tab reorganised into 4 sub-tabs (Health & Metrics, Cleanup &
  Feedback, Groups, Danger Zone)
- **D2:** `card_section()` component + `h4` CSS fix for heading hierarchy
- **D3:** Card capacity and content separation rules updated
- **D4:** Cleanup feedback copy updated for honesty (does not auto-act)
- **D5:** Rules section labelled as read-only with config instructions
- **D6:** Global diagnostics panel inlined into Health & Metrics sub-tab
