"""Tests for LLM profile switching and per-provider configuration.

These cover the invariants that make the two-profile setup safe to flip:
embeddings cannot be swapped by a profile, credentials fail loudly at
construction, ZDR is asserted on the wire, and the refine fit check honours a
model's response ceiling rather than only its context window.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config_manager import ConfigManager
from atlas.llm.profiles import apply_profile, resolve_profile_name
from atlas.llm.registry import ModelRegistry
from atlas.pipeline.tokens import estimate_tokens, fits_in_context
from atlas.settings import Settings

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def _effective(profile: str):
    return ConfigManager(config_dir=CONFIG_DIR, profile=profile).load_yaml_defaults()


# ---------------------------------------------------------------------------
# Profile merge
# ---------------------------------------------------------------------------


def test_profiles_switch_generation_roles() -> None:
    local = _effective("local").models["roles"]
    api = _effective("api").models["roles"]

    assert local["judge_model"]["provider"] == "lmstudio"
    assert api["judge_model"]["provider"] == "openrouter"
    assert local["refine_model"]["model_name"] != api["refine_model"]["model_name"]


def test_profile_switches_pipeline_tuning_too() -> None:
    """A profile must move the tuning with the models, not just the ids.

    Flipping to hosted models while keeping a 16k context budget would keep
    sectioning documents that now fit whole — paying for the migration without
    getting the benefit.
    """
    local = _effective("local").pipeline["limits"]
    api = _effective("api").pipeline["limits"]

    assert api["max_context_tokens"] > local["max_context_tokens"]
    assert api["refine_max_section_tokens"] > local["refine_max_section_tokens"]
    assert api["judge_max_context_tokens"] > local["judge_max_context_tokens"]


def test_embed_model_is_identical_across_profiles() -> None:
    """The whole point of pinning embeddings outside the profile system."""
    local = _effective("local").models["roles"]["embed_model"]
    api = _effective("api").models["roles"]["embed_model"]
    assert local == api
    assert local["provider"] == "embeddings"


def test_profile_may_not_override_embed_model() -> None:
    """Guard the invariant even if a future profile author tries."""
    models = {
        "roles": {"embed_model": {"provider": "embeddings", "model_name": "m"}},
        "profiles": {"sneaky": {"models": {"roles": {"embed_model": {"provider": "openrouter"}}}}},
    }
    with pytest.raises(RuntimeError, match="not profile-switchable"):
        apply_profile(models=models, pipeline={}, profile_name="sneaky")


def test_unknown_profile_names_the_available_ones() -> None:
    models = {"roles": {}, "profiles": {"local": {}, "api": {}}}
    with pytest.raises(RuntimeError, match="api, local"):
        apply_profile(models=models, pipeline={}, profile_name="nope")


def test_no_profile_is_a_passthrough() -> None:
    """Config with no profile selected must behave as it did before profiles."""
    models = {"roles": {"a": 1}}
    pipeline = {"limits": {"x": 2}}
    m, p = apply_profile(models=models, pipeline=pipeline, profile_name=None)
    assert m is models and p is pipeline


def test_env_overrides_config_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_LLM_PROFILE", "local")
    assert resolve_profile_name({"active_profile": "api"}) == "local"
    # An explicit argument still wins over the environment.
    assert resolve_profile_name({"active_profile": "api"}, override="api") == "api"


def test_profiles_block_is_stripped_from_effective_config() -> None:
    eff = _effective("api")
    assert "profiles" not in eff.models
    assert eff.models["active_profile"] == "api"


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


def test_missing_api_key_fails_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail at boot naming the variable, not mid-ingest with a 401."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = Settings(openrouter_api_key="")
    registry = ModelRegistry(settings=settings, models_cfg=_effective("api").models)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        registry.provider_for("openrouter")


def test_zdr_is_asserted_in_every_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    registry = ModelRegistry(settings=Settings(), models_cfg=_effective("api").models)
    provider = registry.provider_for("openrouter")
    assert provider._extra_body["provider"]["zdr"] is True
    assert provider._headers["Authorization"] == "Bearer sk-or-test"


def test_providers_get_independent_base_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason the old single-base_url registry could not do this at all."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    registry = ModelRegistry(settings=Settings(), models_cfg=_effective("api").models)
    assert "openrouter.ai" in registry.provider_for("openrouter")._v1
    assert "openrouter.ai" not in registry.provider_for("embeddings")._v1


def test_sidecar_needs_no_credentials() -> None:
    registry = ModelRegistry(settings=Settings(), models_cfg=_effective("api").models)
    assert registry.provider_for("embeddings")._headers == {}


def test_provider_instances_are_cached() -> None:
    registry = ModelRegistry(settings=Settings(), models_cfg=_effective("api").models)
    assert registry.provider_for("embeddings") is registry.provider_for("embeddings")


def test_role_carries_declared_output_ceiling() -> None:
    registry = ModelRegistry(settings=Settings(), models_cfg=_effective("api").models)
    assert registry.resolve("refine_model").max_output_tokens == 65536


# ---------------------------------------------------------------------------
# Fit check
# ---------------------------------------------------------------------------


def test_output_ceiling_rejects_what_context_alone_would_allow() -> None:
    """The trap this check exists to close.

    A model may advertise a 1M context but cap responses at 48k. Refine emits a
    full rewrite, so a 100k-token document cannot be done in one pass even
    though the prompt fits comfortably.
    """
    doc = "x" * 400_000            # ~100k tokens
    assert fits_in_context(doc, 1_048_576)          # context alone: fits
    assert not fits_in_context(doc, 1_048_576, max_output_tokens=48_000)


def test_output_ceiling_allows_documents_within_it() -> None:
    doc = "x" * 40_000             # ~10k tokens
    assert fits_in_context(doc, 1_048_576, max_output_tokens=48_000)


def test_context_ceiling_still_applies_without_an_output_cap() -> None:
    doc = "x" * 400_000
    assert not fits_in_context(doc, 16_384)


def test_judge_style_check_ignores_output_ratio() -> None:
    """Judge emits a handful of scores, so its budget is prompt-dominated."""
    doc = "x" * 400_000            # ~100k tokens
    assert not fits_in_context(doc, 50_000, output_ratio=0.0)
    assert fits_in_context(doc, 200_000, output_ratio=0.0)
    assert estimate_tokens(doc) > 0
