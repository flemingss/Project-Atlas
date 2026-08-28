from __future__ import annotations

import argparse
from pathlib import Path


def build_pdf(*, out_path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except Exception as e:
        raise SystemExit(
            "Missing dependency 'reportlab'. Install it with: pip install reportlab\n"
            f"Original import error: {e!r}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        title="Atlas Synthetic PDF Test Document",
        author="Project Atlas",
        subject="Synthetic document for PDF ingest evaluation",
    )

    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Atlas Synthetic PDF Test Document", styles["Title"]))
    story.append(Paragraph("Purpose: exercise PDF text extraction + chunking without prod docs.", styles["BodyText"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Section 1 — Plain text", styles["Heading2"]))
    story.append(
        Paragraph(
            "Keyword anchors: ALPHA_BRAVO_CHARLIE, RAG_EVAL_SENTINEL_001, and QDRANT_PAYLOAD_CHECK.",
            styles["BodyText"],
        )
    )
    story.append(
        Paragraph(
            "This paragraph is intentionally long to test wrapping. "
            "Atlas should be able to ingest this PDF, extract text, and chunk it consistently. "
            "If extraction fails, you may see few or zero chunks indexed.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 12))

    story.append(Paragraph("Section 2 — Bullets", styles["Heading2"]))
    story.append(Paragraph("First bullet: ingestion should preserve order.", styles["BodyText"], bulletText="•"))
    story.append(Paragraph("Second bullet: corpus scoping should apply.", styles["BodyText"], bulletText="•"))
    story.append(Paragraph("Third bullet: HITL workflows should remain optional.", styles["BodyText"], bulletText="•"))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Section 3 — Small table", styles["Heading2"]))
    data = [
        ["Field", "Value"],
        ["tenant_id", "local"],
        ["project_id", "default"],
        ["corpus_id", "pdf-synthetic"],
        ["doc_id_hint", "atlas-synth-pdf"],
    ]
    table = Table(data, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Section 4 — Code-ish block", styles["Heading2"]))
    story.append(
        Paragraph(
            "<font name='Courier'>curl -H \"X-Atlas-Admin-Token: ...\" http://localhost:18080/admin/looking-glass/qdrant</font>",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 12))

    story.append(Paragraph("Section 5 — Page break stress", styles["Heading2"]))
    for i in range(1, 45):
        story.append(Paragraph(f"Line {i:02d}: The quick brown fox jumps over the lazy dog.", styles["BodyText"]))

    doc.build(story)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic PDF for Atlas ingestion testing.")
    parser.add_argument(
        "--out",
        default=str(Path("artifacts") / "samples" / "atlas_synthetic.pdf"),
        help="Output PDF path (default: artifacts/samples/atlas_synthetic.pdf)",
    )
    args = parser.parse_args()

    out_path = Path(args.out).resolve()
    build_pdf(out_path=out_path)
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
