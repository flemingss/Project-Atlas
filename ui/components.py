"""
Atlas UI — shared component library.
Every UI pattern that appears more than once in app.py lives here.
Import from this module — never duplicate layout primitives in app.py.
"""

from __future__ import annotations

import contextlib
from typing import Any, Generator

import streamlit as st

from ui import theme


def page_header(title: str, subtitle: str = "") -> None:
    """Renders the top-of-page header with title and optional subtitle caption."""
    st.markdown('<div class="atlas-page-header">', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.markdown("</div>", unsafe_allow_html=True)


def section_header(title: str, caption: str = "", *, help: str = "") -> None:
    """Renders a consistent section title + optional caption within a tab."""
    st.markdown('<div class="atlas-section-header">', unsafe_allow_html=True)
    if help:
        st.subheader(title, help=help)
    else:
        st.subheader(title)
    if caption:
        st.caption(caption)
    st.markdown("</div>", unsafe_allow_html=True)


def status_pill(label: str, *, ok: bool, detail: str = "") -> None:
    """
    Renders a status indicator. Replaces the old _status_badge().
    Uses a styled st.success / st.error with consistent message format.
    Format: "✅ {label}" or "❌ {label}" with detail as st.caption below.
    """
    if ok:
        st.success(f"✅ {label}")
    else:
        st.error(f"❌ {label}")
    if detail:
        st.caption(detail)


def auth_gate(message: str = "Admin token required for this section.") -> None:
    """
    Renders an info banner when admin access is missing.
    Replaces the repeated `if not admin_headers: st.info(...)` pattern.
    """
    st.info(message)


def field_row(*labels_and_widgets: Any) -> None:
    """
    Helper that documents intent: always use theme.COL_HALF for two-column input rows.
    Not a wrapper — just ensures callers use the named constant.
    Documented in the style guide.
    """
    # This is a documentation-intent function.
    # Callers should use: col1, col2 = st.columns(theme.COL_HALF)
    pass


def action_bar(*button_specs: dict) -> list[bool]:
    """
    Renders a row of equal-width full-container-width buttons.
    Each spec is {"label": str, "disabled": bool (optional), "key": str (optional)}.
    Returns a list of booleans (one per button, True if clicked).
    Ensures use_container_width=True on every button.
    """
    if not button_specs:
        return []
    cols = st.columns([1] * len(button_specs))
    results: list[bool] = []
    for col, spec in zip(cols, button_specs):
        with col:
            clicked = st.button(
                spec["label"],
                disabled=bool(spec.get("disabled", False)),
                key=spec.get("key"),
                use_container_width=True,
            )
            results.append(clicked)
    return results


def data_table(rows: list[dict], *, empty_msg: str = "No data to display.") -> None:
    """
    Renders a dataframe with consistent styling and a proper empty state.
    Replaces bare st.dataframe() calls.
    """
    if not rows:
        st.caption(empty_msg)
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)


def detail_expander(label: str = "Details", data: Any = None, *, expanded: bool = False) -> None:
    """
    Renders a collapsed expander containing JSON or code.
    Replaces the repeated `with st.expander("Details (raw)", expanded=False): st.json(...)` pattern.
    """
    with st.expander(label, expanded=expanded):
        if data is not None:
            st.json(data)


def ingest_result(title: str, detail: str = "") -> None:
    """
    Renders a success state after an ingest operation.
    Shows st.success(title) and st.caption(detail) if detail is non-empty.
    """
    st.success(title)
    if detail:
        st.caption(detail)


@contextlib.contextmanager
def danger_zone(*, caption: str, warning: str) -> Generator[None, None, None]:
    """
    Returns a styled expander context manager for destructive operations.
    Renders a red-tinted expander (via CSS class) with a caption and warning inside.
    Usage: with danger_zone(caption="...", warning="..."): ...
    """
    st.markdown('<div class="atlas-danger">', unsafe_allow_html=True)
    with st.expander("⚠️ Danger zone", expanded=False):
        st.caption(caption)
        st.warning(warning)
        yield
    st.markdown("</div>", unsafe_allow_html=True)


def search_hit_card(
    i: int,
    title: str,
    snippet: str,
    metrics: dict[str, str],
    raw_data: dict,
) -> None:
    """
    Renders a single search result card.
    - title: the expander title (clean, human-readable)
    - snippet: the text preview
    - metrics: dict of label→value for st.metric() row
    - raw_data: shown in a detail_expander inside
    Auto-expands the first result (i == 1).
    """
    with st.expander(title, expanded=(i == 1)):
        st.write(snippet)
        metric_labels = list(metrics.keys())
        metric_values = list(metrics.values())
        cols = st.columns(theme.COL_QUARTERS)
        for col, lbl, val in zip(cols, metric_labels, metric_values):
            col.metric(lbl, val)
        detail_expander("Details (raw)", data=raw_data)


def run_detail_card(run_data: dict, node_runs: list, artifacts: list) -> None:
    """
    Renders the full run detail view (run JSON + steps table + artifacts table).
    Used in the History tab after "Load details".
    """
    section_header("Run")
    st.json(run_data)

    section_header("Steps")
    data_table(node_runs if isinstance(node_runs, list) else [], empty_msg="No steps recorded.")

    section_header("Files & outputs")
    data_table(artifacts if isinstance(artifacts, list) else [], empty_msg="No artifacts recorded.")
