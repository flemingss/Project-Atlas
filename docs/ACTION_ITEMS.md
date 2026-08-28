# Action items — remaining work

**Status as of 2026-08-29 (end of day).** `main` is healthy (CI green). Open work is now tracked as **GitHub issues** — see the table below. This file remains the design/context backdrop; the issues are the actionable tracker.

## Open GitHub issues (the live tracker)

| Issue | Item | State |
|-------|------|-------|
| #64 | P0-01 intermittent full-suite failure | **Reproduced** — order-dependent test-isolation flake (NOT resource pressure). See issue for ranked hypotheses. |
| #65 | P1-02 Docling 2.123.1 upgrade | **Evaluated** — NO-GO on synthetic fixture (structural heading regression, recall unchanged). Needs a real-doc check. |
| #66 | P1-07 bulk VLM auto-resume | Open — product decision (recommend manual resume). |

## Completed 2026-08-29 (this session)

P0-02 (Docling broken-vs-absent), P0-03 (prod compose `ATLAS_ENV=prod`), P0-04 (RAG 502 `str(e)` leak), P1-01 (layout parser page cap), P1-03 (coverage gate → `atlas.vlm_ingest`), P1-04 (explicit ruff `select`), P1-05 (non-root container), P1-06 (thumbnails pagination + web caller). Also fixed EXE001 exec-bit cross-platform bug. Full suite: **759 passed / 0 failed**, coverage 89.67% (gate 80%).

| Doc | Role |
|-----|------|
| `TECHNICAL_DESIGN.md` | Roadmap and end-state (some checkboxes here are still open; some GitHub issues were closed without the work) |
| `ARCHITECTURE.md` | Current system shape |
| `docs/TECHNICAL_DEBT.md` | Confirmed defects and debt, with evidence |
| `docs/WORKLOG.md` | Session process log (what was verified, what was deferred) |
| `CHANGELOG.md` `[Unreleased]` | What already landed since `v0.8.0` |
| `.github/dependabot.yml` | Deferred dependency majors and why |

---

## Where to start (resume here tomorrow)

1. **P0-01 (#64)** — fix the order-dependent test-isolation flake (reproduced; ranked hypotheses in the issue). Highest value: it can redden CI at random.
2. **P1-02 (#65)** — run a **real prod-like document** through Docling 2.76.0 vs 2.123.1 before deciding; the synthetic-fixture regression may not generalize.
3. **P1-07 (#66)** — make the manual-vs-auto resume product call, then implement if warranted.
4. **P2** — product features (12D VLM quality audit, HITL strip-and-rejudge, parallel VLM, etc.) below.

The per-item detail sections are kept below for the open items; completed items (2026-08-29, listed above) are collapsed to their issue/commit references.

---

## P0 — correctness, safety, or CI reliability

### P0-01 — Intermittent full-suite failure → **issue #64**
- **Status:** **Reproduced 2026-08-29** (run 6 of 8). Root cause narrowed: it is an **order-dependent test-isolation flake**, NOT the resource-pressure import crash previously hypothesized.
- **Captured:** `tests/test_docling_ingest.py::test_rag_ingest_pdf_low_quality_returns_error_code` → `assert 502 == 200`. The test monkeypatches `parse_document_path` (Docling mocked), so it cannot be an import failure — shared state leaks between tests under full-suite ordering.
- **Action:** reproduce deterministically (pairwise / seeded orders), then add an autouse reset for the Settings/config singletons. Ranked hypotheses in #64.

### P0-02 — Docling failures reported as "not installed" → ✅ **DONE 2026-08-29**
`DoclingBrokenInstallError` now distinguished from `DoclingUnavailableError`; explicit backend fails loudly, `auto` logs a DEGRADED warning on broken install. Commit `584a657`.

### P0-03 — `ATLAS_ENV` defaults to `dev` in the production compose → ✅ **DONE 2026-08-29**
Base `docker-compose.yml` now defaults `ATLAS_ENV` to `prod`; dev flow sets `dev` via `.env`. Commit `a63fb44`.

### P0-04 — RAG 502 bodies leak `str(e)` → ✅ **DONE 2026-08-29**
Stable `_UPSTREAM_ERROR_DETAIL` returned; full exception logged server-side. Commit `590e61f`.

---

## P1 — scale, supply chain, and gates that would have caught real bugs

### P1-01 — Page-cap asymmetry between parsers → ✅ **DONE 2026-08-29**
Layout parser now preflights page count via PyMuPDF and refuses oversized PDFs with the same `DoclingLimitsError` / `DOC_PAGE_LIMIT_EXCEEDED` contract Docling uses, before buffering any page image. Commit `78cf18d`.

### P1-02 — Docling pin is 53 releases behind → **issue #65**
- **Status:** **Evaluated 2026-08-29** the valid way (clean `pip-compile` lock + throwaway image rebuild + `ingest_quality.py` gate). **NO-GO as-is.**
- **Result:** gate fired a *structural* regression — 2.123.1 stops promoting `Section N — …` paragraphs to `#` headings on the synthetic fixture (−2 headings, −1963 chars); **content recall unchanged** on both fixtures. Build succeeds cleanly.
- **Caveats:** may be fixture-shape specific (reportlab Heading2 is a weak signal) → run a **real prod-like doc** before deciding; a docling format option may restore promotion. Eval container needs `--user root` (2.123.1 reads `/root/.local/share/fonts`). Artifacts in `eval_fixtures/` (uncommitted).
- **Action:** real-doc A/B, then decide. Never upgrade Docling inside a running container. See `docs/TECHNICAL_DEBT.md` §7.

### P1-03 — Coverage gate covers only `atlas.pipeline` → ✅ **DONE 2026-08-29**
Added `--cov=atlas.vlm_ingest`; 80% fail-under gate holds (pipeline ~91%, vlm_ingest ~93%). Commit `f5b4644`.

### P1-04 — No explicit ruff `select` → ✅ **DONE 2026-08-29**
Pinned `select` = default base (E4/E7/E9/F) + exactly the debt-bearing codes named in the ignore list (selected individually so sibling rules like S101 are not pulled in). E501 stays off. Commit `f5b4644`.

### P1-05 — Container runs as root → ✅ **DONE 2026-08-29**
Runtime stage now `USER atlas` (uid 1000, matching dev host). `HOME`/`PIP_CACHE_DIR` point at root-owned baked caches read-only (no `chown -R`, so model layers stay cached). Devcontainer workspace keeps `user: root` override. Commit `6b38762`.

### P1-06 — `/thumbnails` is unpaginated → ✅ **DONE 2026-08-29**
Endpoint paginated (`limit`/`offset`, default 200 max 1000) returning `{pages,total,offset,limit,has_more}`. Web caller unwraps `.pages` with `limit=1000` to preserve current grid behavior; true incremental paging is a documented follow-up. Commit `786e6df`.

### P1-07 — Bulk VLM does not auto-resume after API restart → **issue #66**
- **Status:** Open. Durability landed (ledger + checkpoints + rehydrate); the *loop* still does not restart itself.
- **Action:** product decision — offer an operator "Resume processing" (UI already lists ledger sessions). Default should stay manual so a crash does not silently spend VLM tokens.

---

## P2 — product features still on the roadmap

### P2-01 — Phase 12D: automated VLM quality audit
- **Status:** **Open. GitHub #30 was closed as completed on 2026-03-04 without the feature.** Copilot PR #38 (`copilot/add-vlm-quality-audit`) implemented per-page status sync, then closed in favour of #37. There is no `quality_audit.py`, no `/vlm-audit` endpoint, no `pdf_parser.vlm_audit` config.
- **Source:** `TECHNICAL_DESIGN.md` Phase 12D (still 🔲).
- **Action:** reopen or replace #30 before any implementation. Do not treat the closed issue as done.

### P2-02 — HITL strip-and-rejudge (Layer 3A)
- **Status:** Open. Layers 1–2 of `PIPELINE_QUALITY_IMPROVEMENTS.md` are done.
- **Missing:** `POST /admin/hitl/tasks/{id}/strip-and-rejudge`, section checkboxes in Review, a re-judge path that bypasses refine, E2E coverage.
- **Action:** only worth doing if HITL volume on real corpus still justifies it. Measure first.

### P2-03 — Batch/parallel VLM page processing
- **Status:** Open. Sequential bulk loop today (`TECHNICAL_DESIGN.md` Phase 12E).
- **Constraint:** VLM spend and provider rate limits. Parallelism is a throughput feature, not a correctness one.
- **Action:** defer until a timed 2,000-page job shows wall-clock is the limiter rather than token cost.

### P2-04 — Cost/latency analysis: VLM vs Docling
- **Status:** Open. Partial timing exists (operator testing: ~17 s/page average on a 42-page doc; 2,000 pages ≈ 9–50 h).
- **Action:** run both parsers on the same representative docs and record $/page and s/page. Informs whether `backend: vision` is a default or a surgical tool.

### P2-05 — Dedicated ingest regression corpus
- **Status:** Partial. #15 was closed with *mocked* Docling tests (`parse_document_path` patched). `tests/test_docling_e2e.py` now runs the real converter against generated PDFs (quality floor, not exact string) and is skipped unless models are cached — so CI still does not exercise it. `scripts/ingest_quality.py` is the upgrade gate, not a corpus.
- **Action:** keep generated fixtures (do not commit production manuals). Optionally add a nightly/manual job that is *not* skipped, on an image that has the baked models.

### P2-06 — Sectional refinement context loss
- **Status:** Open. Foot-gun, not a bug in current defaults.
- **Location:** `refine_document_sectional` splits on token counts and refines independently.
- **Action:** sliding-window overlap or split-only-on-headers, when a real document shows a boundary hallucination. Documented in `docs/TECHNICAL_DEBT.md` §2A.

### P2-07 — Hybrid search / rerank
- **Status:** Explicitly deferred. Gate exists: `scripts/retrieval_eval.py` + `eval/retrieval_golden.example.json`. Open only if HitRate@10 < 0.90 on a stable corpus.
- **Action:** run the harness against the real corpus before writing any BM25/rerank code.

### P2-08 — Supersedes chains and grace-period hiding
- **Status:** Explicitly deferred. v1 rollback is doc_version granularity (`TECHNICAL_DESIGN.md` §7.4).
- **Action:** none until operator UX and retention requirements are explicit.

### P2-09 — Dify "push" / HITL hub
- **Status:** Optional experiment. Compose profile exists; Atlas does not rely on Dify. `TECHNICAL_DESIGN.md` still marks "Push to Dify" as a placeholder.
- **Action:** leave it. Do not build a Dify integration unless that experiment is revived on purpose.

---

## P3 — registered debt (do not expand; burn down when touching the area)

These are already *held* by CI/Dependabot. They are not incidents. Remove an ignore/exemption only when the findings are actually fixed.

### Lint / types
- **P3-01** — ruff ignore list in `pyproject.toml` (BLE001 ×47, S110 ×12, RUF059 ×16, …). Codes leave the list when fixed; never join it.
- **P3-02** — mypy `ignore_errors` on 11 modules (ingest OCR/layout, qdrant Filter unions, deterministic LLM, admin maintenance). Same rule.
- **P3-03** — `react-hooks/set-state-in-effect` off in `web/.eslintrc.cjs` (11 sites). Real fix is migrating those pages to React Query and `use-mobile` to `useSyncExternalStore`, then re-adopting eslint-plugin-react-hooks 7 *after* the V8 JIT crash is gone.

### Dependabot-deferred majors (`.github/dependabot.yml`)
- **P3-04** — ESLint 9+ flat config (+ `eslint-plugin-react-refresh` 0.5, which peer-requires it).
- **P3-05** — `eslint-plugin-react-hooks` 7 — React Compiler analysis triggers a flaky V8 crash during lint on Node 20 and 22. 4.6.2 is stable. Revisit when the plugin or V8 fixes it. Coupled with P3-03.
- **P3-06** — Tailwind 4 + `tailwind-merge` 3 (one migration).
- **P3-07** — TypeScript 7 — builds, but `typescript-eslint` does not support 7.0 (upstream #10940, needs ≥7.1). `tsconfig.json` is already migrated; bump is one line once the plugin lands.
- **P3-08** — React 19 (+ types). Framework major; full sweep.
- **P3-09** — `pdfjs-dist` 5+ — worker/version coupling with the editor PDF pane.
- **P3-10** — `react-resizable-panels` 4 — renamed exports and data-attribute contract.
- **P3-11** — Python 3.14 — lock + suite pass on 3.14.7 (697/1 skip at the time); **not** adopted because CI does not exercise Docling/onnxruntime/PyMuPDF. Bump `Dockerfile` and `ci.yml` together (CI enforces they match). Needs a full image build and a real ingest.
- **P3-12** — Node image majors — stay on LTS (22 now). 25 is current/non-LTS.

### Hygiene / known bounds
- **P3-13** — `personal_configs/pipeline.yml` is tracked (since `4a275ea`). Scanned: no secrets. Hygiene, not exposure.
- **P3-14** — Session cache can hold up to 50 source PDFs in RAM. Bounded by LRU + cold release.
- **P3-15** — Parse-retry pile-up if model cache is empty and egress fails. Largely defused by baking parse models into the image. Leave unless it reappears.
- **P3-16** — Watch: one-off pytest shutdown segfault (faulthandler, after tests ran). Not reproduced. Capture the dump if it returns.
- **P3-17** — Assign the next version and ship `[Unreleased]`. Last tag and `pyproject.toml` are both `0.8.0`; everything since is unversioned.

---

## Tracking failures (process, not product)

GitHub Issues was the tracker through Phase 6 and then went dark. Several closures do not match the repo:

| Issue | Closed as | Reality |
|-------|-----------|---------|
| **#30** Phase 12D VLM quality audit | completed (2026-03-04) | Feature was never implemented. Reopen or replace. See P2-01. |
| **#15** P4.2 ingest regression corpus | completed (mocked Docling tests) | Real converter tests exist now (`test_docling_e2e.py`) but skip in CI without cached models. See P2-05. |
| **#34** VRAM monitoring in `ConcurrencyGuard` | completed via PR #39 | `src/atlas/concurrency.py` was later deleted. **Do not reopen.** GPU VRAM checks are out of scope unless local VLM inference returns. |
| **#19** Phases 1–6 tracking | completed | Fine for that era. Phases 7–14 and the 2026-08 hardening never got successor issues. |

**Stale remote branches** (all predate the August work; do not merge):

- `origin/copilot/add-vlm-quality-audit`
- `origin/copilot/add-headless-field-to-response`
- `origin/copilot/fix-setsession-page-progress`
- `origin/copilot/implement-vram-monitoring`
- `origin/copilot/remove-dead-code-vlm-ingest`
- `origin/copilot/top-down-review-e2e-testing`
- `origin/docs/technical-debt-review`

Delete them when convenient. They are not open work.

This environment cannot open GitHub issues (`gh` is read-only here). Suggested issue titles, matching the IDs above:

```
P0-01  test: capture and fix the intermittent full-suite failure
P0-02  fix(ingest): stop reporting Docling import errors as "not installed"
P0-03  fix(compose): default ATLAS_ENV to prod in the production compose file
P0-04  fix(api): stop leaking str(e) from RAG 502 handlers
P1-01  fix(ingest): enforce atlas_pdf_max_pages on the layout parser
P1-02  chore(deps): evaluate Docling 2.123 via lockfile + ingest_quality gate
P1-03  test: extend coverage gate to atlas.vlm_ingest
P1-04  chore: pin an explicit ruff select
P1-05  security: run the API container as non-root
P1-06  fix(vlm): paginate the thumbnails endpoint
P2-01  feat: Phase 12D VLM quality audit (reopen #30)
P2-02  feat: HITL strip-and-rejudge
```

---

## Doc drift found in this pass (fixed alongside this file)

These were not product defects; they made the remaining-work picture wrong:

- Test counts frozen at **698 / 54 files** in README, ARCHITECTURE, CI comments, TECHNICAL_DESIGN era notes. Latest CI: **733 passed, 6 skipped** across **60** test files.
- SPA stack still described as **Vite 6 / Zustand 4**; `web/package.json` is Vite 8, Zustand 5, `@vitejs/plugin-react` 6.
- `web/README.md` proxied the API to **:18080**; `web/vite.config.ts` targets **:28080**. It also pointed at the removed `web/src/pages/vlm-ingest/` tree.
- `ARCHITECTURE.md` still said "Phase 11 in progress" and "v0.7.2".
- `PIPELINE_QUALITY_IMPROVEMENTS.md` still said Layer 2 was current and Layer 3B was the planned editor (the React editor shipped).
- `CHANGELOG.md` `[Unreleased]` stopped before the 2026-08-28 afternoon work (VLM ledger durability, Alembic, stream-commit, Docling e2e, token bootstrap).
- GitHub #30 closed as completed for work that is not in the tree (P2-01).
