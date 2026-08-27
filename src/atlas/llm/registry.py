from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from atlas.llm.deterministic import DeterministicProvider
from atlas.llm.openai_compat import OpenAICompatibleProvider
from atlas.llm.provider import ILlmProvider
from atlas.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedModel:
    role: str
    provider_name: str
    model_name: str
    params: dict[str, Any]
    think_tag: str | None = None
    max_output_tokens: int | None = None


class ModelRegistry:
    def __init__(self, *, settings: Settings, models_cfg: dict[str, Any]):
        self._settings = settings
        self._cfg = models_cfg
        # Providers are stateless HTTP clients, but building one re-reads env
        # and re-parses config. Several roles share a provider, and the registry
        # is reconstructed per request in the API layer, so cache within an
        # instance to keep resolution cheap.
        self._provider_cache: dict[str, ILlmProvider] = {}

    # ------------------------------------------------------------------
    # Env resolution
    # ------------------------------------------------------------------

    def _env(self, name: str) -> str:
        """Read a config-named env var from the process or from Settings.

        Both matter. Under docker compose the value arrives in the real
        environment; running Atlas directly it is only in .env, which pydantic
        loads onto Settings without exporting to os.environ. Checking one and
        not the other produces a provider that works in exactly one of the two
        supported ways of running this project.
        """
        val = os.environ.get(name)
        if val:
            return val
        # Settings fields are the lowercased env name (pydantic-settings default).
        return str(getattr(self._settings, name.lower(), "") or "")

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------

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

        # Declared ceiling on what this model can emit in one response. Not sent
        # to the API — it is the budget the orchestrator needs to decide whether
        # a document can be refined whole or must be split. See tokens.fits_in_context.
        raw_cap = entry.get("max_output_tokens")
        max_output_tokens = int(raw_cap) if raw_cap else None

        return ResolvedModel(
            role=role,
            provider_name=entry["provider"],
            model_name=entry["model_name"],
            params=params,
            think_tag=think_tag,
            max_output_tokens=max_output_tokens,
        )

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------

    def provider_for(self, provider_name: str) -> ILlmProvider:
        cached = self._provider_cache.get(provider_name)
        if cached is not None:
            return cached
        provider = self._build_provider(provider_name)
        self._provider_cache[provider_name] = provider
        return provider

    def _build_provider(self, provider_name: str) -> ILlmProvider:
        providers = self._cfg.get("providers", {})
        if provider_name not in providers:
            raise KeyError(f"Unknown provider: {provider_name}")
        p = providers[provider_name]
        ptype = p.get("type")

        if ptype == "openai_compat":
            return self._build_openai_compat(provider_name, p)

        if ptype == "deterministic":
            return DeterministicProvider()

        if ptype in ("openai", "anthropic"):
            raise NotImplementedError(
                f"Provider type '{ptype}' is not implemented. OpenAI-compatible "
                "endpoints (including OpenAI's own API and any gateway in front "
                "of Anthropic) are served by type 'openai_compat' with an explicit "
                "base_url and api_key_env — prefer that."
            )

        raise ValueError(f"Unsupported provider type: {ptype}")

    def _build_openai_compat(self, name: str, p: dict[str, Any]) -> ILlmProvider:
        # Base URL precedence: env var named by config > literal in config >
        # the legacy global setting. The legacy fallback keeps single-endpoint
        # setups working; without per-provider URLs there is no way to run a
        # cloud gateway and a local embedding sidecar at the same time.
        base_url = ""
        if p.get("base_url_env"):
            base_url = self._env(str(p["base_url_env"]))
        if not base_url and p.get("base_url"):
            base_url = str(p["base_url"])
        if not base_url:
            base_url = self._settings.atlas_openai_base_url

        api_key = ""
        api_key_env = p.get("api_key_env")
        if api_key_env:
            api_key = self._env(str(api_key_env))
            if not api_key:
                raise RuntimeError(
                    f"Provider '{name}' requires an API key from ${api_key_env}, "
                    "but it is unset in both the environment and .env. Set it, or "
                    "switch profiles (ATLAS_LLM_PROFILE) to one that does not use "
                    f"provider '{name}'."
                )

        extra_body: dict[str, Any] = dict(p.get("extra_body", {}) or {})

        # Zero-data-retention enforcement, asserted per request rather than
        # relying on a gateway account setting staying configured. Belt and
        # braces: the account policy already binds, so this cannot narrow
        # routing further — but it survives someone relaxing that policy later,
        # and it puts the guarantee in version control where it is reviewable.
        enforce_zdr = bool(p.get("enforce_zdr", False))
        if enforce_zdr:
            provider_block = dict(extra_body.get("provider", {}) or {})
            provider_block["zdr"] = True
            extra_body["provider"] = provider_block

        timeouts = p.get("timeouts", {}) or {}

        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key or None,
            extra_headers=dict(p.get("headers", {}) or {}) or None,
            extra_body=extra_body or None,
            connect_timeout_s=float(timeouts.get("connect_s", 10.0)),
            read_timeout_s=float(timeouts.get("read_s", 120.0)),
            write_timeout_s=float(timeouts.get("write_s", 60.0)),
            zdr_enforced=enforce_zdr,
        )
