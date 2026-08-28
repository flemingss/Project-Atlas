"""Measure and compare ingest quality across a change to the parsing stack.

Typical use around a Docling upgrade:

    # before
    python scripts/ingest_quality.py --input-dir samples --out baseline.json

    # ... upgrade docling, rebuild ...

    python scripts/ingest_quality.py --input-dir samples --out after.json \
        --baseline baseline.json

The comparison run exits non-zero if anything looks like a regression, so it
can gate an upgrade rather than merely inform one.

Ground truth is optional. Put a ``<document>.pdf.expected.txt`` beside any
document, one required string per line, and recall is measured against it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from atlas.eval.ingest_quality import compare, measure_docling, regressions
except ModuleNotFoundError:
    # Support running from a source checkout without `pip install -e .`.
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))
    from atlas.eval.ingest_quality import compare, measure_docling, regressions

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html",
}


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("no documents measured")
        return
    header = f"{'document':<34} {'backend':<34} {'ok':<3} {'secs':>7} {'chars':>8} {'hdgs':>5} {'tbls':>5} {'recall':>7}"
    print(header)
    print("-" * len(header))
    for r in rows:
        recall = r.get("recall")
        recall_s = "-" if recall is None else f"{recall * 100:.0f}%"
        print(
            f"{r['document'][:34]:<34} {r['backend'][:34]:<34} "
            f"{'y' if r['ok'] else 'n':<3} {r['elapsed_s']:>7.2f} {r['chars']:>8} "
            f"{r['headings']:>5} {r['tables']:>5} {recall_s:>7}"
        )
        if r.get("missing"):
            for m in r["missing"][:5]:
                print(f"{'':<34} MISSING: {m[:60]}")
        if r.get("error"):
            print(f"{'':<34} ERROR: {r['error'][:80]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", required=True, help="directory of documents to parse")
    ap.add_argument("--out", help="write metrics JSON here")
    ap.add_argument("--baseline", help="compare against a previous --out file")
    ap.add_argument(
        "--no-table-extraction",
        action="store_true",
        help="also measure with table structure recognition disabled (much faster, flattens tables)",
    )
    args = ap.parse_args()

    root = Path(args.input_dir)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    docs = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _MIME_BY_SUFFIX
    )
    if not docs:
        print(f"no supported documents under {root}", file=sys.stderr)
        return 2

    flags = [True, False] if args.no_table_extraction else [True]
    rows: list[dict] = []
    for doc in docs:
        mime = _MIME_BY_SUFFIX[doc.suffix.lower()]
        for flag in flags:
            print(f"parsing {doc.name} (table_extraction={flag}) ...", file=sys.stderr)
            rows.append(
                measure_docling(doc, mime_type=mime, table_extraction=flag).to_dict()
            )

    print()
    _print_table(rows)

    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")

    if not args.baseline:
        return 0

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    diff = compare(baseline, rows)
    problems = regressions(diff)

    print("\n=== compared with baseline ===")
    for row in diff:
        changed = {
            k: v for k, v in row.items()
            if k.endswith(("_delta", "_changed")) and v not in (0, 0.0)
        }
        if changed:
            print(f"{row['document']} [{row['backend']}]: {changed}")

    if problems:
        print("\nREGRESSIONS:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nno regressions detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
