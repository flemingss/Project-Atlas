from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.api_admin import make_admin_router
from atlas.config_manager import ConfigManager
from atlas.db import make_engine, make_sessionmaker
from atlas.db_init import ensure_schema


def _write_minimal_yaml_config(root_dir: Path) -> None:
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "pipeline.yaml").write_text(
        "version: 1\nthresholds: { judge_cutoff_refine: 5 }\nlimits: { chunk_max_chars: 1000 }\n",
        encoding="utf-8",
    )
    (root_dir / "config" / "models.yaml").write_text(
        "version: 1\nproviders: { lmstudio: { type: openai_compat } }\nroles: { embed_model: { provider: lmstudio, model_name: text-embedding, params: {} } }\n",
        encoding="utf-8",
    )


def _make_test_app(*, tmp_root: Path) -> FastAPI:
    _write_minimal_yaml_config(tmp_root)
    config_manager = ConfigManager(root_dir=tmp_root)

    db_path = tmp_root / "test.sqlite"
    engine = make_engine(f"sqlite+pysqlite:///{db_path}")
    ensure_schema(engine)
    session_factory = make_sessionmaker(engine)

    app = FastAPI()
    app.include_router(make_admin_router(config_manager=config_manager, session_factory=session_factory))
    return app


def test_db_reset_requires_strict_admin_token(tmp_path: Path, monkeypatch) -> None:
    # Configure a token in env (overrides any .env file).
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.setenv("ATLAS_ADMIN_TOKEN", "test-token")

    app = _make_test_app(tmp_root=tmp_path)
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
