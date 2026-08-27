"""Tests for atlas.ingest.model_manager — ONNX model management."""

from __future__ import annotations

import pytest

from atlas.ingest.model_manager import REQUIRED_MODELS, ModelManager

# ---------------------------------------------------------------------------
# REQUIRED_MODELS constant
# ---------------------------------------------------------------------------

def test_required_models_count():
    assert len(REQUIRED_MODELS) == 5


def test_required_models_contains_layout():
    assert "layout.onnx" in REQUIRED_MODELS


def test_required_models_contains_det():
    assert "det.onnx" in REQUIRED_MODELS


def test_required_models_contains_rec():
    assert "rec.onnx" in REQUIRED_MODELS


def test_required_models_contains_ocr_res():
    assert "ocr.res" in REQUIRED_MODELS


def test_required_models_contains_tsr():
    assert "tsr.onnx" in REQUIRED_MODELS


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the ModelManager singleton before and after each test."""
    ModelManager.reset_instance()
    yield
    ModelManager.reset_instance()


def test_singleton_returns_same_instance(tmp_path):
    m1 = ModelManager.get_instance(models_dir=str(tmp_path / "models"))
    m2 = ModelManager.get_instance(models_dir=str(tmp_path / "other"))
    # Second call ignores models_dir — returns the same singleton
    assert m1 is m2


def test_singleton_after_reset_creates_new(tmp_path):
    m1 = ModelManager.get_instance(models_dir=str(tmp_path / "m1"))
    ModelManager.reset_instance()
    m2 = ModelManager.get_instance(models_dir=str(tmp_path / "m2"))
    assert m1 is not m2
    assert m1.models_dir != m2.models_dir


# ---------------------------------------------------------------------------
# models_available
# ---------------------------------------------------------------------------

def test_models_available_false_when_dir_missing(tmp_path):
    mgr = ModelManager.get_instance(models_dir=str(tmp_path / "nonexistent"))
    available = mgr.models_available()
    # Returns dict[str, bool] — all should be False
    assert isinstance(available, dict)
    assert all(v is False for v in available.values())


def test_models_available_partial(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    # Only create one model file
    (d / "layout.onnx").write_bytes(b"fake")
    mgr = ModelManager.get_instance(models_dir=str(d))
    available = mgr.models_available()
    assert available["layout.onnx"] is True
    assert available["det.onnx"] is False


def test_models_available_all_present(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    for name in REQUIRED_MODELS:
        (d / name).write_bytes(b"fake")
    mgr = ModelManager.get_instance(models_dir=str(d))
    available = mgr.models_available()
    assert all(v is True for v in available.values())
    assert len(available) == 5


# ---------------------------------------------------------------------------
# get_model_path
# ---------------------------------------------------------------------------

def test_get_model_path_exact_match(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    for name in REQUIRED_MODELS:
        (d / name).write_bytes(b"fake")
    mgr = ModelManager.get_instance(models_dir=str(d))
    path = mgr.get_model_path("layout.onnx")
    assert path.name == "layout.onnx"
    assert path.exists()


def test_get_model_path_short_name(tmp_path):
    """Short name 'layout' resolves to 'layout.onnx'."""
    d = tmp_path / "models"
    d.mkdir()
    for name in REQUIRED_MODELS:
        (d / name).write_bytes(b"fake")
    mgr = ModelManager.get_instance(models_dir=str(d))
    path = mgr.get_model_path("layout")
    assert path.name == "layout.onnx"


def test_get_model_path_ocr_res(tmp_path):
    """Exact match for non-.onnx file 'ocr.res'."""
    d = tmp_path / "models"
    d.mkdir()
    for name in REQUIRED_MODELS:
        (d / name).write_bytes(b"fake")
    mgr = ModelManager.get_instance(models_dir=str(d))
    path = mgr.get_model_path("ocr.res")
    assert path.name == "ocr.res"


def test_get_model_path_unknown_raises(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    mgr = ModelManager.get_instance(models_dir=str(d))
    with pytest.raises(ValueError, match="Unknown model name"):
        mgr.get_model_path("nonexistent_model")


def test_get_model_path_missing_file_raises(tmp_path):
    """Known model name but file not on disk → FileNotFoundError."""
    d = tmp_path / "models"
    d.mkdir()
    mgr = ModelManager.get_instance(models_dir=str(d))
    with pytest.raises(FileNotFoundError):
        mgr.get_model_path("layout")


# ---------------------------------------------------------------------------
# models_dir property
# ---------------------------------------------------------------------------

def test_models_dir_property(tmp_path):
    d = tmp_path / "my_models"
    mgr = ModelManager.get_instance(models_dir=str(d))
    assert mgr.models_dir == d
