# Atlas UI Style Guide

A concise reference card for contributors working on the Streamlit operator console.

---

## 1. Colour Palette

| Name      | Hex       | When to use                                  |
|-----------|-----------|----------------------------------------------|
| PRIMARY   | `#0068C9` | Links, active states, primary actions        |
| DANGER    | `#D9534F` | Errors, destructive actions, danger zones    |
| SUCCESS   | `#28A745` | Success messages, OK status indicators       |
| MUTED     | `#6C757D` | Secondary text, captions, metadata           |

All colour constants live in `ui/theme.py`. **Never hardcode hex values** anywhere else.

---

## 2. Component Reference

| Pattern                                   | Use this function                        |
|-------------------------------------------|------------------------------------------|
| Page title + subtitle                     | `components.page_header()`               |
| Section title within a tab               | `components.section_header()`            |
| API / admin status indicator             | `components.status_pill()`               |
| "Admin required" info banner             | `components.auth_gate()`                 |
| Row of equal-width action buttons        | `components.action_bar()`                |
| Dataframe / list display                 | `components.data_table()`                |
| Collapsible raw JSON/code block          | `components.detail_expander()`           |
| Ingest success message                   | `components.ingest_result()`             |
| Destructive action zone                  | `components.danger_zone()`               |
| Search result card                       | `components.search_hit_card()`           |
| Run detail (history tab)                 | `components.run_detail_card()`           |

---

## 3. Hard Rules — What Is Never Allowed

- **No `unsafe_allow_html=True` outside `styles.py` and `components.py`.**
  All CSS injection is centralised in `ui/styles.py`. `components.py` may use
  `unsafe_allow_html=True` only for structural HTML wrappers that apply
  predefined CSS classes (e.g., `.atlas-page-header`, `.atlas-section-header`,
  `.atlas-danger`) and must not wrap user-supplied content.

- **No raw `st.columns([1,1])` for action button rows — use `action_bar()`.**
  `action_bar()` guarantees `use_container_width=True` on every button.

- **No magic numbers in layout — use `theme.py` constants.**
  Use `theme.COL_HALF`, `theme.TEXT_AREA_MD`, `theme.MAX_SNIPPET_CHARS`, etc.

- **No `st.subheader()` directly in tabs — use `section_header()`.**
  `section_header()` applies the `.atlas-section-header` CSS class for consistent spacing.

- **No bare `st.json()` dumped into the main flow — use `detail_expander()`.**
  Raw JSON belongs in a collapsed expander, not inline.

---

## 4. Template: Adding a New Tab

```python
# 1. Add a constant to ui/theme.py
TAB_MY_NEW_TAB = "🆕 My New Tab"

# 2. Add it to the tabs list in main()
tabs = st.tabs([..., theme.TAB_MY_NEW_TAB])

# 3. Implement the tab
with tabs[N]:
    components.section_header("My New Tab", caption="What this tab does.")
    if not admin_headers:
        components.auth_gate()  # if admin-only
    else:
        # ... tab content using components.*
        pass
```

---

## 5. Template: Adding a New Section Within an Existing Tab

```python
# Inside any tab block:
components.section_header("My Section", caption="Optional description.")

col1, col2 = st.columns(theme.COL_HALF)
# ... inputs

clicked = components.action_bar(
    {"label": "Do thing", "key": "unique_key"},
)
if clicked[0]:
    # ... handle action
    components.ingest_result("Done!", "Details here.")
    components.detail_expander("Details (raw)", data=response_data)
```
