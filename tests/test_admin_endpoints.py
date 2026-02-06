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


def test_effective_config_uses_yaml_when_no_db_active(tmp_path: Path) -> None:
    app = _make_test_app(tmp_root=tmp_path)
    client = TestClient(app)

    res = client.get("/admin/config/effective")
    assert res.status_code == 200
    data = res.json()
    assert data["source"]["db"] is None
    assert "pipeline" in data
    assert "models" in data


def test_config_versions_create_and_activate_updates_effective_config(tmp_path: Path) -> None:
    app = _make_test_app(tmp_root=tmp_path)
    client = TestClient(app)

    # Create a config version that changes the embed model name.
    create = client.post(
        "/admin/config-versions",
        json={
            "name": "test-embed-model",
            "base": "yaml",
            "activate": True,
            "patch": {"models": {"roles": {"embed_model": {"model_name": "embed-v2"}}}},
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["is_active"] is True
    assert isinstance(created["id"], int)

    effective = client.get("/admin/config/effective").json()
    assert effective["source"]["db"]["active_id"] == created["id"]
    assert effective["models"]["roles"]["embed_model"]["model_name"] == "embed-v2"
