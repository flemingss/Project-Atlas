#!/usr/bin/env python
"""Smoke-test the built Atlas image: parse a manufactured PDF as the runtime user.

Run *inside* the image, as its default (non-root) user, with no services:

    docker run --rm -v "$PWD/scripts:/smoke:ro" <image> python /smoke/image_smoke.py

The unit suite cannot see an image-level regression — CI never builds the
image and the devcontainer shell is root — which is how Docling was dead in
the shipped image for a day (2026-08-30, cache under 0700 /root). This
script exists to catch that class of failure on every push to main:

1. the process is not root and the baked model cache is readable;
2. Docling imports and parses a PDF that contains every extraction
   situation seen on real documents so far (see ``_write_fixture``);
3. the cleanup node repairs what it is supposed to repair.

Exit status is non-zero on any failed check. Output is a one-line-per-check
report so a red run says *what* broke, not just that something did.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# The fixture: one PDF, every situation
# ---------------------------------------------------------------------------

RUNNING_HEADER = "ATLAS SMOKE DATASHEET"
# Doubled digits in a small font: Docling 2.76.0 dropped one of the "11" /
# "99" on a real datasheet footer (Microsemi, 2026-08-30). Placed in the body
# area, not the bottom margin: Docling classifies bottom-margin text as page
# furniture and leaves it out of the markdown export altogether (the smoke
# prints whether that happened, but does not fail on it).
FOOTER = (
    "Within the USA: +1 (800) 713-4113  Outside the USA: +1 (949) 380-6100  "
    "Fax: +1 (949) 215-4996  Part 090-15200-601  Serials 1100 2200 3399 4455"
)
LEFT_COLUMN = [
    "ALPHALEFTONE The left column opens with a paragraph about hardware timestamping.",
    "ALPHALEFTTWO A second left-column paragraph follows the first without interruption.",
]
RIGHT_COLUMN = [
    "BRAVORIGHTONE The right column starts its own topic on oscillator holdover.",
    "BRAVORIGHTTWO A second right-column paragraph continues that topic.",
]
BULLETS = ["Ultra-high bandwidth NTP time server", "Stratum 1 operation via GNSS", "Dual power supply option"]
TABLE_HEADER = ["Config", "Input BNCs", "", "Output"]  # "Input BNCs" spans columns 1-2
TABLE_ROWS = [
    ["Standard", "IRIG B", "10 MHz", "off"],
    ["FlexPort", "1 MHz", "5 MHz", "off"],
]


def _write_fixture(path: Path) -> None:
    import fitz  # PyMuPDF, part of the image

    doc = fitz.open()
    for page_no in range(3):
        page = doc.new_page()
        # Running header on every page — Docling turns these into a "##" each.
        page.insert_text((72, 50), RUNNING_HEADER, fontsize=16)
        if page_no == 0:
            # Two-column body: reading order must keep each column's paragraphs in order.
            y = 100.0
            for para in LEFT_COLUMN:
                page.insert_textbox(fitz.Rect(72, y, 300, y + 90), para, fontsize=10)
                y += 95
            y = 100.0
            for para in RIGHT_COLUMN:
                page.insert_textbox(fitz.Rect(320, y, 545, y + 90), para, fontsize=10)
                y += 95
            # Bullets with a real bullet glyph, as PDF generators emit them.
            y = 310.0
            page.insert_text((72, y), "Features", fontsize=13)
            for item in BULLETS:
                y += 18
                page.insert_text((80, y), f"• {item}", fontsize=10)
            # Superscript exponent: "10" at body size, "-7" raised and small.
            y += 40
            page.insert_text((72, y), "Oscillator aging: ±1×10", fontsize=11)
            page.insert_text((72 + fitz.get_text_length("Oscillator aging: ±1×10", fontsize=11) + 1, y - 3), "-7", fontsize=7)
            page.insert_text((300, y), "(1", fontsize=11)
            page.insert_text((300 + fitz.get_text_length("(1", fontsize=11) + 1, y - 3), "st", fontsize=7)
            page.insert_text((300 + fitz.get_text_length("(1", fontsize=11) + 12, y), " 24 hours)", fontsize=11)
            # The doubled-digit line, small font, inside the body area.
            page.insert_text((72, 620), FOOTER, fontsize=6)
        elif page_no == 1:
            # Ruled table with a header cell spanning two columns.
            x0, y0, col_w, row_h = 72.0, 120.0, 110.0, 26.0
            page.insert_text((72, 100), "Timing Input/Output Module", fontsize=13)
            # header: col 0, merged cols 1-2, col 3
            page.draw_rect(fitz.Rect(x0, y0, x0 + col_w, y0 + row_h))
            page.insert_text((x0 + 6, y0 + 17), TABLE_HEADER[0], fontsize=10)
            page.draw_rect(fitz.Rect(x0 + col_w, y0, x0 + 3 * col_w, y0 + row_h))
            page.insert_text((x0 + col_w + 6, y0 + 17), TABLE_HEADER[1], fontsize=10)
            page.draw_rect(fitz.Rect(x0 + 3 * col_w, y0, x0 + 4 * col_w, y0 + row_h))
            page.insert_text((x0 + 3 * col_w + 6, y0 + 17), TABLE_HEADER[3], fontsize=10)
            for r, row in enumerate(TABLE_ROWS, start=1):
                for c, cell in enumerate(row):
                    x, y = x0 + c * col_w, y0 + r * row_h
                    page.draw_rect(fitz.Rect(x, y, x + col_w, y + row_h))
                    page.insert_text((x + 6, y + 17), cell, fontsize=10)
        # A true bottom-margin footer on every page — informative only (see FOOTER note).
        page.insert_text((40, 800), f"Doc 900-00715-000 Rev C  page {page_no + 1}", fontsize=6)
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "ok  " if ok else "FAIL"
        print(f"[{mark}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            self.failures.append(name)


def main() -> int:
    rep = Report()

    # 1. Runtime posture -------------------------------------------------
    uid = os.getuid()
    rep.check("runtime user is not root", uid != 0, f"uid={uid}")

    from atlas.startup_validation import _warn_parse_model_cache

    cache_warning = _warn_parse_model_cache()
    rep.check("parse-model cache readable by this uid", cache_warning is None, cache_warning or "")

    # 2. Parse -------------------------------------------------------------
    import importlib.metadata as md

    from atlas.ingest.docling_adapter import parse_document_path
    from atlas.pipeline.cleanup import CleanupNode
    from atlas.pipeline.guardrails import dropped_facts

    print(f"       docling {md.version('docling')}")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "smoke.pdf"
        _write_fixture(pdf)
        t0 = time.time()
        try:
            result = parse_document_path(doc_path=pdf, source_mime_type="application/pdf")
        except Exception as e:  # noqa: BLE001 — report, don't crash
            rep.check("docling parses the fixture", False, f"{type(e).__name__}: {e}")
            return _finish(rep)
        raw = result.markdown_projection
        rep.check("docling parses the fixture", bool(raw.strip()), f"{len(raw)} chars in {time.time() - t0:.1f}s, method={result.meta.get('extraction_method')}")

    cleaned = asyncio.run(CleanupNode().clean(markdown=raw)).cleaned_markdown

    # 3. Content checks on the raw parse -------------------------------------
    missing = dropped_facts(FOOTER, raw)
    rep.check("every digit group in the small-font footer survives", not missing, f"missing={missing}" if missing else "713-4113, 215-4996, 1100/2200/3399/4455 all present")

    order_ok = all(
        raw.find(a) != -1 and raw.find(b) != -1 and raw.find(a) < raw.find(b)
        for a, b in (("ALPHALEFTONE", "ALPHALEFTTWO"), ("BRAVORIGHTONE", "BRAVORIGHTTWO"))
    )
    rep.check("two-column reading order keeps each column in sequence", order_ok)

    rep.check("bulleted items are recovered", all(b in raw for b in BULLETS))
    print(f"[info] bottom-margin footer {'kept' if '900-00715-000' in raw else 'dropped as page furniture'} by docling")
    rep.check("table rows are recovered as a markdown table", any(ln.startswith("|") and "IRIG B" in ln for ln in raw.splitlines()))

    # 4. Cleanup checks ---------------------------------------------------------
    rep.check("no '-  text' / '- • text' list residue after cleanup", not re.search(r"^[-*] ([ •]|\s{2,})", cleaned, re.M))
    rep.check("superscript exponent rejoined (10^-7)", "10^-7" in cleaned or "10-7" in cleaned, "raw had " + repr(next((m.group() for m in re.finditer(r"10[^\n]{0,4}7", raw)), "nothing")))
    heading_lines = [ln for ln in cleaned.splitlines() if re.match(r"^#{1,6}\s+" + re.escape(RUNNING_HEADER) + r"\s*$", ln)]
    raw_heading_lines = [ln for ln in raw.splitlines() if re.match(r"^#{1,6}\s+" + re.escape(RUNNING_HEADER) + r"\s*$", ln)]
    rep.check("running header appears once as a heading after cleanup", RUNNING_HEADER in cleaned and len(heading_lines) <= 1, f"raw headings={len(raw_heading_lines)}, cleaned={len(heading_lines)}")
    if raw.count("Input BNCs") > 1:
        rep.check("spanned table header collapsed to one cell", cleaned.count("Input BNCs") == 1, f"raw={raw.count('Input BNCs')} cleaned={cleaned.count('Input BNCs')}")
    else:
        print(f"[skip] spanned table header — docling emitted 'Input BNCs' {raw.count('Input BNCs')}x (no span to collapse)")

    return _finish(rep)


def _finish(rep: Report) -> int:
    if rep.failures:
        print(f"\nFAILED {len(rep.failures)} check(s): {rep.failures}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
