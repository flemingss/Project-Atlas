"""OCR post-processing for text detection and recognition.

Ported from RAGFlow's deepdoc/vision/postprocess.py (Apache 2.0, InfiniFlow/ragflow).
"""
from __future__ import annotations

import copy
import re

import cv2
import numpy as np
import pyclipper
from shapely.geometry import Polygon


def build_post_process(config: dict, global_config: dict | None = None):
    """Factory function — instantiate a post-processor by name.

    Parameters
    ----------
    config:
        Must contain a ``"name"`` key whose value is one of
        ``"DBPostProcess"`` or ``"CTCLabelDecode"``.  Remaining keys are
        forwarded as keyword arguments.
    global_config:
        Optional extra kwargs merged into *config*.
    """
    support_dict = {
        "DBPostProcess": DBPostProcess,
        "CTCLabelDecode": CTCLabelDecode,
    }

    config = copy.deepcopy(config)
    module_name = config.pop("name")
    if module_name == "None":
        return None
    if global_config is not None:
        config.update(global_config)
    module_class = support_dict.get(module_name)
    if module_class is None:
        raise ValueError(
            "post process only support {}".format(list(support_dict))
        )
    return module_class(**config)


# ---------------------------------------------------------------------------
# DBPostProcess — Differentiable-Binarization text-detection post-processing
# ---------------------------------------------------------------------------


class DBPostProcess:
    """Post-process for Differentiable Binarization (DB) text detection.

    Takes detection model output maps and generates bounding boxes for
    detected text regions.
    """

    def __init__(
        self,
        thresh: float = 0.3,
        box_thresh: float = 0.7,
        max_candidates: int = 1000,
        unclip_ratio: float = 2.0,
        use_dilation: bool = False,
        score_mode: str = "fast",
        box_type: str = "quad",
        **kwargs,
    ) -> None:
        self.thresh = thresh
        self.box_thresh = box_thresh
        self.max_candidates = max_candidates
        self.unclip_ratio = unclip_ratio
        self.min_size = 3
        self.score_mode = score_mode
        self.box_type = box_type
        assert score_mode in [
            "slow",
            "fast",
        ], "Score mode must be in [slow, fast] but got: {}".format(score_mode)

        self.dilation_kernel = (
            None if not use_dilation else np.array([[1, 1], [1, 1]])
        )

    # -- polygon path (box_type == "poly") ---------------------------------

    def polygons_from_bitmap(
        self,
        pred: np.ndarray,
        _bitmap: np.ndarray,
        dest_width: int,
        dest_height: int,
    ):
        """Extract polygon contours from the binarized prediction map."""
        bitmap = _bitmap
        height, width = bitmap.shape

        boxes: list = []
        scores: list = []

        contours, _ = cv2.findContours(
            (bitmap * 255).astype(np.uint8),
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        for contour in contours[: self.max_candidates]:
            epsilon = 0.002 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            points = approx.reshape((-1, 2))
            if points.shape[0] < 4:
                continue

            score = self.box_score_fast(pred, points.reshape(-1, 2))
            if self.box_thresh > score:
                continue

            if points.shape[0] > 2:
                box = self.unclip(points, self.unclip_ratio)
                if len(box) > 1:
                    continue
            else:
                continue

            box = box.reshape(-1, 2)

            _, sside = self.get_mini_boxes(box.reshape((-1, 1, 2)))
            if sside < self.min_size + 2:
                continue

            box = np.array(box)
            box[:, 0] = np.clip(
                np.round(box[:, 0] / width * dest_width), 0, dest_width
            )
            box[:, 1] = np.clip(
                np.round(box[:, 1] / height * dest_height), 0, dest_height
            )
            boxes.append(box.tolist())
            scores.append(score)
        return boxes, scores

    # -- quad path (box_type == "quad") -------------------------------------

    def boxes_from_bitmap(
        self,
        pred: np.ndarray,
        _bitmap: np.ndarray,
        dest_width: int,
        dest_height: int,
    ):
        """Find contours in the binarized prediction map and produce
        minimum-area rotated bounding boxes.
        """
        bitmap = _bitmap
        height, width = bitmap.shape

        outs = cv2.findContours(
            (bitmap * 255).astype(np.uint8),
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if len(outs) == 3:
            _img, contours, _ = outs[0], outs[1], outs[2]
        elif len(outs) == 2:
            contours, _ = outs[0], outs[1]
        else:
            contours = outs[0] if outs else []

        num_contours = min(len(contours), self.max_candidates)

        boxes: list = []
        scores: list = []
        for index in range(num_contours):
            contour = contours[index]
            points, sside = self.get_mini_boxes(contour)
            if sside < self.min_size:
                continue
            points = np.array(points)
            if self.score_mode == "fast":
                score = self.box_score_fast(pred, points.reshape(-1, 2))
            else:
                score = self.box_score_slow(pred, contour)
            if self.box_thresh > score:
                continue

            box = self.unclip(points, self.unclip_ratio).reshape(-1, 1, 2)
            box, sside = self.get_mini_boxes(box)
            if sside < self.min_size + 2:
                continue
            box = np.array(box)

            box[:, 0] = np.clip(
                np.round(box[:, 0] / width * dest_width), 0, dest_width
            )
            box[:, 1] = np.clip(
                np.round(box[:, 1] / height * dest_height), 0, dest_height
            )
            boxes.append(box.astype("int32"))
            scores.append(score)
        return np.array(boxes, dtype="int32"), scores

    # -- helpers ------------------------------------------------------------

    def unclip(self, box: np.ndarray, unclip_ratio: float) -> np.ndarray:
        """Expand a detected region using *pyclipper*."""
        poly = Polygon(box)
        distance = poly.area * unclip_ratio / poly.length
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        expanded = np.array(offset.Execute(distance))
        return expanded

    def get_mini_boxes(self, contour: np.ndarray):
        """Return the 4-point minimum bounding rectangle (sorted clockwise)
        and its shortest side length.
        """
        bounding_box = cv2.minAreaRect(contour)
        points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])

        index_1, index_2, index_3, index_4 = 0, 1, 2, 3
        if points[1][1] > points[0][1]:
            index_1 = 0
            index_4 = 1
        else:
            index_1 = 1
            index_4 = 0
        if points[3][1] > points[2][1]:
            index_2 = 2
            index_3 = 3
        else:
            index_2 = 3
            index_3 = 2

        box = [
            points[index_1],
            points[index_2],
            points[index_3],
            points[index_4],
        ]
        return box, min(bounding_box[1])

    def box_score_fast(self, bitmap: np.ndarray, _box: np.ndarray) -> float:
        """Score a box using the mean prediction value inside its polygon."""
        h, w = bitmap.shape[:2]
        box = _box.copy()
        xmin = np.clip(np.floor(box[:, 0].min()).astype("int32"), 0, w - 1)
        xmax = np.clip(np.ceil(box[:, 0].max()).astype("int32"), 0, w - 1)
        ymin = np.clip(np.floor(box[:, 1].min()).astype("int32"), 0, h - 1)
        ymax = np.clip(np.ceil(box[:, 1].max()).astype("int32"), 0, h - 1)

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        cv2.fillPoly(mask, box.reshape(1, -1, 2).astype("int32"), 1)
        return cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0]

    def box_score_slow(self, bitmap: np.ndarray, contour: np.ndarray) -> float:
        """Score a box using the polygon mean (slower but more accurate)."""
        h, w = bitmap.shape[:2]
        contour = contour.copy()
        contour = np.reshape(contour, (-1, 2))

        xmin = np.clip(np.min(contour[:, 0]), 0, w - 1)
        xmax = np.clip(np.max(contour[:, 0]), 0, w - 1)
        ymin = np.clip(np.min(contour[:, 1]), 0, h - 1)
        ymax = np.clip(np.max(contour[:, 1]), 0, h - 1)

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)

        contour[:, 0] = contour[:, 0] - xmin
        contour[:, 1] = contour[:, 1] - ymin

        cv2.fillPoly(mask, contour.reshape(1, -1, 2).astype("int32"), 1)
        return cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0]

    # -- main entry point ---------------------------------------------------

    def __call__(self, outs_dict: dict, shape_list: np.ndarray) -> list[dict]:
        """Process detection model output into bounding boxes.

        Parameters
        ----------
        outs_dict:
            Dict with a ``"maps"`` key containing the raw prediction tensor
            of shape ``(B, 1, H, W)``.
        shape_list:
            Array of shape ``(B, 4)`` with
            ``[src_h, src_w, ratio_h, ratio_w]`` per batch element.

        Returns
        -------
        List of dicts, each with a ``"points"`` key holding the detected
        boxes for one batch element.
        """
        pred = outs_dict["maps"]
        if not isinstance(pred, np.ndarray):
            pred = pred.numpy()
        pred = pred[:, 0, :, :]
        segmentation = pred > self.thresh

        boxes_batch: list[dict] = []
        for batch_index in range(pred.shape[0]):
            src_h, src_w, ratio_h, ratio_w = shape_list[batch_index]
            if self.dilation_kernel is not None:
                mask = cv2.dilate(
                    np.array(segmentation[batch_index]).astype(np.uint8),
                    self.dilation_kernel,
                )
            else:
                mask = segmentation[batch_index]
            if self.box_type == "poly":
                boxes, scores = self.polygons_from_bitmap(
                    pred[batch_index], mask, src_w, src_h
                )
            elif self.box_type == "quad":
                boxes, scores = self.boxes_from_bitmap(
                    pred[batch_index], mask, src_w, src_h
                )
            else:
                raise ValueError(
                    "box_type can only be one of ['quad', 'poly']"
                )

            boxes_batch.append({"points": boxes})
        return boxes_batch


# ---------------------------------------------------------------------------
# CTC label decode — text recognition post-processing
# ---------------------------------------------------------------------------


class BaseRecLabelDecode:
    """Convert between text-label and text-index."""

    def __init__(
        self,
        character_dict_path: str | None = None,
        use_space_char: bool = False,
    ) -> None:
        self.beg_str = "sos"
        self.end_str = "eos"
        self.reverse = False
        self.character_str: list[str] | str = []

        if character_dict_path is None:
            self.character_str = "0123456789abcdefghijklmnopqrstuvwxyz"
            dict_character = list(self.character_str)
        else:
            with open(character_dict_path, "rb") as fin:
                lines = fin.readlines()
                for line in lines:
                    line_decoded = line.decode("utf-8").strip("\n").strip("\r\n")
                    self.character_str.append(line_decoded)  # type: ignore[union-attr]
            if use_space_char:
                self.character_str.append(" ")  # type: ignore[union-attr]
            dict_character = list(self.character_str)
            if "arabic" in (character_dict_path or ""):
                self.reverse = True

        dict_character = self.add_special_char(dict_character)
        self.dict: dict[str, int] = {}
        for i, char in enumerate(dict_character):
            self.dict[char] = i
        self.character = dict_character

    def pred_reverse(self, pred: str) -> str:
        """Reverse prediction for RTL scripts (e.g. Arabic)."""
        pred_re: list[str] = []
        c_current = ""
        for c in pred:
            if not bool(re.search("[a-zA-Z0-9 :*./%+-]", c)):
                if c_current != "":
                    pred_re.append(c_current)
                pred_re.append(c)
                c_current = ""
            else:
                c_current += c
        if c_current != "":
            pred_re.append(c_current)

        return "".join(pred_re[::-1])

    def add_special_char(self, dict_character: list[str]) -> list[str]:
        return dict_character

    def decode(
        self,
        text_index: np.ndarray,
        text_prob: np.ndarray | None = None,
        is_remove_duplicate: bool = False,
    ) -> list[tuple[str, float]]:
        """Convert text-index into text-label."""
        result_list: list[tuple[str, float]] = []
        ignored_tokens = self.get_ignored_tokens()
        batch_size = len(text_index)
        for batch_idx in range(batch_size):
            selection = np.ones(len(text_index[batch_idx]), dtype=bool)
            if is_remove_duplicate:
                selection[1:] = (
                    text_index[batch_idx][1:] != text_index[batch_idx][:-1]
                )
            for ignored_token in ignored_tokens:
                selection &= text_index[batch_idx] != ignored_token

            char_list = [
                self.character[text_id]
                for text_id in text_index[batch_idx][selection]
            ]
            if text_prob is not None:
                conf_list = text_prob[batch_idx][selection]
            else:
                conf_list = [1] * len(selection)
            if len(conf_list) == 0:
                conf_list = [0]

            text = "".join(char_list)

            if self.reverse:  # for Arabic recognition
                text = self.pred_reverse(text)

            result_list.append((text, np.mean(conf_list).tolist()))
        return result_list

    def get_ignored_tokens(self) -> list[int]:
        return [0]  # CTC blank


class CTCLabelDecode(BaseRecLabelDecode):
    """CTC label decoder for text recognition.

    Converts model prediction tensor to ``(text, confidence)`` pairs via
    argmax and CTC-style duplicate/blank removal.
    """

    def __init__(
        self,
        character_dict_path: str | None = None,
        use_space_char: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(character_dict_path, use_space_char)

    def __call__(
        self,
        preds: np.ndarray,
        label: np.ndarray | None = None,
        *args,
        **kwargs,
    ) -> list[tuple[str, float]] | tuple[list, list]:
        """Decode prediction tensor to text + confidence.

        Parameters
        ----------
        preds:
            Raw model output of shape ``(B, T, C)`` where *T* is the
            sequence length and *C* the number of character classes.
        label:
            Optional ground-truth indices for evaluation.
        """
        if isinstance(preds, (tuple, list)):
            preds = preds[-1]
        if not isinstance(preds, np.ndarray):
            preds = preds.numpy()
        preds_idx = preds.argmax(axis=2)
        preds_prob = preds.max(axis=2)
        text = self.decode(preds_idx, preds_prob, is_remove_duplicate=True)
        if label is None:
            return text
        label_decoded = self.decode(label)
        return text, label_decoded

    def add_special_char(self, dict_character: list[str]) -> list[str]:
        """Prepend ``'blank'`` token at index 0."""
        dict_character = ["blank"] + dict_character
        return dict_character
