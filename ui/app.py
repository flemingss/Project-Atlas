from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import traceback
import zipfile
from datetime import datetime, timezone
from typing import Any

import httpx
import streamlit as st
import yaml

from ui import components, theme
from ui.styles import inject_styles


def _ui_upload_timeout_s() -> float:
    raw = (os.environ.get("ATLAS_UI_UPLOAD_TIMEOUT_S") or "").strip()
    if not raw:
        return 600.0
    try:
        val = float(raw)
        return val if val > 0 else 600.0
    except Exception:
        return 600.0


def _base_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def _slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "document"


def _stable_doc_id_from_name(name: str) -> str:
    slug = _slugify(name)
    h = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{h}"


def _admin_headers(token: str) -> dict[str, str]:
    token = (token or "").strip()
    return {"X-Atlas-Admin-Token": token} if token else {}


def _default_admin_token() -> str:
    token = (os.environ.get("ATLAS_ADMIN_TOKEN") or "").strip()
    if token:
        return token
    try:
        secret = st.secrets.get("ATLAS_ADMIN_TOKEN")  # type: ignore[attr-defined]
        if isinstance(secret, str) and secret.strip():
            return secret.strip()
    except Exception:
        pass
    return ""


def _is_json_response(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return "application/json" in ctype

def _render_response(resp: httpx.Response) -> None:
    if _is_json_response(resp):
        try:
            st.json(resp.json())
            return
        except Exception:
            pass
    st.code(resp.text)


def _summarize_ingest(resp_json: dict[str, Any] | None) -> tuple[str, str]:
    if not resp_json:
        return "Ingest completed", ""
    ok = bool(resp_json.get("ok"))
    chunks = int(resp_json.get("chunks_upserted") or 0)
    doc_id = str(resp_json.get("doc_id") or "")
    if not ok:
        return "Ingest failed", "Try again or check the service logs/history."
    if chunks <= 0:
        return "Uploaded - 0 chunks indexed", (
            f"doc_id={doc_id}. This can happen if the document is empty, unsupported, or paused for review."
        )
    return "Uploaded and indexed", f"doc_id={doc_id}. Chunks indexed: {chunks}."


def _request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
) -> tuple[httpx.Response, Any | None]:
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.request(method, url, headers=headers, json=json_body, params=params)
    if _is_json_response(resp):
        try:
            return resp, resp.json()
        except Exception:
            return resp, None
    return resp, None


def _load_group_registry(api: str, admin_headers: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    tenants_resp, tenants_data = _request_json(
        method="GET",
        url=f"{api}/admin/tenants",
        headers=admin_headers,
        timeout_s=30.0,
    )
    projects_resp, projects_data = _request_json(
        method="GET",
        url=f"{api}/admin/projects",
        headers=admin_headers,
        timeout_s=30.0,
    )
    corpora_resp, corpora_data = _request_json(
        method="GET",
        url=f"{api}/admin/corpora",
        headers=admin_headers,
        timeout_s=30.0,
    )
    if tenants_resp.status_code >= 400 or projects_resp.status_code >= 400 or corpora_resp.status_code >= 400:
        return {"tenants": [], "projects": [], "corpora": []}
    return {
        "tenants": list((tenants_data or {}).get("tenants") or []),
        "projects": list((projects_data or {}).get("projects") or []),
        "corpora": list((corpora_data or {}).get("corpora") or []),
    }


def _create_scope_entry(
    *,
    api: str,
    admin_headers: dict[str, str],
    endpoint: str,
    payload: dict[str, Any],
    label: str,
) -> tuple[bool, str]:
    resp, data = _request_json(
        method="POST",
        url=f"{api}{endpoint}",
        headers=admin_headers,
        json_body=payload,
        timeout_s=30.0,
    )
    if resp.status_code >= 400:
        try:
            detail = (data or {}).get("detail")
        except Exception:
            detail = None
        return False, f"{label} create failed ({resp.status_code}): {detail or resp.text}"
    return True, f"{label} created"


def _diag_init() -> None:
    st.session_state.setdefault("diag_events", [])


def _diag_add(event: dict[str, Any]) -> None:
    _diag_init()
    event["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st.session_state["diag_events"].append(event)
    st.session_state["diag_events"] = st.session_state["diag_events"][-theme.MAX_DIAG_EVENTS:]

def _diag_ensure_session_started(api_base: str) -> None:
    _diag_init()
    if st.session_state.get("diag_session_started"):
        return
    st.session_state["diag_session_started"] = True
    _diag_add(
        {
            "type": "ui_loaded",
            "ts": datetime.now(timezone.utc).isoformat(),
            "api_base": api_base,
        }
    )


def _diag_bundle(api_base: str) -> str:
    _diag_init()
    bundle = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "api_base": api_base,
            "event_count": len(st.session_state.get("diag_events", []) or []),
        },
        "events": st.session_state.get("diag_events", []) or [],
    }
    return json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"


def _request_json_diag(
    *,
    label: str,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
) -> tuple[httpx.Response, Any | None]:
    """Fire an HTTP request, log diagnostics, and return (response, json).

    On timeout or connection errors a **synthetic 504** response is returned
    instead of raising — this keeps the UI alive so the user sees a clear
    ``st.error`` rather than the global crash handler.
    """
    start = time.perf_counter()
    try:
        resp, data = _request_json(
            method=method,
            url=url,
            headers=headers,
            json_body=json_body,
            params=params,
            timeout_s=timeout_s,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _diag_add(
            {
                "type": "http",
                "label": label,
                "method": method,
                "url": url,
                "status": int(resp.status_code),
                "elapsed_ms": elapsed_ms,
            }
        )
        return resp, data
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        # Graceful degradation: return a synthetic 504 so callers can show
        # ``st.error`` instead of crashing the entire Streamlit session.
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _diag_add(
            {
                "type": "timeout",
                "label": label,
                "method": method,
                "url": url,
                "elapsed_ms": elapsed_ms,
                "error": repr(e),
            }
        )
        synth = httpx.Response(
            status_code=504,
            request=httpx.Request(method, url),
            content=f"Request timed out after {timeout_s:.0f}s — the backend may still be processing. "
            f"If using a local LLM, allow more time for inference to complete. ({e})".encode(),
        )
        return synth, None
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _diag_add(
            {
                "type": "exception",
                "label": label,
                "method": method,
                "url": url,
                "elapsed_ms": elapsed_ms,
                "error": repr(e),
            }
        )
        raise


def main() -> None:
    st.set_page_config(page_title="Atlas Operator Console", layout="wide")
    inject_styles()

    components.page_header("Project Atlas", subtitle="Knowledge base management console")

    with st.sidebar:
        # -- Connection (hidden in expander after first setup) ----------------
        st.session_state.setdefault("admin_token", _default_admin_token())
        with st.expander("Connection", expanded=not bool(st.session_state.get("health_status"))):
            api_url = st.text_input("Atlas URL", value=os.environ.get("ATLAS_API_URL", "http://127.0.0.1:18080"))
            admin_token = st.text_input(
                "Token",
                type="password",
                key="admin_token",
                help="Required for admin features.",
            )

        api = _base_url(api_url)
        admin_headers = _admin_headers(admin_token)

        # -- Workspace / Collection (always visible) --------------------------
        st.markdown('<div class="atlas-sidebar-label">Workspace</div>', unsafe_allow_html=True)
        default_tenant = os.environ.get("ATLAS_DEFAULT_TENANT_ID", "local")
        default_project = os.environ.get("ATLAS_DEFAULT_PROJECT_ID", "default")
        default_corpus = os.environ.get("ATLAS_DEFAULT_CORPUS_ID", "default")

        if admin_headers:
            if "group_registry" not in st.session_state:
                st.session_state["group_registry"] = _load_group_registry(api, admin_headers)
            reg = st.session_state.get("group_registry", {"tenants": [], "projects": [], "corpora": []})

            # -- Workspace selector --
            tenant_options = [str(t.get("tenant_id") or "") for t in reg.get("tenants", []) if str(t.get("tenant_id") or "").strip()]
            if default_tenant not in tenant_options:
                tenant_options = [default_tenant, *tenant_options] if default_tenant else tenant_options
            if not tenant_options:
                tenant_options = [default_tenant]
            sel_tenant = st.selectbox(theme.LABEL_WORKSPACE, options=tenant_options, index=0, label_visibility="collapsed")

            # -- Project selector (filtered by workspace) --
            project_options = [
                str(p.get("project_id") or "")
                for p in reg.get("projects", [])
                if str(p.get("tenant_id") or "") == sel_tenant
                and str(p.get("project_id") or "").strip()
            ]
            if default_project not in project_options:
                project_options = [default_project, *project_options] if default_project else project_options
            if not project_options:
                project_options = [default_project]
            sel_project = st.selectbox(theme.LABEL_PROJECT, options=project_options, index=0)

            # -- Collection selector (filtered by workspace + project) --
            corpus_options = [
                str(c.get("corpus_id") or "")
                for c in reg.get("corpora", [])
                if str(c.get("tenant_id") or "") == sel_tenant
                and str(c.get("project_id") or "") == sel_project
                and str(c.get("corpus_id") or "").strip()
            ]
            if default_corpus not in corpus_options:
                corpus_options = [default_corpus, *corpus_options] if default_corpus else corpus_options
            if not corpus_options:
                corpus_options = [default_corpus]
            sel_corpus = st.selectbox(theme.LABEL_COLLECTION, options=corpus_options, index=0)

            st.session_state["scope_tenant_id"] = sel_tenant
            st.session_state["scope_project_id"] = sel_project
            st.session_state["scope_corpus_id"] = sel_corpus

            st.markdown(
                f'<div class="atlas-workspace-banner">'
                f'<strong>{sel_tenant}</strong> / <strong>{sel_project}</strong> / <strong>{sel_corpus}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.session_state["scope_tenant_id"] = st.text_input(theme.LABEL_WORKSPACE, value=default_tenant)
            st.session_state["scope_project_id"] = st.text_input(theme.LABEL_PROJECT, value=default_project)
            st.session_state["scope_corpus_id"] = st.text_input(theme.LABEL_COLLECTION, value=default_corpus)

        tenant_id = str(st.session_state.get("scope_tenant_id", default_tenant))
        project_id = str(st.session_state.get("scope_project_id", default_project))
        corpus_id = str(st.session_state.get("scope_corpus_id", default_corpus))

        # -- Scope-change cache invalidation ----------------------------------
        # When the user switches workspace, project, or collection, any cached
        # data from the previous scope (runs, HITL tasks, library docs) must be
        # discarded so stale cross-scope results are never shown.
        _current_scope = (tenant_id, project_id, corpus_id)
        if st.session_state.get("_last_scope") != _current_scope:
            for _stale_key in ("runs_cache", "hitl_tasks", "hitl_current", "lib_docs"):
                st.session_state.pop(_stale_key, None)
            st.session_state["_last_scope"] = _current_scope

        # -- Status -----------------------------------------------------------
        # Auto-connect on first load when env token is populated so the
        # operator never needs to manually click "Test connection".
        _needs_auto_connect = (
            "health_status" not in st.session_state
            and bool(api)
            and bool(_default_admin_token())
        )

        if _needs_auto_connect:
            try:
                h_resp, h_json = _request_json_diag(label="health (auto)", method="GET", url=f"{api}/health")
                st.session_state["health_status"] = (h_resp.status_code, h_json, h_resp.text)
                if admin_headers:
                    a_resp, a_json = _request_json_diag(
                        label="admin effective config (auto)",
                        method="GET", url=f"{api}/admin/config/effective", headers=admin_headers,
                    )
                    st.session_state["admin_status"] = (a_resp.status_code, a_json, a_resp.text)
            except Exception:
                pass  # Graceful — user can still click manually

        if st.button("Test connection", use_container_width=True, key="sidebar_test_btn"):
            with st.spinner("Checking..."):
                h_resp, h_json = _request_json_diag(label="health", method="GET", url=f"{api}/health")
            st.session_state["health_status"] = (h_resp.status_code, h_json, h_resp.text)

            if admin_headers:
                with st.spinner("Checking admin..."):
                    a_resp, a_json = _request_json_diag(
                        label="admin effective config",
                        method="GET", url=f"{api}/admin/config/effective", headers=admin_headers
                    )
                st.session_state["admin_status"] = (a_resp.status_code, a_json, a_resp.text)

        hs = st.session_state.get("health_status")
        if hs:
            code, h_json, raw = hs
            components.status_pill("Connected to Atlas", ok=int(code) < 400, detail=("" if int(code) < 400 else raw))
        else:
            st.caption("Not tested yet.")

        if not admin_headers:
            st.info("Viewer mode -- admin features are disabled.")
        else:
            ads = st.session_state.get("admin_status")
            if ads:
                code, _, raw = ads
                components.status_pill("Admin access", ok=int(code) < 400, detail=("" if int(code) < 400 else raw))

        # -- Admin tools (collapsed, visually gated) --------------------------
        # Moved to dedicated Admin tab -- sidebar is now a light context rail.

    # -- Build tab list (Admin tab shown only with valid token) ----------------
    tab_labels = [theme.TAB_HOME, theme.TAB_UPLOAD, theme.TAB_LIBRARY, theme.TAB_SEARCH, theme.TAB_REVIEW]
    if admin_headers:
        tab_labels.append(theme.TAB_ADMIN)
    tabs = st.tabs(tab_labels)  # type: ignore[arg-type]

    # =====================================================================
    # HOME
    # =====================================================================
    with tabs[0]:
        components.tab_header(
            "Welcome",
            theme.COPY_HOME,
            workspace=tenant_id,
            collection=corpus_id,
            project=project_id,
        )

        _connected = bool(st.session_state.get("health_status") and int(st.session_state["health_status"][0]) < 400)
        _has_docs = bool(st.session_state.get("lib_docs")) or bool(st.session_state.get("last_doc_id"))
        _has_searched = bool(st.session_state.get("last_query"))
        _has_reviewed = bool(st.session_state.get("hitl_last_action"))

        with components.card(hero=True):
            components.card_header("Getting started", "Complete these steps to set up your collection.")

            # Step 1 — Connect
            components.checklist_item(_connected, "1.", "Connect to Atlas", "Use the sidebar to set your URL and test the connection.")
            if not _connected:
                st.caption("Tip: Set `ATLAS_ADMIN_TOKEN` as an environment variable and the console will auto-connect on load.")

            # Step 2 — Workspace (always done)
            components.checklist_item(True, "2.", "Choose a workspace and project", "Select your scope in the sidebar to organise documents.")

            # Step 3 — Upload
            components.checklist_item(_has_docs, "3.", "Upload your first document", "Go to the Upload tab and add a file or paste text.")
            if not _has_docs and _connected:
                st.caption("Navigate to the **Upload** tab above to add your first document.")

            # Step 4 — Search
            components.checklist_item(_has_searched, "4.", "Search your collection", "Head to Search and try a question.")
            if not _has_searched and _has_docs:
                st.caption("Navigate to the **Search** tab above to try a question against your documents.")

            # Step 5 — Review
            components.checklist_item(_has_reviewed, "5.", "Review flagged content", "If any documents need review, the Review tab will show them.")
            if not _has_reviewed and _has_docs:
                st.caption("Navigate to the **Review** tab above to check for flagged documents.")

    # =====================================================================
    # UPLOAD
    # =====================================================================
    with tabs[1]:
        components.tab_header(
            "Upload",
            theme.COPY_UPLOAD,
            workspace=tenant_id,
            collection=corpus_id,
            project=project_id,
        )

        upload_mode = st.radio(
            "Source",
            options=["Upload file", "Write or paste content"],
            horizontal=True,
            label_visibility="collapsed",
        )

        # -- Card 1: Main upload form ----------------------------------------
        with components.card(hero=True):
            components.card_header("Add a document")
            if upload_mode == "Upload file":
                uploaded = st.file_uploader("Choose a file", type=None)
                default_name = ""
                if uploaded is not None:
                    default_name = os.path.splitext(uploaded.name)[0]

                doc_name = st.text_input(
                    "Document name",
                    value=st.session_state.get("last_doc_name", default_name),
                    key="upload_file_doc_name",
                    help="A friendly name so you can find this document later.",
                )

                is_finalized = st.checkbox(
                    theme.LABEL_MAKE_SEARCH,
                    value=bool(st.session_state.get("last_is_finalized", True)),
                    help="When enabled, this document will appear in search results once processing completes.",
                    key="upload_file_is_finalized",
                )

                can_upload = uploaded is not None and bool((doc_name or "").strip())
                do_upload = components.primary_button("Upload and index", disabled=not can_upload, key="upload_file_btn")

            else:  # Write or paste content
                text_doc_name = st.text_input(
                    "Document name",
                    value=st.session_state.get("last_text_doc_name", "Quick note"),
                    key="upload_text_doc_name",
                )
                text = st.text_area(
                    "Content",
                    height=theme.TEXT_AREA_SM,
                    value="# Hello\n\nPaste content here.",
                    key="upload_text_body",
                )
                text_mime = st.selectbox(
                    "Format",
                    options=["text/plain", "text/markdown"],
                    index=1,
                    key="upload_text_mime",
                )

                text_is_finalized = st.checkbox(
                    theme.LABEL_MAKE_SEARCH,
                    value=bool(st.session_state.get("last_is_finalized", True)),
                    help="When enabled, this document will appear in search results once processing completes.",
                    key="upload_text_is_finalized",
                )

                do_upload = False
                can_upload_text = bool((text_doc_name or "").strip()) and bool((text or "").strip())
                do_text = components.primary_button("Upload and index", key="upload_text_btn", disabled=not can_upload_text)

        # -- Card 2: Advanced options (collapsed) -----------------------------
        with st.expander("Advanced options", expanded=False):
            if upload_mode == "Upload file":
                adv_col1, adv_col2 = st.columns(theme.COL_HALF)
                doc_version = adv_col1.text_input(
                    "Version",
                    value=st.session_state.get("last_doc_version", "1"),
                    key="upload_file_doc_version",
                )
                is_sensitive = adv_col2.checkbox(
                    theme.LABEL_SENSITIVE,
                    value=bool(st.session_state.get("last_is_sensitive", True)),
                    help="If enabled, the pipeline may route content to human review.",
                    key="upload_file_is_sensitive",
                )
                use_custom_id = st.checkbox("Use custom document ID", value=False, key="upload_file_use_custom_id")
                if use_custom_id:
                    doc_id = st.text_input(
                        "Document ID",
                        value=st.session_state.get("last_doc_id", ""),
                        key="upload_file_doc_id",
                    )
                else:
                    doc_id = _stable_doc_id_from_name(
                        f"{(corpus_id or '').strip()}:{doc_name or (uploaded.name if uploaded else 'document')}"
                    )
                    st.caption(f"Auto-generated ID: `{doc_id}`")
                source_mime_type = st.text_input(
                    "MIME type override (optional)",
                    value="",
                    key="upload_file_mime_override",
                    help="Leave blank to auto-detect from the file.",
                )
            else:
                text_doc_id = _stable_doc_id_from_name(
                    f"{(corpus_id or '').strip()}:{((text_doc_name or '').strip() or 'Quick note')}"
                )
                adv_txt_col1, adv_txt_col2 = st.columns(theme.COL_HALF)
                text_doc_version = adv_txt_col1.text_input(
                    "Version",
                    value=st.session_state.get("last_text_doc_version", "1"),
                    key="upload_text_doc_version",
                )
                text_is_sensitive = adv_txt_col2.checkbox(
                    theme.LABEL_SENSITIVE,
                    value=bool(st.session_state.get("last_is_sensitive", True)),
                    help="If enabled, the pipeline may route content to human review.",
                    key="upload_text_is_sensitive",
                )
                st.caption(f"Auto-generated ID: `{text_doc_id}`")

        # -- Upload execution -------------------------------------------------
        if upload_mode == "Upload file" and do_upload:
            if uploaded is None:
                st.warning("Pick a file first.")
            else:
                st.session_state["last_doc_name"] = doc_name
                st.session_state["last_doc_id"] = doc_id
                st.session_state["last_doc_version"] = doc_version
                st.session_state["last_is_finalized"] = bool(is_finalized)
                st.session_state["last_is_sensitive"] = bool(is_sensitive)

                files = {
                    "file": (
                        uploaded.name,
                        uploaded.getvalue(),
                        uploaded.type or "application/octet-stream",
                    )
                }
                data = {
                    "doc_id": doc_id,
                    "doc_version": doc_version,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "corpus_id": corpus_id,
                    "is_finalized": json.dumps(bool(is_finalized)),
                    "is_sensitive": json.dumps(bool(is_sensitive)),
                }
                if source_mime_type.strip():
                    data["source_mime_type"] = source_mime_type.strip()

                with st.spinner("Uploading and indexing -- this may take a moment..."):
                    try:
                        with httpx.Client(timeout=_ui_upload_timeout_s()) as client:
                            start = time.perf_counter()
                            resp = client.post(f"{api}/rag/ingest/file", files=files, data=data)
                            elapsed_ms = int((time.perf_counter() - start) * 1000)
                            _diag_add(
                                {
                                    "type": "http",
                                    "label": "ingest/file",
                                    "method": "POST",
                                    "url": f"{api}/rag/ingest/file",
                                    "status": int(resp.status_code),
                                    "elapsed_ms": elapsed_ms,
                                }
                            )
                    except (httpx.TimeoutException, httpx.ConnectError) as _te:
                        elapsed_ms = int((time.perf_counter() - start) * 1000)
                        _diag_add(
                            {
                                "type": "timeout",
                                "label": "ingest/file",
                                "method": "POST",
                                "url": f"{api}/rag/ingest/file",
                                "elapsed_ms": elapsed_ms,
                                "error": repr(_te),
                            }
                        )
                        st.error(
                            f"Upload timed out after {_ui_upload_timeout_s():.0f}s — the backend may still be processing. ({_te})"
                        )
                        st.stop()

                if resp.status_code >= 400:
                    components.friendly_error(
                        "The file could not be uploaded. Check that the file is a supported format and try again.",
                        status_code=resp.status_code,
                        raw_text=resp.text,
                    )
                    st.stop()

                payload = resp.json() if _is_json_response(resp) else {}
                title, detail = _summarize_ingest(payload if isinstance(payload, dict) else None)

                # Structured result card (A2) — plain-language feedback
                _p = payload if isinstance(payload, dict) else {}
                _run_id_val: int | None = None
                if admin_headers:
                    try:
                        r_resp, r_data = _request_json_diag(
                            label="admin runs",
                            method="GET",
                            url=f"{api}/admin/runs",
                            headers=admin_headers,
                            params={"limit": 50, "tenant_id": tenant_id, "project_id": project_id},
                        )
                        if r_resp.status_code < 400 and isinstance(r_data, list):
                            for r in r_data:
                                if str(r.get("doc_id")) == str(doc_id) and str(r.get("doc_version")) == str(doc_version):
                                    _run_id_val = int(r["id"])
                                    st.session_state["last_run_id"] = _run_id_val
                                    break
                    except Exception:
                        pass

                components.ingest_result_card(
                    doc_name=doc_name or "",
                    doc_id=doc_id,
                    chunks=int(_p.get("chunks_upserted") or 0),
                    searchable=bool(is_finalized),
                    paused_for_review=bool(_p.get("paused_for_hitl")),
                    run_id=_run_id_val,
                    error_message=_p.get("error_message") if _p.get("error_code") else None,
                    extraction_meta=_p.get("extraction_meta") if isinstance(_p.get("extraction_meta"), dict) else None,
                )
                components.detail_expander("Full response (JSON)", data=payload)

        if upload_mode == "Write or paste content" and do_text:
            is_finalized = bool(text_is_finalized)
            is_sensitive = bool(text_is_sensitive)
            st.session_state["last_is_finalized"] = is_finalized
            st.session_state["last_is_sensitive"] = is_sensitive
            st.session_state["last_text_doc_name"] = text_doc_name
            st.session_state["last_text_doc_version"] = text_doc_version
            st.session_state["last_doc_id"] = text_doc_id
            payload = {
                "doc_id": text_doc_id,
                "doc_version": text_doc_version,
                "text": text,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "corpus_id": corpus_id,
                "is_finalized": is_finalized,
                "is_sensitive": is_sensitive,
                "source_mime_type": text_mime,
                "metadata": {},
            }
            with st.spinner("Indexing -- this may take a moment..."):
                resp, data = _request_json_diag(
                    label="ingest/text",
                    method="POST",
                    url=f"{api}/rag/ingest/text",
                    json_body=payload,
                    timeout_s=300.0,
                )
            if resp.status_code >= 400:
                components.friendly_error(
                    "The text could not be indexed. Check your input and try again.",
                    status_code=resp.status_code,
                    raw_text=resp.text if hasattr(resp, 'text') else "",
                )
                st.stop()

            title, detail = _summarize_ingest(data if isinstance(data, dict) else None)

            # Structured result card (A2)
            _tp = data if isinstance(data, dict) else {}
            components.ingest_result_card(
                doc_name=text_doc_name or "",
                doc_id=text_doc_id,
                chunks=int(_tp.get("chunks_upserted") or 0),
                searchable=bool(is_finalized),
                paused_for_review=bool(_tp.get("paused_for_hitl")),
                error_message=_tp.get("error_message") if _tp.get("error_code") else None,
                extraction_meta=_tp.get("extraction_meta") if isinstance(_tp.get("extraction_meta"), dict) else None,
            )
            components.detail_expander("Full response (JSON)", data=data)

        # -- Processing History (B2: absorbed from standalone History tab) -----
        st.divider()
        with st.expander("Processing history", expanded=False):
            if not admin_headers:
                st.caption("Admin token required to view processing history.")
            else:
                components.card_header("Processing runs", "Recent ingest and pipeline runs for this workspace.")
                hist_col1, hist_col2 = st.columns(theme.COL_HALF)
                limit = hist_col1.number_input("Max rows", min_value=1, max_value=500, value=100, key="runs_limit")
                refresh = hist_col2.button("Refresh", key="runs_refresh_btn")

                if refresh or "runs_cache" not in st.session_state:
                    resp, data = _request_json_diag(
                        label="admin runs",
                        method="GET",
                        url=f"{api}/admin/runs",
                        headers=admin_headers,
                        params={"limit": int(limit), "tenant_id": tenant_id, "project_id": project_id},
                    )
                    st.session_state["runs_cache"] = (resp.status_code, data, resp.text)

                status_code, runs_data, runs_text = st.session_state.get("runs_cache", (0, None, ""))
                if status_code >= 400:
                    st.error(f"{status_code}: {runs_text}")
                runs_list = runs_data if isinstance(runs_data, list) else []
                if runs_list:
                    rows = []
                    for r in runs_list:
                        rows.append(
                            {
                                "run_id": r.get("id"),
                                "status": r.get("status"),
                                "doc_id": r.get("doc_id"),
                                "version": r.get("doc_version"),
                                "updated": r.get("updated_at"),
                                "error": r.get("error_code") or "",
                            }
                        )
                    components.data_table(rows)
                else:
                    st.caption("No runs returned.")

                # Run details viewer
                if runs_list:
                    run_ids: list[int] = []
                    for r in runs_list:
                        rid = r.get("id")
                        if rid is None:
                            continue
                        try:
                            run_ids.append(int(rid))
                        except Exception:
                            continue

                    if run_ids:
                        st.divider()
                        components.card_header("Run details", "Select a run to inspect steps and artifacts.")
                        default_run = st.session_state.get("last_run_id")
                        if isinstance(default_run, int) and default_run in run_ids:
                            default_idx = run_ids.index(default_run)
                        else:
                            default_idx = 0

                        selected_run_id = st.selectbox("Select run", options=run_ids, index=default_idx)

                        if components.primary_button("Load details", key="history_load_btn"):
                            run_id = int(selected_run_id)
                            with st.spinner("Loading run..."):
                                r1, d1 = _request_json_diag(
                                    label="admin run detail", method="GET", url=f"{api}/admin/runs/{run_id}", headers=admin_headers
                                )
                                r2, d2 = _request_json_diag(
                                    label="admin node runs",
                                    method="GET",
                                    url=f"{api}/admin/runs/{run_id}/node-runs",
                                    headers=admin_headers,
                                )
                                r3, d3 = _request_json_diag(
                                    label="admin artifacts",
                                    method="GET",
                                    url=f"{api}/admin/runs/{run_id}/artifacts",
                                    headers=admin_headers,
                                )

                            if r1.status_code >= 400:
                                st.error(f"{r1.status_code}: {r1.text}")
                            else:
                                if r2.status_code >= 400:
                                    st.error(f"{r2.status_code}: {r2.text}")
                                    d2 = []
                                if r3.status_code >= 400:
                                    st.error(f"{r3.status_code}: {r3.text}")
                                    d3 = []
                                components.run_detail_card(d1 or {}, d2 or [], d3 or [])

    # =====================================================================
    # SEARCH
    # =====================================================================
    with tabs[3]:
        components.tab_header(
            "Search",
            theme.COPY_SEARCH,
            workspace=tenant_id,
            collection=corpus_id,
            project=project_id,
        )

        # -- Card 1: Query area -----------------------------------------------
        with components.card(hero=True):
            components.card_header("Ask a question")
            query = st.text_input(
                "What are you looking for?",
                value=st.session_state.get("last_query", ""),
                placeholder="Type a question or keyword...",
                label_visibility="collapsed",
                help="Enter a natural language question or keywords. Atlas will find the most relevant passages from your uploaded documents.",
            )
            qr_col1, qr_col2, qr_col3 = st.columns([3, 1, 1])
            with qr_col2:
                top_k = st.number_input("Max results", min_value=1, max_value=50, value=5, label_visibility="collapsed")
            with qr_col3:
                fidelity_mode = st.selectbox(
                    "Result quality",
                    options=["Verified only", "Include partially verified", "Show everything"],
                    index=0,
                    label_visibility="collapsed",
                    help="Controls which chunks are included based on quality verification status.",
                )

            query_s = (query or "").strip()
            do_search = components.primary_button("Search", disabled=not bool(query_s), key="search_btn")

        # -- Card 2: Results ---------------------------------------------------
        if do_search:
            st.session_state["last_query"] = query_s
            _fidelity_map = {
                "Verified only": "verified",
                "Include partially verified": "verified+partial",
                "Show everything": "all",
            }
            payload = {
                "query": query_s,
                "top_k": int(top_k),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "corpus_id": corpus_id,
                "fidelity_mode": _fidelity_map.get(fidelity_mode, "verified"),
            }
            with st.spinner("Searching..."):
                resp, data = _request_json_diag(
                    label="rag/search",
                    method="POST",
                    url=f"{api}/rag/search",
                    json_body=payload,
                    timeout_s=120.0,
                )
            if resp.status_code >= 400:
                components.friendly_error(
                    "Search could not be completed. The server may be busy — try again in a moment.",
                    status_code=resp.status_code,
                    raw_text=resp.text,
                )
            else:
                hits = (data or {}).get("hits") or []
                if not hits:
                    components.empty_state("No results found. Try different keywords or check that documents have been uploaded and indexed.")
                else:
                    st.caption(f"{len(hits)} result(s)")
                    for i, h in enumerate(hits, start=1):
                        payload_h = h.get("payload") or {}
                        doc_id_h = h.get("doc_id")
                        doc_ver_h = payload_h.get("doc_version")
                        filename_h = payload_h.get("source_filename") or ""
                        _fid_flag = payload_h.get("fidelity_flag", "")
                        _fid_label = {"verified": "Verified", "partial": "Partially verified", "low_confidence": "Needs review", "needs_review": "Needs review"}.get(_fid_flag, _fid_flag)
                        score = h.get("score")
                        snippet = (h.get("text") or "").strip().replace("\n", " ")
                        if len(snippet) > theme.MAX_SNIPPET_CHARS:
                            snippet = snippet[:theme.MAX_SNIPPET_CHARS] + "..."
                        # Show source filename prominently; fall back to doc_id
                        _display_name = filename_h or doc_id_h
                        card_title = f"#{i} — {_display_name}"
                        metrics = {
                            "Source": str(filename_h or doc_id_h),
                            "Version": str(doc_ver_h),
                            "Chunk": str(h.get("chunk_index")),
                            "Score": f"{float(score or 0.0):.3f}",
                            "Quality": _fid_label,
                        }
                        components.search_hit_card(i, card_title, snippet, metrics, h)
    
    # =====================================================================
    # REVIEW (HITL)
    # =====================================================================
    with tabs[4]:
        components.tab_header(
            "Review",
            theme.COPY_REVIEW,
            workspace=tenant_id,
            collection=corpus_id,
            project=project_id,
        )

        if not admin_headers:
            components.auth_gate("Admin token required to review documents.")
        else:
            # Auto-load pending count
            if "hitl_tasks" not in st.session_state:
                _auto_resp, _auto_data = _request_json_diag(
                    label="admin hitl tasks auto",
                    method="GET",
                    url=f"{api}/admin/hitl/tasks",
                    headers=admin_headers,
                    params={"limit": 200, "status": "pending", "tenant_id": tenant_id, "project_id": project_id},
                )
                if _auto_resp.status_code < 400:
                    st.session_state["hitl_tasks"] = _auto_data or []

            tasks = st.session_state.get("hitl_tasks", [])
            pending_count = sum(1 for t in tasks if t.get("status") == "pending")

            # -- Card 1: Status + start reviewing -----------------------------
            with components.card():
                if pending_count > 0:
                    components.card_header("Inbox", f"{pending_count} document(s) need your attention.")
                    components.stats_strip(Pending=pending_count, Total=len(tasks))
                else:
                    components.card_header("Inbox")
                    components.empty_state("No documents need review right now. Nice work!")

            assigned_to = "operator"
            current = st.session_state.get("hitl_current")

            if not current and pending_count > 0:
                if components.primary_button("Start reviewing", key="hitl_start_btn"):
                    resp, data = _request_json_diag(
                        label="admin hitl next",
                        method="POST",
                        url=f"{api}/admin/hitl/tasks/next",
                        headers=admin_headers,
                        params={"assigned_to": assigned_to},
                    )
                    if resp.status_code >= 400:
                        components.friendly_error(
                            "No review tasks are available right now. New tasks appear as documents are processed.",
                            status_code=resp.status_code,
                            raw_text=resp.text,
                        )
                    else:
                        st.session_state["hitl_current"] = data
                        st.rerun()

            # -- Card 2: Current review task ----------------------------------
            if current:
                with components.card(elevated=True):
                    components.card_header(
                        f"Reviewing: {current.get('doc_id')}",
                        f"v{current.get('doc_version')}",
                    )

                    # A3 — Plain-language flagging reason derived from task data
                    _judge = float(current.get("judge_score") or 0)
                    _sensitive = bool(current.get("is_sensitive"))
                    _priority = float(current.get("priority_score") or 0)
                    _meta_source = ((current.get("meta") or {}).get("source") or "").strip()

                    _reason_parts: list[str] = []
                    if _judge <= 2:
                        _reason_parts.append(f"low quality score ({_judge:.0f}/5)")
                    elif _judge <= 3:
                        _reason_parts.append(f"borderline quality score ({_judge:.0f}/5)")
                    if _sensitive:
                        _reason_parts.append("marked as sensitive")
                    if _meta_source and _meta_source != "pipeline":
                        _reason_parts.append(f"source: {_meta_source}")

                    if _reason_parts:
                        _urgency = "High" if _priority >= 14 else ("Medium" if _priority >= 8 else "Low")
                        _reason_colour = {"High": theme.DANGER, "Medium": "#E6A817", "Low": theme.MUTED}.get(_urgency, theme.MUTED)
                        st.markdown(
                            f'<div style="background:{theme.BG_ALT}; border-left:4px solid {_reason_colour}; '
                            f'padding:0.5rem 0.75rem; border-radius:4px; margin-bottom:0.75rem; font-size:0.9rem;">'
                            f'<strong style="color:{_reason_colour};">Urgency: {_urgency}</strong> &mdash; '
                            f'Flagged because: {", ".join(_reason_parts)}'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    # -- Rich judge/refine context (surfaced from task meta) --
                    _task_meta = current.get("meta") or {}
                    _sub_scores = _task_meta.get("judge_sub_scores", {})
                    _judge_rationale = _task_meta.get("judge_rationale", "")
                    _score_history = _task_meta.get("judge_score_history", [])
                    _refine_retries = _task_meta.get("refine_retries", 0)
                    _refine_total = _task_meta.get("refine_total_attempts", 0)
                    _last_improvements = _task_meta.get("last_refine_improvements", [])

                    if _sub_scores or _judge_rationale or _score_history:
                        with st.expander("Judge & refine context", expanded=False):
                            if _sub_scores:
                                st.markdown("**Per-dimension scores:**")
                                _score_parts = []
                                for _dim, _s in _sub_scores.items():
                                    _label = _dim.replace("_", " ").title()
                                    _colour = theme.DANGER if _s <= 2 else ("#E6A817" if _s <= 3 else theme.SUCCESS)
                                    _score_parts.append(f'<span style="color:{_colour};font-weight:600;">{_label}: {_s}/5</span>')
                                st.markdown(" &nbsp;|&nbsp; ".join(_score_parts), unsafe_allow_html=True)
                            if _judge_rationale:
                                st.markdown(f"**Rationale:** {_judge_rationale}")
                            if _score_history:
                                st.markdown(f"**Score history:** {' → '.join(str(s) for s in _score_history)}")
                            if _refine_retries or _refine_total:
                                st.caption(f"Refine attempts: {_refine_retries} successful, {_refine_total} total")
                            if _last_improvements:
                                st.caption(f"Last improvements: {', '.join(_last_improvements)}")

                    # A5 — Render Before as rich markdown; keep After editable
                    left, right = st.columns(theme.COL_HALF)
                    with left:
                        st.markdown("**Before** (original)")
                        _before_md = current.get("before_md") or ""
                        with st.container(height=theme.TEXT_AREA_MD + 20):
                            st.markdown(_before_md)
                    with right:
                        st.markdown("**After** (edit below)")
                        after_md = st.text_area("After", height=theme.TEXT_AREA_MD, value=current.get("after_md") or current.get("before_md") or "", label_visibility="collapsed")

                    reason = st.text_input("Reason for edit", value="review", key="hitl_review_reason")

                    # A4 — Merged "Approve and continue" (saves + resumes pipeline)
                    #       plus Skip with reason dropdown
                    act_col1, act_col2 = st.columns([2, 1])
                    with act_col1:
                        do_approve = components.primary_button("Approve and continue", key="hitl_accept")
                    with act_col2:
                        _skip_reason = st.selectbox(
                            "Skip reason",
                            options=["Not sure", "Looks fine to me", "Needs someone else", "Other"],
                            key="hitl_skip_reason",
                            label_visibility="collapsed",
                        )
                        do_skip = components.secondary_button("Skip", key="hitl_skip")

                # A4 — Single approve action: complete + auto-resume pipeline
                if do_approve:
                    tid = int(current["id"])
                    payload = {"after_md": after_md, "reason_for_edit": reason}
                    with st.spinner("Saving review and resuming pipeline..."):
                        resp, data = _request_json_diag(
                            label="admin hitl complete",
                            method="POST",
                            url=f"{api}/admin/hitl/tasks/{tid}/complete",
                            headers=admin_headers,
                            json_body=payload,
                            timeout_s=120.0,
                        )
                    if resp.status_code >= 400:
                        components.friendly_error(
                            "The review could not be saved. Please try again.",
                            status_code=resp.status_code,
                            raw_text=resp.text,
                        )
                    else:
                        # Auto-resume pipeline (best-effort)
                        try:
                            with httpx.Client(timeout=300.0) as client:
                                start = time.perf_counter()
                                _resume_resp = client.post(f"{api}/admin/hitl/tasks/{tid}/resume", headers=admin_headers)
                                elapsed_ms = int((time.perf_counter() - start) * 1000)
                                _diag_add(
                                    {
                                        "type": "http",
                                        "label": "admin hitl resume (auto)",
                                        "method": "POST",
                                        "url": f"{api}/admin/hitl/tasks/{tid}/resume",
                                        "status": int(_resume_resp.status_code),
                                        "elapsed_ms": elapsed_ms,
                                    }
                                )
                        except (httpx.TimeoutException, httpx.ConnectError) as _timeout_err:
                            elapsed_ms = int((time.perf_counter() - start) * 1000)
                            _diag_add(
                                {
                                    "type": "timeout",
                                    "label": "admin hitl resume (auto)",
                                    "method": "POST",
                                    "url": f"{api}/admin/hitl/tasks/{tid}/resume",
                                    "elapsed_ms": elapsed_ms,
                                    "error": repr(_timeout_err),
                                }
                            )
                        except Exception:
                            pass  # Resume is best-effort; review is already saved

                        # Check resume outcome for user feedback
                        _resume_ok = True
                        try:
                            if _resume_resp.status_code >= 400:
                                _resume_ok = False
                        except NameError:
                            _resume_ok = False

                        if _resume_ok:
                            st.success("Review saved and pipeline resumed! Loading next...")
                        else:
                            st.warning(
                                "Review saved, but the pipeline could not be resumed "
                                "(it may have reached the maximum resume limit). "
                                "An admin can inspect the run for details."
                            )
                        st.session_state["hitl_last_action"] = "approved"
                        st.session_state.pop("hitl_current", None)
                        st.session_state.pop("hitl_tasks", None)
                        next_resp, next_data = _request_json_diag(
                            label="admin hitl next",
                            method="POST",
                            url=f"{api}/admin/hitl/tasks/next",
                            headers=admin_headers,
                            params={"assigned_to": assigned_to},
                        )
                        if next_resp.status_code < 400 and next_data:
                            st.session_state["hitl_current"] = next_data
                        st.rerun()

                if do_skip:
                    tid = int(current["id"])
                    _diag_add({"type": "hitl_skip", "task_id": tid, "reason": _skip_reason})
                    with st.spinner("Skipping task..."):
                        skip_resp, _skip_data = _request_json_diag(
                            label="admin hitl skip",
                            method="POST",
                            url=f"{api}/admin/hitl/tasks/{tid}/skip",
                            headers=admin_headers,
                            json_body={"reason": _skip_reason},
                            timeout_s=60.0,
                        )
                    if skip_resp.status_code >= 400:
                        components.friendly_error(
                            "The task could not be skipped. Please try again.",
                            status_code=skip_resp.status_code,
                            raw_text=skip_resp.text,
                        )
                    else:
                        st.success("Task skipped. Loading next...")
                        st.session_state["hitl_last_action"] = "skipped"
                        st.session_state.pop("hitl_current", None)
                        st.session_state.pop("hitl_tasks", None)
                        next_resp, next_data = _request_json_diag(
                            label="admin hitl next",
                            method="POST",
                            url=f"{api}/admin/hitl/tasks/next",
                            headers=admin_headers,
                            params={"assigned_to": assigned_to},
                        )
                        if next_resp.status_code < 400 and next_data:
                            st.session_state["hitl_current"] = next_data
                        st.rerun()

                components.detail_expander("Full task details (JSON)", data=current)

            # -- Card 3: Full queue (collapsed) -------------------------------
            with st.expander("Full review queue", expanded=False):
                fq_col1, fq_col2 = st.columns(theme.COL_HALF)
                status = fq_col1.selectbox("Status filter", options=["", "pending", "in_progress", "completed", "skipped", "rejected"], index=0)
                fq_limit = fq_col2.number_input("Max rows", min_value=1, max_value=500, value=100, key="hitl_limit")

                if st.button("Refresh queue", use_container_width=True, key="hitl_refresh_queue"):
                    params: dict[str, Any] = {"limit": int(fq_limit), "tenant_id": tenant_id, "project_id": project_id}
                    if status:
                        params["status"] = status
                    with st.spinner("Loading..."):
                        resp, data = _request_json_diag(
                            label="admin hitl tasks",
                            method="GET",
                            url=f"{api}/admin/hitl/tasks",
                            headers=admin_headers,
                            params=params,
                        )
                    if resp.status_code >= 400:
                        st.error(f"{resp.status_code}: {resp.text}")
                    else:
                        st.session_state["hitl_tasks"] = data or []

                queue_tasks = st.session_state.get("hitl_tasks", [])
                if queue_tasks:
                    rows = []
                    for t in queue_tasks:
                        rows.append(
                            {
                                "task_id": t.get("id"),
                                "status": t.get("status"),
                                "priority": t.get("priority_score"),
                                "doc_id": t.get("doc_id"),
                                "version": t.get("doc_version"),
                                "assigned_to": t.get("assigned_to"),
                                "updated": t.get("updated_at"),
                            }
                        )
                    components.data_table(rows)
                else:
                    st.caption("Queue is empty.")

    # =====================================================================
    # MY COLLECTION (formerly Library + Versions & Export)
    # =====================================================================
    with tabs[2]:
        import pandas as _pd  # local import - pandas is available via the atlas venv

        components.tab_header(
            "My Collection",
            theme.COPY_LIBRARY,
            workspace=tenant_id,
            collection=corpus_id,
            project=project_id,
        )

        if not admin_headers:
            components.auth_gate("Admin token required to browse documents.")
        else:
            reg = st.session_state.get("group_registry")
            if not isinstance(reg, dict):
                reg = _load_group_registry(api, admin_headers)
                st.session_state["group_registry"] = reg

            corpus_scope = (corpus_id or "").strip() or "default"

            # -- Card 1: Collection overview + filters -------------------------
            with components.card():
                components.card_header("Collection overview", f"Documents in {corpus_scope}.")
                fc1, fc2, fc3 = st.columns([3, 1, 1])
                lib_filter = fc1.text_input(
                    "Filter",
                    value="",
                    placeholder="Filter by doc_id...",
                    key="lib_filter",
                    label_visibility="collapsed",
                )
                lib_finalized = fc2.checkbox(theme.LABEL_SEARCHABLE + " only", value=True, key="lib_finalized")
                lib_refresh = fc3.button("Refresh", use_container_width=True, key="lib_refresh")

                if lib_refresh or "lib_docs" not in st.session_state:
                    with st.spinner("Loading documents..."):
                        lib_resp, lib_data = _request_json_diag(
                            label="library docs",
                            method="GET",
                            url=f"{api}/admin/looking-glass/docs",
                            headers=admin_headers,
                            params={
                                "limit": 200,
                                "tenant_id": tenant_id,
                                "project_id": project_id,
                                "corpus_id": corpus_scope,
                            },
                        )
                    if lib_resp.status_code >= 400:
                        st.error(f"{lib_resp.status_code}: {lib_resp.text}")
                    else:
                        st.session_state["lib_docs"] = (lib_data or {}).get("docs", [])

                all_docs: list[dict[str, Any]] = st.session_state.get("lib_docs", [])

                visible = all_docs
                if lib_filter.strip():
                    q = lib_filter.strip().lower()
                    visible = [d for d in visible if q in (d.get("doc_id") or "").lower()]
                if lib_finalized:
                    visible = [d for d in visible if d.get("is_finalized") is True]

                _finalized_count = sum(1 for d in all_docs if d.get("is_finalized") is True)
                _pending_count = len(all_docs) - _finalized_count
                components.stats_strip(
                    Documents=len(all_docs),
                    Searchable=_finalized_count,
                    Pending=_pending_count,
                    Shown=len(visible),
                )

            if not visible:
                components.empty_state("No documents in this collection yet.")
                st.caption("Use the Upload tab to add your first document.")
            else:
                # -- Card 2: Documents table ----------------------------------
                with components.card():
                    components.card_header("Documents")
                    table_rows = []
                    for _d in visible:
                        table_rows.append({
                            "Select": False,
                            "doc_id": str(_d.get("doc_id") or ""),
                            "corpus": str(_d.get("corpus_id") or corpus_scope),
                            "version": str(_d.get("doc_version") or ""),
                            "finalized": bool(_d.get("is_finalized")),
                            "sensitive": bool(_d.get("is_sensitive")),
                            "mime": str(_d.get("source_mime_type") or ""),
                            "created_at": str(_d.get("created_at") or "")[:19],
                        })

                    lib_df = _pd.DataFrame(table_rows)
                    edited_lib_df = st.data_editor(
                        lib_df,
                        column_config={
                            "Select": st.column_config.CheckboxColumn("Y", default=False, width="small"),
                            "doc_id": st.column_config.TextColumn("Document ID", disabled=True),
                            "corpus": st.column_config.TextColumn(theme.LABEL_COLLECTION, disabled=True, width="small"),
                            "version": st.column_config.TextColumn("Ver", disabled=True, width="small"),
                            "finalized": st.column_config.CheckboxColumn(theme.LABEL_SEARCHABLE, disabled=True, width="small"),
                            "sensitive": st.column_config.CheckboxColumn(theme.LABEL_SENSITIVE, disabled=True, width="small"),
                            "mime": st.column_config.TextColumn("Type", disabled=True, width="medium"),
                            "created_at": st.column_config.TextColumn("Created", disabled=True, width="medium"),
                        },
                        hide_index=True,
                        use_container_width=True,
                        key="lib_table",
                    )

                selected_doc_ids: list[str] = []
                if edited_lib_df is not None:
                    try:
                        selected_doc_ids = (
                            edited_lib_df[edited_lib_df["Select"] == True]["doc_id"].tolist()  # noqa: E712
                        )
                    except Exception:
                        selected_doc_ids = []

                # Chunk viewer (collapsed)
                with st.expander("View chunks", expanded=False):
                    default_view_id = selected_doc_ids[0] if len(selected_doc_ids) == 1 else st.session_state.get("last_doc_id", "")
                    chunk_view_id = st.text_input(
                        "Document ID to inspect",
                        value=default_view_id,
                        key="lib_chunk_view_id",
                    )
                    if st.button("Load chunks", key="lib_load_chunks", disabled=not bool((chunk_view_id or "").strip())):
                        with st.spinner("Loading chunks..."):
                            rc, dc = _request_json_diag(
                                label="library chunks",
                                method="GET",
                                url=f"{api}/admin/looking-glass/docs/{chunk_view_id.strip()}",
                                headers=admin_headers,
                            )
                        if rc.status_code >= 400:
                            st.error(f"{rc.status_code}: {rc.text}")
                        else:
                            chunks_list = (dc or {}).get("chunks", [])
                            st.caption(f"{len(chunks_list)} chunk(s) for `{chunk_view_id.strip()}`")
                            for _ch in chunks_list[:50]:
                                idx_ch = _ch.get("chunk_index", "?")
                                fin_ch = "Y" if _ch.get("is_finalized") else "N"
                                preview_ch = str(_ch.get("text") or "")[:280].strip()
                                st.markdown(f"**Chunk {idx_ch}** | v{_ch.get('doc_version', '?')} | finalized {fin_ch}")
                                st.text(preview_ch + ("..." if len(str(_ch.get("text") or "")) > 280 else ""))
                                st.divider()
                            if len(chunks_list) > 50:
                                st.caption(f"...{len(chunks_list) - 50} more chunks not shown")

                # -- Card 3: Selected actions ---------------------------------
                if selected_doc_ids:
                    sel_label = ", ".join(f"`{d}`" for d in selected_doc_ids[:4])
                    if len(selected_doc_ids) > 4:
                        sel_label += f" ...+{len(selected_doc_ids) - 4} more"

                    with components.card():
                        components.card_header(
                            f"{len(selected_doc_ids)} document(s) selected",
                            "Export or delete the selected documents.",
                        )

                        act_l, act_r = st.columns(theme.COL_HALF)

                    with act_l.expander("Export selected", expanded=True):
                        lib_sel_fmt = st.radio(
                            "Format",
                            ["Full package (lift & shift)", "Markdown only (lean RAG)"],
                            key="lib_sel_fmt",
                            help=(
                                "Full: ZIP per document containing manifest.json, artifacts, index.json, document.md.\n"
                                "Lean: ZIP of clean .md files only - drop straight into another RAG system."
                            ),
                        )
                        lib_sel_fmt_param = "lean" if "lean" in lib_sel_fmt.lower() else "full"

                        if len(selected_doc_ids) == 1:
                            if st.button(
                                f"Generate export for `{selected_doc_ids[0]}`",
                                use_container_width=True,
                                key="lib_export_single_btn",
                            ):
                                _did = selected_doc_ids[0]
                                try:
                                    with st.spinner(f"Exporting {_did}..."):
                                        with httpx.Client(timeout=180.0) as _ec:
                                            _er = _ec.get(
                                                f"{api}/admin/docs/{_did}/export",
                                                headers=admin_headers,
                                                params={
                                                    "tenant_id": tenant_id,
                                                    "project_id": project_id,
                                                    "corpus_id": corpus_scope,
                                                    "format": lib_sel_fmt_param,
                                                },
                                            )
                                except (httpx.TimeoutException, httpx.ConnectError) as _te:
                                    st.error(f"Export timed out \u2014 the backend may still be working. ({_te})")
                                    _er = None
                                if _er is not None and _er.status_code >= 400:
                                    st.error(f"{_er.status_code}: {_er.text}")
                                elif _er is not None:
                                    _etag = "lean" if lib_sel_fmt_param == "lean" else "export"
                                    st.download_button(
                                        "Download ZIP",
                                        data=_er.content,
                                        file_name=f"{_did}_{_etag}.zip",
                                        mime="application/zip",
                                        key="lib_dl_single",
                                    )
                        else:
                            if st.button(
                                f"Generate export ({len(selected_doc_ids)} docs)",
                                use_container_width=True,
                                key="lib_export_multi_btn",
                            ):
                                _multi_buf = io.BytesIO()
                                _multi_errors: list[str] = []
                                with zipfile.ZipFile(_multi_buf, "w", compression=zipfile.ZIP_DEFLATED) as _zout:
                                    for _did in selected_doc_ids:
                                        with st.spinner(f"Exporting {_did}..."):
                                            try:
                                                with httpx.Client(timeout=180.0) as _ec:
                                                    _er = _ec.get(
                                                        f"{api}/admin/docs/{_did}/export",
                                                        headers=admin_headers,
                                                        params={
                                                            "tenant_id": tenant_id,
                                                            "project_id": project_id,
                                                            "corpus_id": corpus_scope,
                                                            "format": lib_sel_fmt_param,
                                                        },
                                                    )
                                                if _er.status_code >= 400:
                                                    _multi_errors.append(f"{_did}: HTTP {_er.status_code}")
                                                else:
                                                    _zout.writestr(
                                                        f"{_did.replace('/', '_')}.zip",
                                                        _er.content,
                                                    )
                                            except Exception as _exc:
                                                _multi_errors.append(f"{_did}: {_exc}")
                                if _multi_errors:
                                    st.warning("Some exports failed: " + "; ".join(_multi_errors))
                                _etag = "lean" if lib_sel_fmt_param == "lean" else "export"
                                st.download_button(
                                    f"Download combined ZIP ({len(selected_doc_ids)} docs)",
                                    data=_multi_buf.getvalue(),
                                    file_name=f"{corpus_scope}_{_etag}_{len(selected_doc_ids)}docs.zip",
                                    mime="application/zip",
                                    key="lib_dl_multi",
                                )

                    with act_r.expander("Delete selected", expanded=False):
                        st.warning(
                            f"Permanently removes **{len(selected_doc_ids)} document(s)** "
                            f"from Qdrant. This cannot be undone."
                        )
                        lib_del_confirm = st.text_input(
                            "Type CONFIRM to enable",
                            value="",
                            key="lib_del_confirm",
                        )
                        if components.danger_button(
                            f"Delete {len(selected_doc_ids)} document(s)",
                            key="lib_del_btn",
                            disabled=(lib_del_confirm.strip() != "CONFIRM"),
                        ):
                            _del_errors: list[str] = []
                            for _did in selected_doc_ids:
                                try:
                                    with httpx.Client(timeout=60.0) as _dc:
                                        _dr = _dc.delete(
                                            f"{api}/admin/docs/{_did}",
                                            headers=admin_headers,
                                            params={
                                                "tenant_id": tenant_id,
                                                "project_id": project_id,
                                                "corpus_id": corpus_scope,
                                            },
                                        )
                                    if _dr.status_code >= 400:
                                        _del_errors.append(f"{_did}: HTTP {_dr.status_code}")
                                except Exception as _exc:
                                    _del_errors.append(f"{_did}: {_exc}")
                            if _del_errors:
                                st.error("Some deletes failed: " + "; ".join(_del_errors))
                            else:
                                st.success(f"Deleted {len(selected_doc_ids)} document(s).")
                            st.session_state.pop("lib_docs", None)
                            st.rerun()

                else:
                    st.caption("Tick one or more rows above to export or delete.")

            # -- Version control (B1: absorbed from Versions & Export tab) -----
            st.divider()
            with components.card():
                components.card_header(
                    theme.LABEL_VERSION_ACTIVE,
                    "Check or change which version of a document is used for search results.",
                )
                vc_doc_id = st.text_input("Document ID", value=st.session_state.get("last_doc_id", ""), key="vc_doc_id")
                new_version = st.text_input("Set version", value="1", key="vc_version")
                vc_doc_id_s = (vc_doc_id or "").strip()

                vc_col1, vc_col2 = st.columns(theme.COL_HALF)
                with vc_col1:
                    vc_show = components.secondary_button("Show current version", disabled=not bool(vc_doc_id_s), key="ver_show")
                with vc_col2:
                    vc_set = components.secondary_button("Set version", disabled=not bool(vc_doc_id_s), key="ver_set")

                if vc_show:
                    resp, data = _request_json_diag(
                        label="admin active version get",
                        method="GET",
                        url=f"{api}/admin/docs/{vc_doc_id_s}/active-version",
                        headers=admin_headers,
                        params={"tenant_id": tenant_id, "project_id": project_id, "corpus_id": corpus_id},
                    )
                    if resp.status_code >= 400:
                        st.error(f"{resp.status_code}: {resp.text}")
                    if data is not None:
                        st.json(data)
                    else:
                        _render_response(resp)

                if vc_set:
                    payload = {
                        "doc_version": new_version,
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "corpus_id": corpus_id,
                    }
                    resp, data = _request_json_diag(
                        label="admin active version set",
                        method="POST",
                        url=f"{api}/admin/docs/{vc_doc_id_s}/active-version",
                        headers=admin_headers,
                        json_body=payload,
                    )
                    if resp.status_code >= 400:
                        st.error(f"{resp.status_code}: {resp.text}")
                    if data is not None:
                        st.success("Version updated.")
                        st.json(data)
                    else:
                        _render_response(resp)

            # -- Collection export (B1: absorbed from Versions & Export tab) ---
            with components.card():
                components.card_header("Collection export", "Download the entire collection as a portable package.")

                se_format = st.radio(
                    "Format",
                    options=["Full package (lift & shift)", "Markdown only (lean RAG)"],
                    horizontal=True,
                    key="export_format_radio",
                    help=(
                        "Full: ZIP with manifests, artifacts, and index data.\n"
                        "Lean: flat folder of clean .md files - drop into another RAG system."
                    ),
                )
                se_fmt_param = "lean" if "lean" in se_format.lower() else "full"

                with st.expander("Advanced export options", expanded=False):
                    se_max_docs = int(st.number_input(
                        "Max documents",
                        min_value=1,
                        max_value=20000,
                        value=2000,
                        key="export_max_docs",
                    ))

                do_coll_export = components.primary_button("Generate collection export", key="export_go_btn")

                if do_coll_export:
                    try:
                        with st.spinner("Generating collection export..."):
                            with httpx.Client(timeout=600.0) as _ce_client:
                                _ce_resp = _ce_client.get(
                                    f"{api}/admin/corpora/{corpus_scope}/export",
                                    headers=admin_headers,
                                    params={
                                        "tenant_id": tenant_id,
                                        "project_id": project_id,
                                        "max_docs": se_max_docs,
                                        "format": se_fmt_param,
                                    },
                                )
                    except (httpx.TimeoutException, httpx.ConnectError) as _te:
                        st.error(f"Collection export timed out — the backend may still be working. ({_te})")
                        _ce_resp = None
                    if _ce_resp is not None and _ce_resp.status_code >= 400:
                        st.error(f"Export failed ({_ce_resp.status_code}): {_ce_resp.text}")
                    elif _ce_resp is not None:
                        _ce_tag = "lean" if se_fmt_param == "lean" else "export"
                        st.success(f"Ready! {len(_ce_resp.content):,} bytes")
                        st.download_button(
                            "Download collection ZIP",
                            data=_ce_resp.content,
                            file_name=f"{corpus_scope}_{_ce_tag}.zip",
                            mime="application/zip",
                            key="export_dl_corpus",
                        )

    # =====================================================================
    # ADMIN (only present when admin token is set)
    # =====================================================================
    if admin_headers:
        with tabs[5]:
            components.tab_header(
                "Admin tools",
                theme.COPY_ADMIN,
                workspace=tenant_id,
                collection=corpus_id,
                project=project_id,
            )

            admin_tabs = st.tabs([
                "Health & Metrics",
                "Cleanup & Feedback",
                "Groups",
                "Danger Zone",
            ])

            # =============================================================
            # Admin sub-tab 0: Health & Metrics
            # =============================================================
            with admin_tabs[0]:

                # -- Card 1: Pipeline metrics ---------------------------------
                with components.card(hero=True):
                    components.card_header(
                        "Pipeline health",
                        "Processing metrics for this workspace and collection.",
                    )
                    if components.primary_button("Load metrics", key="ct_metrics_btn"):
                        resp_m, metrics_data = _request_json_diag(
                            label="pipeline metrics",
                            method="GET",
                            url=f"{api}/admin/looking-glass/metrics",
                            headers=admin_headers,
                            params={"tenant_id": tenant_id, "project_id": project_id, "corpus_id": corpus_id},
                        )
                        if int(resp_m.status_code) < 400 and isinstance(metrics_data, dict):
                            mcol1, mcol2, mcol3, mcol4 = st.columns(theme.COL_QUARTERS)
                            with mcol1:
                                st.metric("Runs", metrics_data.get("workflow_runs", {}).get("total", 0))
                            with mcol2:
                                completion_rate = metrics_data.get("workflow_runs", {}).get("completion_rate")
                                if isinstance(completion_rate, (int, float)):
                                    st.metric("Success rate", f"{completion_rate * 100:.1f}%")
                                else:
                                    st.metric("Success rate", "N/A")
                            with mcol3:
                                st.metric("HITL tasks", metrics_data.get("hitl", {}).get("total", 0))
                            with mcol4:
                                st.metric("Feedback items", metrics_data.get("cleanup_feedback", {}).get("total", 0))
                            components.detail_expander("Full metrics JSON", data=metrics_data)
                        else:
                            st.error(f"Failed to load metrics ({resp_m.status_code})")

                # -- Card 2: Session diagnostics (inlined) --------------------
                with components.card():
                    components.card_header(
                        "Session diagnostics",
                        "API call log and exception capture for this browser session.",
                    )

                    _diag_init()
                    _diag_ensure_session_started(api)

                    diag_events = list(st.session_state.get("diag_events") or [])
                    st.metric("Recorded events", len(diag_events))

                    adm_diag_col1, adm_diag_col2 = st.columns(theme.COL_HALF)
                    with adm_diag_col1:
                        if st.button("Clear log", use_container_width=True, key="adm_diag_clear"):
                            st.session_state["diag_events"] = []
                            st.session_state["diag_session_started"] = False
                            _diag_ensure_session_started(api)
                    with adm_diag_col2:
                        st.download_button(
                            "Download logs (JSON)",
                            data=_diag_bundle(api),
                            file_name="atlas_ui_diagnostics.json",
                            mime="application/json",
                            use_container_width=True,
                            key="adm_diag_download",
                        )

                    # Inline event table (replaces global diagnostics panel)
                    if not diag_events:
                        st.caption("No diagnostics yet. Click 'Test connection' or run an action.")
                    else:
                        events = list(reversed(diag_events))
                        rows = []
                        for e in events[:theme.MAX_DIAG_ROWS]:
                            rows.append(
                                {
                                    "ts": e.get("ts"),
                                    "type": e.get("type"),
                                    "label": e.get("label"),
                                    "method": e.get("method"),
                                    "status": e.get("status"),
                                    "elapsed_ms": e.get("elapsed_ms"),
                                    "url": e.get("url"),
                                    "error": e.get("error"),
                                }
                            )
                        components.data_table(rows)

            # =============================================================
            # Admin sub-tab 1: Cleanup & Feedback
            # =============================================================
            with admin_tabs[1]:

                # -- Card 1: Active cleanup rules -----------------------------
                with components.card():
                    components.card_header(
                        "Active cleanup rules",
                        "Rules from the effective config (YAML defaults + DB overrides). "
                        "Applied rules take effect immediately — no restart required.",
                    )
                    cfg_resp, cfg_data = _request_json(
                        method="GET",
                        url=f"{api}/admin/config/effective",
                        headers=admin_headers,
                    )
                    eff_rules: list[dict[str, Any]] = []
                    config_source_db = False
                    if cfg_data and isinstance(cfg_data, dict):
                        pipeline_cfg = cfg_data.get("pipeline") or {}
                        eff_rules = pipeline_cfg.get("cleanup_rules") or []
                        config_source_db = bool((cfg_data.get("source") or {}).get("db"))
                    if config_source_db:
                        st.caption("Rules loaded from DB config version (overrides YAML defaults).")
                    else:
                        st.caption("Rules loaded from pipeline.yaml (no DB override active).")
                    if eff_rules:
                        for idx, rule in enumerate(eff_rules):
                            rule_name = rule.get("name", f"rule_{idx}")
                            with st.expander(f"Rule: {rule_name}"):
                                # Editable YAML view
                                _edit_key = f"ct_rule_yaml_{idx}"
                                _orig_yaml = yaml.dump(rule, default_flow_style=False, sort_keys=False)
                                edited_yaml = st.text_area(
                                    "Rule YAML (editable)",
                                    value=_orig_yaml,
                                    height=theme.TEXT_AREA_MD,
                                    key=_edit_key,
                                )
                                _is_dirty = edited_yaml.strip() != _orig_yaml.strip()
                                if _is_dirty:
                                    st.info("Unsaved changes detected.")

                                btn_cols = st.columns([1, 1, 1, 1])
                                with btn_cols[0]:
                                    _validate_clicked = components.secondary_button(
                                        "Validate", key=f"ct_val_rule_{idx}",
                                    )
                                with btn_cols[1]:
                                    _save_clicked = components.primary_button(
                                        "Save changes", key=f"ct_save_rule_{idx}",
                                        disabled=not _is_dirty,
                                    )
                                with btn_cols[2]:
                                    _dryrun_clicked = components.secondary_button(
                                        "Dry-run", key=f"ct_dry_rule_{idx}",
                                    )
                                with btn_cols[3]:
                                    _remove_clicked = components.danger_button(
                                        f"Remove", key=f"ct_remove_rule_{idx}",
                                    )

                                # --- Validate action ---
                                if _validate_clicked:
                                    try:
                                        _parsed_edit = yaml.safe_load(edited_yaml)
                                        _as_list = [_parsed_edit] if isinstance(_parsed_edit, dict) else _parsed_edit
                                        resp_v, v_data = _request_json_diag(
                                            label=f"validate rule {rule_name}",
                                            method="POST",
                                            url=f"{api}/admin/config/validate-rules",
                                            headers=admin_headers,
                                            json_body=_as_list,
                                            timeout_s=15.0,
                                        )
                                        if int(resp_v.status_code) < 400 and isinstance(v_data, dict):
                                            if v_data.get("valid"):
                                                st.success("Rule is valid.")
                                            else:
                                                for _ve in v_data.get("errors", []):
                                                    st.warning(f"Validation: {_ve}")
                                        else:
                                            st.error(f"Validation request failed ({resp_v.status_code})")
                                    except yaml.YAMLError as _ye:
                                        st.error(f"YAML parse error: {_ye}")

                                # --- Save action ---
                                if _save_clicked:
                                    with st.spinner("Saving rule..."):
                                        resp_s, s_data = _request_json_diag(
                                            label=f"save rule {rule_name}",
                                            method="POST",
                                            url=f"{api}/admin/cleanup-rules/apply",
                                            headers=admin_headers,
                                            json_body={"rule_yaml": edited_yaml},
                                            timeout_s=30.0,
                                        )
                                    if int(resp_s.status_code) < 400:
                                        st.success("Rule saved. Active immediately.")
                                        st.rerun()
                                    else:
                                        st.error(f"Save failed ({resp_s.status_code})")
                                        components.detail_expander("Error details", data=s_data)

                                # --- Dry-run action ---
                                _dr_open_key = f"_dryrun_open_{idx}"
                                if _dryrun_clicked:
                                    st.session_state[_dr_open_key] = not st.session_state.get(_dr_open_key, False)

                                if st.session_state.get(_dr_open_key):
                                    st.divider()
                                    st.caption("Paste a markdown snippet below, then click **Run** to test this rule.")
                                    _dr_sample = st.text_area(
                                        "Markdown sample",
                                        height=theme.TEXT_AREA_SM,
                                        key=f"ct_dr_input_{idx}",
                                        placeholder="Paste a markdown snippet to test this rule against...",
                                    )
                                    _dr_btn_cols = st.columns([1, 1, 4])
                                    with _dr_btn_cols[0]:
                                        _run_dr = components.primary_button("Run", key=f"ct_dr_run_{idx}")
                                    with _dr_btn_cols[1]:
                                        if st.button("Close", key=f"ct_dr_close_{idx}"):
                                            st.session_state.pop(_dr_open_key, None)
                                            st.rerun()

                                    if _run_dr and _dr_sample.strip():
                                        with st.spinner("Running dry-run..."):
                                            resp_dr, dr_data = _request_json_diag(
                                                label=f"dry-run rule {rule_name}",
                                                method="POST",
                                                url=f"{api}/admin/cleanup-rules/dry-run",
                                                headers=admin_headers,
                                                json_body={
                                                    "markdown_sample": _dr_sample,
                                                    "tenant_id": tenant_id,
                                                    "project_id": project_id,
                                                    "corpus_id": corpus_id,
                                                },
                                                timeout_s=30.0,
                                            )
                                        if int(resp_dr.status_code) < 400 and isinstance(dr_data, dict):
                                            _matched = dr_data.get("matched_rule")
                                            _changed = dr_data.get("changed", False)
                                            if _matched:
                                                st.caption(f"Matched rule: **{_matched}** | Changed: {'Yes' if _changed else 'No'}")
                                            else:
                                                st.warning("No rule matched this document context.")
                                            if _changed:
                                                st.text_area(
                                                    "Cleaned output",
                                                    value=dr_data.get("cleaned_markdown", ""),
                                                    height=theme.TEXT_AREA_SM,
                                                    disabled=True,
                                                    key=f"ct_dr_out_{idx}",
                                                )
                                            _fix_counts = dr_data.get("fix_counts", {})
                                            if _fix_counts:
                                                st.caption(f"Fix counts: {_fix_counts}")
                                        else:
                                            st.error(f"Dry-run failed ({resp_dr.status_code})")
                                            components.detail_expander("Details", data=dr_data)
                                    elif _run_dr:
                                        st.warning("Paste a markdown sample first.")

                                # --- Remove action ---
                                if _remove_clicked:
                                    with st.spinner(f"Removing rule '{rule_name}'..."):
                                        resp_rm, rm_data = _request_json_diag(
                                            label=f"remove cleanup rule {rule_name}",
                                            method="DELETE",
                                            url=f"{api}/admin/cleanup-rules/{rule_name}",
                                            headers=admin_headers,
                                            timeout_s=30.0,
                                        )
                                    if int(resp_rm.status_code) < 400:
                                        st.success(f"Rule '{rule_name}' removed.")
                                        st.rerun()
                                    else:
                                        st.error(f"Remove failed ({resp_rm.status_code})")
                                        components.detail_expander("Error details", data=rm_data)
                    else:
                        st.caption("No cleanup rules configured. Use the suggestion card below to create one.")

                    # -- Export / Import controls --------------------------
                    st.divider()
                    exp_imp_cols = st.columns([1, 1, 2])
                    with exp_imp_cols[0]:
                        _export_clicked = components.secondary_button(
                            "Export rules", key="ct_export_rules",
                        )
                    with exp_imp_cols[1]:
                        _show_import = components.secondary_button(
                            "Import rules", key="ct_show_import",
                        )

                    if _export_clicked:
                        with st.spinner("Downloading rules..."):
                            try:
                                import httpx as _httpx
                                _exp_resp = _httpx.get(
                                    f"{api}/admin/cleanup-rules/export",
                                    headers=admin_headers,
                                    timeout=15.0,
                                )
                                if _exp_resp.status_code < 400:
                                    st.download_button(
                                        label="Save cleanup_rules.yaml",
                                        data=_exp_resp.content,
                                        file_name="cleanup_rules.yaml",
                                        mime="application/x-yaml",
                                        key="ct_export_download",
                                    )
                                else:
                                    st.error(f"Export failed ({_exp_resp.status_code})")
                            except Exception as _ex:
                                st.error(f"Export error: {_ex}")

                    _import_open_key = "_ct_import_open"
                    if _show_import:
                        st.session_state[_import_open_key] = not st.session_state.get(_import_open_key, False)

                    if st.session_state.get(_import_open_key):
                        st.caption("Upload a YAML file containing cleanup rules to import.")
                        uploaded = st.file_uploader(
                            "Rules YAML file",
                            type=["yaml", "yml"],
                            key="ct_import_file",
                        )
                        _imp_mode = st.radio(
                            "Import mode",
                            options=["replace", "merge"],
                            index=0,
                            horizontal=True,
                            key="ct_import_mode",
                            help="**Replace**: overwrite all existing rules. **Merge**: add new rules, update existing by name.",
                        )
                        _imp_btn = components.primary_button("Import", key="ct_import_btn", disabled=uploaded is None)
                        if _imp_btn and uploaded is not None:
                            _yaml_content = uploaded.read().decode("utf-8")
                            with st.spinner("Importing rules..."):
                                resp_imp, imp_data = _request_json_diag(
                                    label="import cleanup rules",
                                    method="POST",
                                    url=f"{api}/admin/cleanup-rules/import",
                                    headers=admin_headers,
                                    json_body={
                                        "rules_yaml": _yaml_content,
                                        "mode": _imp_mode,
                                    },
                                    timeout_s=30.0,
                                )
                            if int(resp_imp.status_code) < 400 and isinstance(imp_data, dict):
                                st.success(
                                    f"Imported {len(imp_data.get('imported', []))} rule(s) "
                                    f"({_imp_mode} mode). {imp_data.get('rules_count', '?')} total rules active."
                                )
                                st.session_state.pop(_import_open_key, None)
                                st.rerun()
                            else:
                                st.error(f"Import failed ({resp_imp.status_code})")
                                components.detail_expander("Error details", data=imp_data)

                # -- Card 2: Quality feedback ---------------------------------
                with components.card():
                    components.card_header(
                        "Quality feedback",
                        "Report cleanup issues so patterns can be tracked. "
                        "Feedback is recorded for developer review \u2014 it does not "
                        "automatically change processing.",
                    )
                    fb_cols = st.columns([2, 1])
                    with fb_cols[0]:
                        fb_doc_id = st.text_input("Document ID", key="ct_fb_doc_id", placeholder="e.g. my-doc-abc123")
                    with fb_cols[1]:
                        fb_category = st.selectbox(
                            "Category",
                            options=["formatting", "missing_content", "ocr_artifact", "hallucination", "other"],
                            key="ct_fb_category",
                        )
                    fb_comment = st.text_area("Comment / description", key="ct_fb_comment", height=theme.TEXT_AREA_SM)
                    if components.secondary_button("Submit feedback", key="ct_fb_submit", disabled=not bool((fb_doc_id or "").strip())):
                        fb_body = {
                            "tenant_id": tenant_id,
                            "project_id": project_id,
                            "corpus_id": corpus_id,
                            "doc_id": (fb_doc_id or "").strip(),
                            "category": fb_category,
                            "description": fb_comment or "",
                        }
                        resp_fb, data_fb = _request_json_diag(
                            label="submit cleanup feedback",
                            method="POST",
                            url=f"{api}/admin/cleanup-feedback",
                            headers=admin_headers,
                            json_body=fb_body,
                        )
                        if int(resp_fb.status_code) < 400:
                            st.success("Feedback recorded.")
                            st.caption(
                                "This is tracked for pattern analysis \u2014 "
                                "a developer or admin can review trends and create cleanup rules "
                                "to address recurring issues. Feedback does not automatically "
                                "change processing."
                            )
                        else:
                            st.error(f"Failed ({resp_fb.status_code})")
                        components.detail_expander("Response", data=data_fb)

                    st.divider()

                    # --- Feedback overview (categories) ---
                    components.card_section("Feedback overview")
                    if st.button("Load feedback categories", key="ct_fb_cats_btn", use_container_width=True):
                        resp_cats, cats_data = _request_json_diag(
                            label="feedback categories",
                            method="GET",
                            url=f"{api}/admin/cleanup-feedback/categories",
                            headers=admin_headers,
                            params={"tenant_id": tenant_id, "project_id": project_id, "corpus_id": corpus_id},
                        )
                        if int(resp_cats.status_code) < 400 and isinstance(cats_data, dict):
                            if cats_data:
                                cat_rows = [{"category": k, "count": v} for k, v in cats_data.items()]
                                components.data_table(cat_rows)
                            else:
                                st.caption("No feedback recorded yet.")
                        else:
                            st.error(f"Failed to load categories ({resp_cats.status_code})")

                # -- Card 3: AI rule suggestion -------------------------------
                with components.card():
                    components.card_header(
                        "Suggest a cleanup rule",
                        "Paste problematic markdown and describe the issues. "
                        "Atlas will suggest a rule you can add to pipeline.yaml.",
                    )
                    sug_sample = st.text_area(
                        "Sample markdown", key="ct_sug_sample", height=theme.TEXT_AREA_MD,
                        placeholder="Paste a representative markdown snippet here...",
                    )
                    sug_issues = st.text_area(
                        "Observed issues", key="ct_sug_issues", height=theme.TEXT_AREA_SM,
                        placeholder="e.g. Page numbers appear in every chunk, headings are inconsistent...",
                    )
                    if components.primary_button("Suggest rule", key="ct_sug_btn", disabled=not bool((sug_sample or "").strip())):
                        sug_body: dict[str, Any] = {
                            "markdown_sample": sug_sample or "",
                            "issues": sug_issues or "",
                            "context": {
                                "tenant_id": tenant_id,
                                "project_id": project_id,
                                "corpus_id": corpus_id,
                            },
                        }
                        with st.spinner("Asking the LLM for a rule suggestion -- this may take a few minutes with local models..."):
                            resp_sug, sug_data = _request_json_diag(
                                label="suggest cleanup rule",
                                method="POST",
                                url=f"{api}/admin/cleanup-rules/suggest",
                                headers=admin_headers,
                                json_body=sug_body,
                                timeout_s=300.0,
                            )
                        if int(resp_sug.status_code) < 400 and isinstance(sug_data, dict):
                            rule_yaml = sug_data.get("rule_yaml", "")
                            rationale = sug_data.get("rationale", "")
                            validation_errors = sug_data.get("validation_errors", [])
                            # Persist suggestion in session state so it survives reruns
                            st.session_state["_pending_rule"] = {
                                "rule_yaml": rule_yaml,
                                "rationale": rationale,
                                "validation_errors": validation_errors,
                            }
                        else:
                            st.error(f"Suggestion failed ({resp_sug.status_code})")
                            components.detail_expander("Error details", data=sug_data)
                            st.session_state.pop("_pending_rule", None)

                    # -- Display pending suggestion (persisted across reruns) --
                    pending = st.session_state.get("_pending_rule")
                    if pending:
                        rule_yaml = pending["rule_yaml"]
                        rationale = pending["rationale"]
                        validation_errors = pending.get("validation_errors", [])
                        if rule_yaml:
                            st.success("Rule suggested! Review and edit below, then apply.")
                            st.markdown(f"**Rationale:** {rationale}")

                            # Editable YAML — user can tweak the LLM suggestion
                            edited_sug = st.text_area(
                                "Suggested rule YAML (editable)",
                                value=rule_yaml,
                                height=theme.TEXT_AREA_MD,
                                key="ct_sug_yaml_edit",
                            )

                            # Live re-validate if the user edited the suggestion
                            _sug_val_errors = validation_errors
                            if edited_sug.strip() != rule_yaml.strip():
                                st.caption("You edited the suggestion — re-validating...")
                                try:
                                    _sug_parsed = yaml.safe_load(edited_sug)
                                    _sug_as_list = [_sug_parsed] if isinstance(_sug_parsed, dict) else _sug_parsed
                                    _rv, _rvd = _request_json_diag(
                                        label="re-validate edited suggestion",
                                        method="POST",
                                        url=f"{api}/admin/config/validate-rules",
                                        headers=admin_headers,
                                        json_body=_sug_as_list,
                                        timeout_s=15.0,
                                    )
                                    if int(_rv.status_code) < 400 and isinstance(_rvd, dict):
                                        _sug_val_errors = _rvd.get("errors", [])
                                        if not _sug_val_errors:
                                            st.success("Edited rule is valid.")
                                    else:
                                        _sug_val_errors = [f"Validation request failed ({_rv.status_code})"]
                                except yaml.YAMLError as _ye:
                                    _sug_val_errors = [f"YAML parse error: {_ye}"]

                            if _sug_val_errors:
                                st.warning(f"{len(_sug_val_errors)} validation issue(s) — fix before applying.")
                                for ve in _sug_val_errors:
                                    st.caption(f"- {ve}")

                            apply_col, dryrun_col, dismiss_col = st.columns(3)
                            with apply_col:
                                if components.primary_button(
                                    "Apply rule to live config",
                                    key="ct_apply_rule_btn",
                                    disabled=bool(_sug_val_errors),
                                ):
                                    with st.spinner("Applying rule..."):
                                        resp_apply, apply_data = _request_json_diag(
                                            label="apply cleanup rule",
                                            method="POST",
                                            url=f"{api}/admin/cleanup-rules/apply",
                                            headers=admin_headers,
                                            json_body={"rule_yaml": edited_sug},
                                            timeout_s=30.0,
                                        )
                                    if int(resp_apply.status_code) < 400:
                                        applied_names = (apply_data or {}).get("applied", [])
                                        st.success(
                                            f"Rule applied! ({', '.join(applied_names)}) "
                                            "Active immediately — no restart required."
                                        )
                                        st.session_state.pop("_pending_rule", None)
                                        st.rerun()
                                    else:
                                        st.error(f"Apply failed ({resp_apply.status_code})")
                                        components.detail_expander("Error details", data=apply_data)
                            with dryrun_col:
                                if components.secondary_button("Dry-run preview", key="ct_sug_dryrun_btn"):
                                    st.session_state["_sug_dryrun_open"] = True
                            with dismiss_col:
                                if st.button("Dismiss suggestion", key="ct_dismiss_sug"):
                                    st.session_state.pop("_pending_rule", None)
                                    st.session_state.pop("_sug_dryrun_open", None)
                                    st.rerun()

                            # Dry-run panel for suggestions
                            if st.session_state.get("_sug_dryrun_open"):
                                st.divider()
                                components.card_section("Dry-run preview")
                                _sug_dr_sample = st.text_area(
                                    "Paste markdown sample",
                                    height=theme.TEXT_AREA_SM,
                                    key="ct_sug_dr_input",
                                    placeholder="Paste markdown to test the rule against...",
                                )
                                if _sug_dr_sample.strip():
                                    with st.spinner("Running dry-run..."):
                                        resp_dr, dr_data = _request_json_diag(
                                            label="dry-run suggestion",
                                            method="POST",
                                            url=f"{api}/admin/cleanup-rules/dry-run",
                                            headers=admin_headers,
                                            json_body={
                                                "markdown_sample": _sug_dr_sample,
                                                "tenant_id": tenant_id,
                                                "project_id": project_id,
                                                "corpus_id": corpus_id,
                                            },
                                            timeout_s=30.0,
                                        )
                                    if int(resp_dr.status_code) < 400 and isinstance(dr_data, dict):
                                        _m = dr_data.get("matched_rule")
                                        _c = dr_data.get("changed", False)
                                        st.caption(f"Matched: **{_m or 'None'}** | Changed: {'Yes' if _c else 'No'}")
                                        if _c:
                                            st.text_area(
                                                "Cleaned output",
                                                value=dr_data.get("cleaned_markdown", ""),
                                                height=theme.TEXT_AREA_SM,
                                                disabled=True,
                                                key="ct_sug_dr_out",
                                            )
                                        _fc = dr_data.get("fix_counts", {})
                                        if _fc:
                                            st.caption(f"Fix counts: {_fc}")
                                    else:
                                        st.error(f"Dry-run failed ({resp_dr.status_code})")
                        else:
                            st.info(f"No rule suggested. {rationale}")
                            st.session_state.pop("_pending_rule", None)

            # =============================================================
            # Admin sub-tab 2: Groups
            # =============================================================
            with admin_tabs[2]:

                # -- Card 1: Group overview + create --------------------------
                with components.card():
                    components.card_header(
                        "Group management",
                        "Create and inspect workspaces, projects, and collections.",
                    )

                    if st.button("Refresh groups", key="adm_scope_refresh_btn", use_container_width=True):
                        st.session_state.pop("group_registry", None)
                        st.rerun()

                    reg = st.session_state.get("group_registry", {"tenants": [], "projects": [], "corpora": []})

                    grp_col1, grp_col2, grp_col3 = st.columns(theme.COL_THIRDS)
                    with grp_col1:
                        st.metric("Workspaces", len(reg.get("tenants", [])))
                    with grp_col2:
                        st.metric("Projects", len(reg.get("projects", [])))
                    with grp_col3:
                        st.metric("Collections", len(reg.get("corpora", [])))

                    with st.expander("View all groups", expanded=False):
                        grp_tab1, grp_tab2, grp_tab3 = st.tabs(["Workspaces", "Projects", "Collections"])
                        with grp_tab1:
                            if reg.get("tenants"):
                                components.data_table([{"tenant_id": t.get("tenant_id"), "display_name": t.get("display_name", "")} for t in reg["tenants"]])
                            else:
                                st.caption("No workspaces found.")
                        with grp_tab2:
                            if reg.get("projects"):
                                components.data_table([{"tenant_id": p.get("tenant_id"), "project_id": p.get("project_id"), "display_name": p.get("display_name", "")} for p in reg["projects"]])
                            else:
                                st.caption("No projects found.")
                        with grp_tab3:
                            if reg.get("corpora"):
                                components.data_table([{"tenant_id": c.get("tenant_id"), "project_id": c.get("project_id"), "corpus_id": c.get("corpus_id"), "display_name": c.get("display_name", "")} for c in reg["corpora"]])
                            else:
                                st.caption("No collections found.")

                    st.divider()
                    components.card_section("Create new")
                    st.caption(
                        "Groups follow a hierarchy: **Workspace → Project → Collection**. "
                        "Create them in that order — a Project requires an existing Workspace, "
                        "and a Collection requires an existing Project."
                    )
                    create_kind = st.selectbox("Type", options=["Workspace", "Project", "Collection"], key="adm_scope_create_kind")

                    # Show parent-scope context so user knows what they're creating inside.
                    _ck = (create_kind or "").strip().lower()
                    _has_tenant = tenant_id in [str(t.get("tenant_id") or "") for t in reg.get("tenants", [])]
                    _proj_list = [
                        p for p in reg.get("projects", [])
                        if str(p.get("tenant_id") or "") == tenant_id
                    ]
                    _has_project = project_id in [str(p.get("project_id") or "") for p in _proj_list]

                    if _ck == "project":
                        if _has_tenant:
                            st.info(f"Will create inside workspace **{tenant_id}**  (selected in sidebar).")
                        else:
                            st.warning(
                                f"Workspace **{tenant_id}** does not exist yet. "
                                "Create the Workspace first, then create a Project inside it."
                            )
                    elif _ck == "collection":
                        _problems: list[str] = []
                        if not _has_tenant:
                            _problems.append(f"Workspace **{tenant_id}** does not exist.")
                        if not _has_project:
                            _problems.append(f"Project **{project_id}** does not exist in workspace **{tenant_id}**.")
                        if _problems:
                            st.warning(
                                " ".join(_problems) + " Create the missing parent(s) first."
                            )
                        else:
                            st.info(
                                f"Will create inside workspace **{tenant_id}** / project **{project_id}**  (selected in sidebar)."
                            )

                    new_id = st.text_input("ID", value="", key="adm_scope_create_id")
                    new_name = st.text_input("Display name (optional)", value="", key="adm_scope_create_name")

                    # Block creation if required parent is missing.
                    _create_blocked = not bool((new_id or "").strip())
                    if _ck == "project" and not _has_tenant:
                        _create_blocked = True
                    if _ck == "collection" and (not _has_tenant or not _has_project):
                        _create_blocked = True

                    if components.secondary_button("Create", disabled=_create_blocked, key="adm_scope_create_btn"):
                        kind = _ck
                        if kind == "workspace":
                            ok, msg = _create_scope_entry(
                                api=api, admin_headers=admin_headers,
                                endpoint="/admin/tenants",
                                payload={"tenant_id": new_id, "display_name": new_name},
                                label="Workspace",
                            )
                        elif kind == "project":
                            ok, msg = _create_scope_entry(
                                api=api, admin_headers=admin_headers,
                                endpoint="/admin/projects",
                                payload={"tenant_id": tenant_id, "project_id": new_id, "display_name": new_name},
                                label="Project",
                            )
                        else:
                            ok, msg = _create_scope_entry(
                                api=api, admin_headers=admin_headers,
                                endpoint="/admin/corpora",
                                payload={
                                    "tenant_id": tenant_id, "project_id": project_id,
                                    "corpus_id": new_id, "display_name": new_name,
                                },
                                label="Collection",
                            )
                        if ok:
                            st.success(msg)
                            st.session_state["group_registry"] = _load_group_registry(api, admin_headers)
                        else:
                            st.error(msg)

            # =============================================================
            # Admin sub-tab 3: Danger Zone
            # =============================================================
            with admin_tabs[3]:

                # -- Card 1: Database reset -----------------------------------
                with components.card():
                    components.card_header(
                        "Database reset",
                        "Clears Postgres tables, Qdrant vectors, and/or artifact files. "
                        "This cannot be undone.",
                    )
                    st.warning(
                        "This is destructive. Clears Postgres tables, Qdrant vectors, "
                        "and/or artifact files so you can re-import from scratch. "
                        "**Existing runs, documents, and chunks will be permanently lost.**"
                    )
                    confirm = st.text_input("Type RESET to confirm", value="", key="adm_db_reset_confirm")
                    col1, col2, col3 = st.columns(theme.COL_THIRDS)
                    with col1:
                        do_pg = st.checkbox("Reset Postgres", value=True, key="adm_db_reset_pg")
                    with col2:
                        do_qd = st.checkbox("Clear Qdrant", value=True, key="adm_db_reset_qdrant")
                    with col3:
                        do_art = st.checkbox("Clear artifacts", value=False, key="adm_db_reset_artifacts")

                    if components.danger_button(
                        "Reset database -- this cannot be undone",
                        key="adm_db_reset_btn",
                        disabled=(confirm.strip().upper() != "RESET"),
                    ):
                        with st.spinner("Resetting..."):
                            resp, data = _request_json_diag(
                                label="admin db reset",
                                method="POST",
                                url=f"{api}/admin/db/reset",
                                headers=admin_headers,
                                json_body={
                                    "confirm": confirm,
                                    "postgres": bool(do_pg),
                                    "qdrant": bool(do_qd),
                                    "artifacts": bool(do_art),
                                },
                                timeout_s=120.0,
                            )
                        if int(resp.status_code) < 400:
                            st.success("Database has been reset. All data has been removed.")
                        else:
                            st.error(f"Reset failed ({resp.status_code})")
                        components.detail_expander("Details (JSON)", data=data)

                # -- Card 2: Restore stock config -----------------------------
                with components.card():
                    components.card_header(
                        "Restore stock config",
                        "Reset pipeline.yaml and/or models.yaml to the shipped defaults. "
                        "Any custom cleanup rules or model overrides will be lost.",
                    )
                    st.info(
                        "This overwrites the live configuration files with the stock "
                        "`.example` copies that shipped with the repo. Useful when "
                        "bad edits break the pipeline or before committing to Git."
                    )
                    rc1, rc2 = st.columns(2)
                    with rc1:
                        restore_pipe = st.checkbox("Restore pipeline.yaml", value=True, key="adm_restore_pipeline")
                    with rc2:
                        restore_models = st.checkbox("Restore models.yaml", value=True, key="adm_restore_models")
                    restore_confirm = st.text_input("Type RESTORE to confirm", value="", key="adm_restore_confirm")
                    if components.danger_button(
                        "Restore stock config",
                        key="adm_restore_stock_btn",
                        disabled=(restore_confirm.strip().upper() != "RESTORE"),
                    ):
                        with st.spinner("Restoring stock configuration..."):
                            resp, data = _request_json_diag(
                                label="admin restore stock config",
                                method="POST",
                                url=f"{api}/admin/config/restore-stock",
                                headers=admin_headers,
                                json_body={
                                    "confirm": restore_confirm,
                                    "pipeline": bool(restore_pipe),
                                    "models": bool(restore_models),
                                },
                                timeout_s=30.0,
                            )
                        if int(resp.status_code) < 400:
                            restored_files = (data or {}).get("restored", [])
                            st.success(f"Stock config restored: {', '.join(restored_files) if restored_files else 'none'}")
                        else:
                            st.error(f"Restore failed ({resp.status_code})")
                        components.detail_expander("Details (JSON)", data=data)

                # -- Card 3: Corpus import ------------------------------------
                with components.card():
                    components.card_header(
                        "Collection import",
                        "Upload a previously exported collection ZIP to restore or migrate data.",
                    )
                    corpus_scope_adm = (corpus_id or "").strip() or "default"
                    imp = st.file_uploader("Choose a ZIP file", type=["zip"], key="adm_corpus_import_zip")
                    if components.secondary_button("Import collection", disabled=imp is None, key="adm_import_btn"):
                        if imp is None:
                            st.warning("Pick a ZIP file first.")
                        else:
                            files = {"file": (imp.name, imp.getvalue(), "application/zip")}
                            imp_data = {
                                "tenant_id": tenant_id,
                                "project_id": project_id,
                                "is_finalized": json.dumps(True),
                                "is_sensitive": json.dumps(True),
                            }
                            with st.spinner("Importing collection \u2014 this may take several minutes for large archives..."):
                                try:
                                    with httpx.Client(timeout=600.0) as client:
                                        start = time.perf_counter()
                                        resp = client.post(
                                            f"{api}/admin/corpora/{corpus_scope_adm}/import",
                                            headers=admin_headers,
                                            files=files,
                                            data=imp_data,
                                        )
                                        elapsed_ms = int((time.perf_counter() - start) * 1000)
                                        _diag_add(
                                            {
                                                "type": "http",
                                                "label": "admin corpus import",
                                                "method": "POST",
                                                "url": f"{api}/admin/corpora/{corpus_scope_adm}/import",
                                                "status": int(resp.status_code),
                                                "elapsed_ms": elapsed_ms,
                                            }
                                        )
                                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                                    _diag_add({"type": "error", "label": "admin corpus import timeout", "error": str(exc)})
                                    st.error("Corpus import timed out \u2014 the backend may still be processing. Check server logs.")
                                    resp = None
                            if resp is not None and resp.status_code >= 400:
                                st.error(f"Import failed ({resp.status_code}): {resp.text}")
                            elif resp is not None:
                                st.success("Import complete!")
                                components.detail_expander(
                                    "Details (JSON)",
                                    data=resp.json() if _is_json_response(resp) else None,
                                )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Best-effort capture of UI exceptions so users can download logs
        # even when the main UI fails to render.
        api = _base_url(os.environ.get("ATLAS_API_URL", "http://127.0.0.1:18080"))
        _diag_init()
        _diag_ensure_session_started(api)
        _diag_add(
            {
                "type": "ui_exception",
                "error": repr(e),
                "traceback": traceback.format_exc(),
            }
        )
        st.error("The UI hit an unexpected error. Diagnostics were captured; download them from the sidebar.")
        with st.sidebar:
            st.header("Diagnostics")
            st.download_button(
                "Download logs (json)",
                data=_diag_bundle(api),
                file_name="atlas_ui_diagnostics.json",
                mime="application/json",
                use_container_width=True,
                key="crash_diag_download",
            )
