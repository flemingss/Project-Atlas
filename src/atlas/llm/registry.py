from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.llm.deterministic import DeterministicProvider
from atlas.llm.openai_compat import OpenAICompatibleProvider
from atlas.llm.provider import ILlmProvider
from atlas.settings import Settings


@dataclass(frozen=True)
class ResolvedModel:
    role: str
    provider_name: str
    model_name: str
    params: dict[str, Any]


class ModelRegistry:
    def __init__(self, *, settings: Settings, models_cfg: dict[str, Any]):
        self._settings = settings
        self._cfg = models_cfg

    def resolve(self, role: str) -> ResolvedModel:
        roles = self._cfg.get("roles", {})
        if role not in roles:
            raise KeyError(f"Unknown model role: {role}")
        entry = roles[role]
        return ResolvedModel(
            role=role,
            provider_name=entry["provider"],
            model_name=entry["model_name"],
            params=entry.get("params", {}) or {},
        )

    def provider_for(self, provider_name: str) -> ILlmProvider:
        providers = self._cfg.get("providers", {})
        if provider_name not in providers:
            raise KeyError(f"Unknown provider: {provider_name}")
        p = providers[provider_name]
        ptype = p.get("type")

        if ptype == "openai_compat":
            # Base URL is set via env var to make swapping IP easy.
            return OpenAICompatibleProvider(base_url=self._settings.atlas_openai_base_url)

        if ptype == "deterministic":
            return DeterministicProvider()

        if ptype in ("openai", "anthropic"):
            # Wired for config purposes; implementation comes next.
            raise NotImplementedError(f"Provider type '{ptype}' not implemented yet")

        raise ValueError(f"Unsupported provider type: {ptype}")
