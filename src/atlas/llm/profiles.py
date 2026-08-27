"""Profile resolution for models/pipeline config.

A *profile* is a named patch applied over the YAML defaults. It exists because
local and hosted inference are not interchangeable at the same settings: a
model served from LM Studio on one GPU wants a small context budget, one heavy
task at a time, and fail-fast timeouts, while a rate-limited cloud gateway
wants a large context budget, real concurrency, and long read timeouts. Flipping
only the model ids between those two worlds buys the cost of a migration
without the benefit — the pipeline would keep sectioning documents and
serialising work as if it were still talking to a single local GPU.

So a profile patches *both* configs. Model ids and their ceilings live in the
models patch; context budget, concurrency, and retry posture live in the
pipeline patch. One switch moves the whole posture together.

Deliberately excluded: the embedding role. Embeddings are infrastructure, not a
profile choice. A vector search only returns meaningful results when the query
and the documents were embedded by the same model, so silently swapping the
embedder underneath an existing corpus corrupts retrieval without raising
anything — Qdrant cannot detect it when the dimension happens to match. Keeping
``embed_model`` outside the profile system makes that class of mistake
impossible rather than merely discouraged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from atlas.deep_merge import deep_merge

log = logging.getLogger(__name__)

# Roles a profile is not permitted to touch. See module docstring.
PROFILE_IMMUTABLE_ROLES = frozenset({"embed_model"})


def resolve_profile_name(models_cfg: dict[str, Any], *, override: str | None = None) -> str | None:
    """Pick the active profile: explicit override > env > config default."""
    if override:
        return override
    env = os.environ.get("ATLAS_LLM_PROFILE")
    if env:
        return env
    active = models_cfg.get("active_profile")
    return str(active) if active else None


def apply_profile(
    *,
    models: dict[str, Any],
    pipeline: dict[str, Any],
    profile_name: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (models, pipeline) with *profile_name* merged over them.

    Returns the inputs unchanged when no profile is selected, so a config with
    no ``profiles:`` block behaves exactly as it did before profiles existed.
    """
    if not profile_name:
        return models, pipeline

    profiles = models.get("profiles") or {}
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles)) or "<none defined>"
        raise RuntimeError(
            f"Unknown LLM profile '{profile_name}'. Available profiles: {available}. "
            "Set ATLAS_LLM_PROFILE to one of these, or fix active_profile in models.yaml."
        )

    patch = profiles[profile_name] or {}
    models_patch = dict(patch.get("models", {}) or {})
    pipeline_patch = patch.get("pipeline", {}) or {}

    # Guard the immutable roles rather than trusting every future profile author.
    patched_roles = (models_patch.get("roles") or {})
    illegal = PROFILE_IMMUTABLE_ROLES & set(patched_roles)
    if illegal:
        raise RuntimeError(
            f"Profile '{profile_name}' tries to override {sorted(illegal)}, which is "
            "not profile-switchable. Embeddings must stay pinned across profiles: "
            "changing the embedder invalidates every vector already in Qdrant, and "
            "when the dimension happens to match it corrupts search silently rather "
            "than failing. Change it deliberately in the base roles block instead."
        )

    merged_models = deep_merge(models, models_patch)
    merged_pipeline = deep_merge(pipeline, pipeline_patch)

    # The profiles block itself is not part of the effective config.
    merged_models.pop("profiles", None)
    merged_models["active_profile"] = profile_name

    log.info(
        "LLM profile '%s' applied (roles patched: %s)",
        profile_name,
        ", ".join(sorted(patched_roles)) or "none",
    )
    return merged_models, merged_pipeline
