"""ONNX model manager for layout-aware PDF parsing.

Downloads and caches ONNX models from HuggingFace InfiniFlow/deepdoc repo.
Models are downloaded on first use and cached in the configured models directory.

Derived from RAGFlow's model loading patterns (Apache 2.0, InfiniFlow/ragflow).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# HuggingFace repo containing the ONNX models
_HF_REPO_ID = "InfiniFlow/deepdoc"

# Required model files and their purposes
REQUIRED_MODELS: dict[str, str] = {
    "layout.onnx": "Page layout recognition (text, title, table, figure, etc.)",
    "det.onnx": "Text detection (bounding box localisation)",
    "rec.onnx": "Text recognition (OCR character recognition)",
    "ocr.res": "OCR resource / dictionary file",
    "tsr.onnx": "Table structure recognition",
}


def _default_models_dir() -> Path:
    """Return the default model cache directory.

    Precedence:
      1. ``ATLAS_MODELS_DIR`` environment variable
      2. ``./models/deepdoc`` relative to the working directory
    """
    env = os.environ.get("ATLAS_MODELS_DIR")
    if env:
        return Path(env)
    return Path("models") / "deepdoc"


class ModelManager:
    """Thread-safe singleton that downloads and caches ONNX models.

    Usage::

        mgr = ModelManager.get_instance()
        mgr.ensure_models()
        layout_path = mgr.get_model_path("layout")
    """

    _instance: ModelManager | None = None
    _lock = threading.Lock()

    def __init__(self, models_dir: Path | str | None = None) -> None:
        self._models_dir = Path(models_dir) if models_dir else _default_models_dir()
        self._download_lock = threading.Lock()
        self._downloaded = False
        logger.info("ModelManager initialised — cache dir: %s", self._models_dir)

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, models_dir: Path | str | None = None) -> ModelManager:
        """Return the global singleton, creating it on first call.

        Parameters
        ----------
        models_dir:
            Override the default cache directory.  Only used when the
            singleton is first created; subsequent calls ignore this arg.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(models_dir=models_dir)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful for testing)."""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def models_dir(self) -> Path:
        """Return the resolved models cache directory."""
        return self._models_dir

    def get_model_path(self, model_name: str) -> Path:
        """Return the local path for a model file.

        Parameters
        ----------
        model_name:
            Logical model name **without** extension (e.g. ``"layout"``).
            The method resolves ``layout`` → ``layout.onnx`` automatically
            for ``.onnx`` models, or looks for an exact filename match among
            :data:`REQUIRED_MODELS`.

        Raises
        ------
        FileNotFoundError
            If the resolved path does not exist on disk.
        ValueError
            If *model_name* cannot be mapped to a known model file.
        """
        # Try exact match first (e.g. "ocr.res")
        if model_name in REQUIRED_MODELS:
            p = self._models_dir / model_name
        else:
            # Try appending .onnx
            candidate = f"{model_name}.onnx"
            if candidate in REQUIRED_MODELS:
                p = self._models_dir / candidate
            else:
                raise ValueError(
                    f"Unknown model name '{model_name}'. "
                    f"Known models: {', '.join(sorted(REQUIRED_MODELS))}"
                )
        if not p.exists():
            raise FileNotFoundError(
                f"Model file not found at {p}. "
                "Call ensure_models() to download required models."
            )
        return p

    def ensure_models(self) -> bool:
        """Download all required models if they are not already cached.

        Returns ``True`` when every required file is present on disk
        after the call.
        """
        if self._downloaded and self._all_present():
            return True

        with self._download_lock:
            # Double-check after acquiring lock
            if self._downloaded and self._all_present():
                return True

            missing = [
                name for name in REQUIRED_MODELS
                if not (self._models_dir / name).exists()
            ]
            if not missing:
                logger.info("All models already cached in %s", self._models_dir)
                self._downloaded = True
                return True

            logger.info(
                "Downloading %d missing model(s) from HuggingFace '%s' → %s: %s",
                len(missing), _HF_REPO_ID, self._models_dir,
                ", ".join(missing),
            )

            try:
                self._download_models(missing)
            except Exception:
                logger.exception("Model download failed")
                return False

            # Verify everything arrived
            still_missing = [
                name for name in REQUIRED_MODELS
                if not (self._models_dir / name).exists()
            ]
            if still_missing:
                logger.error(
                    "Models still missing after download: %s", still_missing
                )
                return False

            self._downloaded = True
            logger.info("All models ready in %s", self._models_dir)
            return True

    def models_available(self) -> dict[str, bool]:
        """Return a mapping of model name → exists-on-disk for health checks."""
        return {
            name: (self._models_dir / name).exists()
            for name in REQUIRED_MODELS
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _all_present(self) -> bool:
        return all(
            (self._models_dir / name).exists() for name in REQUIRED_MODELS
        )

    def _download_models(self, missing: list[str]) -> None:
        """Download model files from HuggingFace Hub.

        Tries ``snapshot_download`` first (grabs the whole repo once).
        Falls back to ``hf_hub_download`` file-by-file if needed.
        """
        try:
            from huggingface_hub import snapshot_download

            self._models_dir.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                repo_id=_HF_REPO_ID,
                local_dir=str(self._models_dir),
                local_dir_use_symlinks=False,
                allow_patterns=[f"*{name}" for name in missing],
            )
            logger.info("snapshot_download completed for %s", _HF_REPO_ID)
        except Exception as exc:
            logger.warning(
                "snapshot_download failed (%s), falling back to hf_hub_download",
                exc,
            )
            self._download_individual(missing)

    def _download_individual(self, filenames: list[str]) -> None:
        """Download models one-by-one via ``hf_hub_download``."""
        from huggingface_hub import hf_hub_download

        self._models_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            logger.info("Downloading %s from %s …", name, _HF_REPO_ID)
            downloaded_path = hf_hub_download(
                repo_id=_HF_REPO_ID,
                filename=name,
                local_dir=str(self._models_dir),
                local_dir_use_symlinks=False,
            )
            logger.info("Downloaded %s → %s", name, downloaded_path)
