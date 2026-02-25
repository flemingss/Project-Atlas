from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import make_test_app


def test_db_reset_requires_strict_admin_token(tmp_path: Path, monkeypatch: Any) -> None:
    # Configure a token in env (overrides any .env file).
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "test-token")

    app, _ = make_test_app(tmp_path, monkeypatch, include_rag=False)
    client = TestClient(app)

    # Missing header -> forbidden
    res = client.post("/admin/db/reset", json={"confirm": "RESET", "postgres": True, "qdrant": False})
    assert res.status_code == 403

    # Wrong header -> forbidden
    res = client.post(
        "/admin/db/reset",
        headers={"X-Atlas-Admin-Token": "wrong"},
        json={"confirm": "RESET", "postgres": True, "qdrant": False},
    )
    assert res.status_code == 403

    # Correct header but missing confirm -> bad request
    res = client.post(
        "/admin/db/reset",
        headers={"X-Atlas-Admin-Token": "test-token"},
        json={"confirm": "nope", "postgres": True, "qdrant": False},
    )
    assert res.status_code == 400

    # Correct token + confirm should succeed. Keep qdrant disabled in unit tests.
    res = client.post(
        "/admin/db/reset",
        headers={"X-Atlas-Admin-Token": "test-token"},
        json={"confirm": "RESET", "postgres": True, "qdrant": False, "artifacts": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["postgres"]["ok"] is True
    assert data["qdrant"]["ok"] is True
    assert data["qdrant"].get("skipped") is True
