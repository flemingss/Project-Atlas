"""Hybrid text extraction — merges programmatic PDF chars with OCR.

Handles per-page text extraction, horizontal/vertical merging, and
multi-column layout detection.

Derived from RAGFlow's RAGFlowPdfParser OCR and text-merge logic
(Apache 2.0, InfiniFlow/ragflow).
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from .layout_recognizer import LayoutRecognizer
from .ocr import OCR

logger = logging.getLogger(__name__)

# CJK / Unicode punctuation character sets used by merge heuristics.
# Defined via \uXXXX escapes to avoid Python 3.14+ confusable-character errors.
_CONCAT_TAIL = set(",;:'\"\uff0c\u3001\u2019\u201d\uff1b\uff1a-")
_CONCAT_PRETAIL = set(",;:'\"\uff0c\u2019\u201d\u3001\uff1b\uff1a")
_CONCAT_HEAD = set("\u3002\uff1b\uff1f\uff01?\u201d\uff09),\uff0c\u3001\uff1a")
_TERM_TAIL = set("\u3002\uff1f\uff01?")


class HybridTextExtractor:
    """Extracts text from PDF page images using a hybrid pipeline.

    Combines pdfplumber programmatic character data with ONNX-based
    OCR detection and recognition to produce a comprehensive set of
    text boxes per page.
    """

    def __init__(self, ocr: OCR, layout_recognizer: LayoutRecognizer) -> None:
        self.ocr = ocr
        self.layout_recognizer = layout_recognizer

    # ------------------------------------------------------------------
    # Per-page extraction
    # ------------------------------------------------------------------

    def extract_page(
        self,
        page_image: np.ndarray,
        pdfplumber_chars: list[dict[str, Any]],
        page_number: int,
        zoom: float = 3.0,
    ) -> tuple[list[dict[str, Any]], float]:
        """Extract text boxes from a single page.

        Steps:
        1. Run OCR text detection to get bounding boxes.
        2. Convert detected regions to box dicts.
        3. Merge pdfplumber programmatic chars into overlapping OCR boxes.
        4. For boxes without programmatic text, run batch OCR recognition.
        5. Filter empty boxes and compute mean height.

        Parameters
        ----------
        page_image:
            Page image as BGR numpy array (rendered at ``72 * zoom`` DPI).
        pdfplumber_chars:
            Characters from ``page.dedupe_chars()`` — list of dicts with
            ``x0``, ``top``, ``x1``, ``bottom``, ``text`` keys.
        page_number:
            0-based page index.
        zoom:
            Zoom factor relative to 72 DPI.

        Returns
        -------
        ``(boxes, mean_height)`` — list of OCR box dicts and the median
        character height for the page.
        """
        # Step 1: detect text regions
        det_result = self.ocr.detect(page_image)
        if det_result is None:
            logger.debug("Page %d: no text detected by OCR", page_number)
            return [], 0.0

        det_boxes = list(det_result)

        # Step 2: convert to box dicts, normalise coordinates
        bxs: list[dict[str, Any]] = []
        for box_coords, (_txt, _score) in det_boxes:
            box_arr = np.array(box_coords) if not isinstance(box_coords, np.ndarray) else box_coords
            x0 = float(box_arr[0][0]) / zoom
            x1 = float(box_arr[1][0]) / zoom
            top = float(box_arr[0][1]) / zoom
            bottom = float(box_arr[-1][1]) / zoom
            if x0 > x1 or top > bottom:
                continue
            bxs.append({
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
                "text": "",
                "page_number": page_number,
                "chars": [],
            })

        if not bxs:
            return [], 0.0

        # Compute preliminary mean_height from pdfplumber chars
        if pdfplumber_chars:
            mean_height = float(np.median(sorted([c.get("height", c["bottom"] - c["top"]) for c in pdfplumber_chars])))
        else:
            mean_height = 0.0

        # Sort boxes by Y position
        bxs = LayoutRecognizer.sort_Y_firstly(bxs, mean_height / 3 if mean_height > 0 else 5)

        # Step 3: merge pdfplumber chars into overlapping OCR boxes
        lefted_chars: list[dict] = []
        for c in pdfplumber_chars:
            # Build a box dict for the char for overlap matching
            char_box = {
                "x0": c["x0"],
                "x1": c["x1"],
                "top": c["top"],
                "bottom": c["bottom"],
            }
            ii = LayoutRecognizer.find_overlapped_with_threshold(char_box, bxs, thr=0.3)
            if ii is None:
                lefted_chars.append(c)
                continue
            ch = c["bottom"] - c["top"]
            bh = bxs[ii]["bottom"] - bxs[ii]["top"]
            if bh > 0 and abs(ch - bh) / max(ch, bh) >= 0.7 and c.get("text", "") != " ":
                lefted_chars.append(c)
                continue
            bxs[ii]["chars"].append(c)

        # Sort chars within each box and build text
        for b in bxs:
            if not b["chars"]:
                del b["chars"]
                continue
            char_heights = [c.get("height", c["bottom"] - c["top"]) for c in b["chars"]]
            m_ht = float(np.mean(char_heights)) if char_heights else 5
            for c in LayoutRecognizer.sort_Y_firstly(b["chars"], m_ht):
                char_text = c.get("text", "")
                if char_text == " " and b["text"]:
                    if re.match(r"[0-9a-zA-Zа-яА-Я,.?;:!%%]", b["text"][-1]):
                        b["text"] += " "
                else:
                    b["text"] += char_text
            del b["chars"]

        # Step 4: batch recognition for boxes without text
        boxes_to_rec: list[dict] = []
        for b in bxs:
            if not b["text"]:
                left = b["x0"] * zoom
                right = b["x1"] * zoom
                top_ = b["top"] * zoom
                bott = b["bottom"] * zoom
                pts = np.array(
                    [[left, top_], [right, top_], [right, bott], [left, bott]],
                    dtype=np.float32,
                )
                b["_crop"] = self.ocr.get_rotate_crop_image(page_image, pts)
                boxes_to_rec.append(b)

        if boxes_to_rec:
            crops = [b["_crop"] for b in boxes_to_rec]
            texts = self.ocr.recognize_batch(crops)
            for i, b in enumerate(boxes_to_rec):
                b["text"] = texts[i]
                del b["_crop"]

        # Step 5: filter empty boxes
        bxs = [b for b in bxs if b.get("text", "")]

        # Recompute mean_height from actual OCR boxes
        if bxs:
            mean_height = float(np.median([b["bottom"] - b["top"] for b in bxs]))
        elif mean_height == 0.0:
            mean_height = 10.0

        return bxs, mean_height

    # ------------------------------------------------------------------
    # Horizontal merging
    # ------------------------------------------------------------------

    @staticmethod
    def merge_text_horizontal(
        boxes: list[dict[str, Any]],
        mean_heights: dict[int, float],
    ) -> list[dict[str, Any]]:
        """Horizontally merge adjacent boxes in the same layout region.

        Boxes are merged when they:
        - Are on the same page and in the same column (``col_id``)
        - Have the same ``layoutno``
        - Are not table/figure/equation regions
        - Are close vertically (y distance < mean_height / 3)

        Parameters
        ----------
        boxes:
            Sorted list of OCR box dicts.
        mean_heights:
            Per-page mean character heights, keyed by page number.

        Returns
        -------
        Merged list of box dicts (modified in place).
        """
        i = 0
        while i < len(boxes) - 1:
            b = boxes[i]
            b_ = boxes[i + 1]

            if b["page_number"] != b_["page_number"]:
                i += 1
                continue
            if b.get("col_id") != b_.get("col_id"):
                i += 1
                continue
            if b.get("layoutno", "0") != b_.get("layoutno", "1"):
                i += 1
                continue
            if b.get("layout_type", "") in ("table", "figure", "equation"):
                i += 1
                continue

            mh = mean_heights.get(b["page_number"], 10)
            y_dis = abs(
                (b_["top"] + b_["bottom"] - b["top"] - b["bottom"]) / 2
            )
            if y_dis < mh / 3:
                boxes[i]["x1"] = b_["x1"]
                boxes[i]["top"] = (b["top"] + b_["top"]) / 2
                boxes[i]["bottom"] = (b["bottom"] + b_["bottom"]) / 2
                boxes[i]["text"] += b_["text"]
                boxes.pop(i + 1)
                continue
            i += 1
        return boxes

    # ------------------------------------------------------------------
    # Vertical merging
    # ------------------------------------------------------------------

    @staticmethod
    def merge_text_vertical(
        boxes: list[dict[str, Any]],
        mean_heights: dict[int, float],
        is_english: bool = False,
    ) -> list[dict[str, Any]]:
        """Vertically merge boxes into paragraphs.

        Boxes are merged when they:
        - Have the same ``layoutno``
        - Are not too far apart vertically (< 1.5× mean_height)
        - Have sufficient horizontal overlap (> 30 %)
        - Show continuation signals (comma/semicolon at end) rather than
          termination signals (period at end)

        Parameters
        ----------
        boxes:
            Sorted list of OCR box dicts.
        mean_heights:
            Per-page mean character heights.
        is_english:
            Whether the document is primarily English.

        Returns
        -------
        Merged list of box dicts.
        """
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        for b in boxes:
            grouped[(b["page_number"], "x")].append(b)

        merged_boxes: list[dict[str, Any]] = []
        for (pg, _), grp in grouped.items():
            grp = sorted(grp, key=lambda x: (x["top"], x["x0"]))
            if not grp:
                continue

            mh = mean_heights.get(pg, 10)
            if mh <= 0:
                mh = float(np.median([b["bottom"] - b["top"] for b in grp])) if grp else 10

            i = 0
            while i + 1 < len(grp):
                b = grp[i]
                b_ = grp[i + 1]

                # Remove page-number-like lines at page boundaries
                if b.get("page_number", 0) < b_.get("page_number", 0) and re.match(
                    r"[0-9  •一—-]+$", b["text"]
                ):
                    grp.pop(i)
                    continue

                if not b["text"].strip():
                    grp.pop(i)
                    continue

                if b.get("layoutno") != b_.get("layoutno"):
                    i += 1
                    continue

                # Too far apart vertically
                if b_["top"] - b["bottom"] > mh * 1.5:
                    i += 1
                    continue

                # Insufficient horizontal overlap
                overlap = max(
                    0, min(b["x1"], b_["x1"]) - max(b["x0"], b_["x0"])
                )
                denom = max(1, min(b["x1"] - b["x0"], b_["x1"] - b_["x0"]))
                if overlap / denom < 0.3:
                    i += 1
                    continue

                # Continuation signals
                concatting = [
                    b["text"].strip()[-1] in _CONCAT_TAIL,
                    len(b["text"].strip()) > 1
                    and b["text"].strip()[-2] in _CONCAT_PRETAIL,
                    b_["text"].strip()
                    and b_["text"].strip()[0] in _CONCAT_HEAD,
                ]
                # Termination signals
                feats = [
                    b.get("layoutno", 0) != b_.get("layoutno", 0),
                    b["text"].strip()[-1] in _TERM_TAIL,
                    is_english and b["text"].strip()[-1] in ".!?",
                    b["page_number"] == b_["page_number"]
                    and b_["top"] - b["bottom"] > mh * 1.5,
                ]
                # Detach signals
                detach = [b["x1"] < b_["x0"], b["x0"] > b_["x1"]]

                if (any(feats) and not any(concatting)) or any(detach):
                    i += 1
                    continue

                # Merge
                b["text"] = (b["text"].rstrip() + " " + b_["text"].lstrip()).strip()
                b["bottom"] = b_["bottom"]
                b["x0"] = min(b["x0"], b_["x0"])
                b["x1"] = max(b["x1"], b_["x1"])
                grp.pop(i + 1)

            merged_boxes.extend(grp)

        return merged_boxes

    # ------------------------------------------------------------------
    # Column assignment
    # ------------------------------------------------------------------

    @staticmethod
    def assign_columns(
        boxes: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Detect multi-column layout using KMeans clustering.

        For each page, clusters the ``x0`` values of boxes to detect
        columns, then assigns a ``col_id`` to each box.

        Parameters
        ----------
        boxes:
            List of box dicts with ``x0``, ``x1``, ``page_number`` keys.

        Returns
        -------
        ``(boxes, global_column_count)`` — annotated boxes and the
        majority-vote column count across pages.
        """
        if not boxes:
            return boxes, 1

        if all("col_id" in b for b in boxes):
            majority = Counter(b["col_id"] for b in boxes).most_common(1)
            return boxes, (majority[0][0] + 1) if majority else 1

        try:
            from sklearn.cluster import KMeans
            from sklearn.metrics import silhouette_score as _silhouette
        except ImportError:
            logger.warning("scikit-learn not available; assuming single column")
            for b in boxes:
                b["col_id"] = 0
            return boxes, 1

        by_page: dict[int, list[dict]] = defaultdict(list)
        for b in boxes:
            by_page[b["page_number"]].append(b)

        page_cols: dict[int, int] = {}

        for pg, bxs in by_page.items():
            if not bxs:
                page_cols[pg] = 1
                continue

            x0s_raw = np.array([b["x0"] for b in bxs], dtype=float)
            min_x0 = float(np.min(x0s_raw))
            max_x1 = float(np.max([b["x1"] for b in bxs]))
            width = max_x1 - min_x0
            indent_tol = width * 0.12

            # Snap indented starts to the leftmost value
            x0s = []
            for x in x0s_raw:
                x0s.append([min_x0] if abs(x - min_x0) < indent_tol else [x])
            x0s_arr = np.array(x0s, dtype=float)

            max_try = min(4, len(bxs))
            if max_try < 2:
                max_try = 1
            best_k = 1
            best_score = -1.0

            for k in range(1, max_try + 1):
                km = KMeans(n_clusters=k, n_init="auto", random_state=42)
                labels = km.fit_predict(x0s_arr)
                if k > 1:
                    try:
                        score = _silhouette(x0s_arr, labels)
                    except ValueError:
                        continue
                else:
                    score = 0.0
                if score > best_score:
                    best_score = score
                    best_k = k

            page_cols[pg] = best_k
            logger.debug("Page %d: best_k=%d, score=%.2f", pg, best_k, best_score)

        global_cols = Counter(page_cols.values()).most_common(1)[0][0]
        logger.info("Global column count (majority vote): %d", global_cols)

        # Final column assignment per page
        for pg, bxs in by_page.items():
            if not bxs:
                continue
            k = page_cols[pg]
            if len(bxs) < k:
                k = 1
            x0s_arr = np.array([[b["x0"]] for b in bxs], dtype=float)
            km = KMeans(n_clusters=k, n_init="auto", random_state=42)
            labels = km.fit_predict(x0s_arr)
            centers = km.cluster_centers_.flatten()
            order = np.argsort(centers)
            remap = {int(orig): new for new, orig in enumerate(order)}
            for b, lb in zip(bxs, labels):
                b["col_id"] = remap[int(lb)]

        return boxes, global_cols
