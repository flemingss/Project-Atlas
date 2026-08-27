from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from atlas.llm.profiles import apply_profile, resolve_profile_name


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
    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        config_dir: Path | None = None,
        profile: str | None = None,
    ):
        if config_dir is None:
            if root_dir is None:
                raise ValueError("ConfigManager requires either root_dir or config_dir")
            config_dir = root_dir / "config"

        self._config_dir = config_dir
        self._pipeline_path = config_dir / "pipeline.yaml"
        self._models_path = config_dir / "models.yaml"
        # Explicit profile override. When None the profile is resolved from
        # ATLAS_LLM_PROFILE or models.yaml's active_profile, so tests that
        # construct a ConfigManager without one keep the pre-profile behaviour.
        self._profile_override = profile
        self._cached: EffectiveConfig | None = None

    def load_yaml_defaults(self) -> EffectiveConfig:
        pipeline = self._load_yaml(self._pipeline_path)
        models = self._load_yaml(self._models_path)

        # Profiles are resolved here rather than in ModelRegistry so that every
        # consumer — the eight registry call sites, startup validation, and the
        # admin config endpoints — sees one already-resolved effective config.
        profile_name = resolve_profile_name(models, override=self._profile_override)
        models, pipeline = apply_profile(
            models=models, pipeline=pipeline, profile_name=profile_name
        )

        effective = {"pipeline": pipeline, "models": models}
        return EffectiveConfig(
            pipeline=pipeline,
            models=models,
            source={
                "yaml": {
                    "config_dir": str(self._config_dir),
                    "pipeline": str(self._pipeline_path),
                    "models": str(self._models_path),
                    "profile": profile_name or "<none>",
                }
            },
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
