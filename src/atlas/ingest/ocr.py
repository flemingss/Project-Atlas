"""ONNX-based OCR engine — text detection and recognition.

Ported from RAGFlow's deepdoc/vision/ocr.py (Apache 2.0, InfiniFlow/ragflow).
"""
from __future__ import annotations

import copy
import gc
import logging
import math
import os
import time
from typing import Any

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    import onnxruntime as ort
except ImportError:
    ort = None  # type: ignore[assignment]

from .model_manager import ModelManager
from .postprocess import build_post_process

logger = logging.getLogger(__name__)

# Module-level cache for loaded ONNX sessions (keyed by file path).
# Type annotation deferred since ort may not be available (VLM-only builds).
_loaded_models: dict = {}


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def load_model(
    model_dir: str,
    name: str,
) -> tuple:
    """Load (or retrieve from cache) an ONNX model.

    Parameters
    ----------
    model_dir:
        Directory containing the ``.onnx`` file.
    name:
        Model stem — e.g. ``"det"`` resolves to ``det.onnx``.

    Returns
    -------
    ``(session, run_options)`` tuple.
    """
    if cv2 is None or ort is None:
        raise ImportError(
            "OCR module requires cv2 and onnxruntime. "
            "These are only available in the full Docker build (not slim). "
            "For VLM-only deployments, disable Docling-based document processing."
        )
    
    model_file_path = os.path.join(model_dir, name + ".onnx")

    cached = _loaded_models.get(model_file_path)
    if cached is not None:
        logger.info("load_model %s — reusing cached session", model_file_path)
        return cached

    if not os.path.exists(model_file_path):
        raise ValueError("Model file not found: {}".format(model_file_path))

    # Detect CUDA availability via torch (best-effort).
    def _cuda_is_available() -> bool:
        try:
            import torch  # noqa: F401
            return torch.cuda.is_available()
        except Exception:
            return False

    options = ort.SessionOptions()
    options.enable_cpu_mem_arena = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 2

    run_options = ort.RunOptions()

    if _cuda_is_available():
        sess = ort.InferenceSession(
            model_file_path,
            options=options,
            providers=["CUDAExecutionProvider"],
        )
        logger.info("load_model %s — using CUDA", model_file_path)
    else:
        sess = ort.InferenceSession(
            model_file_path,
            options=options,
            providers=["CPUExecutionProvider"],
        )
        run_options.add_run_config_entry(
            "memory.enable_memory_arena_shrinkage", "cpu"
        )
        logger.info("load_model %s — using CPU", model_file_path)

    result = (sess, run_options)
    _loaded_models[model_file_path] = result
    return result


# ---------------------------------------------------------------------------
# Preprocessing operators (inlined from RAGFlow's operators.py)
# ---------------------------------------------------------------------------


class _DetResizeForTest:
    """Resize image for text detection, keeping the aspect ratio and rounding
    dimensions to multiples of 32.
    """

    def __init__(
        self,
        limit_side_len: int = 960,
        limit_type: str = "max",
        image_shape: list[int] | None = None,
        **kwargs,
    ) -> None:
        self.limit_side_len = limit_side_len
        self.limit_type = limit_type
        self.image_shape = image_shape

    def __call__(self, data: dict) -> dict:
        img = data["image"]
        src_h, src_w, _ = img.shape
        if sum([src_h, src_w]) < 64:
            img = self._pad(img)

        if self.image_shape is not None:
            img, (ratio_h, ratio_w) = self._resize_fixed(img)
        else:
            img, (ratio_h, ratio_w) = self._resize_limit(img)

        data["image"] = img
        data["shape"] = np.array([src_h, src_w, ratio_h, ratio_w])
        return data

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _pad(im: np.ndarray, value: int = 0) -> np.ndarray:
        h, w, c = im.shape
        im_pad = np.zeros((max(32, h), max(32, w), c), np.uint8) + value
        im_pad[:h, :w, :] = im
        return im_pad

    def _resize_fixed(self, img: np.ndarray):
        resize_h, resize_w = self.image_shape  # type: ignore[misc]
        ori_h, ori_w = img.shape[:2]
        ratio_h = float(resize_h) / ori_h
        ratio_w = float(resize_w) / ori_w
        img = cv2.resize(img, (int(resize_w), int(resize_h)))
        return img, (ratio_h, ratio_w)

    def _resize_limit(self, img: np.ndarray):
        limit_side_len = self.limit_side_len
        h, w, _c = img.shape

        if self.limit_type == "max":
            if max(h, w) > limit_side_len:
                ratio = float(limit_side_len) / max(h, w)
            else:
                ratio = 1.0
        elif self.limit_type == "min":
            if min(h, w) < limit_side_len:
                ratio = float(limit_side_len) / min(h, w)
            else:
                ratio = 1.0
        else:
            ratio = float(limit_side_len) / max(h, w)

        resize_h = max(int(round(h * ratio / 32) * 32), 32)
        resize_w = max(int(round(w * ratio / 32) * 32), 32)

        if resize_w <= 0 or resize_h <= 0:
            return img, (1.0, 1.0)

        img = cv2.resize(img, (resize_w, resize_h))
        ratio_h = resize_h / float(h)
        ratio_w = resize_w / float(w)
        return img, (ratio_h, ratio_w)


class _NormalizeImage:
    """Normalise image: ``(img * scale - mean) / std``."""

    def __init__(
        self,
        scale: float | str = "1./255.",
        mean: list[float] | None = None,
        std: list[float] | None = None,
        order: str = "hwc",
        **kwargs,
    ) -> None:
        if isinstance(scale, str):
            import ast

            if "/" in scale:
                parts = scale.split("/")
                scale = ast.literal_eval(parts[0]) / ast.literal_eval(parts[1])
            else:
                scale = ast.literal_eval(scale)
        self.scale = np.float32(scale if scale is not None else 1.0 / 255.0)
        mean = mean if mean is not None else [0.485, 0.456, 0.406]
        std = std if std is not None else [0.229, 0.224, 0.225]

        shape = (3, 1, 1) if order == "chw" else (1, 1, 3)
        self.mean = np.array(mean).reshape(shape).astype("float32")
        self.std = np.array(std).reshape(shape).astype("float32")

    def __call__(self, data: dict) -> dict:
        img = data["image"]
        assert isinstance(img, np.ndarray), "invalid input 'img' in NormalizeImage"
        data["image"] = (img.astype("float32") * self.scale - self.mean) / self.std
        return data


class _ToCHWImage:
    """Transpose HWC image to CHW layout."""

    def __call__(self, data: dict) -> dict:
        img = data["image"]
        data["image"] = img.transpose((2, 0, 1))
        return data


class _KeepKeys:
    """Select specific keys from the data dict and return as a list."""

    def __init__(self, keep_keys: list[str], **kwargs) -> None:
        self.keep_keys = keep_keys

    def __call__(self, data: dict) -> list:
        return [data[key] for key in self.keep_keys]


def _transform(data: dict, ops: list) -> list | dict | None:
    """Run a pipeline of preprocessing operators on *data*."""
    for op in ops:
        data = op(data)
        if data is None:
            return None
    return data


# ---------------------------------------------------------------------------
# TextDetector — DBNet text detection
# ---------------------------------------------------------------------------


class TextDetector:
    """DBNet-based text detection using an ONNX model (``det.onnx``)."""

    def __init__(self, model_dir: str) -> None:
        pre_process_list = [
            _DetResizeForTest(limit_side_len=960, limit_type="max"),
            _NormalizeImage(
                std=[0.229, 0.224, 0.225],
                mean=[0.485, 0.456, 0.406],
                scale="1./255.",
                order="hwc",
            ),
            _ToCHWImage(),
            _KeepKeys(keep_keys=["image", "shape"]),
        ]
        postprocess_params = {
            "name": "DBPostProcess",
            "thresh": 0.3,
            "box_thresh": 0.5,
            "max_candidates": 1000,
            "unclip_ratio": 1.5,
            "use_dilation": False,
            "score_mode": "fast",
            "box_type": "quad",
        }

        self.postprocess_op = build_post_process(postprocess_params)
        self.predictor, self.run_options = load_model(model_dir, "det")
        self.input_tensor = self.predictor.get_inputs()[0]

        # If the model has a fixed spatial size, override the resize operator.
        img_h, img_w = self.input_tensor.shape[2:]
        if (
            isinstance(img_h, int)
            and isinstance(img_w, int)
            and img_h > 0
            and img_w > 0
        ):
            pre_process_list[0] = _DetResizeForTest(image_shape=[img_h, img_w])

        self.preprocess_op = pre_process_list

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def order_points_clockwise(pts: np.ndarray) -> np.ndarray:
        """Order 4 corner points: TL, TR, BR, BL."""
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        tmp = np.delete(pts, (np.argmin(s), np.argmax(s)), axis=0)
        diff = np.diff(np.array(tmp), axis=1)
        rect[1] = tmp[np.argmin(diff)]
        rect[3] = tmp[np.argmax(diff)]
        return rect

    @staticmethod
    def clip_det_res(
        points: np.ndarray, img_height: int, img_width: int
    ) -> np.ndarray:
        for pno in range(points.shape[0]):
            points[pno, 0] = int(min(max(points[pno, 0], 0), img_width - 1))
            points[pno, 1] = int(min(max(points[pno, 1], 0), img_height - 1))
        return points

    def filter_tag_det_res(
        self, dt_boxes: list | np.ndarray, image_shape: tuple
    ) -> np.ndarray:
        """Filter out small / invalid detected boxes and clip to image bounds."""
        img_height, img_width = image_shape[0:2]
        dt_boxes_new = []
        for box in dt_boxes:
            if isinstance(box, list):
                box = np.array(box)
            box = self.order_points_clockwise(box)
            box = self.clip_det_res(box, img_height, img_width)
            rect_width = int(np.linalg.norm(box[0] - box[1]))
            rect_height = int(np.linalg.norm(box[0] - box[3]))
            if rect_width <= 3 or rect_height <= 3:
                continue
            dt_boxes_new.append(box)
        dt_boxes = np.array(dt_boxes_new)
        return dt_boxes

    # -- main entry ---------------------------------------------------------

    def __call__(self, img: np.ndarray) -> tuple[np.ndarray | None, float]:
        """Detect text regions in *img*.

        Returns ``(dt_boxes, elapsed_time)`` where *dt_boxes* is an
        ``(N, 4, 2)`` int32 array of quadrilateral vertices, or *None*.
        """
        ori_im = img.copy()
        data: dict = {"image": img}

        st = time.time()
        result = _transform(data, self.preprocess_op)
        if result is None:
            return None, 0.0
        img_tensor, shape_list = result
        img_tensor = np.expand_dims(img_tensor, axis=0)
        shape_list = np.expand_dims(shape_list, axis=0)
        img_tensor = img_tensor.copy()

        input_dict = {self.input_tensor.name: img_tensor}
        outputs: Any = None
        for attempt in range(4):
            try:
                outputs = self.predictor.run(None, input_dict, self.run_options)
                break
            except Exception as exc:
                if attempt >= 3:
                    raise exc
                time.sleep(5)

        post_result = self.postprocess_op({"maps": outputs[0]}, shape_list)
        dt_boxes = post_result[0]["points"]
        dt_boxes = self.filter_tag_det_res(dt_boxes, ori_im.shape)

        return dt_boxes, time.time() - st

    def close(self) -> None:
        logger.info("Close text detector.")
        if hasattr(self, "predictor"):
            del self.predictor
        gc.collect()

    def __del__(self) -> None:
        self.close()


# ---------------------------------------------------------------------------
# TextRecognizer — CRNN text recognition
# ---------------------------------------------------------------------------


class TextRecognizer:
    """CRNN-based text recognition using an ONNX model (``rec.onnx``)."""

    def __init__(self, model_dir: str) -> None:
        self.rec_image_shape = [3, 48, 320]
        self.rec_batch_num = 16
        postprocess_params = {
            "name": "CTCLabelDecode",
            "character_dict_path": os.path.join(model_dir, "ocr.res"),
            "use_space_char": True,
        }
        self.postprocess_op = build_post_process(postprocess_params)
        self.predictor, self.run_options = load_model(model_dir, "rec")
        self.input_tensor = self.predictor.get_inputs()[0]

    def resize_norm_img(
        self, img: np.ndarray, max_wh_ratio: float
    ) -> np.ndarray:
        """Resize preserving aspect ratio, normalise, and pad to fixed width."""
        imgC, imgH, imgW = self.rec_image_shape

        assert imgC == img.shape[2]
        imgW = int(imgH * max_wh_ratio)
        w = self.input_tensor.shape[3:][0]
        if isinstance(w, str):
            pass
        elif w is not None and w > 0:
            imgW = w
        h, w = img.shape[:2]
        ratio = w / float(h)
        if math.ceil(imgH * ratio) > imgW:
            resized_w = imgW
        else:
            resized_w = int(math.ceil(imgH * ratio))

        resized_image = cv2.resize(img, (resized_w, imgH))
        resized_image = resized_image.astype("float32")
        resized_image = resized_image.transpose((2, 0, 1)) / 255
        resized_image -= 0.5
        resized_image /= 0.5
        padding_im = np.zeros((imgC, imgH, imgW), dtype=np.float32)
        padding_im[:, :, 0:resized_w] = resized_image
        return padding_im

    def __call__(
        self, img_list: list[np.ndarray]
    ) -> tuple[list[tuple[str, float]], float]:
        """Recognise text in a list of cropped text-region images.

        Returns ``(results, elapsed_time)`` where *results* is a list of
        ``(text, confidence)`` tuples in the same order as *img_list*.
        """
        img_num = len(img_list)
        # Sort by aspect ratio for efficient batching.
        width_list = [img.shape[1] / float(img.shape[0]) for img in img_list]
        indices = np.argsort(np.array(width_list))
        rec_res: list[tuple[str, float]] = [("", 0.0)] * img_num
        batch_num = self.rec_batch_num
        st = time.time()

        for beg_img_no in range(0, img_num, batch_num):
            end_img_no = min(img_num, beg_img_no + batch_num)
            norm_img_batch = []
            imgC, imgH, imgW = self.rec_image_shape[:3]
            max_wh_ratio = imgW / imgH
            for ino in range(beg_img_no, end_img_no):
                h, w = img_list[indices[ino]].shape[0:2]
                wh_ratio = w * 1.0 / h
                max_wh_ratio = max(max_wh_ratio, wh_ratio)
            for ino in range(beg_img_no, end_img_no):
                norm_img = self.resize_norm_img(
                    img_list[indices[ino]], max_wh_ratio
                )
                norm_img = norm_img[np.newaxis, :]
                norm_img_batch.append(norm_img)
            norm_img_batch_arr = np.concatenate(norm_img_batch)
            norm_img_batch_arr = norm_img_batch_arr.copy()

            input_dict = {self.input_tensor.name: norm_img_batch_arr}
            outputs: Any = None
            for attempt in range(4):
                try:
                    outputs = self.predictor.run(
                        None, input_dict, self.run_options
                    )
                    break
                except Exception as exc:
                    if attempt >= 3:
                        raise exc
                    time.sleep(5)

            preds = outputs[0]
            rec_result = self.postprocess_op(preds)
            for rno in range(len(rec_result)):
                rec_res[indices[beg_img_no + rno]] = rec_result[rno]

        return rec_res, time.time() - st

    def close(self) -> None:
        logger.info("Close text recognizer.")
        if hasattr(self, "predictor"):
            del self.predictor
        gc.collect()

    def __del__(self) -> None:
        self.close()


# ---------------------------------------------------------------------------
# OCR — facade combining detection + recognition
# ---------------------------------------------------------------------------


class OCR:
    """Full OCR pipeline: detect text regions → crop → recognise.

    Parameters
    ----------
    model_dir:
        Path to directory containing ``det.onnx``, ``rec.onnx`` and
        ``ocr.res``.  When *None*, :class:`ModelManager` resolves the
        path automatically.
    """

    def __init__(self, model_dir: str | None = None) -> None:
        if model_dir is None:
            mgr = ModelManager.get_instance()
            mgr.ensure_models()
            model_dir = str(mgr.models_dir)

        self.text_detector = TextDetector(model_dir)
        self.text_recognizer = TextRecognizer(model_dir)
        self.drop_score = 0.5

    # -- helpers ------------------------------------------------------------

    def get_rotate_crop_image(
        self, img: np.ndarray, points: np.ndarray
    ) -> np.ndarray:
        """Perspective-transform a quadrilateral text region from *img*.

        For tall boxes (height/width >= 1.5) the method tests the original
        orientation plus 90-degree clockwise and counter-clockwise rotations,
        returning the crop with the highest OCR confidence.
        """
        assert len(points) == 4, "shape of points must be 4*2"
        img_crop_width = int(
            max(
                np.linalg.norm(points[0] - points[1]),
                np.linalg.norm(points[2] - points[3]),
            )
        )
        img_crop_height = int(
            max(
                np.linalg.norm(points[0] - points[3]),
                np.linalg.norm(points[1] - points[2]),
            )
        )
        pts_std = np.float32(
            [
                [0, 0],
                [img_crop_width, 0],
                [img_crop_width, img_crop_height],
                [0, img_crop_height],
            ]
        )
        M = cv2.getPerspectiveTransform(points, pts_std)
        dst_img = cv2.warpPerspective(
            img,
            M,
            (img_crop_width, img_crop_height),
            borderMode=cv2.BORDER_REPLICATE,
            flags=cv2.INTER_CUBIC,
        )
        dst_img_height, dst_img_width = dst_img.shape[0:2]
        if dst_img_height * 1.0 / dst_img_width >= 1.5:
            # Try original orientation.
            rec_result, _ = self.text_recognizer([dst_img])
            _text, score = rec_result[0]
            best_score = score
            best_img = dst_img

            # Try clockwise 90-degree rotation.
            rotated_cw = np.rot90(dst_img, k=3)
            rec_result, _ = self.text_recognizer([rotated_cw])
            _cw_text, rotated_cw_score = rec_result[0]
            if rotated_cw_score > best_score:
                best_score = rotated_cw_score
                best_img = rotated_cw

            # Try counter-clockwise 90-degree rotation.
            rotated_ccw = np.rot90(dst_img, k=1)
            rec_result, _ = self.text_recognizer([rotated_ccw])
            _ccw_text, rotated_ccw_score = rec_result[0]
            if rotated_ccw_score > best_score:
                best_img = rotated_ccw

            dst_img = best_img
        return dst_img

    @staticmethod
    def sorted_boxes(dt_boxes: np.ndarray | list) -> list:
        """Sort text boxes top-to-bottom, then left-to-right.

        Parameters
        ----------
        dt_boxes:
            Detected text boxes with shape ``(N, 4, 2)``.

        Returns
        -------
        Sorted list of boxes, each with shape ``(4, 2)``.
        """
        num_boxes = len(dt_boxes)
        _boxes = sorted(dt_boxes, key=lambda x: (x[0][1], x[0][0]))
        _boxes = list(_boxes)

        for i in range(num_boxes - 1):
            for j in range(i, -1, -1):
                if (
                    abs(_boxes[j + 1][0][1] - _boxes[j][0][1]) < 10
                    and _boxes[j + 1][0][0] < _boxes[j][0][0]
                ):
                    _boxes[j], _boxes[j + 1] = _boxes[j + 1], _boxes[j]
                else:
                    break
        return _boxes

    # -- public API ---------------------------------------------------------

    def detect(self, img: np.ndarray):
        """Detect text boxes in *img*.

        Returns an iterator of ``(box, ("", 0))`` pairs, or *None* if no
        boxes are found.
        """
        if img is None:
            return None

        dt_boxes, _elapse = self.text_detector(img)

        if dt_boxes is None or len(dt_boxes) == 0:
            return None

        return zip(
            self.sorted_boxes(dt_boxes),
            [("", 0) for _ in range(len(dt_boxes))],
        )

    def recognize(self, ori_im: np.ndarray, box: np.ndarray) -> str:
        """Recognise text for a single box in *ori_im*.

        Returns the recognised string, or ``""`` if confidence is below
        ``drop_score``.
        """
        img_crop = self.get_rotate_crop_image(ori_im, box)

        rec_res, _elapse = self.text_recognizer([img_crop])
        text, score = rec_res[0]
        if score < self.drop_score:
            return ""
        return text

    def recognize_batch(self, img_list: list[np.ndarray]) -> list[str]:
        """Batch recognition for a list of cropped text-region images."""
        rec_res, _elapse = self.text_recognizer(img_list)
        texts = []
        for text, score in rec_res:
            if score < self.drop_score:
                text = ""
            texts.append(text)
        return texts

    def __call__(
        self, img: np.ndarray
    ) -> list[tuple[list, tuple[str, float]]] | None:
        """Full pipeline: detect → sort → crop → recognise → filter.

        Parameters
        ----------
        img:
            Input image (BGR, ``np.ndarray``).

        Returns
        -------
        List of ``(box_coords, (text, score))`` tuples for all detected
        text regions with confidence >= ``drop_score``, or *None* if no
        text is detected.
        """
        if img is None:
            return None

        ori_im = img.copy()
        dt_boxes, _det_elapse = self.text_detector(img)

        if dt_boxes is None or len(dt_boxes) == 0:
            return None

        img_crop_list = []
        dt_boxes_sorted = self.sorted_boxes(dt_boxes)

        for bno in range(len(dt_boxes_sorted)):
            tmp_box = copy.deepcopy(dt_boxes_sorted[bno])
            img_crop = self.get_rotate_crop_image(ori_im, tmp_box)
            img_crop_list.append(img_crop)

        rec_res, _rec_elapse = self.text_recognizer(img_crop_list)

        filter_boxes, filter_rec_res = [], []
        for box, rec_result in zip(dt_boxes_sorted, rec_res):
            text, score = rec_result
            if score >= self.drop_score:
                filter_boxes.append(box)
                filter_rec_res.append(rec_result)

        return list(
            zip([a.tolist() for a in filter_boxes], filter_rec_res)
        )
