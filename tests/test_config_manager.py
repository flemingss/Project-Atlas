from __future__ import annotations

from pathlib import Path

from atlas.config_manager import ConfigManager


def test_load_yaml_defaults_has_hash() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    mgr = ConfigManager(root_dir=root_dir)
    effective = mgr.load_yaml_defaults()
    assert isinstance(effective.hash, str)
    assert len(effective.hash) == 64
    assert "thresholds" in effective.pipeline
    assert "roles" in effective.models
