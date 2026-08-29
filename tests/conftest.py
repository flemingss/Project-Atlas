from __future__ import annotations

import pytest

import atlas.diagnostics as _diagnostics_module
from atlas.ingest.model_manager import ModelManager


@pytest.fixture(autouse=True)
def _atlas_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from any developer .env file.

    The repo's Settings loads values from .env by default. Locally, developers may have
    ATLAS_ADMIN_TOKEN set in .env, which would cause all /admin tests to require a header.
    In unit tests we default to an open dev admin surface unless a test explicitly sets a token.
    """

    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "")


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    """Reset the two module-level singletons between tests.

    DiagnosticsManager holds an in-memory event list; ModelManager caches HF models.
    Neither resets itself between tests, so accumulated state can leak across the
    suite. Kill both deterministically instead of letting order decide.
    """
    ModelManager.reset_instance()
    _diagnostics_module._global_diagnostics = None
