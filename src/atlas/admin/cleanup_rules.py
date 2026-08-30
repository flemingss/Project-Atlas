"""Cleanup-feedback & cleanup-rule management routes."""

from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from atlas.config_manager import ConfigManager
from atlas.config_versions import (
    ConfigVersionCreateRequest,
    create_config_version,
    get_active_config_version,
)
from atlas.feedback_ledger import (
    FeedbackCreateRequest,
    FeedbackResponse,
    create_feedback,
    delete_feedback,
    feedback_category_counts,
    get_feedback,
    list_feedback,
    to_feedback_response,
)
from atlas.rule_suggester import suggest_cleanup_rule
from atlas.settings import Settings

# ── Request models ───────────────────────────────────────────────────

class RuleSuggestionRequest(BaseModel):
    markdown_sample: str = ""
    issues: str = ""
    context: dict[str, str] = {}


class ApplyCleanupRuleRequest(BaseModel):
    """Push a cleanup rule into the active DB config version."""
    rule_yaml: str
    name: str = ""
    notes: str = ""


class ImportCleanupRulesRequest(BaseModel):
    """Import cleanup rules from a YAML string."""
    rules_yaml: str
    mode: str = "replace"
    name: str = ""
    notes: str = ""


class CleanupDryRunRequest(BaseModel):
    """Test cleanup rules against a markdown sample without ingesting."""
    markdown_sample: str
    tenant_id: str = "local"
    project_id: str = "default"
    corpus_id: str = "default"
    mime_type: str = "application/pdf"
    filename: str = ""
    rule_yaml: str = ""
    """Candidate rule YAML to preview; if set, it is used instead of the active rules."""


# ── Route registration ───────────────────────────────────────────────

def register_cleanup_routes(
    r: APIRouter,
    *,
    session_factory: sessionmaker[Session],
    config_manager: ConfigManager,
    settings: Settings,
) -> None:
    """Register cleanup-feedback and cleanup-rule endpoints on *r*."""

    # ------------------------------------------------------------------
    # Cleanup feedback endpoints (Phase 7B)
    # ------------------------------------------------------------------

    @r.post("/cleanup-feedback", response_model=FeedbackResponse, status_code=201)
    def feedback_create(req: FeedbackCreateRequest) -> FeedbackResponse:
        with session_factory() as session:
            row = create_feedback(session, req=req)
        return to_feedback_response(row)

    @r.get("/cleanup-feedback", response_model=list[FeedbackResponse])
    def feedback_list(
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
        doc_id: str | None = Query(default=None),
        category: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[FeedbackResponse]:
        with session_factory() as session:
            rows = list_feedback(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=corpus_id,
                doc_id=doc_id,
                category=category,
                limit=int(limit),
            )
        return [to_feedback_response(r) for r in rows]

    @r.get("/cleanup-feedback/categories")
    def feedback_categories(
        tenant_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        corpus_id: str | None = Query(default=None),
    ) -> dict[str, int]:
        with session_factory() as session:
            return feedback_category_counts(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                corpus_id=corpus_id,
            )

    @r.get("/cleanup-feedback/{feedback_id}", response_model=FeedbackResponse)
    def feedback_get(feedback_id: int) -> FeedbackResponse:
        with session_factory() as session:
            row = get_feedback(session, feedback_id=feedback_id)
        if row is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="feedback not found")
        return to_feedback_response(row)

    @r.delete("/cleanup-feedback/{feedback_id}")
    def feedback_delete(feedback_id: int) -> dict[str, bool]:
        with session_factory() as session:
            deleted = delete_feedback(session, feedback_id=feedback_id)
        if not deleted:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="feedback not found")
        return {"deleted": True}

    # ------------------------------------------------------------------
    # Cleanup rule suggestion endpoint (Phase 7D)
    # ------------------------------------------------------------------

    @r.post("/cleanup-rules/suggest")
    async def cleanup_rule_suggest(req: RuleSuggestionRequest) -> dict[str, Any]:
        """Ask the LLM to suggest a cleanup rule for the given markdown sample."""
        from atlas.llm.registry import ModelRegistry

        eff = config_manager.get()
        registry = ModelRegistry(settings=settings, models_cfg=eff.models)
        for role in ("chat_model", "refine_model"):
            try:
                resolved = registry.resolve(role)
                break
            except KeyError:
                continue
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="No chat or refine model configured")
        provider = registry.provider_for(resolved.provider_name)
        result = await suggest_cleanup_rule(
            provider=provider,
            model=resolved.model_name,
            markdown_sample=req.markdown_sample,
            issues=req.issues,
            context=req.context,
            params=resolved.params,
        )
        return result

    @r.post("/cleanup-rules/apply")
    def apply_cleanup_rule(req: ApplyCleanupRuleRequest) -> dict[str, Any]:
        """Validate and apply a cleanup rule by creating a new DB config version."""
        import yaml as _yaml
        from fastapi import HTTPException

        from atlas.startup_validation import validate_cleanup_rules

        try:
            parsed = _yaml.safe_load(req.rule_yaml)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")

        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or not parsed:
            raise HTTPException(status_code=400, detail="Expected a YAML list of rule entries")

        errors = validate_cleanup_rules(parsed)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is not None:
            current_pipeline = active.payload.get("pipeline", {})
        else:
            current_pipeline = yaml_defaults.pipeline

        existing_rules: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])

        new_names = {r["name"] for r in parsed if "name" in r}
        merged_rules = [r for r in existing_rules if r.get("name") not in new_names]
        merged_rules.extend(parsed)

        cv_req = ConfigVersionCreateRequest(
            name=req.name or f"apply-rule-{parsed[0].get('name', 'unknown')}",
            notes=req.notes or f"Applied cleanup rule(s): {', '.join(new_names)}",
            base="current",
            patch={"pipeline": {"cleanup_rules": merged_rules}},
            activate=True,
        )
        row = create_config_version(session_factory(), req=cv_req, yaml_defaults=yaml_defaults)

        return {
            "ok": True,
            "config_version_id": row.id,
            "config_hash": row.config_hash,
            "rules_count": len(merged_rules),
            "applied": [r.get("name") for r in parsed],
        }

    @r.post("/cleanup-rules/dry-run")
    async def cleanup_rules_dry_run(req: CleanupDryRunRequest) -> dict[str, Any]:
        """Test the active cleanup rules against a markdown sample."""
        import yaml as _yaml

        from atlas.pipeline.cleanup import CleanupNode
        from atlas.pipeline.cleanup_rules import DocContext, find_matching_rule, parse_rules

        if req.rule_yaml.strip():
            try:
                candidate = _yaml.safe_load(req.rule_yaml)
            except _yaml.YAMLError as exc:
                return {"matched": False, "errors": [f"invalid rule_yaml: {exc}"]}
            if isinstance(candidate, dict):
                raw_rules = list(candidate.get("cleanup_rules", []) or [])
            elif isinstance(candidate, list):
                raw_rules = list(candidate)
            else:
                raw_rules = []
            if not raw_rules:
                return {"matched": False, "errors": ["no cleanup_rules found in rule_yaml"]}
            source = "provided:rule_yaml"
            pipeline_cfg = {"cleanup_rules": raw_rules}
        else:
            yaml_defaults = config_manager.get()
            with session_factory() as session:
                active = get_active_config_version(session)

            if active is not None:
                pipeline_cfg = active.payload.get("pipeline", {})
                source = f"db:config_version#{active.id}"
            else:
                pipeline_cfg = yaml_defaults.pipeline
                source = "yaml-defaults"

            raw_rules = list(pipeline_cfg.get("cleanup_rules", []) or [])
        parsed_rules = parse_rules(raw_rules)

        doc_ctx = DocContext(
            tenant_id=req.tenant_id,
            project_id=req.project_id,
            corpus_id=req.corpus_id,
            mime_type=req.mime_type,
            filename=req.filename,
        )

        matched = find_matching_rule(parsed_rules, doc_ctx)

        node = CleanupNode()
        result = await node.clean(
            markdown=req.markdown_sample,
            doc_context={
                "tenant_id": req.tenant_id,
                "project_id": req.project_id,
                "corpus_id": req.corpus_id,
                "mime_type": req.mime_type,
                "filename": req.filename,
            },
            config=pipeline_cfg,
        )

        return {
            "config_source": source,
            "rules_available": len(parsed_rules),
            "rules_names": [r.name for r in parsed_rules],
            "doc_context": {
                "tenant_id": req.tenant_id,
                "project_id": req.project_id,
                "corpus_id": req.corpus_id,
                "mime_type": req.mime_type,
                "filename": req.filename,
            },
            "matched_rule": matched.name if matched else None,
            "matched_rule_steps": len(matched.steps) if matched else 0,
            "rules_applied": result.rules_applied,
            "rule_tags": result.rule_tags,
            "fix_counts": result.fix_counts,
            "input_length": len(req.markdown_sample),
            "output_length": len(result.cleaned_markdown),
            "changed": req.markdown_sample != result.cleaned_markdown,
            "cleaned_markdown": result.cleaned_markdown,
        }

    @r.get("/cleanup-rules/export")
    def export_cleanup_rules() -> StreamingResponse:
        """Export the active cleanup rules as a downloadable YAML file."""
        import yaml as _yaml

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is not None:
            current_pipeline = active.payload.get("pipeline", {})
        else:
            current_pipeline = yaml_defaults.pipeline

        rules: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])
        yaml_str = _yaml.dump(rules, default_flow_style=False, sort_keys=False, allow_unicode=True)
        buf = io.BytesIO(yaml_str.encode("utf-8"))
        return StreamingResponse(
            buf,
            media_type="application/x-yaml",
            headers={"Content-Disposition": "attachment; filename=cleanup_rules.yaml"},
        )

    @r.post("/cleanup-rules/import")
    def import_cleanup_rules(req: ImportCleanupRulesRequest) -> dict[str, Any]:
        """Import cleanup rules from YAML."""
        import yaml as _yaml
        from fastapi import HTTPException

        from atlas.startup_validation import validate_cleanup_rules

        if req.mode not in ("replace", "merge"):
            raise HTTPException(status_code=400, detail="mode must be 'replace' or 'merge'")

        try:
            parsed = _yaml.safe_load(req.rules_yaml)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")

        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise HTTPException(status_code=400, detail="Expected a YAML list of rule entries")

        errors = validate_cleanup_rules(parsed)
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if req.mode == "merge":
            if active is not None:
                current_pipeline = active.payload.get("pipeline", {})
            else:
                current_pipeline = yaml_defaults.pipeline
            existing: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])
            new_names = {r["name"] for r in parsed if "name" in r}
            merged = [r for r in existing if r.get("name") not in new_names]
            merged.extend(parsed)
            final_rules = merged
        else:
            final_rules = parsed

        imported_names = [r.get("name", "unnamed") for r in parsed]

        cv_req = ConfigVersionCreateRequest(
            name=req.name or f"import-rules-{req.mode}",
            notes=req.notes or f"Imported {len(parsed)} rule(s) ({req.mode}): {', '.join(imported_names)}",
            base="current",
            patch={"pipeline": {"cleanup_rules": final_rules}},
            activate=True,
        )
        row = create_config_version(session_factory(), req=cv_req, yaml_defaults=yaml_defaults)

        return {
            "ok": True,
            "mode": req.mode,
            "config_version_id": row.id,
            "config_hash": row.config_hash,
            "rules_count": len(final_rules),
            "imported": imported_names,
        }

    @r.delete("/cleanup-rules/{rule_name}")
    def remove_cleanup_rule(rule_name: str) -> dict[str, Any]:
        """Remove a cleanup rule by name."""
        from fastapi import HTTPException

        yaml_defaults = config_manager.get()
        with session_factory() as session:
            active = get_active_config_version(session)

        if active is not None:
            current_pipeline = active.payload.get("pipeline", {})
        else:
            current_pipeline = yaml_defaults.pipeline

        existing_rules: list[dict[str, Any]] = list(current_pipeline.get("cleanup_rules", []) or [])
        filtered = [r for r in existing_rules if r.get("name") != rule_name]

        if len(filtered) == len(existing_rules):
            raise HTTPException(status_code=404, detail=f"No rule named '{rule_name}' found")

        cv_req = ConfigVersionCreateRequest(
            name=f"remove-rule-{rule_name}",
            notes=f"Removed cleanup rule: {rule_name}",
            base="current",
            patch={"pipeline": {"cleanup_rules": filtered}},
            activate=True,
        )
        row = create_config_version(session_factory(), req=cv_req, yaml_defaults=yaml_defaults)

        return {
            "ok": True,
            "config_version_id": row.id,
            "config_hash": row.config_hash,
            "rules_count": len(filtered),
            "removed": rule_name,
        }
