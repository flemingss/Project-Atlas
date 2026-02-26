# Atlas UI Style Guide

A concise reference card for contributors working on the Streamlit operator console.

---

## 1. Colour Palette

| Token            | Hex       | When to use                                  |
|------------------|-----------|----------------------------------------------|
| `PRIMARY`        | `#0068C9` | Links, active states, primary action buttons |
| `PRIMARY_DARK`   | `#0054A3` | Hover state for primary buttons              |
| `DANGER`         | `#D9534F` | Errors, destructive actions, danger buttons  |
| `SUCCESS`        | `#28A745` | Success messages, OK status indicators       |
| `MUTED`          | `#6C757D` | Secondary text, captions, metadata           |
| `MUTED_LIGHT`    | `#ADB5BD` | Borders, dividers, disabled text             |
| `BORDER`         | `#E8EAED` | Card borders, rule lines                     |
| `BG_SURFACE`     | `#FAFBFC` | Card backgrounds                             |
| `BG_ALT`         | `#F5F7FA` | Hero card tint, alternating rows             |
| `BG_PRIMARY_TINT`| `#F0F6FF` | Light blue accent background                 |
| `TEXT_PRIMARY`    | `#1A1D23` | Headings, body text                          |
| `TEXT_SECONDARY`  | `#4A5568` | Subtitles, descriptions                      |

All colour constants live in `ui/theme.py`. **Never hardcode hex values** anywhere else.

---

## 2. Layout Principles

### Page skeleton (locked)

Every tab follows the same top-level structure:

1. **Tab header** -- title + subtitle + scope strip (via `tab_header()`)
2. **Max 3 cards** -- each introduced by `card_header(title, caption)`
3. **One primary action** per tab -- the main thing the user does

### Sidebar (context rail only)

The sidebar is a light context rail. It contains **only**:

- Atlas URL + Token
- Workspace / Collection selector
- Connection and health status pills

All admin operations live in the **Admin tab**, not the sidebar.

### Operator vs Admin separation

- **Operator surfaces** (Upload, Search, Library, etc.) are available to all connected users.
- **Admin surfaces** (Admin tab, version control) require an admin token.
- Use `auth_gate()` to block content when no token is present.
- Use `admin_section()` to visually gate admin-only controls within operator tabs.

---

## 3. Tab Inventory

| Tab               | Index | Token constant      | Purpose                                      |
|-------------------|-------|---------------------|----------------------------------------------|
| Home              | 0     | `TAB_HOME`          | Onboarding checklist, getting started         |
| Upload            | 1     | `TAB_UPLOAD`        | Add documents to the collection (includes History) |
| My Collection     | 2     | `TAB_LIBRARY`       | Browse, manage, version, and export documents |
| Search            | 3     | `TAB_SEARCH`        | Query the collection                          |
| Review            | 4     | `TAB_REVIEW`        | HITL task queue for flagged content           |
| Admin             | 5     | `TAB_ADMIN`         | Sub-tabs: Health & Metrics, Cleanup & Feedback, Groups, Danger Zone |

The Admin tab is **conditionally rendered** -- it only appears when a valid admin token is set.

---

## 4. Component Reference

### Layout primitives

| Pattern                        | Function                          | Notes                                         |
|--------------------------------|-----------------------------------|-----------------------------------------------|
| Styled content card            | `card(hero=, elevated=)`          | Context manager. `hero=True` for main card.   |
| Bulk-actions card              | `actions_card()`                  | Context manager. Used in Library for export/delete. |
| Page title + subtitle          | `page_header(title, subtitle)`    | One per page, at the very top.                |
| Section title within a tab     | `section_header(title, caption)`  | Consistent heading with CSS class.            |
| Tab locked skeleton            | `tab_header(title, subtitle, ...)` | `section_header` + `scope_strip`. Use in every tab. |
| Card title + caption           | `card_header(title, caption)`     | Place at top of every `card()` block.         |
| Sub-section inside card        | `card_section(title, caption)` | Use instead of `#### ...`. Renders `.atlas-card-section`. |
| Visual gap between sections    | `section_gap()`                   | Breathing room within a tab.                  |

### Scope & context

| Pattern                        | Function                          | Notes                                         |
|--------------------------------|-----------------------------------|-----------------------------------------------|
| Inline breadcrumb              | `scope_strip(workspace, collection, project=)` | Workspace . Project . Collection        |
| Context banner (legacy)        | `scope_banner(workspace, collection)` | Prefer `scope_strip` for new code.        |
| Horizontal stats               | `stats_strip(**stats)`            | `stats_strip(Documents=42, Pending=4)`        |

### Status & feedback

| Pattern                        | Function                          | Notes                                         |
|--------------------------------|-----------------------------------|-----------------------------------------------|
| Status indicator               | `status_pill(label, ok=)`         | Green success or red error.                   |
| "Admin required" banner        | `auth_gate(message=)`             | Info-level Streamlit banner.                  |
| Admin warning banner           | `admin_warning()`                 | Destructive-ops warning for sidebar/admin.    |
| Ingest success message         | `ingest_result(title, detail)`    | Success toast after upload.                   |
| Friendly empty state           | `empty_state(message, button_label=, button_key=)` | Shown when a list has no data.  |
| Onboarding checklist item      | `checklist_item(done, number, title, description)` | Used on the Home tab.          |

### Buttons

| Pattern                        | Function                          | Notes                                         |
|--------------------------------|-----------------------------------|-----------------------------------------------|
| Primary action button          | `primary_button(label, disabled=, key=)` | Blue accent, `type="primary"`. **One per tab.** |
| Secondary / ghost button       | `secondary_button(label, disabled=, key=)` | Muted ghost styling for de-ranked actions.   |
| Destructive action button      | `danger_button(label, disabled=, key=)` | Red button for delete, reset, etc.            |
| Row of equal-width buttons     | `action_bar(*button_specs)`       | Returns `list[bool]`. Each spec needs `label` + `key`. |

### Data display

| Pattern                        | Function                          | Notes                                         |
|--------------------------------|-----------------------------------|-----------------------------------------------|
| Dataframe / list               | `data_table(rows, empty_msg=)`    | Auto-handles empty state.                     |
| Collapsed JSON block           | `detail_expander(label, data=)`   | Never dump raw JSON inline -- use this.       |
| Search result card             | `search_hit_card(i, title, snippet, metrics, raw)` | Expands first result.           |
| Full run detail view           | `run_detail_card(run_data, node_runs, artifacts)` | Used in Upload > Processing history.              |

### Admin containers

| Pattern                        | Function                          | Notes                                         |
|--------------------------------|-----------------------------------|-----------------------------------------------|
| Admin-gated container          | `admin_section(label=)`           | Context manager. Dashed border, muted opacity. |
| Danger zone expander           | `danger_zone(caption=, warning=)` | Context manager. Red-tinted for destructive ops. |

---

## 5. CSS Classes (styles.py)

All CSS lives in `ui/styles.py`. Key classes:

| Class                    | Applied by                  | Purpose                              |
|--------------------------|-----------------------------|--------------------------------------|
| `.atlas-card`            | `card()`                    | Standard card border + background    |
| `.atlas-card-elevated`   | `card(elevated=True)`       | Slightly shadowed card               |
| `.atlas-card-hero`       | `card(hero=True)`           | Light-tinted primary card            |
| `.atlas-card-header`     | `card_header()`             | Card title styling                   |
| `.atlas-card-caption`    | `card_header()` (inner)     | Muted subtitle inside card header    |
| `.atlas-page-header`     | `page_header()`             | Top-of-page title area               |
| `.atlas-section-header`  | `section_header()`          | Consistent heading spacing           |
| `.atlas-scope-strip`     | `scope_strip()`             | Inline breadcrumb row                |
| `.atlas-scope-banner`    | `scope_banner()`            | Legacy context banner                |
| `.atlas-admin-gate`      | `admin_section()`           | Dashed border + muted opacity        |
| `.atlas-admin-gate-label`| `admin_section()` (inner)   | "Admin only" label                   |
| `.atlas-danger`          | `danger_zone()`             | Red-tinted container                 |
| `.atlas-primary-action`  | `primary_button()`          | Accent button wrapper                |
| `.atlas-danger-btn`      | `danger_button()`           | Red button wrapper                   |
| `.atlas-secondary-btn`   | `secondary_button()`        | Ghost button wrapper                 |
| `.atlas-search-hit`      | `search_hit_card()`         | Search result styling                |
| `.atlas-card-section`    | `card_section()`            | Sub-heading inside card body, top border separator |
| `.atlas-section-gap`     | `section_gap()`             | Vertical breathing room              |

---

## 6. Hard Rules -- What Is Never Allowed

1. **No `unsafe_allow_html=True` outside `styles.py` and `components.py`.**
   All CSS injection is centralised in `ui/styles.py`. `components.py` may use
   `unsafe_allow_html=True` only for structural HTML wrappers that apply
   predefined CSS classes. Must not wrap user-supplied content.

2. **No raw `st.columns([1,1])` for action button rows -- use `action_bar()`.**
   `action_bar()` guarantees `use_container_width=True` on every button.

3. **No magic numbers in layout -- use `theme.py` constants.**
   Column ratios: `COL_HALF`, `COL_THIRDS`, `COL_QUARTERS`, `COL_MAIN_ASIDE`, `COL_ASIDE_MAIN`.
   Widget sizing: `TEXT_AREA_SM`, `TEXT_AREA_MD`, `TEXT_AREA_LG`.
   Content limits: `MAX_SNIPPET_CHARS`, `MAX_DIAG_ROWS`, `MAX_DIAG_EVENTS`.

4. **No `st.subheader()` directly in tabs -- use `section_header()`.**
   `section_header()` applies `.atlas-section-header` for consistent spacing.

5. **No bare `st.json()` in the main flow -- use `detail_expander()`.**
   Raw JSON belongs in a collapsed expander, not inline.

6. **No admin controls in the sidebar.**
   Sidebar is a context rail only. Admin operations belong in the Admin tab.

7. **Tab capacity: max 5 cards per top-level tab, max 3 cards per sub-tab.**
   If a tab needs more than 5 cards, introduce sub-tabs (`st.tabs()` nested
   inside the tab). The first card in any tab or sub-tab should contain the
   primary content or most-viewed information — it must be visible without
   scrolling past a previous card.

7a. **Content separation: dangerous and infrequent operations get their own sub-tab.**
   DB reset, bulk delete, config editing, and similar destructive/rare actions
   belong in dedicated sub-tabs or behind `danger_zone()` expanders. They must
   never appear above or alongside high-traffic content like metrics, feedback,
   or document management controls.

8. **One `primary_button()` per tab or sub-tab.**
   When a tab uses sub-tabs, each sub-tab independently gets at most one
   `primary_button()`. Secondary actions use `secondary_button()`. Destructive
   actions use `danger_button()`. Sub-tabs whose sole purpose is destructive
   operations (e.g. Danger Zone) should have **zero** `primary_button()` calls
   — only `danger_button()`.

9. **Every tab starts with `tab_header()`.**
   This renders the locked skeleton: section header + scope strip.

10. **All microcopy lives in `theme.py`.**
    Tab labels (`TAB_*`), subtitles (`COPY_*`), and terminology (`LABEL_*`) are constants.

11. **No bare `st.markdown("#### ...")` inside cards — use `card_section()`.**
    Bare markdown headings bypass the design system's heading hierarchy and
    produce inconsistent sizing. `card_section()` applies `.atlas-card-section`
    for correct visual weight below `card_header()`.

---

## 7. Microcopy Constants

| Constant                | Value                                                    |
|-------------------------|----------------------------------------------------------|
| `COPY_HOME`             | Get started with your knowledge base -- connect, create, upload. |
| `COPY_UPLOAD`           | Add new documents into this collection and make them searchable. |
| `COPY_LIBRARY`          | Browse, manage, and export documents in this collection.           |
| `COPY_SEARCH`           | Ask questions and see how Atlas answers from this collection. |
| `COPY_REVIEW`           | Fix documents where automation was not confident.         |
| `COPY_ADMIN`            | Advanced operations for Atlas administrators.             |
| `LABEL_WORKSPACE`       | Workspace                                                 |
| `LABEL_COLLECTION`      | Collection                                                |
| `LABEL_PROJECT`         | Project                                                   |
| `LABEL_MAKE_SEARCH`     | Make searchable                                           |
| `LABEL_VERSION_ACTIVE`  | Version used for answers                                  |

---

## 8. Template: Adding a New Tab

```python
# 1. Add constants to ui/theme.py
TAB_MY_TAB  = "My Tab"
COPY_MY_TAB = "One sentence explaining what this tab does."

# 2. Add it to the tab_labels list in main()
tab_labels = [..., theme.TAB_MY_TAB]

# 3. Implement the tab using the locked skeleton
with tabs[N]:
    components.tab_header(
        "My Tab",
        theme.COPY_MY_TAB,
        workspace=tenant_id,
        collection=corpus_id,
        project=project_id,
    )

    # Card 1 (hero -- the primary content)
    with components.card(hero=True):
        components.card_header("Main thing", "What the user does here.")
        # ... inputs
        components.primary_button("Do the thing", key="my_tab_go")

    # Card 2 (optional secondary content)
    with components.card():
        components.card_header("Details", "Supporting information.")
        # ... content
```

---

## 9. Template: Adding a New Component

```python
# 1. Define the function in ui/components.py
def my_component(label: str, *, some_flag: bool = False) -> None:
    """Renders a ... (docstring required)."""
    cls = "atlas-my-component-active" if some_flag else "atlas-my-component"
    st.markdown(f'<div class="{cls}">{label}</div>', unsafe_allow_html=True)

# 2. Add the CSS class in ui/styles.py (inside the _CSS string)
# .atlas-my-component { ... }

# 3. Add the component to this style guide (Section 4)
```
