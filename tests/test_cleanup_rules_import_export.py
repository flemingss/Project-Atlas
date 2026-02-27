"""Tests for cleanup rules import/export endpoints.

Covers:
- ``GET /admin/cleanup-rules/export`` — download active rules as YAML
- ``POST /admin/cleanup-rules/import`` — import rules with replace and merge modes
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from tests.helpers import make_test_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_RULE_A = {
    "name": "rule-alpha",
    "match": {"corpus_id": "corp-a"},
    "steps": [{"kind": "strip_lines_matching", "pattern": "^Page \\d+$"}],
}

SAMPLE_RULE_B = {
    "name": "rule-beta",
    "match": {"corpus_id": "corp-b"},
    "steps": [{"kind": "fix_bullets"}],
}

SAMPLE_RULE_C = {
    "name": "rule-gamma",
    "match": {"corpus_id": "corp-c"},
    "steps": [{"kind": "normalize_headings"}],
}


async def _seed_rules(client: AsyncClient, rules: list[dict]) -> None:
    """Push rules into the effective config via the apply endpoint."""
    for rule in rules:
        resp = await client.post(
            "/admin/cleanup-rules/apply",
            json={"rule_yaml": yaml.dump(rule)},
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Export with no rules returns an empty YAML list."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/admin/cleanup-rules/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-yaml")
    assert "cleanup_rules.yaml" in resp.headers.get("content-disposition", "")
    data = yaml.safe_load(resp.content)
    assert data == [] or data is None  # empty list or null for no rules


@pytest.mark.asyncio
async def test_export_with_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Export returns currently active rules as YAML."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await _seed_rules(c, [SAMPLE_RULE_A, SAMPLE_RULE_B])
        resp = await c.get("/admin/cleanup-rules/export")
    assert resp.status_code == 200
    data = yaml.safe_load(resp.content)
    assert isinstance(data, list)
    assert len(data) == 2
    names = {r["name"] for r in data}
    assert names == {"rule-alpha", "rule-beta"}


# ---------------------------------------------------------------------------
# Import — replace mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace mode overwrites all existing rules."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Seed one rule
        await _seed_rules(c, [SAMPLE_RULE_A])

        # Import with replace — entirely new list
        resp = await c.post(
            "/admin/cleanup-rules/import",
            json={
                "rules_yaml": yaml.dump([SAMPLE_RULE_B, SAMPLE_RULE_C]),
                "mode": "replace",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["mode"] == "replace"
    assert body["rules_count"] == 2
    assert set(body["imported"]) == {"rule-beta", "rule-gamma"}


@pytest.mark.asyncio
async def test_import_replace_empty_clears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace with an empty list clears all rules."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await _seed_rules(c, [SAMPLE_RULE_A])
        resp = await c.post(
            "/admin/cleanup-rules/import",
            json={"rules_yaml": "[]", "mode": "replace"},
        )
    assert resp.status_code == 200
    assert resp.json()["rules_count"] == 0


# ---------------------------------------------------------------------------
# Import — merge mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_merge_adds_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Merge mode adds new rules without removing existing ones."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await _seed_rules(c, [SAMPLE_RULE_A])
        resp = await c.post(
            "/admin/cleanup-rules/import",
            json={
                "rules_yaml": yaml.dump([SAMPLE_RULE_B]),
                "mode": "merge",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "merge"
    assert body["rules_count"] == 2  # A + B


@pytest.mark.asyncio
async def test_import_merge_updates_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Merge mode replaces a rule with the same name."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await _seed_rules(c, [SAMPLE_RULE_A])
        # Import a rule with the same name but different steps
        updated_a = {
            **SAMPLE_RULE_A,
            "steps": [{"kind": "fix_bullets"}],
        }
        resp = await c.post(
            "/admin/cleanup-rules/import",
            json={
                "rules_yaml": yaml.dump([updated_a]),
                "mode": "merge",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rules_count"] == 1  # replaced, not duplicated


# ---------------------------------------------------------------------------
# Import — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_invalid_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Import rejects malformed YAML."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/admin/cleanup-rules/import",
            json={"rules_yaml": "{{bad yaml:"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_import_invalid_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Import rejects rules that fail schema validation."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        bad_rule = [{"name": "bad", "steps": [{"kind": "nonexistent_step"}]}]
        resp = await c.post(
            "/admin/cleanup-rules/import",
            json={"rules_yaml": yaml.dump(bad_rule)},
        )
    assert resp.status_code == 422
    assert "validation_errors" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_import_bad_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Import rejects an invalid mode parameter."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/admin/cleanup-rules/import",
            json={
                "rules_yaml": yaml.dump([SAMPLE_RULE_A]),
                "mode": "invalid",
            },
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Round-trip: export → import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roundtrip_export_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rules exported as YAML can be imported back identically."""
    app, _ = make_test_app(tmp_path, monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await _seed_rules(c, [SAMPLE_RULE_A, SAMPLE_RULE_B])

        # Export
        exp_resp = await c.get("/admin/cleanup-rules/export")
        assert exp_resp.status_code == 200
        exported_yaml = exp_resp.content.decode("utf-8")

        # Clear rules
        await c.post(
            "/admin/cleanup-rules/import",
            json={"rules_yaml": "[]", "mode": "replace"},
        )

        # Re-import
        imp_resp = await c.post(
            "/admin/cleanup-rules/import",
            json={"rules_yaml": exported_yaml, "mode": "replace"},
        )
        assert imp_resp.status_code == 200
        assert imp_resp.json()["rules_count"] == 2

        # Verify via export
        exp2_resp = await c.get("/admin/cleanup-rules/export")
        reimported = yaml.safe_load(exp2_resp.content)
        original = yaml.safe_load(exported_yaml)
        assert reimported == original
