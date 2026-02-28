#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
# --------------------------------------------------------------------------
# Layout recognition module for Project Atlas.
#
# Ported from RAGFlow's deepdoc engine (Apache 2.0, InfiniFlow/ragflow).
# Adapted to be fully standalone — no imports from ragflow.
# --------------------------------------------------------------------------
"""ONNX-based page layout recogniser.

Classifies regions on a PDF page image into one of 10 layout types
(text, title, figure, table, header, footer, …) using an ONNX model
trained on the PubLayNet / DocLayNet family of datasets.

Key behaviours ported from RAGFlow:
* Noise filtering — headers only in the top 10 % of the page, footers
  only in the bottom 10 %.
* Cross-page ``Counter`` dedup — repeating headers / footers that appear
  on every page are removed.
* NMS-style dedup for overlapping layout boxes.
* Layout-type tagging of OCR boxes via overlap analysis.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from copy import deepcopy
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
import onnxruntime as ort

from .model_manager import ModelManager
from .types import GARBAGE_LAYOUT_TYPES as GARBAGE_LAYOUT_TYPES  # noqa: F401 — re-export
from .types import LayoutType as LayoutType  # noqa: F401 — re-export

logger = logging.getLogger(__name__)

# Label list for the PaddleDetection-style layout model (index 0 = background)
_PADDLE_LABELS: list[str] = [
    "_background_",
    "Text",
    "Title",
    "Figure",
    "Figure caption",
    "Table",
    "Table caption",
    "Header",
    "Footer",
    "Reference",
    "Equation",
]

# Label list for the YOLOv10-style layout model (no background class)
_YOLO_LABELS: list[str] = [
    "title",
    "Text",
    "Reference",
    "Figure",
    "Figure caption",
    "Table",
    "Table caption",
    "Table caption",
    "Equation",
    "Figure caption",
]

# Garbage layout type names (lower-cased) used for noise filtering
_GARBAGE_NAMES: list[str] = ["footer", "header", "reference"]

# Canonical iteration order – garbage types first so they are tagged before
# other types claim the OCR boxes.
_LAYOUT_TAG_ORDER: list[str] = [
    "footer", "header", "reference",
    "figure caption", "table caption",
    "title", "table", "text", "figure", "equation",
]


# ---------------------------------------------------------------------------
# NMS helper (standalone, mirrors operators.nms from RAGFlow)
# ---------------------------------------------------------------------------

def _nms(bboxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    """Non-maximum suppression on axis-aligned boxes.

    Parameters
    ----------
    bboxes : (N, 4) array of ``[x1, y1, x2, y2]``
    scores : (N,) array
    iou_thresh : IoU threshold above which the weaker box is suppressed.

    Returns
    -------
    List of kept indices.
    """
    x1 = bboxes[:, 0]
    y1 = bboxes[:, 1]
    x2 = bboxes[:, 2]
    y2 = bboxes[:, 3]
    areas = (y2 - y1) * (x2 - x1)

    indices: list[int] = []
    order = scores.argsort()[::-1]
    while order.size > 0:
        i = order[0]
        indices.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        ious = (w * h) / (areas[i] + areas[order[1:]] - w * h)
        keep = np.where(ious <= iou_thresh)[0]
        order = order[keep + 1]
    return indices


# ---------------------------------------------------------------------------
# LayoutRecognizer
# ---------------------------------------------------------------------------

class LayoutRecognizer:
    """Run the ONNX layout model and tag OCR boxes with layout types.

    Supports two model flavours:

    * **PaddleDetection** format — the model has a ``scale_factor`` input and
      outputs ``[class_id, score, x0, y0, x1, y1]`` rows.
    * **YOLOv10** format — the model has a single image input and outputs
      ``[x0, y0, x1, y1, score, class_id]`` rows.

    The flavour is auto-detected from the ONNX input names.
    """

    def __init__(
        self,
        model_dir: Path | str | None = None,
        *,
        model_name: str = "layout",
    ) -> None:
        """Load the ONNX layout model.

        Parameters
        ----------
        model_dir:
            Directory containing the ``.onnx`` files.  When *None* the
            :class:`ModelManager` singleton resolves the path.
        model_name:
            Stem of the model file (default ``"layout"``).
        """
        if model_dir is None:
            mgr = ModelManager.get_instance()
            mgr.ensure_models()
            model_path = mgr.get_model_path(model_name)
        else:
            model_path = Path(model_dir) / f"{model_name}.onnx"

        if not model_path.exists():
            raise FileNotFoundError(f"Layout model not found: {model_path}")

        logger.info("Loading layout model from %s", model_path)

        options = ort.SessionOptions()
        options.enable_cpu_mem_arena = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 2

        providers = ["CPUExecutionProvider"]
        try:
            import torch  # noqa: F401 — only to check CUDA availability
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
            self.ort_sess.get_inputs()[0].shape[2:4]
        )

        # Detect model flavour
        self._is_paddle = "scale_factor" in self.input_names
        self.label_list: list[str] = _PADDLE_LABELS if self._is_paddle else _YOLO_LABELS
        self.garbage_layouts: list[str] = _GARBAGE_NAMES

        # YOLOv10 specific settings
        self._stride = 32
        self._center = True

        logger.info(
            "Layout model loaded — flavour=%s, labels=%d, input_shape=%s",
            "paddle" if self._is_paddle else "yolo",
            len(self.label_list),
            self.input_shape,
        )

    # ------------------------------------------------------------------
    # Static geometry helpers (ported from RAGFlow Recognizer)
    # ------------------------------------------------------------------

    @staticmethod
    def sort_Y_firstly(arr: list[dict], threshold: float) -> list[dict]:
        """Sort boxes top-to-bottom, breaking ties left-to-right."""
        def _cmp(c1: dict, c2: dict) -> int:
            diff = c1["top"] - c2["top"]
            if abs(diff) < threshold:
                diff = c1["x0"] - c2["x0"]
            return -1 if diff < 0 else (1 if diff > 0 else 0)
        return sorted(arr, key=cmp_to_key(_cmp))

    @staticmethod
    def overlapped_area(a: dict, b: dict, ratio: bool = True) -> float:
        """Compute the overlap area (or ratio) between two boxes.

        When *ratio* is True (default), returns the fraction of box *a*
        covered by the intersection.
        """
        tp, btm, x0, x1 = a["top"], a["bottom"], a["x0"], a["x1"]
        if b["x0"] > x1 or b["x1"] < x0:
            return 0.0
        if b["bottom"] < tp or b["top"] > btm:
            return 0.0
        x0_ = max(b["x0"], x0)
        x1_ = min(b["x1"], x1)
        if x0_ > x1_:
            return 0.0
        tp_ = max(b["top"], tp)
        btm_ = min(b["bottom"], btm)
        if tp_ > btm_:
            return 0.0
        ov = (btm_ - tp_) * (x1_ - x0_)
        if ov > 0 and ratio:
            denom = (x1 - x0) * (btm - tp)
            ov = ov / denom if denom != 0 else 0.0
        return ov

    @staticmethod
    def layouts_cleanup(
        boxes: list[dict],
        layouts: list[dict],
        far: int = 2,
        thr: float = 0.7,
    ) -> list[dict]:
        """NMS-style dedup for layout boxes.

        Compares nearby layout boxes of the same type and removes the one
        with the lower score (or less OCR-box coverage).
        """
        def _not_overlapped(a: dict, b: dict) -> bool:
            return any([
                a["x1"] < b["x0"],
                a["x0"] > b["x1"],
                a["bottom"] < b["top"],
                a["top"] > b["bottom"],
            ])

        i = 0
        while i + 1 < len(layouts):
            j = i + 1
            while (
                j < min(i + far, len(layouts))
                and (
                    layouts[i].get("type", "") != layouts[j].get("type", "")
                    or _not_overlapped(layouts[i], layouts[j])
                )
            ):
                j += 1
            if j >= min(i + far, len(layouts)):
                i += 1
                continue
            ov_ij = LayoutRecognizer.overlapped_area(layouts[i], layouts[j])
            ov_ji = LayoutRecognizer.overlapped_area(layouts[j], layouts[i])
            if ov_ij < thr and ov_ji < thr:
                i += 1
                continue

            # Prefer higher-scoring box
            if layouts[i].get("score") and layouts[j].get("score"):
                if layouts[i]["score"] > layouts[j]["score"]:
                    layouts.pop(j)
                else:
                    layouts.pop(i)
                continue

            # Fall back to OCR coverage
            area_i = sum(
                LayoutRecognizer.overlapped_area(b, layouts[i], False)
                for b in boxes
                if not _not_overlapped(b, layouts[i])
            )
            area_j = sum(
                LayoutRecognizer.overlapped_area(b, layouts[j], False)
                for b in boxes
                if not _not_overlapped(b, layouts[j])
            )
            if area_i > area_j:
                layouts.pop(j)
            else:
                layouts.pop(i)

        return layouts

    @staticmethod
    def find_overlapped_with_threshold(
        box: dict,
        boxes: list[dict],
        thr: float = 0.3,
    ) -> int | None:
        """Find the box in *boxes* that overlaps *box* the most.

        Returns the index of the best match, or ``None`` if no box
        exceeds the threshold.
        """
        if not boxes:
            return None
        max_i: int | None = None
        max_ov = thr
        max_rev = 0.0
        for i in range(len(boxes)):
            ov = LayoutRecognizer.overlapped_area(box, boxes[i])
            rev = LayoutRecognizer.overlapped_area(boxes[i], box)
            if (ov, rev) < (max_ov, max_rev):
                continue
            max_i = i
            max_ov = ov
            max_rev = rev
        return max_i

    @staticmethod
    def sort_X_firstly(arr: list[dict], threshold: float) -> list[dict]:
        """Sort boxes left-to-right, breaking ties top-to-bottom."""
        def _cmp(c1: dict, c2: dict) -> int:
            diff = c1["x0"] - c2["x0"]
            if abs(diff) < threshold:
                diff = c1["top"] - c2["top"]
            return -1 if diff < 0 else (1 if diff > 0 else 0)
        return sorted(arr, key=cmp_to_key(_cmp))

    @staticmethod
    def sort_C_firstly(arr: list[dict], thr: float = 0) -> list[dict]:
        """Sort boxes by column index (C), breaking ties by Y position."""
        arr = LayoutRecognizer.sort_X_firstly(arr, thr)
        for i in range(len(arr) - 1):
            for j in range(i, -1, -1):
                if "C" not in arr[j] or "C" not in arr[j + 1]:
                    continue
                if arr[j + 1]["C"] < arr[j]["C"] or (
                    arr[j + 1]["C"] == arr[j]["C"]
                    and arr[j + 1]["top"] < arr[j]["top"]
                ):
                    arr[j], arr[j + 1] = arr[j + 1], arr[j]
        return arr

    @staticmethod
    def sort_R_firstly(arr: list[dict], thr: float = 0) -> list[dict]:
        """Sort boxes by row index (R), breaking ties by X position."""
        arr = LayoutRecognizer.sort_Y_firstly(arr, thr)
        for i in range(len(arr) - 1):
            for j in range(i, -1, -1):
                if "R" not in arr[j] or "R" not in arr[j + 1]:
                    continue
                if arr[j + 1]["R"] < arr[j]["R"] or (
                    arr[j + 1]["R"] == arr[j]["R"]
                    and arr[j + 1]["x0"] < arr[j]["x0"]
                ):
                    arr[j], arr[j + 1] = arr[j + 1], arr[j]
        return arr

    @staticmethod
    def find_horizontally_tightest_fit(
        box: dict,
        boxes: list[dict],
    ) -> int | None:
        """Find the box in *boxes* that is horizontally closest.

        Only considers boxes with the same ``layoutno``.
        Returns the index of the best match, or ``None``.
        """
        if not boxes:
            return None
        min_dis: float = 1_000_000
        min_i: int | None = None
        for i, b in enumerate(boxes):
            if box.get("layoutno", "0") != b.get("layoutno", "0"):
                continue
            dis = min(
                abs(box["x0"] - b["x0"]),
                abs(box["x1"] - b["x1"]),
                abs(box["x0"] + box["x1"] - b["x1"] - b["x0"]) / 2,
            )
            if dis < min_dis:
                min_i = i
                min_dis = dis
        return min_i

    # ------------------------------------------------------------------
    # Preprocessing
    # ------------------------------------------------------------------

    def preprocess(self, image_list: Sequence[np.ndarray]) -> list[dict[str, Any]]:
        """Prepare images for the ONNX model.

        Each element in the returned list is a dict that can be fed
        directly into ``ort_sess.run``.

        For **PaddleDetection** models the preprocessing pipeline is:
          1.  LinearResize to ``[800, 608]`` (no aspect-ratio lock)
          2.  StandardizeImage (``mean=[0.485, 0.456, 0.406]``,
              ``std=[0.229, 0.224, 0.225]``, scale to 0-1)
          3.  Permute → CHW
          4.  PadStride 32

        For **YOLOv10** models:
          1.  Letterbox resize to model input shape, centre-padded
          2.  Scale to 0-1
          3.  Transpose to CHW
          4.  Record scale_factor for postprocess coordinate recovery
        """
        if self._is_paddle:
            return self._preprocess_paddle(image_list)
        return self._preprocess_yolo(image_list)

    # -- PaddleDetection preprocessing ----------------------------------

    def _preprocess_paddle(self, image_list: Sequence[np.ndarray]) -> list[dict[str, Any]]:
        inputs: list[dict[str, Any]] = []
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

            # Normalize
            im = im / 255.0
            im = (im - mean) / std

            # HWC → CHW
            im = im.transpose(2, 0, 1).astype(np.float32)

            # PadStride 32
            _, h, w = im.shape
            pad_h = int(np.ceil(h / 32) * 32)
            pad_w = int(np.ceil(w / 32) * 32)
            if pad_h != h or pad_w != w:
                padded = np.zeros((3, pad_h, pad_w), dtype=np.float32)
                padded[:, :h, :w] = im
                im = padded

            inputs.append({
                "image": im[np.newaxis],
                "im_shape": np.array([[float(target_h), float(target_w)]], dtype=np.float32),
                "scale_factor": np.array([[scale_y, scale_x]], dtype=np.float32),
            })
        return inputs

    # -- YOLOv10 preprocessing -------------------------------------------

    def _preprocess_yolo(self, image_list: Sequence[np.ndarray]) -> list[dict[str, Any]]:
        inputs: list[dict[str, Any]] = []
        new_h, new_w = self.input_shape

        for img in image_list:
            if not isinstance(img, np.ndarray):
                img = np.array(img)
            h, w = img.shape[:2]

            # Scale ratio – fit inside model input keeping aspect ratio
            r = min(new_h / h, new_w / w)
            new_unpad_w = int(round(w * r))
            new_unpad_h = int(round(h * r))

            dw = (new_w - new_unpad_w) / 2.0
            dh = (new_h - new_unpad_h) / 2.0

            im = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
            im = cv2.resize(im, (new_unpad_w, new_unpad_h), interpolation=cv2.INTER_LINEAR)

            top = int(round(dh - 0.1)) if self._center else 0
            bottom = int(round(dh + 0.1))
            left = int(round(dw - 0.1)) if self._center else 0
            right = int(round(dw + 0.1))
            im = cv2.copyMakeBorder(
                im, top, bottom, left, right,
                cv2.BORDER_CONSTANT, value=(114, 114, 114),
            )

            im /= 255.0
            im = im.transpose(2, 0, 1)[np.newaxis].astype(np.float32)

            inputs.append({
                self.input_names[0]: im,
                "scale_factor": [w / new_unpad_w, h / new_unpad_h, dw, dh],
            })
        return inputs

    # ------------------------------------------------------------------
    # Postprocessing
    # ------------------------------------------------------------------

    def postprocess(
        self,
        raw_output: np.ndarray,
        inputs: dict[str, Any],
        thr: float,
    ) -> list[dict[str, Any]]:
        """Convert raw ONNX output to a list of detection dicts.

        Each dict has keys ``type`` (str), ``bbox`` ([x0, y0, x1, y1]),
        and ``score`` (float).
        """
        if self._is_paddle:
            return self._postprocess_paddle(raw_output, thr)
        return self._postprocess_yolo(raw_output, inputs, thr)

    def _postprocess_paddle(
        self, raw: np.ndarray, thr: float,
    ) -> list[dict[str, Any]]:
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
                "score": score,
            })
        return results

    def _postprocess_yolo(
        self,
        raw: np.ndarray,
        inputs: dict[str, Any],
        thr: float,
    ) -> list[dict[str, Any]]:
        thr = max(thr, 0.08)
        boxes = np.squeeze(raw)
        if boxes.ndim < 2 or boxes.shape[0] == 0:
            return []

        scores = boxes[:, 4]
        mask = scores > thr
        boxes = boxes[mask]
        scores = scores[mask]
        if len(boxes) == 0:
            return []

        class_ids = boxes[:, -1].astype(int)
        coords = boxes[:, :4].copy()

        # Remove letterbox padding and rescale
        sf = inputs["scale_factor"]
        coords[:, 0] -= sf[2]
        coords[:, 2] -= sf[2]
        coords[:, 1] -= sf[3]
        coords[:, 3] -= sf[3]
        scale = np.array([sf[0], sf[1], sf[0], sf[1]], dtype=np.float32)
        coords = np.multiply(coords, scale, dtype=np.float32)

        # Per-class NMS
        unique_classes = np.unique(class_ids)
        kept: list[int] = []
        for cid in unique_classes:
            idx = np.where(class_ids == cid)[0]
            keep = _nms(coords[idx], scores[idx], 0.45)
            kept.extend(idx[keep].tolist())

        results: list[dict[str, Any]] = []
        for i in kept:
            cid = int(class_ids[i])
            if 0 <= cid < len(self.label_list):
                results.append({
                    "type": self.label_list[cid].lower(),
                    "bbox": [float(v) for v in coords[i].tolist()],
                    "score": float(scores[i]),
                })
        return results

    # ------------------------------------------------------------------
    # Raw inference (no tagging)
    # ------------------------------------------------------------------

    def _run_inference(
        self,
        image_list: Sequence[np.ndarray],
        thr: float = 0.7,
        batch_size: int = 16,
    ) -> list[list[dict[str, Any]]]:
        """Run layout detection on a list of page images.

        Returns one list of detection dicts per page.
        """
        images = [
            np.array(img) if not isinstance(img, np.ndarray) else img
            for img in image_list
        ]
        results: list[list[dict[str, Any]]] = []
        n_batches = math.ceil(len(images) / batch_size)

        for bi in range(n_batches):
            batch = images[bi * batch_size : (bi + 1) * batch_size]
            inputs_list = self.preprocess(batch)
            logger.debug("Preprocessed batch %d/%d (%d images)", bi + 1, n_batches, len(batch))

            for ins in inputs_list:
                feed = {
                    k: v for k, v in ins.items()
                    if k in self.input_names
                }
                raw: np.ndarray = self.ort_sess.run(None, feed, self.run_options)[0]  # type: ignore[assignment]
                detections = self.postprocess(raw, ins, thr)
                results.append(detections)

        return results

    # ------------------------------------------------------------------
    # Main entry point — __call__
    # ------------------------------------------------------------------

    def __call__(
        self,
        image_list: Sequence[Any],
        ocr_res: list[list[dict]],
        scale_factor: float = 3.0,
        thr: float = 0.2,
        batch_size: int = 16,
        drop: bool = True,
    ) -> tuple[list[dict], list[list[dict]]]:
        """Run layout recognition and tag OCR boxes.

        Parameters
        ----------
        image_list:
            Page images (PIL or ndarray).  One per page.
        ocr_res:
            Per-page list of OCR box dicts.  Each dict must have at
            least ``x0``, ``top``, ``x1``, ``bottom``, ``text``.
        scale_factor:
            Factor by which the page images were scaled relative to
            the coordinate space of *ocr_res*.
        thr:
            Minimum overlap threshold for layout-type assignment.
        batch_size:
            ONNX inference batch size.
        drop:
            If ``True``, garbage-type OCR boxes (header / footer /
            reference) are dropped from the output.

        Returns
        -------
        ``(tagged_ocr_boxes, page_layouts)`` — the OCR boxes annotated
        with ``layout_type`` and ``layoutno``, and the raw per-page
        layout detections.
        """
        assert len(image_list) == len(ocr_res), (
            f"image_list ({len(image_list)}) and ocr_res ({len(ocr_res)}) "
            "must have the same length"
        )

        # Run the ONNX model on all pages
        layouts_per_page = self._run_inference(image_list, thr, batch_size)
        assert len(image_list) == len(layouts_per_page)

        all_boxes: list[dict] = []
        garbages: dict[str, list[str]] = {}
        page_layouts: list[list[dict]] = []

        for pn, raw_lts in enumerate(layouts_per_page):
            bxs = ocr_res[pn]

            # Rescale from model coords to OCR coords, filter by score
            lts: list[dict] = []
            for det in raw_lts:
                score = float(det["score"])
                ltype = det["type"]
                # Garbage types need score >= 0.4 to survive
                if score < 0.4 and ltype in self.garbage_layouts:
                    continue
                lts.append({
                    "type": ltype,
                    "score": score,
                    "x0": det["bbox"][0] / scale_factor,
                    "x1": det["bbox"][2] / scale_factor,
                    "top": det["bbox"][1] / scale_factor,
                    "bottom": det["bbox"][3] / scale_factor,
                    "page_number": pn,
                })

            # Sort by Y position, then NMS cleanup
            if lts:
                avg_h = float(np.mean([lt["bottom"] - lt["top"] for lt in lts]))
                lts = self.sort_Y_firstly(lts, avg_h / 2 if avg_h > 0 else 0)
            lts = self.layouts_cleanup(bxs, lts)
            page_layouts.append(lts)

            # -- Tag each OCR box with its layout type ----------------------

            def _find_and_tag(ty: str) -> None:
                """Assign layout type *ty* to overlapping OCR boxes."""
                nonlocal bxs, lts
                lts_of_ty = [lt for lt in lts if lt["type"] == ty]
                i = 0
                while i < len(bxs):
                    if bxs[i].get("layout_type"):
                        i += 1
                        continue

                    ii = self.find_overlapped_with_threshold(
                        bxs[i], lts_of_ty, thr=0.4,
                    )
                    if ii is None:
                        bxs[i]["layout_type"] = ""
                        i += 1
                        continue

                    lts_of_ty[ii]["visited"] = True

                    # Noise filtering: headers only in top 10 %, footers
                    # only in bottom 10 % of the page image.
                    page_img = image_list[pn]
                    page_h: float
                    if hasattr(page_img, "size"):
                        # PIL Image — .size is (width, height)
                        page_h = page_img.size[1]  # type: ignore[union-attr]
                    elif isinstance(page_img, np.ndarray):
                        page_h = page_img.shape[0]
                    else:
                        page_h = float(np.array(page_img).shape[0])

                    keep_feats = [
                        lts_of_ty[ii]["type"] == "footer"
                        and bxs[i]["bottom"] < page_h * 0.9 / scale_factor,
                        lts_of_ty[ii]["type"] == "header"
                        and bxs[i]["top"] > page_h * 0.1 / scale_factor,
                    ]

                    if (
                        drop
                        and lts_of_ty[ii]["type"] in self.garbage_layouts
                        and not any(keep_feats)
                    ):
                        garbages.setdefault(lts_of_ty[ii]["type"], []).append(
                            bxs[i].get("text", "")
                        )
                        bxs.pop(i)
                        continue

                    bxs[i]["layoutno"] = f"{ty}-{ii}"
                    bxs[i]["layout_type"] = (
                        lts_of_ty[ii]["type"]
                        if lts_of_ty[ii]["type"] != "equation"
                        else "figure"
                    )
                    i += 1

            for lt_name in _LAYOUT_TAG_ORDER:
                _find_and_tag(lt_name)

            # Add placeholder boxes for figure/equation layouts with no text
            fig_lts = [lt for lt in lts if lt["type"] in ("figure", "equation")]
            for idx, lt in enumerate(fig_lts):
                if lt.get("visited"):
                    continue
                placeholder = deepcopy(lt)
                placeholder.pop("type", None)
                placeholder["text"] = ""
                placeholder["layout_type"] = "figure"
                placeholder["layoutno"] = f"figure-{idx}"
                bxs.append(placeholder)

            all_boxes.extend(bxs)

        # -- Cross-page Counter dedup for repeating garbage text ------------
        garbage_set: set[str] = set()
        for _key, texts in garbages.items():
            counts = Counter(texts)
            for text, count in counts.items():
                if count > 1:
                    garbage_set.add(text)

        if garbage_set:
            logger.debug(
                "Removing %d repeating garbage strings across pages",
                len(garbage_set),
            )

        tagged_boxes = [b for b in all_boxes if b.get("text", "").strip() not in garbage_set]

        return tagged_boxes, page_layouts

    # ------------------------------------------------------------------
    # Convenience — forward without tagging
    # ------------------------------------------------------------------

    def forward(
        self,
        image_list: Sequence[np.ndarray],
        thr: float = 0.7,
        batch_size: int = 16,
    ) -> list[list[dict[str, Any]]]:
        """Run layout detection only (no OCR tagging)."""
        return self._run_inference(image_list, thr, batch_size)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the ONNX session."""
        logger.info("Closing LayoutRecognizer ONNX session")
        if hasattr(self, "ort_sess"):
            del self.ort_sess

    def __del__(self) -> None:
        self.close()
