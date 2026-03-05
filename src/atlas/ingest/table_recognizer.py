"""Table structure recognition for Project Atlas.

Detects rows, columns, headers, and spanning cells in table images using
an ONNX model (``tsr.onnx``), then constructs HTML tables from the
recognised structure.

Derived from RAGFlow's TableStructureRecognizer (Apache 2.0, InfiniFlow/ragflow).
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    import onnxruntime as ort
except ImportError:
    ort = None  # type: ignore[assignment]

from .layout_recognizer import LayoutRecognizer
from .model_manager import ModelManager

logger = logging.getLogger(__name__)

# TSR labels — corresponds to the tsr.onnx output class indices.
_TSR_LABELS: list[str] = [
    "table",
    "table column",
    "table row",
    "table column header",
    "table projected row header",
    "table spanning cell",
]


class TableStructureRecognizer:
    """ONNX-based table structure recogniser.

    Loads ``tsr.onnx`` and detects rows, columns, headers, and spanning
    cells within a table image.  The static :meth:`construct_table` method
    turns the tagged OCR boxes into an HTML ``<table>``.
    """

    labels = _TSR_LABELS

    # ------------------------------------------------------------------
    # Construction / ONNX loading
    # ------------------------------------------------------------------

    def __init__(self, model_dir: Path | str | None = None) -> None:
        if cv2 is None or ort is None:
            raise ImportError(
                "TableStructureRecognizer requires cv2 and onnxruntime. "
                "These are only available in the full Docker build (not slim). "
                "For VLM-only deployments, disable Docling-based document processing."
            )
        
        if model_dir is None:
            mgr = ModelManager.get_instance()
            mgr.ensure_models()
            model_path = mgr.get_model_path("tsr")
        else:
            model_path = Path(model_dir) / "tsr.onnx"

        if not model_path.exists():
            raise FileNotFoundError(f"TSR model not found: {model_path}")

        logger.info("Loading TSR model from %s", model_path)

        options = ort.SessionOptions()
        options.enable_cpu_mem_arena = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 2

        providers = ["CPUExecutionProvider"]
        try:
            import torch
            if torch.cuda.is_available():
                providers.insert(0, "CUDAExecutionProvider")
        except ImportError:
            pass

        self.ort_sess = ort.InferenceSession(
            str(model_path), sess_options=options, providers=providers,
        )
        self.run_options = ort.RunOptions()

        self.input_names: list[str] = [n.name for n in self.ort_sess.get_inputs()]
        self.output_names: list[str] = [n.name for n in self.ort_sess.get_outputs()]
        self.input_shape: tuple[int, int] = tuple(  # type: ignore[assignment]
            self.ort_sess.get_inputs()[0].shape[2:4],
        )
        self.label_list = _TSR_LABELS

        # Detect model flavour (PaddleDetection vs YOLOv10)
        self._is_paddle = "scale_factor" in self.input_names

        logger.info(
            "TSR model loaded — flavour=%s, labels=%d, input_shape=%s",
            "paddle" if self._is_paddle else "yolo",
            len(self.label_list),
            self.input_shape,
        )

    # ------------------------------------------------------------------
    # Preprocessing (mirrors Recognizer.preprocess from RAGFlow)
    # ------------------------------------------------------------------

    def _preprocess(self, image_list: Sequence[np.ndarray]) -> list[dict[str, Any]]:
        """Prepare images for the ONNX model."""
        inputs: list[dict[str, Any]] = []

        if self._is_paddle:
            target_h, target_w = 800, 608
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)

            for img in image_list:
                if not isinstance(img, np.ndarray):
                    img = np.array(img)
                im = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
                orig_h, orig_w = im.shape[:2]
                scale_y = target_h / orig_h
                scale_x = target_w / orig_w
                im = cv2.resize(im, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
                im = im / 255.0
                im = (im - mean) / std
                im = im.transpose(2, 0, 1).astype(np.float32)

                _, h, w = im.shape
                pad_h = int(np.ceil(h / 32) * 32)
                pad_w = int(np.ceil(w / 32) * 32)
                if pad_h != h or pad_w != w:
                    padded = np.zeros((3, pad_h, pad_w), dtype=np.float32)
                    padded[:, :h, :w] = im
                    im = padded

                inputs.append({
                    "image": im[np.newaxis],
                    "scale_factor": np.array([[scale_y, scale_x]], dtype=np.float32),
                })
        else:
            hh, ww = self.input_shape
            for img in image_list:
                if not isinstance(img, np.ndarray):
                    img = np.array(img)
                h, w = img.shape[:2]
                im = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
                im = cv2.resize(im, (ww, hh))
                im /= 255.0
                im = im.transpose(2, 0, 1)
                im = im[np.newaxis, :, :, :].astype(np.float32)
                inputs.append({
                    self.input_names[0]: im,
                    "scale_factor": [w / ww, h / hh],
                })
        return inputs

    # ------------------------------------------------------------------
    # Postprocessing
    # ------------------------------------------------------------------

    def _postprocess(
        self,
        raw: np.ndarray,
        inputs: dict[str, Any],
        thr: float,
    ) -> list[dict[str, Any]]:
        """Convert raw ONNX output to detection dicts."""
        if self._is_paddle:
            results: list[dict[str, Any]] = []
            for row in raw:
                cls_id, score = int(row[0]), float(row[1])
                if score < thr:
                    continue
                if cls_id >= len(self.label_list):
                    continue
                results.append({
                    "type": self.label_list[cls_id].lower(),
                    "bbox": [float(v) for v in row[2:6].tolist()],
                    "score": float(score),
                })
            return results

        # YOLOv10-style postprocessing
        def _xywh2xyxy(x: np.ndarray) -> np.ndarray:
            y = np.copy(x)
            y[:, 0] = x[:, 0] - x[:, 2] / 2
            y[:, 1] = x[:, 1] - x[:, 3] / 2
            y[:, 2] = x[:, 0] + x[:, 2] / 2
            y[:, 3] = x[:, 1] + x[:, 3] / 2
            return y

        def _compute_iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
            xmin = np.maximum(box[0], boxes[:, 0])
            ymin = np.maximum(box[1], boxes[:, 1])
            xmax = np.minimum(box[2], boxes[:, 2])
            ymax = np.minimum(box[3], boxes[:, 3])
            inter = np.maximum(0, xmax - xmin) * np.maximum(0, ymax - ymin)
            box_area = (box[2] - box[0]) * (box[3] - box[1])
            boxes_area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            union = box_area + boxes_area - inter
            return inter / np.maximum(union, 1e-8)

        def _iou_filter(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
            order = np.argsort(scores)[::-1]
            keep: list[int] = []
            while order.size > 0:
                idx = order[0]
                keep.append(int(idx))
                ious = _compute_iou(boxes[idx], boxes[order[1:]])
                remaining = np.where(ious < iou_thr)[0]
                order = order[remaining + 1]
            return keep

        boxes_raw = np.squeeze(raw).T
        scores = np.max(boxes_raw[:, 4:], axis=1)
        mask = scores > thr
        boxes_raw = boxes_raw[mask]
        scores = scores[mask]
        if len(boxes_raw) == 0:
            return []

        class_ids = np.argmax(boxes_raw[:, 4:], axis=1)
        coords = boxes_raw[:, :4]
        sf = inputs["scale_factor"]
        input_shape = np.array([sf[0], sf[1], sf[0], sf[1]], dtype=np.float32)
        coords = np.multiply(coords, input_shape, dtype=np.float32)
        coords = _xywh2xyxy(coords)

        unique_classes = np.unique(class_ids)
        kept: list[int] = []
        for cid in unique_classes:
            idx = np.where(class_ids == cid)[0]
            keep = _iou_filter(coords[idx], scores[idx], 0.2)
            kept.extend(idx[keep].tolist())

        return [
            {
                "type": self.label_list[int(class_ids[i])].lower(),
                "bbox": [float(v) for v in coords[i].tolist()],
                "score": float(scores[i]),
            }
            for i in kept
            if int(class_ids[i]) < len(self.label_list)
        ]

    # ------------------------------------------------------------------
    # __call__ — run inference + align rows/columns
    # ------------------------------------------------------------------

    def __call__(
        self,
        images: Sequence[Any],
        thr: float = 0.2,
        batch_size: int = 16,
    ) -> list[list[dict[str, Any]]]:
        """Run table structure recognition on a list of table images.

        Returns one list of structure-element dicts per image.  Row/column
        alignment is normalised after inference.
        """
        imgs = [
            np.array(im) if not isinstance(im, np.ndarray) else im
            for im in images
        ]
        raw_results: list[list[dict[str, Any]]] = []
        n_batches = math.ceil(len(imgs) / batch_size) if imgs else 0

        for bi in range(n_batches):
            batch = imgs[bi * batch_size: (bi + 1) * batch_size]
            inputs_list = self._preprocess(batch)
            for ins in inputs_list:
                feed = {k: v for k, v in ins.items() if k in self.input_names}
                out: np.ndarray = self.ort_sess.run(
                    None, feed, self.run_options,
                )[0]
                detections = self._postprocess(out, ins, thr)
                raw_results.append(detections)

        # Post-process: align rows/columns
        aligned: list[list[dict[str, Any]]] = []
        for tbl in raw_results:
            lts = [
                {
                    "label": b["type"],
                    "score": b["score"],
                    "x0": b["bbox"][0],
                    "x1": b["bbox"][2],
                    "top": b["bbox"][1],
                    "bottom": b["bbox"][3],
                }
                for b in tbl
            ]
            if not lts:
                aligned.append(lts)
                continue

            # Align left/right for rows and headers
            left_vals = [b["x0"] for b in lts if "row" in b["label"] or "header" in b["label"]]
            right_vals = [b["x1"] for b in lts if "row" in b["label"] or "header" in b["label"]]
            if left_vals:
                left = float(np.mean(left_vals)) if len(left_vals) > 4 else float(np.min(left_vals))
                right = float(np.mean(right_vals)) if len(right_vals) > 4 else float(np.max(right_vals))
                for b in lts:
                    if "row" in b["label"] or "header" in b["label"]:
                        if b["x0"] > left:
                            b["x0"] = left
                        if b["x1"] < right:
                            b["x1"] = right

            # Align top/bottom for columns
            top_vals = [b["top"] for b in lts if b["label"] == "table column"]
            bottom_vals = [b["bottom"] for b in lts if b["label"] == "table column"]
            if top_vals:
                top = float(np.median(top_vals)) if len(top_vals) > 4 else float(np.min(top_vals))
                bottom = float(np.median(bottom_vals)) if len(bottom_vals) > 4 else float(np.max(bottom_vals))
                for b in lts:
                    if b["label"] == "table column":
                        if b["top"] > top:
                            b["top"] = top
                        if b["bottom"] < bottom:
                            b["bottom"] = bottom

            aligned.append(lts)
        return aligned

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_caption(bx: dict) -> bool:
        """Check whether *bx* looks like a table/figure caption."""
        patt = [r"[\u56fe\u8868]+[ 0-9:\uff1a]{2,}"]
        text = bx.get("text", "").strip()
        if any(re.match(p, text) for p in patt):
            return True
        if bx.get("layout_type", "").find("caption") >= 0:
            return True
        return False

    @staticmethod
    def blockType(b: dict) -> str:
        """Classify a text box's content type using regex heuristics.

        Returns a short tag: ``Nu`` (numeric), ``Dt`` (date), ``Ca`` (code),
        ``En`` (English), ``NE`` (number+English), ``Sg`` (single char),
        ``Tx`` (short text), ``Lx`` (long text), or ``Ot`` (other).
        """
        patt = [
            (r"^(20|19)[0-9]{2}[年/-][0-9]{1,2}[月/-][0-9]{1,2}日*$", "Dt"),
            (r"^(20|19)[0-9]{2}年$", "Dt"),
            (r"^(20|19)[0-9]{2}[年-][0-9]{1,2}月*$", "Dt"),
            (r"^[0-9]{1,2}[月-][0-9]{1,2}日*$", "Dt"),
            (r"^第*[一二三四1-4]季度$", "Dt"),
            (r"^(20|19)[0-9]{2}年*[一二三四1-4]季度$", "Dt"),
            (r"^(20|19)[0-9]{2}[ABCDE]$", "Dt"),
            (r"^[0-9.,+%/ -]+$", "Nu"),
            (r"^[0-9A-Z/\._~-]+$", "Ca"),
            (r"^[A-Z]*[a-z' -]+$", "En"),
            (r"^[0-9.,+-]+[0-9A-Za-z/$\uffe5%<>\uff08\uff09()' -]+$", "NE"),
            (r"^.{1}$", "Sg"),
        ]
        text = b.get("text", "").strip()
        for p, n in patt:
            if re.search(p, text):
                return n

        # Simple word counting instead of rag_tokenizer
        words = [w for w in text.split() if len(w) > 1]
        if len(words) > 3:
            return "Tx" if len(words) < 12 else "Lx"

        return "Ot"

    # ------------------------------------------------------------------
    # Table construction
    # ------------------------------------------------------------------

    @staticmethod
    def construct_table(
        boxes: list[dict],
        is_english: bool = False,
        html: bool = True,
    ) -> list[str] | str:
        """Build an HTML (or descriptive text) table from tagged OCR boxes.

        Parameters
        ----------
        boxes:
            OCR boxes annotated with ``R``, ``C``, ``H``, ``SP`` keys by
            layout/TSR tagging.
        is_english:
            Whether the document is primarily English.
        html:
            If ``True`` return an HTML ``<table>`` string; otherwise
            return a list of descriptive row strings.

        Returns
        -------
        HTML string (when *html* is True) or list of row description
        strings.
        """
        # Separate caption text
        cap = ""
        i = 0
        while i < len(boxes):
            if TableStructureRecognizer.is_caption(boxes[i]):
                if is_english:
                    cap += " "
                cap += boxes[i].get("text", "")
                boxes.pop(i)
                i -= 1
            i += 1

        if not boxes:
            return "" if html else []

        # Classify block types
        for b in boxes:
            b["btype"] = TableStructureRecognizer.blockType(b)

        max_type_items = Counter([b["btype"] for b in boxes]).items()
        max_type = max(max_type_items, key=lambda x: x[1])[0] if max_type_items else ""
        logger.debug("MAXTYPE: %s", max_type)

        # Sort by row
        rowh = [b["R_bott"] - b["R_top"] for b in boxes if "R" in b]
        rowh_val = float(np.min(rowh)) if rowh else 0
        boxes = LayoutRecognizer.sort_R_firstly(boxes, rowh_val / 2)

        # Group into rows
        boxes[0]["rn"] = 0
        rows: list[list[dict]] = [[boxes[0]]]
        btm = boxes[0]["bottom"]
        for b in boxes[1:]:
            b["rn"] = len(rows) - 1
            lst_r = rows[-1]
            if (
                lst_r[-1].get("R", "") != b.get("R", "")
                or (
                    b["top"] >= btm - 3
                    and lst_r[-1].get("R", "-1") != b.get("R", "-2")
                )
            ):
                btm = b["bottom"]
                b["rn"] += 1
                rows.append([b])
                continue
            btm = (btm + b["bottom"]) / 2.0
            rows[-1].append(b)

        # Sort by column
        colwm = [b["C_right"] - b["C_left"] for b in boxes if "C" in b]
        colwm_val = float(np.min(colwm)) if colwm else 0
        crosspage = len(set(b["page_number"] for b in boxes)) > 1
        if crosspage:
            boxes = LayoutRecognizer.sort_X_firstly(boxes, colwm_val / 2)
        else:
            boxes = LayoutRecognizer.sort_C_firstly(boxes, colwm_val / 2)

        # Group into columns
        boxes[0]["cn"] = 0
        cols: list[list[dict]] = [[boxes[0]]]
        right = boxes[0]["x1"]
        for b in boxes[1:]:
            b["cn"] = len(cols) - 1
            lst_c = cols[-1]
            if (
                (
                    int(b.get("C", "1")) - int(lst_c[-1].get("C", "1")) == 1
                    and b["page_number"] == lst_c[-1]["page_number"]
                )
                or (
                    b["x0"] >= right
                    and lst_c[-1].get("C", "-1") != b.get("C", "-2")
                )
            ):
                right = b["x1"]
                b["cn"] += 1
                cols.append([b])
                continue
            right = (right + b["x1"]) / 2.0
            cols[-1].append(b)

        # Build 2D table
        tbl: list[list[list[dict]]] = [
            [[] for _ in range(len(cols))] for _ in range(len(rows))
        ]
        for b in boxes:
            tbl[b["rn"]][b["cn"]].append(b)

        # Remove single-occupancy columns
        if len(rows) >= 4:
            j = 0
            while j < len(tbl[0]):
                occupied_count, occupied_row = 0, 0
                for i in range(len(tbl)):
                    if tbl[i][j]:
                        occupied_count += 1
                        occupied_row = i
                    if occupied_count > 1:
                        break
                if occupied_count > 1:
                    j += 1
                    continue
                # Check neighbours for text
                has_left = (
                    (j > 0 and tbl[occupied_row][j - 1] and tbl[occupied_row][j - 1][0].get("text"))
                    or j == 0
                )
                has_right = (
                    (j + 1 < len(tbl[occupied_row]) and tbl[occupied_row][j + 1] and tbl[occupied_row][j + 1][0].get("text"))
                    or j + 1 >= len(tbl[occupied_row])
                )
                if has_left and has_right:
                    j += 1
                    continue
                if not tbl[occupied_row][j]:
                    j += 1
                    continue
                bx = tbl[occupied_row][j][0]
                logger.debug("Relocate column single: %s", bx.get("text", ""))

                left_dist, right_dist = 100000.0, 100000.0
                if j > 0 and not has_left:
                    for i_row in range(len(tbl)):
                        if tbl[i_row][j - 1]:
                            left_dist = min(
                                left_dist,
                                float(np.min([bx["x0"] - a["x1"] for a in tbl[i_row][j - 1]])),
                            )
                if j + 1 < len(tbl[0]) and not has_right:
                    for i_row in range(len(tbl)):
                        if tbl[i_row][j + 1]:
                            right_dist = min(
                                right_dist,
                                float(np.min([a["x0"] - bx["x1"] for a in tbl[i_row][j + 1]])),
                            )
                if left_dist >= 100000.0 and right_dist >= 100000.0:
                    j += 1
                    continue
                if left_dist < right_dist:
                    for jj in range(j, len(tbl[0])):
                        for i_row in range(len(tbl)):
                            for a in tbl[i_row][jj]:
                                a["cn"] -= 1
                    if tbl[occupied_row][j - 1]:
                        tbl[occupied_row][j - 1].extend(tbl[occupied_row][j])
                    else:
                        tbl[occupied_row][j - 1] = tbl[occupied_row][j]
                    for i_row in range(len(tbl)):
                        tbl[i_row].pop(j)
                else:
                    for jj in range(j + 1, len(tbl[0])):
                        for i_row in range(len(tbl)):
                            for a in tbl[i_row][jj]:
                                a["cn"] -= 1
                    if tbl[occupied_row][j + 1]:
                        tbl[occupied_row][j + 1].extend(tbl[occupied_row][j])
                    else:
                        tbl[occupied_row][j + 1] = tbl[occupied_row][j]
                    for i_row in range(len(tbl)):
                        tbl[i_row].pop(j)
                cols.pop(j)

        if cols and tbl and tbl[0]:
            if len(cols) != len(tbl[0]):
                logger.warning(
                    "Column count mismatch after cleanup: %d vs %d",
                    len(cols), len(tbl[0]),
                )

        # Remove single-occupancy rows
        if len(cols) >= 4:
            i = 0
            while i < len(tbl):
                occupied_count, occupied_col = 0, 0
                for j_col in range(len(tbl[i])):
                    if tbl[i][j_col]:
                        occupied_count += 1
                        occupied_col = j_col
                    if occupied_count > 1:
                        break
                if occupied_count > 1:
                    i += 1
                    continue
                has_above = (
                    (i > 0 and tbl[i - 1][occupied_col] and tbl[i - 1][occupied_col][0].get("text"))
                    or i == 0
                )
                has_below = (
                    (i + 1 < len(tbl) and tbl[i + 1][occupied_col] and tbl[i + 1][occupied_col][0].get("text"))
                    or i + 1 >= len(tbl)
                )
                if has_above and has_below:
                    i += 1
                    continue
                if not tbl[i][occupied_col]:
                    i += 1
                    continue
                bx = tbl[i][occupied_col][0]
                logger.debug("Relocate row single: %s", bx.get("text", ""))

                up_dist, down_dist = 100000.0, 100000.0
                if i > 0 and not has_above:
                    for j_col in range(len(tbl[i - 1])):
                        if tbl[i - 1][j_col]:
                            up_dist = min(
                                up_dist,
                                float(np.min([bx["top"] - a["bottom"] for a in tbl[i - 1][j_col]])),
                            )
                if i + 1 < len(tbl) and not has_below:
                    for j_col in range(len(tbl[i + 1])):
                        if tbl[i + 1][j_col]:
                            down_dist = min(
                                down_dist,
                                float(np.min([a["top"] - bx["bottom"] for a in tbl[i + 1][j_col]])),
                            )
                if up_dist >= 100000.0 and down_dist >= 100000.0:
                    i += 1
                    continue
                if up_dist < down_dist:
                    for ii in range(i, len(tbl)):
                        for j_col in range(len(tbl[ii])):
                            for a in tbl[ii][j_col]:
                                a["rn"] -= 1
                    if tbl[i - 1][occupied_col]:
                        tbl[i - 1][occupied_col].extend(tbl[i][occupied_col])
                    else:
                        tbl[i - 1][occupied_col] = tbl[i][occupied_col]
                    tbl.pop(i)
                else:
                    for ii in range(i + 1, len(tbl)):
                        for j_col in range(len(tbl[ii])):
                            for a in tbl[ii][j_col]:
                                a["rn"] -= 1
                    if tbl[i + 1][occupied_col]:
                        tbl[i + 1][occupied_col].extend(tbl[i][occupied_col])
                    else:
                        tbl[i + 1][occupied_col] = tbl[i][occupied_col]
                    tbl.pop(i)
                rows.pop(i)

        # Determine header rows
        hdset: set[int] = set()
        for i in range(len(tbl)):
            cnt, h = 0, 0
            for j, arr in enumerate(tbl[i]):
                if not arr:
                    continue
                cnt += 1
                if max_type == "Nu" and arr[0]["btype"] == "Nu":
                    continue
                if any(a.get("H") for a in arr) or (
                    max_type == "Nu" and arr[0]["btype"] != "Nu"
                ):
                    h += 1
            if cnt > 0 and h / cnt > 0.5:
                hdset.add(i)

        # Calculate spans and build output
        span_tbl = TableStructureRecognizer.__cal_spans(boxes, rows, cols, tbl, html)

        if html:
            return TableStructureRecognizer.__html_table(cap, hdset, span_tbl)

        return TableStructureRecognizer.__desc_table(cap, hdset, span_tbl, is_english)

    # ------------------------------------------------------------------
    # Span calculation
    # ------------------------------------------------------------------

    @staticmethod
    def __cal_spans(
        boxes: list[dict],
        rows: list[list[dict]],
        cols: list[list[dict]],
        tbl: list[list[list[dict]]],
        html: bool = True,
    ) -> list[list[list[dict] | None]]:
        """Calculate colspan and rowspan for spanning cells."""
        clft = [float(np.mean([c.get("C_left", c["x0"]) for c in cln])) for cln in cols]
        crgt = [float(np.mean([c.get("C_right", c["x1"]) for c in cln])) for cln in cols]
        rtop = [float(np.mean([c.get("R_top", c["top"]) for c in row])) for row in rows]
        rbtm = [float(np.mean([c.get("R_btm", c["bottom"]) for c in row])) for row in rows]

        for b in boxes:
            if "SP" not in b:
                continue
            b["colspan"] = [b["cn"]]
            b["rowspan"] = [b["rn"]]
            # Column span
            for j in range(len(clft)):
                if j == b["cn"]:
                    continue
                if clft[j] + (crgt[j] - clft[j]) / 2 < b.get("H_left", b["x0"]):
                    continue
                if crgt[j] - (crgt[j] - clft[j]) / 2 > b.get("H_right", b["x1"]):
                    continue
                b["colspan"].append(j)
            # Row span
            for j in range(len(rtop)):
                if j == b["rn"]:
                    continue
                if rtop[j] + (rbtm[j] - rtop[j]) / 2 < b.get("H_top", b["top"]):
                    continue
                if rbtm[j] - (rbtm[j] - rtop[j]) / 2 > b.get("H_bott", b["bottom"]):
                    continue
                b["rowspan"].append(j)

        def _join(arr: list[dict]) -> str:
            if not arr:
                return ""
            return "".join(t.get("text", "") for t in arr)

        # Remove spanning cells by merging content
        for i in range(len(tbl)):
            for j, arr in enumerate(tbl[i]):
                if not arr:
                    continue
                if all("rowspan" not in a and "colspan" not in a for a in arr):
                    continue
                rowspan_set: list[int] = []
                colspan_set: list[int] = []
                for a in arr:
                    if isinstance(a.get("rowspan", 0), list):
                        rowspan_set.extend(a["rowspan"])
                    if isinstance(a.get("colspan", 0), list):
                        colspan_set.extend(a["colspan"])
                rowspan_uniq = sorted(set(rowspan_set))
                colspan_uniq = sorted(set(colspan_set))
                if len(rowspan_uniq) < 2 and len(colspan_uniq) < 2:
                    for a in arr:
                        a.pop("rowspan", None)
                        a.pop("colspan", None)
                    continue
                rowspan_range = list(range(rowspan_uniq[0], rowspan_uniq[-1] + 1))
                colspan_range = list(range(colspan_uniq[0], colspan_uniq[-1] + 1))

                merged: list[dict] = []
                for r in rowspan_range:
                    for c in colspan_range:
                        if r >= len(tbl) or c >= len(tbl[r]):
                            continue
                        arr_txt = _join(merged)
                        if tbl[r][c] and _join(tbl[r][c]) != arr_txt:
                            merged.extend(tbl[r][c])
                        tbl[r][c] = None if html else merged  # type: ignore[assignment]
                for a in merged:
                    if len(rowspan_range) > 1:
                        a["rowspan"] = len(rowspan_range)
                    else:
                        a.pop("rowspan", None)
                    if len(colspan_range) > 1:
                        a["colspan"] = len(colspan_range)
                    else:
                        a.pop("colspan", None)
                if rowspan_range and colspan_range:
                    r0, c0 = rowspan_range[0], colspan_range[0]
                    if r0 < len(tbl) and c0 < len(tbl[r0]):
                        tbl[r0][c0] = merged

        return tbl

    # ------------------------------------------------------------------
    # HTML output
    # ------------------------------------------------------------------

    @staticmethod
    def __html_table(
        cap: str,
        hdset: set[int],
        tbl: list[list[list[dict] | None]],
    ) -> str:
        """Construct an HTML table string."""
        html = "<table>"
        if cap:
            html += f"<caption>{cap}</caption>"
        for i in range(len(tbl)):
            row = "<tr>"
            txts: list[str] = []
            for j, arr in enumerate(tbl[i]):
                if arr is None:
                    continue
                if not arr:
                    row += "<td></td>" if i not in hdset else "<th></th>"
                    continue
                # Sort cell content by Y position and join
                h = min(float(np.min([c["bottom"] - c["top"] for c in arr])) / 2, 10)
                txt = " ".join(
                    c.get("text", "")
                    for c in LayoutRecognizer.sort_Y_firstly(arr, h)
                )
                txts.append(txt)
                sp = ""
                if arr[0].get("colspan") and isinstance(arr[0]["colspan"], int):
                    sp = f'colspan={arr[0]["colspan"]}'
                if arr[0].get("rowspan") and isinstance(arr[0]["rowspan"], int):
                    sp += f' rowspan={arr[0]["rowspan"]}'
                if i in hdset:
                    row += f"<th {sp}>{txt}</th>"
                else:
                    row += f"<td {sp}>{txt}</td>"

            if i in hdset:
                if all(t in hdset for t in txts):
                    continue
                for t in txts:
                    hdset.add(t)  # type: ignore[arg-type]

            if row != "<tr>":
                row += "</tr>"
            else:
                row = ""
            html += "\n" + row
        html += "\n</table>"
        return html

    # ------------------------------------------------------------------
    # Descriptive text output
    # ------------------------------------------------------------------

    @staticmethod
    def __desc_table(
        cap: str,
        hdr_rowno: set[int],
        tbl: list[list[list[dict] | None]],
        is_english: bool,
    ) -> list[str]:
        """Produce descriptive text rows for a table (non-HTML mode)."""
        clmno = len(tbl[0]) if tbl else 0
        rowno = len(tbl)
        headers: dict[int, list[str]] = {}
        de = " for " if is_english else "的"

        lst_hdr: list[str] = []
        for r in sorted(hdr_rowno):
            headers[r] = ["" for _ in range(clmno)]
            for i in range(clmno):
                if r >= len(tbl) or not tbl[r][i]:
                    continue
                txt = " ".join(a.get("text", "").strip() for a in (tbl[r][i] or []))
                headers[r][i] = txt
            if all(not t for t in headers[r]):
                del headers[r]
                hdr_rowno.discard(r)
                continue
            for j in range(clmno):
                if headers[r][j]:
                    continue
                if j < len(lst_hdr):
                    headers[r][j] = lst_hdr[j]
            lst_hdr = headers[r]

        # Merge multi-level headers
        for i in range(rowno):
            if i not in hdr_rowno:
                continue
            for j in range(i + 1, rowno):
                if j not in hdr_rowno:
                    break
                for k in range(clmno):
                    if i not in headers or not headers[i][k]:
                        continue
                    if j not in headers:
                        continue
                    if headers[j][k].find(headers[i][k]) >= 0:
                        continue
                    if len(headers[j][k]) > len(headers[i][k]):
                        headers[j][k] += (de if headers[j][k] else "") + headers[i][k]
                    else:
                        headers[j][k] = headers[i][k] + (de if headers[i][k] else "") + headers[j][k]

        row_txt: list[str] = []
        for i in range(rowno):
            if i in hdr_rowno:
                continue
            rtxt: list[str] = []

            # Find closest header row above
            r = 0
            if headers:
                candidates = [(i - rr, rr) for rr, _ in headers.items() if rr < i]
                if candidates:
                    _, r = min(candidates, key=lambda x: x[0])

            if r not in headers and clmno <= 2:
                for j in range(clmno):
                    if i >= len(tbl) or not tbl[i][j]:
                        continue
                    txt = "".join(a.get("text", "").strip() for a in (tbl[i][j] or []))
                    if txt:
                        rtxt.append(txt)
                if rtxt:
                    row_txt.append("\uff1a".join(rtxt))
                continue

            for j in range(clmno):
                if i >= len(tbl) or not tbl[i][j]:
                    continue
                txt = "".join(a.get("text", "").strip() for a in (tbl[i][j] or []))
                if not txt:
                    continue
                ctt = headers[r][j] if r in headers and j < len(headers[r]) else ""
                if ctt:
                    ctt += "\uff1a"
                ctt += txt
                if ctt:
                    rtxt.append(ctt)

            if rtxt:
                row_txt.append("; ".join(rtxt))

        if cap:
            from_ = " in " if is_english else "来自"
            lq, rq = "\u201c", "\u201d"
            row_txt = [t + f"\t\u2014\u2014{from_}{lq}{cap}{rq}" for t in row_txt]
        return row_txt

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the ONNX session."""
        logger.info("Closing TSR ONNX session")
        if hasattr(self, "ort_sess"):
            del self.ort_sess

    def __del__(self) -> None:
        self.close()
