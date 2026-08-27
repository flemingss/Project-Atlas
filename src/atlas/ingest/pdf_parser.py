"""Layout-aware PDF parser for Project Atlas.

Orchestrates page rendering, layout analysis, hybrid OCR, table structure
recognition, and text merging to produce structured markdown from PDFs.

Derived from RAGFlow's RAGFlowPdfParser (Apache 2.0, InfiniFlow/ragflow).
"""
from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from timeit import default_timer as timer
from typing import Any

import numpy as np

try:
    from .layout_recognizer import LayoutRecognizer
except ImportError:
    # Allow import to succeed even if cv2/onnxruntime unavailable (VLM-only deploys)
    LayoutRecognizer = None  # type: ignore[assignment]

from .model_manager import ModelManager
from .ocr import OCR
from .table_recognizer import TableStructureRecognizer
from .text_extractor import HybridTextExtractor
from .types import (
    LayoutType,
    ParsedRegion,
    PDFParseResult,
    TableResult,
)

logger = logging.getLogger(__name__)

# Module-level lock for thread-safe pdfplumber access
_pdfplumber_lock = threading.Lock()


class LayoutPdfParser:
    """Full layout-aware PDF parser.

    Combines OCR, layout detection, table structure recognition, and
    text merging into a single callable that produces a
    :class:`PDFParseResult`.
    """

    def __init__(self, models_dir: str | Path | None = None) -> None:
        """Initialise all sub-components.

        Parameters
        ----------
        models_dir:
            Path to directory containing ONNX models.  When ``None`` the
            :class:`ModelManager` singleton resolves the path.
        """
        if LayoutRecognizer is None:
            raise ImportError(
                "Docling layout analysis requires cv2 and onnxruntime, which are not available. "
                "This typically means you're using a slim/VLM-only build. "
                "Use the full Dockerfile and docker-compose.yml instead, or switch to VLM ingestion."
            )

        if models_dir is not None:
            models_dir = str(models_dir)

        mgr = ModelManager.get_instance(models_dir)
        mgr.ensure_models()

        self.ocr = OCR(model_dir=models_dir)
        self.layout_recognizer = LayoutRecognizer(model_dir=models_dir)
        self.table_recognizer = TableStructureRecognizer(model_dir=models_dir)
        self.text_extractor = HybridTextExtractor(self.ocr, self.layout_recognizer)

        logger.info("LayoutPdfParser initialised (models_dir=%s)", models_dir)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def __call__(
        self,
        pdf_bytes_or_path: bytes | str | Path,
        from_page: int = 0,
        to_page: int = 100_000,
        zoom: float = 3.0,
    ) -> PDFParseResult:
        """Parse a PDF and return structured regions + markdown.

        Pipeline:
        1. Open PDF with pdfplumber, render pages.
        2. Hybrid OCR each page (programmatic + vision).
        3. Layout recognition (classify regions).
        4. Table structure recognition.
        5. Horizontal then vertical text merging.
        6. Column assignment and reading-order sort.
        7. Markdown assembly.

        Parameters
        ----------
        pdf_bytes_or_path:
            Path to a PDF file or raw PDF bytes.
        from_page:
            0-based inclusive start page.
        to_page:
            0-based exclusive end page (clamped to actual page count).
        zoom:
            Rendering zoom factor (page image DPI = 72 × zoom).

        Returns
        -------
        :class:`PDFParseResult` with regions, tables, markdown, and
        confidence metrics.
        """
        try:
            import pdfplumber
        except ImportError as exc:
            raise ImportError(
                "pdfplumber is required for PDF parsing. "
                "Install it with: pip install pdfplumber"
            ) from exc

        t0 = timer()

        # ------------------------------------------------------------------
        # Step 1: Open PDF and render pages
        # ------------------------------------------------------------------
        page_images: list[Any] = []       # PIL Images
        page_chars: list[list[dict]] = []  # pdfplumber char dicts per page
        total_pages = 0

        with _pdfplumber_lock:
            try:
                if isinstance(pdf_bytes_or_path, (str, Path)):
                    pdf = pdfplumber.open(str(pdf_bytes_or_path))
                else:
                    pdf = pdfplumber.open(BytesIO(pdf_bytes_or_path))
            except Exception:
                logger.exception("Failed to open PDF")
                return PDFParseResult()

            try:
                total_pages = len(pdf.pages)
                end = min(to_page, total_pages)
                selected = pdf.pages[from_page:end]

                for page in selected:
                    try:
                        pi = page.to_image(resolution=int(72 * zoom))
                        page_images.append(pi.annotated)
                    except Exception:
                        logger.warning("Failed to render page %d", page.page_number)
                        page_images.append(None)

                    try:
                        chars = page.dedupe_chars()
                        # dedupe_chars returns a Page object in newer pdfplumber;
                        # extract the .chars list if needed.
                        if hasattr(chars, "chars"):
                            chars = chars.chars
                        page_chars.append(chars if isinstance(chars, list) else [])
                    except Exception:
                        logger.warning(
                            "Failed to extract chars for page %d",
                            page.page_number,
                        )
                        page_chars.append([])
            finally:
                pdf.close()

        n_pages = len(page_images)
        if n_pages == 0:
            logger.warning("No pages to process")
            return PDFParseResult(page_count=total_pages)

        logger.info(
            "Rendered %d pages in %.2fs (zoom=%.1f)",
            n_pages, timer() - t0, zoom,
        )

        # ------------------------------------------------------------------
        # Step 2: Hybrid OCR each page
        # ------------------------------------------------------------------
        t1 = timer()
        per_page_boxes: list[list[dict]] = []
        mean_heights: dict[int, float] = {}
        ocr_scores: list[float] = []
        total_prog_chars = 0

        for pn in range(n_pages):
            img = page_images[pn]
            if img is None:
                per_page_boxes.append([])
                mean_heights[pn] = 10.0
                continue

            img_np = np.array(img)
            chars = page_chars[pn] if pn < len(page_chars) else []
            total_prog_chars += len(chars)

            boxes, mh = self.text_extractor.extract_page(
                img_np, chars, page_number=pn, zoom=zoom,
            )
            per_page_boxes.append(boxes)
            mean_heights[pn] = mh

        logger.info("OCR completed in %.2fs", timer() - t1)

        # ------------------------------------------------------------------
        # Step 3: Layout recognition
        # ------------------------------------------------------------------
        t2 = timer()
        valid_images = []
        valid_indices = []
        for pn in range(n_pages):
            if page_images[pn] is not None:
                valid_images.append(page_images[pn])
                valid_indices.append(pn)

        if valid_images:
            # LayoutRecognizer.__call__ expects same-length lists
            valid_ocr = [per_page_boxes[i] for i in valid_indices]
            tagged_boxes, page_layouts = self.layout_recognizer(
                valid_images, valid_ocr,
                scale_factor=zoom, thr=0.2, drop=True,
            )
        else:
            tagged_boxes = []
            page_layouts = []

        # Collect layout scores
        layout_scores: list[float] = []
        for pl in page_layouts:
            for det in pl:
                if "score" in det:
                    layout_scores.append(float(det["score"]))

        logger.info("Layout recognition completed in %.2fs", timer() - t2)

        # ------------------------------------------------------------------
        # Step 4: Table structure recognition
        # ------------------------------------------------------------------
        t3 = timer()
        table_results: list[TableResult] = []

        # Collect table regions from page_layouts
        table_imgs: list[Any] = []
        table_meta: list[dict] = []
        MARGIN = 10

        for pl_idx, pn in enumerate(valid_indices):
            if pl_idx >= len(page_layouts):
                break
            for lt in page_layouts[pl_idx]:
                if lt.get("type") != "table":
                    continue
                img = page_images[pn]
                if img is None:
                    continue
                left = max(0, lt["x0"] * zoom - MARGIN)
                top = max(0, lt["top"] * zoom - MARGIN)
                right = lt["x1"] * zoom + MARGIN
                bottom = lt["bottom"] * zoom + MARGIN
                try:
                    cropped = img.crop((left, top, right, bottom))
                    table_imgs.append(cropped)
                    table_meta.append({
                        "page_number": pn,
                        "x0": lt["x0"],
                        "top": lt["top"],
                        "x1": lt["x1"],
                        "bottom": lt["bottom"],
                    })
                except Exception:
                    logger.warning("Failed to crop table on page %d", pn)

        if table_imgs:
            tsr_results = self.table_recognizer(table_imgs, thr=0.2)
            # Tag table boxes with R/C/H/SP for construct_table
            table_results = self._process_tables(
                tagged_boxes, tsr_results, table_meta, zoom,
            )
        logger.info(
            "Table recognition completed in %.2fs (%d tables)",
            timer() - t3, len(table_results),
        )

        # ------------------------------------------------------------------
        # Step 5: Text merging
        # ------------------------------------------------------------------
        t4 = timer()

        # Build cumulative page heights for coordinate adjustment
        page_cum_heights = [0.0]
        for pn in range(n_pages):
            img = page_images[pn]
            h = img.size[1] / zoom if img is not None else 0
            page_cum_heights.append(page_cum_heights[-1] + h)

        # Adjust Y coordinates to cumulative space
        for b in tagged_boxes:
            pn = b.get("page_number", 0)
            if pn < len(page_cum_heights):
                b["top"] += page_cum_heights[pn]
                b["bottom"] += page_cum_heights[pn]

        # Column assignment
        tagged_boxes, column_count = HybridTextExtractor.assign_columns(tagged_boxes)

        # Horizontal merge
        tagged_boxes = HybridTextExtractor.merge_text_horizontal(
            tagged_boxes, mean_heights,
        )

        # Determine if English
        is_english = self._detect_english(tagged_boxes)

        # Vertical merge
        tagged_boxes = HybridTextExtractor.merge_text_vertical(
            tagged_boxes, mean_heights, is_english=is_english,
        )

        logger.info("Text merging completed in %.2fs", timer() - t4)

        # ------------------------------------------------------------------
        # Step 6: Reading order
        # ------------------------------------------------------------------
        tagged_boxes = self._reading_order(tagged_boxes)

        # ------------------------------------------------------------------
        # Step 7: Assemble regions and markdown
        # ------------------------------------------------------------------
        regions = self._build_regions(tagged_boxes, page_cum_heights)
        markdown = self._assemble_markdown(tagged_boxes, table_results, page_cum_heights)

        # ------------------------------------------------------------------
        # Confidence metrics
        # ------------------------------------------------------------------
        mean_ocr_conf = float(np.mean(ocr_scores)) if ocr_scores else 1.0
        layout_conf = float(np.mean(layout_scores)) if layout_scores else 1.0

        # OCR coverage: fraction of page area covered by text boxes
        total_page_area = 0.0
        total_text_area = 0.0
        for pn in range(n_pages):
            img = page_images[pn]
            if img is None:
                continue
            pw, ph = img.size
            total_page_area += (pw / zoom) * (ph / zoom)
        for b in tagged_boxes:
            w = b.get("x1", 0) - b.get("x0", 0)
            h = b.get("bottom", 0) - b.get("top", 0)
            total_text_area += w * h
        ocr_coverage = (
            total_text_area / total_page_area if total_page_area > 0 else 0.0
        )

        # Scanned detection: few programmatic chars per page on average
        avg_prog_chars = total_prog_chars / n_pages if n_pages > 0 else 0
        estimated_scanned = avg_prog_chars < 10

        result = PDFParseResult(
            regions=regions,
            tables=table_results,
            markdown=markdown,
            metadata={
                "column_count": column_count,
                "is_english": is_english,
            },
            page_count=total_pages,
            mean_ocr_confidence=mean_ocr_conf,
            layout_confidence=layout_conf,
            ocr_coverage=min(ocr_coverage, 1.0),
            estimated_is_scanned=estimated_scanned,
        )

        logger.info(
            "PDF parsing complete: %d pages, %d regions, %d tables, "
            "scanned=%s, %.2fs total",
            n_pages,
            len(regions),
            len(table_results),
            estimated_scanned,
            timer() - t0,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_tables(
        self,
        boxes: list[dict],
        tsr_results: list[list[dict]],
        table_meta: list[dict],
        zoom: float,
    ) -> list[TableResult]:
        """Tag table boxes with R/C/H/SP and construct HTML tables."""
        results: list[TableResult] = []

        # Flatten TSR components with page/layout annotations
        all_tsr_components: list[dict] = []
        for tbl_idx, (tsr, meta) in enumerate(zip(tsr_results, table_meta)):
            for comp in tsr:
                comp["pn"] = meta["page_number"]
                comp["layoutno"] = tbl_idx
                all_tsr_components.append(comp)

        if not all_tsr_components:
            return results

        # Gather and sort components by type
        def _gather(pattern: str, fzy: float = 10, ption: float = 0.6) -> list[dict]:
            eles = LayoutRecognizer.sort_Y_firstly(
                [r for r in all_tsr_components if re.match(pattern, r["label"])],
                fzy,
            )
            eles = LayoutRecognizer.layouts_cleanup(boxes, eles, 5, ption)
            return LayoutRecognizer.sort_Y_firstly(eles, 0)

        headers = _gather(r".*header$")
        rows = _gather(r".* (row|header)")
        spans = _gather(r".*spanning")
        columns = sorted(
            [r for r in all_tsr_components if re.match(r"table column$", r["label"])],
            key=lambda x: (x["pn"], x["layoutno"], x["x0"]),
        )
        columns = LayoutRecognizer.layouts_cleanup(boxes, columns, 5, 0.5)

        # Tag each table box with R, C, H, SP
        for b in boxes:
            if b.get("layout_type", "") != "table":
                continue

            ii = LayoutRecognizer.find_overlapped_with_threshold(b, rows, thr=0.3)
            if ii is not None:
                b["R"] = ii
                b["R_top"] = rows[ii]["top"]
                b["R_bott"] = rows[ii]["bottom"]

            ii = LayoutRecognizer.find_overlapped_with_threshold(b, headers, thr=0.3)
            if ii is not None:
                b["H_top"] = headers[ii]["top"]
                b["H_bott"] = headers[ii]["bottom"]
                b["H_left"] = headers[ii]["x0"]
                b["H_right"] = headers[ii]["x1"]
                b["H"] = ii

            ii = LayoutRecognizer.find_horizontally_tightest_fit(b, columns)
            if ii is not None:
                b["C"] = ii
                b["C_left"] = columns[ii]["x0"]
                b["C_right"] = columns[ii]["x1"]

            ii = LayoutRecognizer.find_overlapped_with_threshold(b, spans, thr=0.3)
            if ii is not None:
                b["H_top"] = spans[ii]["top"]
                b["H_bott"] = spans[ii]["bottom"]
                b["H_left"] = spans[ii]["x0"]
                b["H_right"] = spans[ii]["x1"]
                b["SP"] = ii

        # Group table boxes by layout number and construct HTML
        table_groups: dict[str, list[dict]] = defaultdict(list)
        for b in boxes:
            if b.get("layout_type") != "table":
                continue
            lout_key = f'{b.get("page_number", 0)}-{b.get("layoutno", "")}'
            table_groups[lout_key].append(b)

        for key, tbl_boxes in table_groups.items():
            if not tbl_boxes:
                continue
            tbl_boxes_copy = deepcopy(tbl_boxes)
            avg_h = float(np.mean([(b["bottom"] - b["top"]) / 2 for b in tbl_boxes_copy]))
            tbl_boxes_copy = LayoutRecognizer.sort_Y_firstly(tbl_boxes_copy, avg_h)

            try:
                html = TableStructureRecognizer.construct_table(
                    tbl_boxes_copy, html=True,
                )
            except Exception:
                logger.warning("Table construction failed for %s", key)
                html = ""

            if isinstance(html, str) and html:
                pn = tbl_boxes[0].get("page_number", 0)
                results.append(TableResult(
                    html=html,
                    page_number=pn,
                    confidence=1.0,
                ))

        return results

    @staticmethod
    def _detect_english(boxes: list[dict]) -> bool:
        """Heuristic: sample text to determine if predominantly English."""
        if not boxes:
            return False
        import random
        sample = random.choices(boxes, k=min(30, len(boxes)))
        combined = " ".join(b.get("text", "") for b in sample)
        match = re.search(
            r"[ a-zA-Z0-9,/;:'\[\]\(\)!@#$%^&*\"?<>._-]{30,}", combined
        )
        return match is not None

    @staticmethod
    def _reading_order(boxes: list[dict]) -> list[dict]:
        """Sort boxes into reading order: page → column → Y position."""
        pages: dict[int, dict[int, list[dict]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for b in boxes:
            pg = b.get("page_number", 0)
            col = b.get("col_id", 0)
            pages[pg][col].append(b)

        for pg in pages:
            for col in pages[pg]:
                pages[pg][col].sort(key=lambda x: (x.get("top", 0), x.get("x0", 0)))

        ordered: list[dict] = []
        for pg in sorted(pages.keys()):
            for col in sorted(pages[pg].keys()):
                ordered.extend(pages[pg][col])

        return ordered

    @staticmethod
    def _build_regions(
        boxes: list[dict],
        page_cum_heights: list[float],
    ) -> list[ParsedRegion]:
        """Convert tagged boxes to ParsedRegion objects."""
        regions: list[ParsedRegion] = []
        for b in boxes:
            text = b.get("text", "").strip()
            if not text:
                continue
            lt_str = b.get("layout_type", "text")
            try:
                lt = LayoutType(lt_str) if lt_str else LayoutType.TEXT
            except ValueError:
                lt = LayoutType.TEXT

            regions.append(ParsedRegion(
                layout_type=lt,
                text=text,
                page_number=b.get("page_number", 0),
                x0=b.get("x0", 0),
                top=b.get("top", 0),
                x1=b.get("x1", 0),
                bottom=b.get("bottom", 0),
            ))
        return regions

    def _assemble_markdown(
        self,
        boxes: list[dict],
        table_results: list[TableResult],
        page_cum_heights: list[float],
    ) -> str:
        """Convert boxes and table results into structured markdown.

        Mapping:
        - ``title`` → ``## {text}``
        - ``table`` → HTML table (from table_results)
        - ``figure_caption`` / ``table_caption`` → ``*{text}*``
        - ``figure`` → ``[Figure]``
        - everything else → paragraph text

        Blank lines are inserted between non-adjacent regions.
        """
        # Build a mapping of (page, layoutno) → table HTML for quick lookup
        table_htmls: dict[str, str] = {}
        for tr in table_results:
            table_htmls[str(tr.page_number)] = tr.html

        # Track which tables have been emitted
        emitted_tables: set[str] = set()
        parts: list[str] = []
        prev_bottom = -1.0
        prev_page = -1

        for b in boxes:
            lt = b.get("layout_type", "")
            text = b.get("text", "").strip()
            pn = b.get("page_number", 0)

            # Insert blank line between non-adjacent regions
            curr_top = b.get("top", 0)
            if prev_page >= 0 and (pn != prev_page or curr_top - prev_bottom > 5):
                if parts and parts[-1] != "":
                    parts.append("")

            prev_bottom = b.get("bottom", 0)
            prev_page = pn

            if lt == "title":
                parts.append(f"## {text}")
            elif lt == "table":
                # Try to find matching table HTML
                lout_key = f'{pn}-{b.get("layoutno", "")}'
                if lout_key not in emitted_tables:
                    # Find best matching table result
                    html = ""
                    for tr in table_results:
                        if tr.page_number == pn:
                            html = tr.html
                            break
                    if html:
                        parts.append(html)
                        emitted_tables.add(lout_key)
                    elif text:
                        parts.append(text)
            elif lt in ("figure_caption", "table_caption"):
                parts.append(f"*{text}*")
            elif lt == "figure":
                if text:
                    parts.append(f"[Figure: {text}]")
                else:
                    parts.append("[Figure]")
            else:
                if text:
                    parts.append(text)

        # Join with newlines; collapse multiple blank lines
        raw = "\n\n".join(parts)
        # Collapse runs of 3+ newlines to 2
        markdown = re.sub(r"\n{3,}", "\n\n", raw).strip()
        return markdown
