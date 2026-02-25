"""
Atlas UI -- shared component library.
Every UI pattern that appears more than once in app.py lives here.
Import from this module -- never duplicate layout primitives in app.py.
"""

from __future__ import annotations

import contextlib
from typing import Any, Generator

import streamlit as st

from ui import theme


# -- Layout primitives --------------------------------------------------------

@contextlib.contextmanager
def card(*, hero: bool = False, elevated: bool = False) -> Generator[None, None, None]:
    """Wrap content in a styled card div. Use hero=True for the primary
    content area of a tab; elevated=True for interactive cards."""
    if hero:
        cls = "atlas-card-hero"
    elif elevated:
        cls = "atlas-card-elevated"
    else:
        cls = "atlas-card"
    st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
    yield
    st.markdown("</div>", unsafe_allow_html=True)


@contextlib.contextmanager
def actions_card() -> Generator[None, None, None]:
    """Wrap bulk-action controls (export/delete) in a styled card."""
    st.markdown('<div class="atlas-actions-card">', unsafe_allow_html=True)
    yield
    st.markdown("</div>", unsafe_allow_html=True)


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
    """Renders a compact status indicator."""
    if ok:
        st.success(label)
    else:
        st.error(label)
    if detail:
        st.caption(detail)


def auth_gate(message: str = "Admin token required for this section.") -> None:
    """Renders an info banner when admin access is missing."""
    st.info(message)


def action_bar(*button_specs: dict[str, Any]) -> list[bool]:
    """Renders a row of equal-width buttons. Returns list of booleans."""
    if not button_specs:
        return []
    cols = st.columns([1] * len(button_specs))
    results: list[bool] = []
    for col, spec in zip(cols, button_specs):
        if "label" not in spec:
            raise ValueError("Button spec must include 'label' key")
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
    """Renders a dataframe with consistent styling and a proper empty state."""
    if not rows:
        st.caption(empty_msg)
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)


def detail_expander(label: str = "Details", data: Any = None, *, expanded: bool = False) -> None:
    """Renders a collapsed expander containing JSON or code."""
    with st.expander(label, expanded=expanded):
        if data is not None:
            st.json(data)


def ingest_result(title: str, detail: str = "") -> None:
    """Renders a success state after an ingest operation."""
    st.success(title)
    if detail:
        st.caption(detail)


@contextlib.contextmanager
def danger_zone(*, caption: str, warning: str) -> Generator[None, None, None]:
    """Styled expander for destructive operations. Red-tinted via CSS."""
    st.markdown('<div class="atlas-danger">', unsafe_allow_html=True)
    with st.expander("Danger zone", expanded=False):
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
    """Renders a single search result card. Auto-expands first result."""
    st.markdown('<div class="atlas-search-hit">', unsafe_allow_html=True)
    with st.expander(title, expanded=(i == 1)):
        st.write(snippet)
        metric_items = list(metrics.items())
        if metric_items:
            cols = st.columns(len(metric_items))
            for col, (lbl, val) in zip(cols, metric_items):
                col.metric(lbl, val)
        detail_expander("Raw details", data=raw_data)
    st.markdown("</div>", unsafe_allow_html=True)


def run_detail_card(run_data: dict, node_runs: list, artifacts: list) -> None:
    """Renders the full run detail view. Used in the History tab."""
    with card(elevated=True):
        section_header("Run")
        st.json(run_data)
    section_header("Steps")
    data_table(node_runs if isinstance(node_runs, list) else [], empty_msg="No steps recorded.")
    section_header("Files & outputs")
    data_table(artifacts if isinstance(artifacts, list) else [], empty_msg="No artifacts recorded.")


# -- Contextual helpers -------------------------------------------------------

def scope_banner(workspace: str, collection: str, *, prefix: str = "") -> None:
    """Renders a context line: 'Searching in: Workspace X / Collection Y'."""
    label = (
        f"{prefix}Workspace <strong>{workspace}</strong>"
        f" &middot; Collection <strong>{collection}</strong>"
    )
    st.markdown(f'<div class="atlas-scope-banner">{label}</div>', unsafe_allow_html=True)


def scope_strip(workspace: str, collection: str, *, project: str = "") -> None:
    """Renders a calm inline breadcrumb: Workspace: X . Project: Y . Collection: Z."""
    parts = [
        f'{theme.LABEL_WORKSPACE}: <strong>{workspace}</strong>',
    ]
    if project:
        parts.append(f'{theme.LABEL_PROJECT}: <strong>{project}</strong>')
    parts.append(f'{theme.LABEL_COLLECTION}: <strong>{collection}</strong>')
    sep = ' <span class="atlas-scope-sep">&middot;</span> '
    inner = sep.join(parts)
    st.markdown(f'<div class="atlas-scope-strip">{inner}</div>', unsafe_allow_html=True)


def card_header(title: str, caption: str = "") -> None:
    """Renders a consistent card title + caption at the top of a card div."""
    cap_html = f'<p class="atlas-card-caption">{caption}</p>' if caption else ""
    st.markdown(
        f'<div class="atlas-card-header"><h3>{title}</h3>{cap_html}</div>',
        unsafe_allow_html=True,
    )


def tab_header(title: str, subtitle: str, workspace: str, collection: str, *, project: str = "") -> None:
    """Renders the locked page skeleton header: section_header + scope_strip."""
    section_header(title, caption=subtitle)
    scope_strip(workspace, collection, project=project)


@contextlib.contextmanager
def admin_section(label: str = "Admin only") -> Generator[None, None, None]:
    """Wraps content in a visually-muted admin-gated container."""
    st.markdown(
        f'<div class="atlas-admin-gate">'
        f'<div class="atlas-admin-gate-label">{label}</div>',
        unsafe_allow_html=True,
    )
    yield
    st.markdown('</div>', unsafe_allow_html=True)


def secondary_button(label: str, *, disabled: bool = False, key: str = "") -> bool:
    """Wraps a button in a ghost/secondary container for de-ranked actions."""
    st.markdown('<div class="atlas-secondary-btn">', unsafe_allow_html=True)
    kwargs: dict[str, Any] = {
        "use_container_width": True,
        "disabled": disabled,
    }
    if key:
        kwargs["key"] = key
    clicked = st.button(label, **kwargs)
    st.markdown("</div>", unsafe_allow_html=True)
    return clicked


def stats_strip(**stats: Any) -> None:
    """Renders a horizontal strip of key-value stats.
    Usage: stats_strip(Documents=42, Searchable=38, Pending=4)
    """
    parts = []
    for label, value in stats.items():
        parts.append(f"<strong>{value}</strong> {label}")
    inner = " &nbsp;&middot;&nbsp; ".join(parts)
    st.markdown(f'<div class="atlas-stats-strip">{inner}</div>', unsafe_allow_html=True)


def empty_state(message: str, *, button_label: str = "", button_key: str = "") -> bool:
    """Renders a friendly empty-state message with optional nav button."""
    st.markdown(
        f'<div class="atlas-empty-state"><p>{message}</p></div>',
        unsafe_allow_html=True,
    )
    if button_label and button_key:
        return st.button(button_label, key=button_key, use_container_width=True)
    return False


def section_gap() -> None:
    """Adds visual breathing room between major sections within a tab."""
    st.markdown('<div class="atlas-section-gap"></div>', unsafe_allow_html=True)


def admin_warning() -> None:
    """Renders the admin-tools warning banner inside the sidebar expander."""
    st.markdown(
        '<div class="atlas-admin-warning">'
        "For Atlas administrators only. "
        "These tools may <strong>delete data</strong> or interrupt processing."
        "</div>",
        unsafe_allow_html=True,
    )


def checklist_item(done: bool, number: str, title: str, description: str) -> None:
    """Renders a single onboarding checklist item with done/todo styling."""
    cls = "atlas-checklist-done" if done else "atlas-checklist-todo"
    mark = "[x]" if done else "[ ]"
    st.markdown(
        f'<div class="atlas-checklist-item {cls}">'
        f"<strong>{mark} {number} {title}</strong><br/>"
        f'<span style="font-size:0.8rem;color:#6C757D">{description}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


def primary_button(label: str, *, disabled: bool = False, key: str = "") -> bool:
    """Wraps a button in the atlas-primary-action container for accent styling."""
    st.markdown('<div class="atlas-primary-action">', unsafe_allow_html=True)
    kwargs: dict[str, Any] = {
        "use_container_width": True,
        "type": "primary",
        "disabled": disabled,
    }
    if key:
        kwargs["key"] = key
    clicked = st.button(label, **kwargs)
    st.markdown("</div>", unsafe_allow_html=True)
    return clicked


def danger_button(label: str, *, disabled: bool = False, key: str = "") -> bool:
    """Wraps a button in a red danger container for destructive actions."""
    st.markdown('<div class="atlas-danger-btn">', unsafe_allow_html=True)
    kwargs: dict[str, Any] = {
        "use_container_width": True,
        "type": "primary",
        "disabled": disabled,
    }
    if key:
        kwargs["key"] = key
    clicked = st.button(label, **kwargs)
    st.markdown("</div>", unsafe_allow_html=True)
    return clicked
