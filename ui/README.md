# Atlas Operator Console — UI

A Streamlit-based operator console for the Project Atlas RAG pipeline.

## Running the UI

```bash
streamlit run ui/app.py
```

Or via Docker Compose (default stack):

```bash
docker compose up -d
```

This exposes the UI at `http://localhost:18501`.

Set the `ATLAS_API_URL` environment variable to point at a running Atlas API instance (defaults to `http://atlas:8080` when running in Compose).

---

## UI Architecture

The UI is built on a four-file design system that keeps all presentational concerns separate from logic.

Every tab follows a **locked page skeleton**: `tab_header` (title + subtitle + scope strip) followed by a maximum of three cards, each introduced by a `card_header`.

### Tabs

Home | Upload | Library | Search | Review | Versions & Export | History

### `ui/theme.py` — Design Tokens

All constants: colour values, column split ratios, widget sizes, content limits, tab labels, and microcopy strings.

**Go here to:** change a layout constant, add a tab label, adjust sizing, update microcopy.

### `ui/components.py` — Shared Component Library

Reusable UI primitives:

| Component | Purpose |
|-----------|---------|
| `page_header` | App-level header with title and subtitle |
| `tab_header` | Per-tab locked skeleton (title + subtitle + scope strip) |
| `card_header` | Card title + optional caption |
| `scope_strip` | Inline breadcrumb (workspace / collection / project) |
| `section_header` | Section divider |
| `status_pill` | Coloured status badge |
| `action_bar` | Row of action buttons |
| `data_table` | Styled dataframe wrapper |
| `admin_section` | Context manager that visually gates admin-only controls |
| `secondary_button` | Ghost-styled de-ranked action button |
| `danger_button` | Red destructive-action button |

**Go here to:** change how a component renders, add a new reusable component.

### `ui/styles.py` — CSS Injection

The single location for all `unsafe_allow_html=True` usage. Contains the `_CSS` string and `inject_styles()`.

Key CSS classes: `.atlas-scope-strip`, `.atlas-card-header`, `.atlas-admin-gate`, `.atlas-secondary-btn`.

**Go here to:** change any CSS override, add new CSS rules.

### `ui/app.py` — Application Logic

The main Streamlit app. Uses `theme`, `components`, and `styles`. Contains all HTTP calls, session state management, and data processing. **Does not contain any raw CSS or magic numbers.**

---

## Changing the Colour Theme

1. Update `[theme]` in `.streamlit/config.toml` (controls Streamlit's native theme).
2. Update the matching constants in `ui/theme.py` (used by Python code).
3. Update references in `ui/styles.py` if any CSS overrides use the old colour.

---

## Style Rules

See [STYLE_GUIDE.md](./STYLE_GUIDE.md) for:
- Colour palette reference
- Component usage reference table
- Hard rules (what is never allowed)
- Templates for adding new tabs and sections
