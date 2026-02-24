"""
Atlas UI design tokens.
All layout constants, colour values, and sizing decisions live here.
Import from this module — never hardcode values in app.py or components.py.
"""

# ── Colours ───────────────────────────────────────────────────────────────────
PRIMARY         = "#0068C9"
DANGER          = "#D9534F"
SUCCESS         = "#28A745"
MUTED           = "#6C757D"

# ── Column split ratios ────────────────────────────────────────────────────────
COL_HALF        = [1, 1]
COL_THIRDS      = [1, 1, 1]
COL_QUARTERS    = [1, 1, 1, 1]
COL_MAIN_ASIDE  = [3, 1]
COL_ASIDE_MAIN  = [1, 3]

# ── Widget sizing ──────────────────────────────────────────────────────────────
TEXT_AREA_SM    = 180
TEXT_AREA_MD    = 260
TEXT_AREA_LG    = 340

# ── Content limits ─────────────────────────────────────────────────────────────
MAX_SNIPPET_CHARS   = 280
MAX_DIAG_ROWS       = 50
MAX_DIAG_EVENTS     = 200

# ── Sidebar ────────────────────────────────────────────────────────────────────
SIDEBAR_SECTION_GROUPS = ["Connection", "Status", "Tools"]

# ── Tab labels ─────────────────────────────────────────────────────────────────
TAB_UPLOAD   = "Upload"
TAB_SEARCH   = "Search"
TAB_HISTORY  = "History"
TAB_REVIEW   = "Review"
TAB_VERSIONS = "Versions & Export"
