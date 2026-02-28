"""Tests for atlas.ingest.postprocess — OCR post-processing factories and classes.

These tests only exercise the factory function and basic instantiation.
No ONNX models or heavy numeric computation is involved.
"""

from __future__ import annotations

import pytest

from atlas.ingest.postprocess import build_post_process, DBPostProcess, CTCLabelDecode


# ---------------------------------------------------------------------------
# build_post_process factory
# ---------------------------------------------------------------------------

def test_build_post_process_db():
    pp = build_post_process(
        {"name": "DBPostProcess", "thresh": 0.3, "box_thresh": 0.5,
         "max_candidates": 1000, "unclip_ratio": 1.5},
    )
    assert isinstance(pp, DBPostProcess)


def test_build_post_process_db_defaults():
    pp = build_post_process({"name": "DBPostProcess"})
    assert isinstance(pp, DBPostProcess)
    assert pp.thresh == 0.3  # default
    assert pp.box_thresh == 0.7  # default


def test_build_post_process_ctc():
    pp = build_post_process(
        {"name": "CTCLabelDecode", "character_dict_path": None},
    )
    assert isinstance(pp, CTCLabelDecode)
    assert pp is not None


def test_build_post_process_ctc_defaults():
    pp = build_post_process({"name": "CTCLabelDecode"})
    assert isinstance(pp, CTCLabelDecode)


def test_build_post_process_none_string():
    """Config name 'None' (string) returns None."""
    pp = build_post_process({"name": "None"})
    assert pp is None


def test_build_post_process_unknown_raises():
    """Unknown post-processor name raises ValueError."""
    with pytest.raises(ValueError, match="post process only support"):
        build_post_process({"name": "UnknownProcessor"})


def test_build_post_process_with_global_config():
    """Global config merges into the per-processor kwargs."""
    pp = build_post_process(
        {"name": "DBPostProcess"},
        global_config={"thresh": 0.1, "box_thresh": 0.2},
    )
    assert isinstance(pp, DBPostProcess)
    assert pp.thresh == 0.1
    assert pp.box_thresh == 0.2


# ---------------------------------------------------------------------------
# DBPostProcess construction
# ---------------------------------------------------------------------------

def test_dbpostprocess_stores_params():
    pp = DBPostProcess(
        thresh=0.25, box_thresh=0.6, max_candidates=500,
        unclip_ratio=1.8, score_mode="fast", box_type="quad",
    )
    assert pp.thresh == 0.25
    assert pp.box_thresh == 0.6
    assert pp.max_candidates == 500
    assert pp.unclip_ratio == 1.8
    assert pp.score_mode == "fast"
    assert pp.box_type == "quad"


def test_dbpostprocess_rejects_bad_score_mode():
    with pytest.raises(AssertionError):
        DBPostProcess(score_mode="invalid")


# ---------------------------------------------------------------------------
# CTCLabelDecode construction
# ---------------------------------------------------------------------------

def test_ctc_label_decode_with_no_dict():
    """CTCLabelDecode with no character dict falls back to built-in charset."""
    decoder = CTCLabelDecode(character_dict_path=None, use_space_char=False)
    # Should have loaded the default character set
    assert hasattr(decoder, "character")
    assert len(decoder.character) > 0


def test_ctc_label_decode_with_space_char():
    """use_space_char only takes effect with a dict file; with None it uses
    the hardcoded 0-9a-z charset. Verify we still get a valid decoder."""
    decoder = CTCLabelDecode(character_dict_path=None, use_space_char=True)
    assert hasattr(decoder, "character")
    # With no dict file the built-in charset is 0-9 + a-z (+ blank prefix)
    assert "a" in decoder.character
    assert "0" in decoder.character
