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

def _safe_text(text: str, *, max_len: int = 2000) -> str:
    t = text or ""
    if len(t) <= max_len:
        return t
    return t[:max_len] + "..."


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

            tenant_options = [str(t.get("tenant_id") or "") for t in reg.get("tenants", []) if str(t.get("tenant_id") or "").strip()]
            if default_tenant not in tenant_options:
                tenant_options = [default_tenant, *tenant_options] if default_tenant else tenant_options
            if not tenant_options:
                tenant_options = [default_tenant]
            sel_tenant = st.selectbox(theme.LABEL_WORKSPACE, options=tenant_options, index=0, label_visibility="collapsed")

            corpus_options = [
                str(c.get("corpus_id") or "")
                for c in reg.get("corpora", [])
                if str(c.get("tenant_id") or "") == sel_tenant
                and str(c.get("corpus_id") or "").strip()
            ]
            if default_corpus not in corpus_options:
                corpus_options = [default_corpus, *corpus_options] if default_corpus else corpus_options
            if not corpus_options:
                corpus_options = [default_corpus]
            sel_corpus = st.selectbox(theme.LABEL_COLLECTION, options=corpus_options, index=0)

            sel_project = default_project
            for c in reg.get("corpora", []):
                if str(c.get("tenant_id") or "") == sel_tenant and str(c.get("corpus_id") or "") == sel_corpus:
                    sel_project = str(c.get("project_id") or default_project)
                    break

            st.session_state["scope_tenant_id"] = sel_tenant
            st.session_state["scope_project_id"] = sel_project
            st.session_state["scope_corpus_id"] = sel_corpus

            st.markdown(
                f'<div class="atlas-workspace-banner">'
                f'<strong>{sel_tenant}</strong> / <strong>{sel_corpus}</strong>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.session_state["scope_tenant_id"] = st.text_input(theme.LABEL_WORKSPACE, value=default_tenant)
            st.session_state["scope_project_id"] = st.text_input("Project", value=default_project)
            st.session_state["scope_corpus_id"] = st.text_input(theme.LABEL_COLLECTION, value=default_corpus)

        tenant_id = str(st.session_state.get("scope_tenant_id", default_tenant))
        project_id = str(st.session_state.get("scope_project_id", default_project))
        corpus_id = str(st.session_state.get("scope_corpus_id", default_corpus))

        # -- Status -----------------------------------------------------------
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
        with st.expander("Admin tools", expanded=False):
            components.admin_warning()

            if not admin_headers:
                components.auth_gate()
            else:
                # Project selector
                project_options = [
                    str(p.get("project_id") or "")
                    for p in reg.get("projects", [])
                    if str(p.get("tenant_id") or "") == tenant_id and str(p.get("project_id") or "").strip()
                ]
                if default_project not in project_options:
                    project_options = [default_project, *project_options] if default_project else project_options
                if not project_options:
                    project_options = [default_project]
                adm_project = st.selectbox("Project", options=project_options, index=0, key="admin_project_sel")
                if adm_project != project_id:
                    st.session_state["scope_project_id"] = adm_project

                if st.button("Refresh groups", key="scope_refresh_btn", use_container_width=True):
                    st.session_state.pop("group_registry", None)
                    st.rerun()

                with st.expander("Create workspace / project / collection", expanded=False):
                    create_kind = st.selectbox("Type", options=["Workspace", "Project", "Collection"], key="scope_create_kind")
                    new_id = st.text_input("ID", value="", key="scope_create_id")
                    new_name = st.text_input("Display name (optional)", value="", key="scope_create_name")
                    if st.button("Create", key="scope_create_btn", use_container_width=True):
                        kind = (create_kind or "").strip().lower()
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

            # Diagnostics
            with st.expander("Diagnostics", expanded=False):
                st.caption("Records recent API calls and exceptions from this UI session.")
                _diag_init()
                _diag_ensure_session_started(api)

                clicked = components.action_bar(
                    {"label": "Show", "key": "diag_show"},
                    {"label": "Hide", "key": "diag_hide"},
                    {"label": "Clear", "key": "diag_clear"},
                )
                if clicked[0]:
                    st.session_state["show_diagnostics"] = True
                if clicked[1]:
                    st.session_state["show_diagnostics"] = False
                if clicked[2]:
                    st.session_state["diag_events"] = []
                    st.session_state["diag_session_started"] = False
                    _diag_ensure_session_started(api)

                st.download_button(
                    "Download logs (JSON)",
                    data=_diag_bundle(api),
                    file_name="atlas_ui_diagnostics.json",
                    mime="application/json",
                    use_container_width=True,
                )

            # DB Reset (danger zone)
            if admin_headers:
                with components.danger_zone(
                    caption="Clears Postgres + Qdrant so you can re-import from scratch.",
                    warning="This is destructive. Existing runs/docs/chunks will be lost.",
                ):
                    confirm = st.text_input("Type RESET to confirm", value="", key="db_reset_confirm")
                    col1, col2, col3 = st.columns(theme.COL_THIRDS)
                    with col1:
                        do_pg = st.checkbox("Reset Postgres", value=True, key="db_reset_pg")
                    with col2:
                        do_qd = st.checkbox("Clear Qdrant", value=True, key="db_reset_qdrant")
                    with col3:
                        do_art = st.checkbox("Clear artifacts", value=False, key="db_reset_artifacts")

                    if st.button("Reset database -- this cannot be undone", use_container_width=True, key="db_reset_btn"):
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

    tabs = st.tabs([theme.TAB_HOME, theme.TAB_UPLOAD, theme.TAB_LIBRARY, theme.TAB_SEARCH, theme.TAB_REVIEW, theme.TAB_VERSIONS, theme.TAB_HISTORY])  # type: ignore[arg-type]

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
        _has_docs = bool(st.session_state.get("lib_docs"))
        _has_searched = bool(st.session_state.get("last_query"))
        _has_reviewed = bool(st.session_state.get("hitl_current"))

        with components.card(hero=True):
            components.card_header("Getting started", "Complete these steps to set up your collection.")
            components.checklist_item(_connected, "1.", "Connect to Atlas", "Use the sidebar to set your URL and test the connection.")
            components.checklist_item(True, "2.", "Choose a workspace", "Pick where your documents will live.")
            components.checklist_item(_has_docs, "3.", "Upload your first document", "Go to the Upload tab and add a file or paste text.")
            components.checklist_item(_has_searched, "4.", "Search your collection", "Head to Search and try a question.")
            components.checklist_item(_has_reviewed, "5.", "Review flagged content", "If any documents need review, the Review tab will show them.")

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
            options=["Upload file", "Paste text"],
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
                do_upload = components.primary_button("Upload and make searchable", disabled=not can_upload, key="upload_file_btn")

            else:  # Paste Text
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

                do_upload = False
                do_text = components.primary_button("Upload and make searchable", key="upload_text_btn")

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
                text_doc_version = st.text_input(
                    "Version",
                    value=st.session_state.get("last_text_doc_version", "1"),
                    key="upload_text_doc_version",
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

                if resp.status_code >= 400:
                    st.error(f"Upload failed ({resp.status_code}): {resp.text}")
                    st.stop()

                payload = resp.json() if _is_json_response(resp) else {}
                title, detail = _summarize_ingest(payload if isinstance(payload, dict) else None)
                st.success(f"Done! {title}")
                components.detail_expander("Details (JSON)", data=payload)

                if admin_headers:
                    try:
                        r_resp, r_data = _request_json_diag(
                            label="admin runs",
                            method="GET",
                            url=f"{api}/admin/runs",
                            headers=admin_headers,
                            params={"limit": 50},
                        )
                        if r_resp.status_code < 400 and isinstance(r_data, list):
                            match = None
                            for r in r_data:
                                if str(r.get("doc_id")) == str(doc_id) and str(r.get("doc_version")) == str(doc_version):
                                    match = r
                                    break
                            if match and match.get("id") is not None:
                                st.info(f"Processing run: #{int(match['id'])} -- check History for status.")
                                st.session_state["last_run_id"] = int(match["id"])
                    except Exception:
                        pass

        if upload_mode == "Paste text" and do_text:
            is_finalized = bool(st.session_state.get("last_is_finalized", True))
            is_sensitive = bool(st.session_state.get("last_is_sensitive", True))
            st.session_state["last_text_doc_name"] = text_doc_name
            st.session_state["last_text_doc_version"] = text_doc_version
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
                    timeout_s=120.0,
                )
            if resp.status_code >= 400:
                st.error(f"Upload failed ({resp.status_code}): {resp.text}")
                st.stop()

            title, detail = _summarize_ingest(data if isinstance(data, dict) else None)
            st.success(f"Done! {title}")
            components.detail_expander("Details (JSON)", data=data)

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
            )
            qr_col1, qr_col2 = st.columns([3, 1])
            with qr_col2:
                top_k = st.number_input("Max results", min_value=1, max_value=50, value=5, label_visibility="collapsed")

            query_s = (query or "").strip()
            do_search = components.primary_button("Search", disabled=not bool(query_s), key="search_btn")

        # -- Card 2: Results ---------------------------------------------------
        if do_search:
            st.session_state["last_query"] = query_s
            payload = {
                "query": query_s,
                "top_k": int(top_k),
                "tenant_id": tenant_id,
                "project_id": project_id,
                "corpus_id": corpus_id,
            }
            with st.spinner("Searching..."):
                resp, data = _request_json_diag(
                    label="rag/search",
                    method="POST",
                    url=f"{api}/rag/search",
                    json_body=payload,
                    timeout_s=60.0,
                )
            if resp.status_code >= 400:
                st.error(f"Search failed ({resp.status_code}): {resp.text}")
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
                        score = h.get("score")
                        snippet = (h.get("text") or "").strip().replace("\n", " ")
                        if len(snippet) > theme.MAX_SNIPPET_CHARS:
                            snippet = snippet[:theme.MAX_SNIPPET_CHARS] + "..."
                        card_title = f"#{i} - {filename_h or doc_id_h}"
                        metrics = {
                            "Version": str(doc_ver_h),
                            "Chunk": str(h.get("chunk_index")),
                            "Score": f"{float(score or 0.0):.3f}",
                            "Doc": str(doc_id_h),
                        }
                        components.search_hit_card(i, card_title, snippet, metrics, h)
    
    # =====================================================================
    # HISTORY
    # =====================================================================
    with tabs[6]:
        components.tab_header(
            "History",
            theme.COPY_HISTORY,
            workspace=tenant_id,
            collection=corpus_id,
            project=project_id,
        )

        if not admin_headers:
            components.auth_gate("Admin token required to view processing history.")
        else:
            # -- Card 1: Runs table -------------------------------------------
            with components.card():
                components.card_header("Processing runs", "Recent ingest and pipeline runs for this workspace.")
                col1, col2 = st.columns(theme.COL_HALF)
                limit = col1.number_input("Max rows", min_value=1, max_value=500, value=100, key="runs_limit")
                refresh = col2.button("Refresh", key="runs_refresh_btn")

                if refresh or "runs_cache" not in st.session_state:
                    resp, data = _request_json_diag(
                        label="admin runs",
                        method="GET",
                        url=f"{api}/admin/runs",
                        headers=admin_headers,
                        params={"limit": int(limit)},
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

            # -- Card 2: Run details (appears after selection) ----------------
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
                    with components.card():
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
                    params={"limit": 200, "status": "pending"},
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
                        st.error(f"Could not claim task ({resp.status_code}): {resp.text}")
                    else:
                        st.session_state["hitl_current"] = data
                        st.rerun()

            # -- Card 2: Current review task ----------------------------------
            if current:
                with components.card(elevated=True):
                    components.card_header(
                        f"Reviewing: {current.get('doc_id')}",
                        f"v{current.get('doc_version')} -- priority {current.get('priority_score', '?')}",
                    )

                    left, right = st.columns(theme.COL_HALF)
                    with left:
                        st.markdown("**Before**")
                        st.text_area("Before", height=theme.TEXT_AREA_MD, value=current.get("before_md") or "", disabled=True, label_visibility="collapsed")
                    with right:
                        st.markdown("**After** (edit below)")
                        after_md = st.text_area("After", height=theme.TEXT_AREA_MD, value=current.get("after_md") or "", label_visibility="collapsed")

                    reason = st.text_input("Reason for edit", value="review", key="hitl_review_reason")

                    # One primary, secondary, link-style
                    act_col1, act_col2, act_col3 = st.columns(3)
                    with act_col1:
                        do_accept = components.primary_button("Save review", key="hitl_accept")
                    with act_col2:
                        do_resume = components.secondary_button("Save and resume indexing", key="hitl_resume_btn")
                    with act_col3:
                        do_skip = components.secondary_button("Skip", key="hitl_skip")

                if do_accept:
                    tid = int(current["id"])
                    payload = {"after_md": after_md, "reason_for_edit": reason}
                    with st.spinner("Saving review..."):
                        resp, data = _request_json_diag(
                            label="admin hitl complete",
                            method="POST",
                            url=f"{api}/admin/hitl/tasks/{tid}/complete",
                            headers=admin_headers,
                            json_body=payload,
                            timeout_s=60.0,
                        )
                    if resp.status_code >= 400:
                        st.error(f"Save failed ({resp.status_code}): {resp.text}")
                    else:
                        st.success("Review saved! Loading next...")
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
                    st.session_state.pop("hitl_current", None)
                    st.rerun()

                if do_resume:
                    tid = int(current["id"])
                    payload = {"after_md": after_md, "reason_for_edit": reason}
                    with st.spinner("Saving and resuming indexing..."):
                        save_resp, _ = _request_json_diag(
                            label="admin hitl complete",
                            method="POST",
                            url=f"{api}/admin/hitl/tasks/{tid}/complete",
                            headers=admin_headers,
                            json_body=payload,
                            timeout_s=60.0,
                        )
                        if save_resp.status_code < 400:
                            with httpx.Client(timeout=120.0) as client:
                                start = time.perf_counter()
                                resp = client.post(f"{api}/admin/hitl/tasks/{tid}/resume", headers=admin_headers)
                                elapsed_ms = int((time.perf_counter() - start) * 1000)
                                _diag_add(
                                    {
                                        "type": "http",
                                        "label": "admin hitl resume",
                                        "method": "POST",
                                        "url": f"{api}/admin/hitl/tasks/{tid}/resume",
                                        "status": int(resp.status_code),
                                        "elapsed_ms": elapsed_ms,
                                    }
                                )
                    if save_resp.status_code >= 400:
                        st.error(f"Save failed ({save_resp.status_code})")
                    elif resp.status_code >= 400:
                        st.error(f"Resume failed ({resp.status_code}): {resp.text}")
                    else:
                        st.success("Review saved and indexing resumed!")
                    st.session_state.pop("hitl_current", None)
                    st.session_state.pop("hitl_tasks", None)
                    st.rerun()

                components.detail_expander("Full task details (JSON)", data=current)

            # -- Card 3: Full queue (collapsed) -------------------------------
            with st.expander("Full review queue", expanded=False):
                fq_col1, fq_col2 = st.columns(theme.COL_HALF)
                status = fq_col1.selectbox("Status filter", options=["", "pending", "in_progress", "completed", "skipped", "rejected"], index=0)
                fq_limit = fq_col2.number_input("Max rows", min_value=1, max_value=500, value=100, key="hitl_limit")

                if st.button("Refresh queue", use_container_width=True, key="hitl_refresh_queue"):
                    params: dict[str, Any] = {"limit": int(fq_limit)}
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
    # VERSIONS & EXPORT
    # =====================================================================
    with tabs[5]:
        components.tab_header(
            "Versions & Export",
            theme.COPY_VERSIONS,
            workspace=tenant_id,
            collection=corpus_id,
            project=project_id,
        )

        if not admin_headers:
            components.auth_gate("Admin token required for version control and export features.")
        else:
            corpus_scope = (corpus_id or "").strip() or "default"

            # -- Card 1: Version control ---------------------------------------
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
                    vc_show = components.primary_button("Show current version", disabled=not bool(vc_doc_id_s), key="ver_show")
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

            # -- Card 2: Export ------------------------------------------------
            with components.card():
                components.card_header("Export", "Download documents or entire collections as portable packages.")

                se_scope = st.radio(
                    "What to export",
                    options=["Entire collection", "Single document"],
                    horizontal=True,
                    key="export_scope_radio",
                )

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

                if se_scope == "Single document":
                    exp_doc_id = st.text_input(
                        "Document ID",
                        value=st.session_state.get("last_doc_id", ""),
                        key="export_doc_id",
                    )
                    exp_version = st.text_input("Version", value="1", key="export_doc_version")
                    can_export = bool((exp_doc_id or "").strip())
                else:
                    exp_doc_id = ""
                    exp_version = "1"
                    can_export = True

                with st.expander("Advanced export options", expanded=False):
                    se_max_docs = int(st.number_input(
                        "Max documents",
                        min_value=1,
                        max_value=20000,
                        value=2000,
                        key="export_max_docs",
                    ))

                do_export = components.primary_button("Generate export", disabled=not can_export, key="export_go_btn")

                if do_export:
                    if se_scope == "Single document":
                        _did = (exp_doc_id or "").strip()
                        with st.spinner(f"Exporting {_did}..."):
                            with httpx.Client(timeout=120.0) as _ec:
                                _er = _ec.get(
                                    f"{api}/admin/docs/{_did}/export",
                                    headers=admin_headers,
                                    params={
                                        "doc_version": exp_version,
                                        "tenant_id": tenant_id,
                                        "project_id": project_id,
                                        "corpus_id": corpus_scope,
                                        "format": se_fmt_param,
                                    },
                                )
                        if _er.status_code >= 400:
                            st.error(f"Export failed ({_er.status_code}): {_er.text}")
                        else:
                            _etag = "lean" if se_fmt_param == "lean" else "export"
                            st.success(f"Ready! {len(_er.content):,} bytes")
                            st.download_button(
                                "Download ZIP",
                                data=_er.content,
                                file_name=f"atlas_{_etag}_{_did}.zip",
                                mime="application/zip",
                                key="export_dl_single",
                            )
                    else:
                        with st.spinner("Generating collection export..."):
                            with httpx.Client(timeout=300.0) as _ce_client:
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
                        if _ce_resp.status_code >= 400:
                            st.error(f"Export failed ({_ce_resp.status_code}): {_ce_resp.text}")
                        else:
                            _ce_tag = "lean" if se_fmt_param == "lean" else "export"
                            st.success(f"Ready! {len(_ce_resp.content):,} bytes")
                            st.download_button(
                                "Download collection ZIP",
                                data=_ce_resp.content,
                                file_name=f"atlas_corpus_{_ce_tag}_{corpus_scope}.zip",
                                mime="application/zip",
                                key="export_dl_corpus",
                            )

            # -- Card 3: Corpus import (admin) --------------------------------
            with components.admin_section("Admin -- Corpus import"):
                st.caption("Upload a previously exported collection ZIP to restore or migrate data.")
                imp = st.file_uploader("Choose a ZIP file", type=["zip"], key="corpus_import_zip")
                if components.secondary_button("Import collection", disabled=imp is None, key="export_import_btn"):
                    if imp is None:
                        st.warning("Pick a ZIP file first.")
                    else:
                        files = {"file": (imp.name, imp.getvalue(), "application/zip")}
                        data = {
                            "tenant_id": tenant_id,
                            "project_id": project_id,
                            "is_finalized": json.dumps(True),
                            "is_sensitive": json.dumps(True),
                        }
                        with st.spinner("Importing collection..."):
                            with httpx.Client(timeout=600.0) as client:
                                start = time.perf_counter()
                                resp = client.post(
                                    f"{api}/admin/corpora/{corpus_scope}/import",
                                    headers=admin_headers,
                                    files=files,
                                    data=data,
                                )
                                elapsed_ms = int((time.perf_counter() - start) * 1000)
                                _diag_add(
                                    {
                                        "type": "http",
                                        "label": "admin corpus import",
                                        "method": "POST",
                                        "url": f"{api}/admin/corpora/{corpus_scope}/import",
                                        "status": int(resp.status_code),
                                        "elapsed_ms": elapsed_ms,
                                    }
                                )
                        if resp.status_code >= 400:
                            st.error(f"Import failed ({resp.status_code}): {resp.text}")
                        else:
                            st.success("Import complete!")
                            components.detail_expander("Details (JSON)", data=resp.json() if _is_json_response(resp) else None)

    # =====================================================================
    # LIBRARY
    # =====================================================================
    with tabs[2]:
        import pandas as _pd  # local import - pandas is available via the atlas venv

        components.tab_header(
            "Library",
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
                components.empty_state(
                    "No documents in this collection yet.",
                    button_label="Go to Upload",
                    button_key="lib_go_upload",
                )
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
                                with st.spinner(f"Exporting {_did}..."):
                                    with httpx.Client(timeout=120.0) as _ec:
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
                                    st.error(f"{_er.status_code}: {_er.text}")
                                else:
                                    _etag = "lean" if lib_sel_fmt_param == "lean" else "export"
                                    st.download_button(
                                        "Download ZIP",
                                        data=_er.content,
                                        file_name=f"atlas_{_etag}_{_did}.zip",
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
                                                with httpx.Client(timeout=120.0) as _ec:
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
                                    file_name=f"atlas_{_etag}_multi_{len(selected_doc_ids)}docs.zip",
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
                                    with httpx.Client(timeout=30.0) as _dc:
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

    if bool(st.session_state.get("show_diagnostics")):
        st.divider()
        components.section_header("Diagnostics")
        _diag_init()
        events = list(st.session_state.get("diag_events") or [])
        if not events:
            st.caption("No diagnostics yet. Click 'Test connection' or run an action.")
        else:
            events = list(reversed(events))
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
