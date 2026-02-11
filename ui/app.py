from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import httpx
import streamlit as st


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


def _status_badge(label: str, *, ok: bool, detail: str = "") -> None:
    if ok:
        st.success(f"{label}: OK")
    else:
        st.error(f"{label}: Not OK")
    if detail:
        st.caption(detail)


def _summarize_ingest(resp_json: dict[str, Any] | None) -> tuple[str, str]:
    if not resp_json:
        return "Ingest completed", ""
    ok = bool(resp_json.get("ok"))
    chunks = int(resp_json.get("chunks_upserted") or 0)
    doc_id = str(resp_json.get("doc_id") or "")
    if not ok:
        return "Ingest failed", "Try again or check the service logs/history."
    if chunks <= 0:
        return "Uploaded — 0 chunks indexed", (
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


def _diag_init() -> None:
    st.session_state.setdefault("diag_events", [])


def _diag_add(event: dict[str, Any]) -> None:
    _diag_init()
    event["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st.session_state["diag_events"].append(event)
    st.session_state["diag_events"] = st.session_state["diag_events"][-200:]

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
    return t[:max_len] + "…"


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
    st.title("Project Atlas")
    st.caption("Operator Console")

    with st.sidebar:
        st.header("Connection")
        api_url = st.text_input("API URL", value=os.environ.get("ATLAS_API_URL", "http://127.0.0.1:18080"))

        st.session_state.setdefault("admin_token", _default_admin_token())
        admin_token = st.text_input(
            "Admin Token (for /admin/*)",
            type="password",
            key="admin_token",
        )

        with st.expander("Advanced", expanded=False):
            tenant_id = st.text_input("Workspace / Tenant", value=os.environ.get("ATLAS_DEFAULT_TENANT_ID", "local"))
            project_id = st.text_input("Project", value=os.environ.get("ATLAS_DEFAULT_PROJECT_ID", "default"))
            corpus_id = st.text_input("Corpus", value=os.environ.get("ATLAS_DEFAULT_CORPUS_ID", "default"))

        api = _base_url(api_url)
        admin_headers = _admin_headers(admin_token)

        st.divider()
        with st.expander("Diagnostics", expanded=False):
            st.caption("Records recent API calls and exceptions from this UI session.")
            _diag_init()
            _diag_ensure_session_started(api)

            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                if st.button("Show", use_container_width=True):
                    st.session_state["show_diagnostics"] = True
            with c2:
                if st.button("Hide", use_container_width=True):
                    st.session_state["show_diagnostics"] = False
            with c3:
                if st.button("Clear", use_container_width=True):
                    st.session_state["diag_events"] = []
                    st.session_state["diag_session_started"] = False
                    _diag_ensure_session_started(api)

            st.download_button(
                "Download logs (json)",
                data=_diag_bundle(api),
                file_name="atlas_ui_diagnostics.json",
                mime="application/json",
                use_container_width=True,
            )

        st.divider()
        st.subheader("Status")
        if st.button("Test connection", use_container_width=True):
            with st.spinner("Checking API..."):
                h_resp, h_json = _request_json_diag(label="health", method="GET", url=f"{api}/health")
            st.session_state["health_status"] = (h_resp.status_code, h_json, h_resp.text)

            if admin_headers:
                with st.spinner("Checking admin access..."):
                    a_resp, a_json = _request_json_diag(
                        label="admin effective config",
                        method="GET", url=f"{api}/admin/config/effective", headers=admin_headers
                    )
                st.session_state["admin_status"] = (a_resp.status_code, a_json, a_resp.text)

        if admin_headers:
            st.divider()
            with st.expander("Danger zone", expanded=False):
                st.caption("Clears Postgres + Qdrant so you can re-import from scratch.")
                st.warning("This is destructive. Existing runs/docs/chunks will be lost.")

                confirm = st.text_input("Type RESET to confirm", value="", key="db_reset_confirm")
                c1, c2, c3 = st.columns([1, 1, 1])
                with c1:
                    do_pg = st.checkbox("Reset Postgres", value=True, key="db_reset_pg")
                with c2:
                    do_qd = st.checkbox("Clear Qdrant", value=True, key="db_reset_qdrant")
                with c3:
                    do_art = st.checkbox("Clear artifacts", value=False, key="db_reset_artifacts")

                if st.button("Reset DB", use_container_width=True, key="db_reset_btn"):
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
                        st.success("Reset complete")
                    else:
                        st.error(f"Reset failed ({resp.status_code})")
                    if data is not None:
                        st.json(data)
                    else:
                        st.code(resp.text)

        hs = st.session_state.get("health_status")
        if hs:
            code, h_json, raw = hs
            _status_badge("API", ok=int(code) < 400, detail=("" if int(code) < 400 else raw))
            if isinstance(h_json, dict):
                st.caption(f"env={h_json.get('env')} status={h_json.get('status')}")

        if not admin_headers:
            st.info("Viewer mode: Admin features are disabled.")
        else:
            ads = st.session_state.get("admin_status")
            if ads:
                code, _, raw = ads
                _status_badge("Admin", ok=int(code) < 400, detail=("" if int(code) < 400 else raw))

    tabs = st.tabs(["Upload", "Search", "History", "Review (HITL)", "Versions & Export"])  # type: ignore[arg-type]

    # --- Upload ---
    with tabs[0]:
        st.subheader("Upload a document")
        st.caption("Upload a file to index it for search.")

        uploaded = st.file_uploader("File", type=None)
        default_name = ""
        if uploaded is not None:
            default_name = os.path.splitext(uploaded.name)[0]

        col1, col2 = st.columns(2)
        doc_name = col1.text_input(
            "Document name",
            value=st.session_state.get("last_doc_name", default_name),
            key="upload_file_doc_name",
        )
        doc_version = col2.text_input(
            "Version",
            value=st.session_state.get("last_doc_version", "1"),
            key="upload_file_doc_version",
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
            st.caption(f"Document ID: {doc_id}")

        with st.expander("Advanced upload options", expanded=False):
            source_mime_type = st.text_input(
                "MIME type override (optional)",
                value="",
                key="upload_file_mime_override",
            )
            col4, col5 = st.columns(2)
            is_finalized = col4.checkbox(
                "Searchable",
                value=bool(st.session_state.get("last_is_finalized", True)),
                help="If enabled, this document can appear in search results.",
                key="upload_file_is_finalized",
            )
            is_sensitive = col5.checkbox(
                "Sensitive",
                value=bool(st.session_state.get("last_is_sensitive", True)),
                help="If enabled, the pipeline may route content to human review depending on thresholds.",
                key="upload_file_is_sensitive",
            )

        can_upload = uploaded is not None and bool((doc_name or "").strip())
        if st.button("Upload & index", disabled=not can_upload, use_container_width=True):
            if uploaded is None:
                st.warning("Pick a file first")
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

                with st.spinner("Uploading and indexing..."):
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
                    st.error(f"{resp.status_code}: {resp.text}")
                    st.stop()

                payload = resp.json() if _is_json_response(resp) else {}
                title, detail = _summarize_ingest(payload if isinstance(payload, dict) else None)
                st.success(title)
                if detail:
                    st.caption(detail)

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
                                st.info(f"Latest run: #{int(match['id'])}")
                                st.session_state["last_run_id"] = int(match["id"])
                    except Exception:
                        pass

                with st.expander("Details (raw)", expanded=False):
                    _render_response(resp)

        st.divider()
        st.subheader("Paste text")
        col_t1, col_t2 = st.columns(2)
        text_doc_name = col_t1.text_input(
            "Document name",
            value=st.session_state.get("last_text_doc_name", "Quick note"),
            key="upload_text_doc_name",
        )
        text_doc_version = col_t2.text_input(
            "Version",
            value=st.session_state.get("last_text_doc_version", "1"),
            key="upload_text_doc_version",
        )
        text_doc_id = _stable_doc_id_from_name(
            f"{(corpus_id or '').strip()}:{((text_doc_name or '').strip() or 'Quick note')}"
        )
        st.caption(f"Document ID: {text_doc_id}")
        text = st.text_area(
            "text",
            height=180,
            value="# Hello\n\nPaste content here.",
            key="upload_text_body",
        )
        text_mime = st.selectbox(
            "source_mime_type",
            options=["text/plain", "text/markdown"],
            index=1,
            key="upload_text_mime",
        )

        if st.button("Index text", use_container_width=True):
            st.session_state["last_text_doc_name"] = text_doc_name
            st.session_state["last_text_doc_version"] = text_doc_version
            payload = {
                "doc_id": text_doc_id,
                "doc_version": text_doc_version,
                "text": text,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "corpus_id": corpus_id,
                "is_finalized": bool(is_finalized),
                "is_sensitive": bool(is_sensitive),
                "source_mime_type": text_mime,
                "metadata": {},
            }
            with st.spinner("Indexing..."):
                resp, data = _request_json_diag(
                    label="ingest/text",
                    method="POST",
                    url=f"{api}/rag/ingest/text",
                    json_body=payload,
                    timeout_s=120.0,
                )
            if resp.status_code >= 400:
                st.error(f"{resp.status_code}: {resp.text}")
                st.stop()

            title, detail = _summarize_ingest(data if isinstance(data, dict) else None)
            st.success(title)
            if detail:
                st.caption(detail)
            with st.expander("Details (raw)", expanded=False):
                if data is not None:
                    st.json(data)
                else:
                    _render_response(resp)

    # --- Search ---
    with tabs[1]:
        st.subheader("Search")
        st.caption("Ask a question and see matching snippets.")
        col1, col2 = st.columns(2)
        query = col1.text_input("Question", value=st.session_state.get("last_query", ""))
        top_k = col2.number_input("top_k", min_value=1, max_value=50, value=5)

        query_s = (query or "").strip()
        if st.button("Search", disabled=not bool(query_s), use_container_width=True):
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
                st.error(f"{resp.status_code}: {resp.text}")
                _render_response(resp)
            else:
                hits = (data or {}).get("hits") or []
                st.caption(f"hits: {len(hits)}")
                for i, h in enumerate(hits, start=1):
                    payload_h = h.get("payload") or {}
                    doc_id_h = h.get("doc_id")
                    doc_ver_h = payload_h.get("doc_version")
                    filename_h = payload_h.get("source_filename") or ""
                    score = h.get("score")
                    snippet = (h.get("text") or "").strip().replace("\n", " ")
                    if len(snippet) > 280:
                        snippet = snippet[:280] + "…"
                    title = f"#{i}  {filename_h or doc_id_h}  (v{doc_ver_h})  score={score}"
                    with st.expander(title, expanded=(i == 1)):
                        st.write(snippet)
                        cols = st.columns(4)
                        cols[0].metric("Version", str(doc_ver_h))
                        cols[1].metric("Chunk", str(h.get("chunk_index")))
                        cols[2].metric("Score", f"{float(score or 0.0):.3f}")
                        cols[3].metric("Doc", str(doc_id_h))
                        with st.expander("Details (raw)", expanded=False):
                            st.json(h)

    # --- History ---
    with tabs[2]:
        st.subheader("Processing history")
        st.caption("See what the system has processed and any errors.")
        if not admin_headers:
            st.info("Admin token required for runs.")
        else:
            col1, col2 = st.columns(2)
            limit = col1.number_input("limit", min_value=1, max_value=500, value=100, key="runs_limit")
            refresh = col2.button("Refresh")

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
                st.dataframe(rows, use_container_width=True, hide_index=True)

                run_ids: list[int] = []
                for r in runs_list:
                    rid = r.get("id")
                    if rid is None:
                        continue
                    try:
                        run_ids.append(int(rid))
                    except Exception:
                        continue

                if not run_ids:
                    st.caption("No numeric run IDs available.")
                else:
                    default_run = st.session_state.get("last_run_id")
                    if isinstance(default_run, int) and default_run in run_ids:
                        default_idx = run_ids.index(default_run)
                    else:
                        default_idx = 0

                    selected_run_id = st.selectbox("Open run", options=run_ids, index=default_idx)

                    if st.button("Load details", use_container_width=True):
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
                            st.subheader("Run")
                            st.json(d1)

                        st.subheader("Steps")
                        if r2.status_code >= 400:
                            st.error(f"{r2.status_code}: {r2.text}")
                        else:
                            st.dataframe(d2 or [], use_container_width=True, hide_index=True)

                        st.subheader("Files & outputs")
                        if r3.status_code >= 400:
                            st.error(f"{r3.status_code}: {r3.text}")
                        else:
                            st.dataframe(d3 or [], use_container_width=True, hide_index=True)
            else:
                st.caption("No runs returned.")

    # --- Review (HITL) ---
    with tabs[3]:
        st.subheader("Review queue")
        st.caption("If a document needs review, it appears here.")
        if not admin_headers:
            st.info("Admin token required for HITL.")
        else:
            col1, col2, col3 = st.columns(3)
            status = col1.selectbox("status filter", options=["", "pending", "in_progress", "completed", "skipped", "rejected"], index=0)
            limit = col2.number_input("limit", min_value=1, max_value=500, value=100, key="hitl_limit")
            assigned_to = col3.text_input("assigned_to (claim next)", value="operator")

            if st.button("Refresh queue", use_container_width=True):
                params: dict[str, Any] = {"limit": int(limit)}
                if status:
                    params["status"] = status
                with st.spinner("Loading tasks..."):
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

            tasks = st.session_state.get("hitl_tasks", [])
            if tasks:
                rows = []
                for t in tasks:
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
                st.dataframe(rows, use_container_width=True, hide_index=True)

            st.divider()
            cols = st.columns(2)
            if cols[0].button("Claim next task", use_container_width=True):
                resp, data = _request_json_diag(
                    label="admin hitl next",
                    method="POST",
                    url=f"{api}/admin/hitl/tasks/next",
                    headers=admin_headers,
                    params={"assigned_to": assigned_to},
                )
                if resp.status_code >= 400:
                    st.error(f"{resp.status_code}: {resp.text}")
                else:
                    st.session_state["hitl_current"] = data

            if cols[1].button("Clear current", use_container_width=True):
                st.session_state.pop("hitl_current", None)

            current = st.session_state.get("hitl_current")
            if current:
                st.subheader(f"Review task #{int(current['id'])}")
                st.caption(f"Document: {current.get('doc_id')}  v{current.get('doc_version')}  •  status={current.get('status')}")

                left, right = st.columns(2)
                with left:
                    st.markdown("**Before**")
                    st.text_area("before_md", height=260, value=current.get("before_md") or "", disabled=True)
                    with st.expander("Before (preview)", expanded=False):
                        st.markdown(current.get("before_md") or "")

                with right:
                    st.markdown("**After**")
                    after_md = st.text_area("after_md", height=260, value=current.get("after_md") or "")
                    with st.expander("After (preview)", expanded=False):
                        st.markdown(after_md or "")

                reason = st.text_input("Reason", value="review")

                c1, c2, c3 = st.columns(3)
                if c1.button("Save review", use_container_width=True):
                    tid = int(current["id"])
                    payload = {"after_md": after_md, "reason_for_edit": reason}
                    with st.spinner("Saving..."):
                        resp, data = _request_json_diag(
                            label="admin hitl complete",
                            method="POST",
                            url=f"{api}/admin/hitl/tasks/{tid}/complete",
                            headers=admin_headers,
                            json_body=payload,
                            timeout_s=60.0,
                        )
                    if resp.status_code >= 400:
                        st.error(f"{resp.status_code}: {resp.text}")
                    else:
                        st.success("Review saved")
                        st.session_state["hitl_current"] = data
                        with st.expander("Details (raw)", expanded=False):
                            st.json(data)

                if c2.button("Resume indexing", use_container_width=True):
                    tid = int(current["id"])
                    with st.spinner("Resuming..."):
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
                    if resp.status_code >= 400:
                        st.error(f"{resp.status_code}: {resp.text}")
                    else:
                        st.success("Indexing resumed")
                    with st.expander("Details (raw)", expanded=False):
                        _render_response(resp)

                if c3.button("Show raw task", use_container_width=True):
                    st.json(current)

    # --- Versions & Export ---
    with tabs[4]:
        st.subheader("Versions and export")
        st.caption("Switch which version is searchable, and export a package.")
        if not admin_headers:
            st.info("Admin token required for docs/export.")
        else:
            col1, col2 = st.columns(2)
            doc_id = col1.text_input("Document ID", value=st.session_state.get("last_doc_id", ""))
            new_version = col2.text_input("Set searchable version", value="1")
            doc_id_s = (doc_id or "").strip()

            c1, c2 = st.columns(2)
            if c1.button(
                "Show current searchable version", use_container_width=True, disabled=not bool(doc_id_s)
            ):
                resp, data = _request_json_diag(
                    label="admin active version get",
                    method="GET",
                    url=f"{api}/admin/docs/{doc_id_s}/active-version",
                    headers=admin_headers,
                    params={"tenant_id": tenant_id, "project_id": project_id, "corpus_id": corpus_id},
                )
                if resp.status_code >= 400:
                    st.error(f"{resp.status_code}: {resp.text}")
                if data is not None:
                    st.json(data)
                else:
                    _render_response(resp)

            if c2.button("Set searchable version", use_container_width=True, disabled=not bool(doc_id_s)):
                payload = {
                    "doc_version": new_version,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "corpus_id": corpus_id,
                }
                resp, data = _request_json_diag(
                    label="admin active version set",
                    method="POST",
                    url=f"{api}/admin/docs/{doc_id_s}/active-version",
                    headers=admin_headers,
                    json_body=payload,
                )
                if resp.status_code >= 400:
                    st.error(f"{resp.status_code}: {resp.text}")
                if data is not None:
                    st.success("Updated")
                    st.json(data)
                else:
                    _render_response(resp)

            st.divider()
            st.subheader("Export package")
            exp_version = st.text_input("Version to export", value="1")
            if st.button("Generate export ZIP", use_container_width=True, disabled=not bool(doc_id_s)):
                params = {
                    "doc_version": exp_version,
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "corpus_id": corpus_id,
                }
                with st.spinner("Generating export..."):
                    with httpx.Client(timeout=120.0) as client:
                        start = time.perf_counter()
                        resp = client.get(
                            f"{api}/admin/docs/{doc_id_s}/export",
                            headers=admin_headers,
                            params=params,
                        )
                        elapsed_ms = int((time.perf_counter() - start) * 1000)
                        _diag_add(
                            {
                                "type": "http",
                                "label": "admin export",
                                "method": "GET",
                                "url": f"{api}/admin/docs/{doc_id_s}/export",
                                "status": int(resp.status_code),
                                "elapsed_ms": elapsed_ms,
                            }
                        )
                if resp.status_code >= 400:
                    st.error(f"{resp.status_code}: {resp.text}")
                else:
                    zip_bytes = resp.content
                    st.success(f"Downloaded {len(zip_bytes)} bytes")
                    st.download_button(
                        label="Download ZIP",
                        data=zip_bytes,
                        file_name=f"export_{doc_id_s}_v{exp_version}.zip",
                        mime="application/zip",
                    )

            st.divider()
            st.subheader("Corpus export/import")

            if st.button("Generate corpus export ZIP", use_container_width=True):
                params = {"tenant_id": tenant_id, "project_id": project_id, "max_docs": 200}
                with st.spinner("Generating corpus export..."):
                    with httpx.Client(timeout=300.0) as client:
                        start = time.perf_counter()
                        resp = client.get(
                            f"{api}/admin/corpora/{(corpus_id or '').strip() or 'default'}/export",
                            headers=admin_headers,
                            params=params,
                        )
                        elapsed_ms = int((time.perf_counter() - start) * 1000)
                        _diag_add(
                            {
                                "type": "http",
                                "label": "admin corpus export",
                                "method": "GET",
                                "url": f"{api}/admin/corpora/{(corpus_id or '').strip() or 'default'}/export",
                                "status": int(resp.status_code),
                                "elapsed_ms": elapsed_ms,
                            }
                        )
                if resp.status_code >= 400:
                    st.error(f"{resp.status_code}: {resp.text}")
                else:
                    zip_bytes = resp.content
                    st.success(f"Downloaded {len(zip_bytes)} bytes")
                    st.download_button(
                        label="Download corpus ZIP",
                        data=zip_bytes,
                        file_name=f"corpus_export_{(corpus_id or '').strip() or 'default'}.zip",
                        mime="application/zip",
                    )

            imp = st.file_uploader("Corpus import ZIP", type=["zip"], key="corpus_import_zip")
            if st.button("Import corpus ZIP", use_container_width=True, disabled=imp is None):
                if imp is None:
                    st.warning("Pick a ZIP first")
                else:
                    files = {"file": (imp.name, imp.getvalue(), "application/zip")}
                    data = {
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "is_finalized": json.dumps(True),
                        "is_sensitive": json.dumps(True),
                    }
                    with st.spinner("Importing corpus..."):
                        with httpx.Client(timeout=600.0) as client:
                            start = time.perf_counter()
                            resp = client.post(
                                f"{api}/admin/corpora/{(corpus_id or '').strip() or 'default'}/import",
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
                                    "url": f"{api}/admin/corpora/{(corpus_id or '').strip() or 'default'}/import",
                                    "status": int(resp.status_code),
                                    "elapsed_ms": elapsed_ms,
                                }
                            )
                    if resp.status_code >= 400:
                        st.error(f"{resp.status_code}: {resp.text}")
                    else:
                        try:
                            st.json(resp.json())
                        except Exception:
                            _render_response(resp)

    if bool(st.session_state.get("show_diagnostics")):
        st.divider()
        st.subheader("Diagnostics")
        _diag_init()
        events = list(st.session_state.get("diag_events") or [])
        if not events:
            st.caption("No diagnostics yet. Click 'Test connection' or run an action.")
        else:
            # Show newest first.
            events = list(reversed(events))
            rows = []
            for e in events[:50]:
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
            st.dataframe(rows, use_container_width=True, hide_index=True)


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
