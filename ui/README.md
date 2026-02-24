# Atlas Operator Console — UI

A Streamlit-based operator console for the Project Atlas RAG pipeline.

## Running the UI

```bash
streamlit run ui/app.py
```

Or via Docker Compose:

```bash
docker compose up ui
```

Set the `ATLAS_API_URL` environment variable to point at a running Atlas API instance.

---

## UI Architecture

The UI is built on a three-file design system that keeps all presentational concerns separate from logic.

### `ui/theme.py` — Design Tokens

All constants: colour values, column split ratios, widget sizes, content limits, and tab labels.

**Go here to:** change a layout constant, add a tab label, adjust sizing.

### `ui/components.py` — Shared Component Library

Reusable UI primitives (`page_header`, `section_header`, `status_pill`, `action_bar`, `data_table`, etc.).

**Go here to:** change how a component renders, add a new reusable component.

### `ui/styles.py` — CSS Injection

The single location for all `unsafe_allow_html=True` usage. Contains the `_CSS` string and `inject_styles()`.

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
