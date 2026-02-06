from __future__ import annotations

import pytest

from atlas.llm.openai_compat import OpenAICompatibleProvider
from atlas.llm.registry import ModelRegistry
from atlas.settings import Settings


def test_model_registry_resolve_and_provider_for_openai_compat() -> None:
    settings = Settings()
    models_cfg = {
        "providers": {"lmstudio": {"type": "openai_compat"}},
        "roles": {"embed_model": {"provider": "lmstudio", "model_name": "embed", "params": {}}},
    }
    reg = ModelRegistry(settings=settings, models_cfg=models_cfg)

    resolved = reg.resolve("embed_model")
    assert resolved.provider_name == "lmstudio"
    assert resolved.model_name == "embed"

    provider = reg.provider_for(resolved.provider_name)
    assert isinstance(provider, OpenAICompatibleProvider)


def test_model_registry_unknown_role_raises() -> None:
    reg = ModelRegistry(settings=Settings(), models_cfg={"providers": {}, "roles": {}})
    with pytest.raises(KeyError):
        reg.resolve("missing")
