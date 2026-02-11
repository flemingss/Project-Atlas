from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _atlas_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from any developer .env file.

    The repo's Settings loads values from .env by default. Locally, developers may have
    ATLAS_ADMIN_TOKEN set in .env, which would cause all /admin tests to require a header.
    In unit tests we default to an open dev admin surface unless a test explicitly sets a token.
    """

    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "")
