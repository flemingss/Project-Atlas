"""Tests for the require_admin_token dependency (normal variant, not _strict).

The conftest blanks the token so admin endpoints are always open by default.
These tests explicitly set/override the token via monkeypatch to verify enforcement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_test_app


def test_admin_endpoint_open_when_no_token_configured(tmp_path: Path, monkeypatch: Any) -> None:
    """With ATLAS_ADMIN_TOKEN="" in dev mode, GET /admin/config/effective returns 200."""
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "")

    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    res = client.get("/admin/config/effective")
    assert res.status_code == 200


def test_admin_endpoint_requires_token_when_configured(tmp_path: Path, monkeypatch: Any) -> None:
    """With ATLAS_ADMIN_TOKEN="secret", GET /admin/config/effective without header returns 403."""
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "secret")

    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    res = client.get("/admin/config/effective")
    assert res.status_code == 403


def test_admin_endpoint_accepts_correct_token(tmp_path: Path, monkeypatch: Any) -> None:
    """With ATLAS_ADMIN_TOKEN="secret", correct header returns 200."""
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "secret")

    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    res = client.get("/admin/config/effective", headers={"X-Atlas-Admin-Token": "secret"})
    assert res.status_code == 200


def test_admin_endpoint_rejects_wrong_token(tmp_path: Path, monkeypatch: Any) -> None:
    """With ATLAS_ADMIN_TOKEN="secret", wrong header returns 403."""
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "secret")

    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    res = client.get("/admin/config/effective", headers={"X-Atlas-Admin-Token": "wrong"})
    assert res.status_code == 403
