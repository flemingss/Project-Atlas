"""
Atlas UI — CSS injection.
This is the ONLY place in the codebase that uses unsafe_allow_html=True.
All visual overrides live here. Call inject_styles() once at the top of main().
"""

import streamlit as st

_CSS = """
<style>
/* ── Tab bar ─────────────────────────────────────────────────────────────────*/
.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    border-bottom: 2px solid #E8EAED;
    padding-bottom: 0;
}
.stTabs [data-baseweb="tab"] {
    padding: 8px 20px;
    border-radius: 8px 8px 0 0;
    font-weight: 500;
    font-size: 0.875rem;
    color: #6C757D;
    border: none;
    background: transparent;
}
.stTabs [aria-selected="true"] {
    color: #0068C9 !important;
    border-bottom: 2px solid #0068C9 !important;
    background: transparent !important;
}

/* ── Buttons ─────────────────────────────────────────────────────────────────*/
.stButton > button {
    border-radius: 8px;
    font-weight: 500;
    font-size: 0.875rem;
    padding: 0.45rem 1.1rem;
    transition: background 0.15s ease, box-shadow 0.15s ease;
}
.stButton > button:hover {
    box-shadow: 0 2px 8px rgba(0,104,201,0.18);
}
.stDownloadButton > button {
    border-radius: 8px;
    font-weight: 500;
    font-size: 0.875rem;
}

/* ── Inputs ──────────────────────────────────────────────────────────────────*/
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div {
    border-radius: 8px;
}

/* ── Dataframes ──────────────────────────────────────────────────────────────*/
.stDataFrame {
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid #E8EAED;
}

/* ── Expanders ───────────────────────────────────────────────────────────────*/
.streamlit-expanderHeader {
    font-weight: 500;
    font-size: 0.875rem;
    color: #1A1D23;
}
[data-testid="stExpander"] {
    border-radius: 8px;
    border: 1px solid #E8EAED;
}

/* ── Metrics ─────────────────────────────────────────────────────────────────*/
[data-testid="stMetricValue"] {
    font-size: 1.05rem;
    font-weight: 600;
}
[data-testid="stMetricLabel"] {
    font-size: 0.75rem;
    color: #6C757D;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────────*/
section[data-testid="stSidebar"] .block-container {
    padding-top: 1.5rem;
    padding-bottom: 2rem;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    font-size: 0.875rem;
}

/* ── Page header ─────────────────────────────────────────────────────────────*/
.atlas-page-header {
    padding-bottom: 0.75rem;
    margin-bottom: 0.5rem;
    border-bottom: 2px solid #E8EAED;
}

/* ── Section header ──────────────────────────────────────────────────────────*/
.atlas-section-header {
    margin-top: 0.5rem;
    margin-bottom: 0.25rem;
}

/* ── Danger zone expander ────────────────────────────────────────────────────*/
.atlas-danger [data-testid="stExpander"] {
    border-color: #F5C2C7;
    background: #FFF5F5;
}
.atlas-danger .streamlit-expanderHeader {
    color: #D9534F;
}

/* ── Status pills ────────────────────────────────────────────────────────────*/
[data-testid="stAlert"] {
    border-radius: 8px;
    font-size: 0.875rem;
}

/* ── Dividers ────────────────────────────────────────────────────────────────*/
hr {
    margin: 1.25rem 0;
    border-color: #E8EAED;
}
</style>
"""


def inject_styles() -> None:
    """Call once at the top of main() before any other st.* calls."""
    st.markdown(_CSS, unsafe_allow_html=True)
