"""
Atlas UI -- CSS injection.
This is the ONLY place in the codebase that uses unsafe_allow_html=True for CSS.
All visual overrides live here. Call inject_styles() once at the top of main().
"""

import streamlit as st

_CSS = """
<style>
/* =============================================================================
   ATLAS DESIGN SYSTEM  v3
   Colour system:
     Accent  #0068C9  -- primary actions (Upload, Search, Accept)
     Muted   #6C757D / #ADB5BD  -- labels, borders, secondary text
     Danger  #D9534F  -- destructive actions only
     Surface #FAFBFC  -- card backgrounds
     Alt     #F5F7FA  -- alternating section backgrounds
   ============================================================================= */

/* -- Global layout -----------------------------------------------------------*/
.main .block-container {
    max-width: 1080px;
    padding-top: 1.75rem;
    padding-bottom: 3rem;
}

/* -- Typography scale (tightened) --------------------------------------------*/
h1 {
    font-size: 1.5rem !important;
    font-weight: 700 !important;
    color: #1A1D23 !important;
    letter-spacing: -0.01em;
    margin-bottom: 0.15rem !important;
}
h2, h3 {
    font-size: 1.1rem !important;
    font-weight: 600 !important;
    color: #1A1D23 !important;
}
h4 {
    font-size: 0.88rem !important;
    font-weight: 600 !important;
    color: #1A1D23 !important;
}
.stCaption, [data-testid="stCaptionContainer"] {
    font-size: 0.8rem !important;
    color: #6C757D !important;
}

/* -- Tab bar -----------------------------------------------------------------*/
.stTabs [data-baseweb="tab-list"] {
    gap: 2px;
    border-bottom: 2px solid #E8EAED;
    padding-bottom: 0;
    background: transparent;
}
.stTabs [data-baseweb="tab"] {
    padding: 10px 22px;
    border-radius: 8px 8px 0 0;
    font-weight: 500;
    font-size: 0.85rem;
    color: #6C757D;
    border: none;
    background: transparent;
    transition: color 0.15s ease, background 0.15s ease;
}
.stTabs [data-baseweb="tab"]:hover {
    background: #F5F7FA;
    color: #1A1D23;
}
.stTabs [aria-selected="true"] {
    color: #0068C9 !important;
    border-bottom: 2px solid #0068C9 !important;
    background: transparent !important;
    font-weight: 600;
}

/* -- Cards (the core visual element) -----------------------------------------*/
.atlas-card {
    border: 1px solid #E8EAED;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.25rem;
    background: #FAFBFC;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}
.atlas-card-elevated {
    border: 1px solid #E8EAED;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.25rem;
    background: #FFFFFF;
    box-shadow: 0 2px 10px rgba(0,0,0,0.07);
}
.atlas-card-hero {
    border: 1px solid #D6E4F0;
    border-radius: 14px;
    padding: 1.75rem 2rem;
    margin-bottom: 1.5rem;
    background: linear-gradient(135deg, #FAFBFC 0%, #F0F6FF 100%);
    box-shadow: 0 2px 12px rgba(0,104,201,0.07);
}

/* -- Buttons -----------------------------------------------------------------*/
.stButton > button {
    border-radius: 8px;
    font-weight: 500;
    font-size: 0.85rem;
    padding: 0.45rem 1.1rem;
    transition: all 0.15s ease;
    border: 1px solid #E8EAED;
}
.stButton > button:hover {
    box-shadow: 0 2px 8px rgba(0,104,201,0.15);
    border-color: #0068C9;
}
.stDownloadButton > button {
    border-radius: 8px;
    font-weight: 500;
    font-size: 0.85rem;
}

/* -- Primary action button ---------------------------------------------------*/
.atlas-primary-action .stButton > button {
    font-size: 0.95rem;
    font-weight: 600;
    padding: 0.65rem 1.5rem;
    background: #0068C9;
    color: white;
    border: none;
    border-radius: 10px;
}
.atlas-primary-action .stButton > button:hover {
    background: #0054A3;
    box-shadow: 0 4px 14px rgba(0,104,201,0.25);
}
.atlas-primary-action .stButton > button:disabled {
    background: #ADB5BD;
    box-shadow: none;
}

/* -- Danger button -----------------------------------------------------------*/
.atlas-danger-btn .stButton > button {
    background: #D9534F;
    color: white;
    border: none;
    border-radius: 10px;
    font-weight: 600;
}
.atlas-danger-btn .stButton > button:hover {
    background: #C9302C;
    box-shadow: 0 4px 14px rgba(217,83,79,0.25);
}
.atlas-danger-btn .stButton > button:disabled {
    background: #ADB5BD;
    box-shadow: none;
}

/* -- Inputs ------------------------------------------------------------------*/
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div {
    border-radius: 8px;
    border-color: #E8EAED;
    transition: border-color 0.15s ease;
}
.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus {
    border-color: #0068C9;
    box-shadow: 0 0 0 2px rgba(0,104,201,0.1);
}

/* -- Dataframes --------------------------------------------------------------*/
.stDataFrame {
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid #E8EAED;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}

/* -- Expanders ---------------------------------------------------------------*/
.streamlit-expanderHeader {
    font-weight: 500;
    font-size: 0.85rem;
    color: #4A5568;
}
[data-testid="stExpander"] {
    border-radius: 10px;
    border: 1px solid #E8EAED;
    background: #FAFBFC;
}

/* -- Metrics -----------------------------------------------------------------*/
[data-testid="stMetricValue"] {
    font-size: 1rem;
    font-weight: 600;
    color: #1A1D23;
}
[data-testid="stMetricLabel"] {
    font-size: 0.7rem;
    color: #ADB5BD;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* -- Sidebar (thin, calm rail) -----------------------------------------------*/
section[data-testid="stSidebar"] {
    background: #FAFBFC;
    border-right: 1px solid #E8EAED;
    width: 280px !important;
}
section[data-testid="stSidebar"] .block-container {
    padding-top: 1rem;
    padding-bottom: 2rem;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    font-size: 0.8rem;
    color: #4A5568;
}
section[data-testid="stSidebar"] h4 {
    font-size: 0.68rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: #ADB5BD !important;
    margin-top: 0.85rem !important;
    margin-bottom: 0.25rem !important;
}
section[data-testid="stSidebar"] hr {
    margin: 0.6rem 0;
    border-color: #E8EAED;
    opacity: 0.5;
}
section[data-testid="stSidebar"] .stTextInput > label,
section[data-testid="stSidebar"] .stSelectbox > label {
    font-size: 0.78rem !important;
    color: #6C757D !important;
}

/* -- Page header -------------------------------------------------------------*/
.atlas-page-header {
    padding-bottom: 0.25rem;
    margin-bottom: 0.15rem;
}

/* -- Section header ----------------------------------------------------------*/
.atlas-section-header {
    margin-top: 0.15rem;
    margin-bottom: 0.4rem;
}

/* -- Section gap (vertical breathing room) -----------------------------------*/
.atlas-section-gap {
    margin-top: 0.75rem;
}

/* -- Scope banner (inline context line) --------------------------------------*/
.atlas-scope-banner {
    background: #F0F6FF;
    border-left: 3px solid #0068C9;
    padding: 0.45rem 0.85rem;
    border-radius: 6px;
    margin-bottom: 0.85rem;
    font-size: 0.8rem;
    color: #4A5568;
}

/* -- Scope strip (calm inline breadcrumb) ------------------------------------*/
.atlas-scope-strip {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.35rem 0;
    margin-bottom: 0.75rem;
    font-size: 0.78rem;
    color: #6C757D;
}
.atlas-scope-strip strong {
    color: #4A5568;
    font-weight: 600;
}
.atlas-scope-strip .atlas-scope-sep {
    color: #ADB5BD;
}

/* -- Card header (consistent title + caption at top of card) -----------------*/
.atlas-card-header {
    margin-bottom: 0.65rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #E8EAED;
}
.atlas-card-header h3 {
    font-size: 0.95rem !important;
    font-weight: 600 !important;
    margin-bottom: 0.1rem !important;
    color: #1A1D23 !important;
}
.atlas-card-header .atlas-card-caption {
    font-size: 0.76rem;
    color: #6C757D;
    margin: 0;
}

/* -- Card section header (sub-section inside a card) -------------------------*/
.atlas-card-section {
    font-size: 0.88rem !important;
    font-weight: 600 !important;
    color: #1A1D23 !important;
    margin-top: 0.75rem !important;
    margin-bottom: 0.2rem !important;
    padding-top: 0.5rem;
    border-top: 1px solid #E8EAED;
}

/* -- Admin gate (visually muted admin-only section) --------------------------*/
.atlas-admin-gate {
    border: 1px dashed #ADB5BD;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
    background: #FAFBFC;
    opacity: 0.92;
}
.atlas-admin-gate-label {
    font-size: 0.68rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #ADB5BD;
    margin-bottom: 0.5rem;
}

/* -- Secondary / ghost button ------------------------------------------------*/
.atlas-secondary-btn .stButton > button {
    background: transparent;
    color: #4A5568;
    border: 1px solid #E8EAED;
    font-weight: 500;
    border-radius: 8px;
}
.atlas-secondary-btn .stButton > button:hover {
    background: #F5F7FA;
    border-color: #ADB5BD;
    color: #1A1D23;
    box-shadow: none;
}

/* -- Review task card --------------------------------------------------------*/
.atlas-review-card {
    border: 1px solid #E8EAED;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.25rem;
    background: #FFFFFF;
    box-shadow: 0 2px 10px rgba(0,0,0,0.07);
}

/* -- Sidebar workspace banner ------------------------------------------------*/
.atlas-workspace-banner {
    background: #F0F6FF;
    border-radius: 8px;
    padding: 0.4rem 0.65rem;
    margin-bottom: 0.4rem;
    font-size: 0.76rem;
    color: #4A5568;
}

/* -- Sidebar section label (muted, small) ------------------------------------*/
.atlas-sidebar-label {
    font-size: 0.68rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: #ADB5BD;
    margin-top: 0.6rem;
    margin-bottom: 0.2rem;
}

/* -- Admin warning -----------------------------------------------------------*/
.atlas-admin-warning {
    background: #FFF5F5;
    border: 1px solid #F5C2C7;
    border-radius: 8px;
    padding: 0.5rem 0.75rem;
    margin-bottom: 0.5rem;
    font-size: 0.76rem;
    color: #842029;
}

/* -- Danger zone expander ----------------------------------------------------*/
.atlas-danger [data-testid="stExpander"] {
    border-color: #F5C2C7;
    background: #FFF5F5;
}
.atlas-danger .streamlit-expanderHeader {
    color: #D9534F;
}

/* -- Status pills ------------------------------------------------------------*/
[data-testid="stAlert"] {
    border-radius: 8px;
    font-size: 0.82rem;
    padding: 0.5rem 0.75rem;
}

/* -- Dividers ----------------------------------------------------------------*/
hr {
    margin: 0.85rem 0;
    border-color: #E8EAED;
    opacity: 0.5;
}

/* -- Search hit card ---------------------------------------------------------*/
.atlas-search-hit {
    margin-bottom: 0.5rem;
}
.atlas-search-hit [data-testid="stExpander"] {
    border-radius: 12px;
    border: 1px solid #E8EAED;
    background: #FFFFFF;
    box-shadow: 0 1px 5px rgba(0,0,0,0.05);
}
.atlas-search-hit .streamlit-expanderHeader {
    font-weight: 600;
    color: #1A1D23;
}

/* -- Library actions card ----------------------------------------------------*/
.atlas-actions-card {
    border: 1px solid #E8EAED;
    border-radius: 12px;
    padding: 1rem 1.25rem;
    background: #FAFBFC;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    margin-bottom: 1rem;
}

/* -- Stats strip (Library / Home) --------------------------------------------*/
.atlas-stats-strip {
    display: flex;
    gap: 1.5rem;
    padding: 0.65rem 1rem;
    background: #F5F7FA;
    border-radius: 10px;
    margin-bottom: 1rem;
    font-size: 0.82rem;
    color: #4A5568;
}
.atlas-stats-strip strong {
    color: #1A1D23;
    font-weight: 600;
}

/* -- Inline badge (for counts) -----------------------------------------------*/
.atlas-badge {
    display: inline-block;
    background: #0068C9;
    color: white;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.1rem 0.45rem;
    border-radius: 10px;
    margin-left: 0.3rem;
}

/* -- Checklist items (Home tab) ----------------------------------------------*/
.atlas-checklist-item {
    padding: 0.55rem 0.85rem;
    border-radius: 8px;
    margin-bottom: 0.3rem;
    font-size: 0.85rem;
}
.atlas-checklist-done {
    background: #F0FFF4;
    border-left: 3px solid #28A745;
}
.atlas-checklist-todo {
    background: #F5F7FA;
    border-left: 3px solid #E8EAED;
}

/* -- Search query hero area --------------------------------------------------*/
.atlas-search-hero {
    background: linear-gradient(135deg, #FAFBFC 0%, #F0F6FF 100%);
    border: 1px solid #D6E4F0;
    border-radius: 14px;
    padding: 1.5rem 1.75rem 1rem;
    margin-bottom: 1.25rem;
    box-shadow: 0 2px 12px rgba(0,104,201,0.06);
}

/* -- Options bar (below search, compact controls) ----------------------------*/
.atlas-options-bar {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.4rem 0;
    margin-bottom: 0.5rem;
    font-size: 0.82rem;
    color: #6C757D;
}

/* -- Empty state illustration ------------------------------------------------*/
.atlas-empty-state {
    text-align: center;
    padding: 2rem 1rem;
    color: #ADB5BD;
}
.atlas-empty-state p {
    font-size: 0.9rem;
    color: #6C757D;
    margin-top: 0.5rem;
}

/* -- Phase C polish ----------------------------------------------------------*/

/* Smooth content transition when switching tabs */
.stTabs [data-baseweb="tab-panel"] {
    animation: atlas-fade-in 0.18s ease-out;
}
@keyframes atlas-fade-in {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
}

/* Quality badge colouring for search results */
.atlas-quality-verified  { color: #28A745; font-weight: 600; font-size: 0.78rem; }
.atlas-quality-partial   { color: #FFC107; font-weight: 600; font-size: 0.78rem; }
.atlas-quality-review    { color: #D9534F; font-weight: 600; font-size: 0.78rem; }

/* Responsive: tighter padding on narrow viewports */
@media (max-width: 768px) {
    .main .block-container { padding-left: 0.75rem; padding-right: 0.75rem; }
    .atlas-card, .atlas-card-elevated { padding: 1rem 1.1rem; }
    .atlas-card-hero { padding: 1.25rem 1.25rem; }
    .stTabs [data-baseweb="tab"] { padding: 8px 12px; font-size: 0.78rem; }
    .atlas-stats-strip { flex-wrap: wrap; gap: 0.75rem; }
    section[data-testid="stSidebar"] { width: 240px !important; }
}

/* Processing history section polish */
.atlas-processing-history [data-testid="stExpander"] {
    border-color: #D6E4F0;
    background: #F8FAFF;
}

/* File upload drop zone highlight */
[data-testid="stFileUploader"] {
    border-radius: 10px;
    transition: border-color 0.15s ease;
}
[data-testid="stFileUploader"]:hover {
    border-color: #0068C9;
}

/* Selectbox and number input consistency */
.stSelectbox > div > div,
.stNumberInput > div > div > input {
    border-radius: 8px;
    border-color: #E8EAED;
    transition: border-color 0.15s ease;
}
.stSelectbox > div > div:focus-within,
.stNumberInput > div > div > input:focus {
    border-color: #0068C9;
    box-shadow: 0 0 0 2px rgba(0,104,201,0.1);
}

/* Download button emphasis (after export) */
.stDownloadButton > button {
    background: #F0F6FF;
    border: 1px solid #0068C9;
    color: #0068C9;
    font-weight: 600;
    border-radius: 10px;
    transition: all 0.15s ease;
}
.stDownloadButton > button:hover {
    background: #0068C9;
    color: white;
    box-shadow: 0 2px 8px rgba(0,104,201,0.2);
}

/* Tooltip / help icon consistency */
[data-testid="stTooltipIcon"] {
    color: #ADB5BD;
    transition: color 0.15s ease;
}
[data-testid="stTooltipIcon"]:hover {
    color: #0068C9;
}
</style>
"""


def inject_styles() -> None:
    """Call once at the top of main() before any other st.* calls."""
    st.markdown(_CSS, unsafe_allow_html=True)
