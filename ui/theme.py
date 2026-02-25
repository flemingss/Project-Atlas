"""
Atlas UI design tokens.
All layout constants, colour values, and sizing decisions live here.
Import from this module -- never hardcode values in app.py or components.py.
"""

# -- Colours -------------------------------------------------------------------
PRIMARY         = "#0068C9"
PRIMARY_DARK    = "#0054A3"
DANGER          = "#D9534F"
SUCCESS         = "#28A745"
MUTED           = "#6C757D"
MUTED_LIGHT     = "#ADB5BD"
BORDER          = "#E8EAED"
BG_SURFACE      = "#FAFBFC"
BG_ALT          = "#F5F7FA"
BG_PRIMARY_TINT = "#F0F6FF"
TEXT_PRIMARY     = "#1A1D23"
TEXT_SECONDARY   = "#4A5568"

# -- Column split ratios --------------------------------------------------------
COL_HALF        = [1, 1]
COL_THIRDS      = [1, 1, 1]
COL_QUARTERS    = [1, 1, 1, 1]
COL_MAIN_ASIDE  = [3, 1]
COL_ASIDE_MAIN  = [1, 3]

# -- Widget sizing --------------------------------------------------------------
TEXT_AREA_SM    = 180
TEXT_AREA_MD    = 260
TEXT_AREA_LG    = 340

# -- Content limits -------------------------------------------------------------
MAX_SNIPPET_CHARS   = 280
MAX_DIAG_ROWS       = 50
MAX_DIAG_EVENTS     = 200

# -- Sidebar --------------------------------------------------------------------
SIDEBAR_SECTION_GROUPS = ["Connection", "Status", "Tools"]

# -- Tab labels with icons (plain ASCII icons) ----------------------------------
ICON_HOME     = ">"
ICON_UPLOAD   = "+"
ICON_LIBRARY  = "#"
ICON_SEARCH   = "?"
ICON_REVIEW   = "!"
ICON_EXPORT   = "^"
ICON_HISTORY  = "~"

TAB_HOME     = "Home"
TAB_UPLOAD   = "Upload"
TAB_LIBRARY  = "Library"
TAB_SEARCH   = "Search"
TAB_REVIEW   = "Review"
TAB_VERSIONS = "Versions & Export"
TAB_HISTORY  = "History"

# -- Friendly microcopy ---------------------------------------------------------
# One-sentence tab explainers (shown as captions under each tab header)
COPY_HOME     = "Get started with your knowledge base -- connect, create, upload."
COPY_UPLOAD   = "Add new documents into this collection and make them searchable."
COPY_LIBRARY  = "Browse and manage documents in this collection."
COPY_SEARCH   = "Ask questions and see how Atlas answers from this collection."
COPY_REVIEW   = "Fix documents where automation was not confident."
COPY_VERSIONS = "Control which document versions are used for answers and export packages."
COPY_HISTORY  = "Inspect processing runs and diagnose failures."

# -- Friendly terminology -------------------------------------------------------
LABEL_WORKSPACE      = "Workspace"
LABEL_PROJECT        = "Project"
LABEL_COLLECTION     = "Collection"
LABEL_MAKE_SEARCH    = "Make searchable"
LABEL_VERSION_ACTIVE = "Version used for answers"
LABEL_SENSITIVE      = "Sensitive"
# Legacy alias
LABEL_SEARCHABLE     = "Searchable"
