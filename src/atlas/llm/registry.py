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
    think_tag: str | None = None


class ModelRegistry:
    def __init__(self, *, settings: Settings, models_cfg: dict[str, Any]):
        self._settings = settings
        self._cfg = models_cfg

    def resolve(self, role: str) -> ResolvedModel:
        roles = self._cfg.get("roles", {})
        if role not in roles:
            raise KeyError(f"Unknown model role: {role}")
        entry = roles[role]
        think_tag = entry.get("think_tag")
        params = dict(entry.get("params", {}) or {})
        # Inject think_tag into params so it flows through provider.chat()
        # to _do_chat(), which pops it before sending to the LLM API.
        if think_tag:
            params["think_tag"] = think_tag
        return ResolvedModel(
            role=role,
            provider_name=entry["provider"],
            model_name=entry["model_name"],
            params=params,
            think_tag=think_tag,
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
