from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class EffectiveConfig:
    pipeline: dict[str, Any]
    models: dict[str, Any]
    source: dict[str, Any]
    hash: str


def _stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ConfigManager:
    def __init__(self, *, root_dir: Path):
        self._root_dir = root_dir
        self._pipeline_path = root_dir / "config" / "pipeline.yaml"
        self._models_path = root_dir / "config" / "models.yaml"
        self._cached: EffectiveConfig | None = None

    def load_yaml_defaults(self) -> EffectiveConfig:
        pipeline = self._load_yaml(self._pipeline_path)
        models = self._load_yaml(self._models_path)
        effective = {"pipeline": pipeline, "models": models}
        return EffectiveConfig(
            pipeline=pipeline,
            models=models,
            source={"yaml": {"pipeline": str(self._pipeline_path), "models": str(self._models_path)}},
            hash=_stable_hash(effective),
        )

    def reload(self) -> EffectiveConfig:
        self._cached = self.load_yaml_defaults()
        return self._cached

    def get(self) -> EffectiveConfig:
        if self._cached is None:
            self._cached = self.load_yaml_defaults()
        return self._cached

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Missing config file: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Config at {path} must be a YAML mapping")
        return data
