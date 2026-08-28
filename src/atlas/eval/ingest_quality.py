"""Measure ingest quality so a change to the stack can be judged, not guessed.

The parsing stack — Docling, its layout and TableFormer models, the fallback
deepdoc parser — is the part of Atlas most likely to change underneath the
code, and the part whose regressions are least visible. A parser upgrade does
not raise; it quietly returns slightly worse text, and nothing downstream
notices until a retrieval answer is wrong.

This module produces a comparable measurement of a parse: how much text came
out, how much structure survived, how long it took, and (when ground truth is
supplied) how much of the document's known content was actually recovered.

Two runs can be diffed, which is the point. Before upgrading Docling, capture a
baseline; after, compare. Deltas are what tell you whether a release helped.

Nothing here asserts a threshold — thresholds belong in tests. This reports.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# A markdown table separator row: |---|---|
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_WORD = re.compile(r"\b\w[\w'-]*\b")


@dataclass
class ParseMetrics:
    """What one backend produced for one document."""

    document: str
    backend: str
    ok: bool
    elapsed_s: float = 0.0
    error: str = ""

    chars: int = 0
    words: int = 0
    headings: int = 0
    tables: int = 0
    # From the adapter's meta — e.g. embedded_text vs ocr. A silent switch to
    # OCR on a born-digital document is a quality regression on its own.
    extraction_method: str = ""
    converter: str = ""

    # Ground-truth recall, when expectations were supplied.
    expected_total: int = 0
    expected_found: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        if not self.expected_total:
            return None
        return self.expected_found / self.expected_total

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recall"] = self.recall
        return d


def measure_markdown(markdown: str) -> dict[str, int]:
    """Structural counts for a markdown projection."""
    return {
        "chars": len(markdown),
        "words": len(_WORD.findall(markdown)),
        "headings": len(_HEADING.findall(markdown)),
        "tables": len(_TABLE_SEP.findall(markdown)),
    }


def check_expectations(markdown: str, expected: list[str]) -> tuple[int, list[str]]:
    """Return (found_count, missing).

    Comparison is case-insensitive and whitespace-normalised: parsers legitimately
    re-wrap lines and normalise cell text (Docling 2.76 rewrites ``1E-11`` as
    ``1e-11``), and treating that as a miss would drown the real signal.
    """
    haystack = " ".join(markdown.lower().split())
    missing = [e for e in expected if " ".join(e.lower().split()) not in haystack]
    return len(expected) - len(missing), missing


def load_expectations(doc_path: Path) -> list[str]:
    """Read ``<document>.expected.txt`` if present — one required string per line.

    Keeping ground truth beside the document means a fixture and its claims
    cannot drift apart the way a hardcoded list in a test does.
    """
    sidecar = doc_path.with_suffix(doc_path.suffix + ".expected.txt")
    if not sidecar.is_file():
        return []
    return [
        line.strip()
        for line in sidecar.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def measure_docling(
    doc_path: Path,
    *,
    mime_type: str = "application/pdf",
    table_extraction: bool = True,
) -> ParseMetrics:
    """Run the real Docling adapter over one document and measure the result."""
    from atlas.ingest.docling_adapter import parse_document_path

    backend = f"docling(table_extraction={table_extraction})"
    started = time.perf_counter()
    try:
        parsed = parse_document_path(
            doc_path=doc_path,
            source_mime_type=mime_type,
            table_extraction=table_extraction,
        )
    except Exception as exc:  # reporting tool: an error is a datapoint
        return ParseMetrics(
            document=doc_path.name,
            backend=backend,
            ok=False,
            elapsed_s=round(time.perf_counter() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed = round(time.perf_counter() - started, 3)
    md = parsed.markdown_projection or ""
    counts = measure_markdown(md)
    expected = load_expectations(doc_path)
    found, missing = check_expectations(md, expected)

    meta = parsed.meta or {}
    return ParseMetrics(
        document=doc_path.name,
        backend=backend,
        ok=True,
        elapsed_s=elapsed,
        extraction_method=str(meta.get("extraction_method", "")),
        converter=str(meta.get("converter", "")),
        expected_total=len(expected),
        expected_found=found,
        missing=missing,
        chars=counts["chars"],
        words=counts["words"],
        headings=counts["headings"],
        tables=counts["tables"],
    )


def compare(
    baseline: list[dict[str, Any]], current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Diff two runs, keyed on (document, backend).

    Only fields where a change means something are reported. A document present
    in one run and not the other is surfaced rather than silently dropped —
    a shrinking corpus is the easiest way to fake an improvement.
    """
    def key(row: dict[str, Any]) -> tuple[str, str]:
        return (row.get("document", ""), row.get("backend", ""))

    base_by_key = {key(r): r for r in baseline}
    cur_by_key = {key(r): r for r in current}

    rows: list[dict[str, Any]] = []
    for k in sorted(base_by_key.keys() | cur_by_key.keys()):
        b, c = base_by_key.get(k), cur_by_key.get(k)
        if b is None:
            rows.append({"document": k[0], "backend": k[1], "status": "added"})
            continue
        if c is None:
            rows.append({"document": k[0], "backend": k[1], "status": "removed"})
            continue
        delta: dict[str, Any] = {"document": k[0], "backend": k[1], "status": "compared"}
        for metric in ("chars", "words", "headings", "tables", "expected_found"):
            delta[f"{metric}_delta"] = int(c.get(metric, 0)) - int(b.get(metric, 0))
        delta["elapsed_s_delta"] = round(
            float(c.get("elapsed_s", 0)) - float(b.get("elapsed_s", 0)), 3
        )
        if b.get("extraction_method") != c.get("extraction_method"):
            delta["extraction_method_changed"] = (
                f"{b.get('extraction_method')} -> {c.get('extraction_method')}"
            )
        if b.get("ok") != c.get("ok"):
            delta["ok_changed"] = f"{b.get('ok')} -> {c.get('ok')}"
        rows.append(delta)
    return rows


def regressions(diff_rows: list[dict[str, Any]]) -> list[str]:
    """Human-readable list of changes that look like they made things worse."""
    out: list[str] = []
    for row in diff_rows:
        where = f"{row['document']} [{row['backend']}]"
        if row.get("status") == "removed":
            out.append(f"{where}: no longer measured")
        if row.get("ok_changed", "").endswith("False"):
            out.append(f"{where}: parse now fails")
        if int(row.get("expected_found_delta", 0)) < 0:
            out.append(
                f"{where}: recovers {abs(row['expected_found_delta'])} fewer expected strings"
            )
        # Text loss matters; text gain is usually just different whitespace.
        if int(row.get("chars_delta", 0)) < 0 and abs(row["chars_delta"]) > 0.02 * 1000:
            out.append(f"{where}: {abs(row['chars_delta'])} fewer characters extracted")
        if int(row.get("tables_delta", 0)) < 0:
            out.append(f"{where}: {abs(row['tables_delta'])} fewer tables recognised")
        if "extraction_method_changed" in row:
            out.append(f"{where}: extraction method {row['extraction_method_changed']}")
    return out
