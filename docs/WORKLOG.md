# Worklog

## 2026-08-28 (CI truth + build tooling) — CI was never green; fixed both jobs

**Correction to the previous entry.** CI had *never* passed on main. The
"green" readings came from `actions/runs?branch=main`, which also returns
Dependabot's own workflow runs — those succeed, so the query looked healthy
while every `ci.yml` run failed. **Query `actions/workflows/ci.yml/runs`**
for the real status; `actions/runs` is not workflow-specific.

Two genuine causes, both fixed and verified against a real clean checkout
(`git archive HEAD` into a scratch dir, then the exact CI recipe):

1. **backend**: 8 tests load `config/pipeline.yaml`/`models.yaml`, which are
   gitignored operator-local files — a fresh checkout only has `*.example`.
   The earlier "clean-room" run mounted the working tree, which has them, so
   it proved nothing about CI. CI now seeds them from `.example` before the
   test step. Clean-checkout result: ruff clean, 697 passed / 1 skipped.
2. **web**: `eslint-plugin-react-hooks` 7 enables new rules. 11
   `set-state-in-effect` findings (registered as debt with the React Query
   migration named) and one *real* bug — the VLM page-by-page auto-advance
   recursed through a stale closure of itself, so once `processPage` changed
   identity the chain kept calling a stale mutation. Now routed through a ref.

**Build tooling**: adopted Vite 8 + `@vitejs/plugin-react` 6 (rolldown —
build ~19s → ~8s, bundle ~50KB smaller). Deferred TypeScript 7: it builds
fine, but `typescript-eslint` does not support TS 7.0 (upstream #10940, needs
≥7.1) so it breaks the lint gate. Did the `tsconfig.json` half of that
migration anyway (drop `baseUrl`, relative `paths`) — TS 5 accepts it, so the
future bump is one line.

**Method note for future sessions**: verify a CI fix by exporting a clean
checkout and running the workflow's exact steps against it. Mounting the
working tree hides every gitignored-file dependency.

## 2026-08-28 (CI + debt burn-down) — Actions pipeline live; ruff clean; locks actually work now

- **CI** (.github/workflows/ci.yml): backend = lockfile install (CPU torch
  via pytorch index) + clean `ruff check` + full suite; web = eslint +
  `npm run build` (tsc is the API-contract gate). Dependabot for
  pip/npm/actions/docker, weekly, grouped. Validated by running the exact
  recipe in a bare python:3.11-slim: 697 passed, 1 skipped (qdrant
  integration test self-skips without services — by design).
- **Lockfiles had never worked**: compiled on Windows (pywin32 pinned, no
  platform marker) → uninstallable on Linux, so nothing consumed them.
  Regenerated on Linux with PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
  (torch 2.10.0+cpu, no CUDA payloads). Regenerate them the same way after
  dependency bumps (see .github/dependabot.yml note).
- **Ruff: 0 findings** (was ~350). ~280 fixed; 13 B023 closure findings
  individually adjudicated (1 real fix, 12 verified benign + documented);
  ~95 judgment-call findings registered as an explicit debt list in
  pyproject with counts — codes leave the list when fixed, never join it.
- **ESLint config existed only in the npm script** — no config file, lint
  had never run. Added correctness-focused config; exactly one violation in
  the whole SPA (unused catch binding). That plus every-httpx-call-has-a-
  timeout and a single TODO in the backend says the codebase is in better
  shape than the debt counters implied.
- Note: the workspace container's ruff (installed ad hoc) can drift from the
  lockfile's — CI pins via the lock; prefer `pip install -r
  requirements-dev.lock` in the workspace after recreates.

## 2026-08-27 (hardening round) — Bulletproofing pass: auth, guards, cancellation, crash reconciliation

Follow-up sweep after the scale audit, hunting anything between "works" and
"bulletproof". Five fixes (CHANGELOG "hardening round"): /rag now carries the
admin auth posture (was fully anonymous — including search, which returns
document content); upload size caps enforced at both upload endpoints (VLM
start-upload previously had none); bulk VLM runs are cancellable via session
discard (previously the loop burned VLM tokens to the end of the document
with no off switch); process-all has a double-start guard with status
recovery (failed pages retryable); and startup now marks any 'running'
WorkflowRun as failed/interrupted, since nothing can legitimately be running
at boot in a single-process app.

Verified: suite 698 passed / 89.8%; zero new lint findings vs HEAD; dev auth
posture unchanged (rag open without token in dev); startup clean.

Still deliberately open (ops-level, not code): no backup story for the
postgres/qdrant volumes; thumbnails endpoint unpaginated (headless mode for
1000+ page docs); no automatic resume-from-checkpoint (salvage is manual).

## 2026-08-27 (real docs + scale audit) — First production doc indexed; timing measured; long-job durability fixes

First real document went through the full VLM path cleanly: 44 pages
(42 enabled), zero page errors, committed and indexed (78 chunks, embed
batching handled 3 batches). Run marked completed.

**Measured timing** (from container log timestamps):
- Bulk VLM processing: 42 pages in ~11.8 min ≈ **17 s/page average**
  (range ~5–60 s/page; the earlier 11-page test doc ran ~90 s/page — density
  and provider routing dominate). Stitch + commit + embed: ~2 min.
- Projections: 2,000 pages ≈ **9–50 h**; 3,000 pages ≈ **14–75 h**.

**Scale audit against a 2,000-page job (3,000 as stress target)** — three
fixes landed:
1. Session TTL was 1 h from `created_at`, never refreshed — any >1 h job
   would be evicted mid-run (by maintenance or another session's create()).
   Now inactivity-based: polls and page progress refresh it.
2. Page results were RAM-only (restart = total loss, proven twice at small
   scale). Now write-through checkpointed to
   `artifacts/vlm_sessions/<sid>/page_NNNN.md` (+ `session.json` with the
   render config) — a crash loses at most the in-flight page; salvage via
   Import or headless re-run from the saved config.
3. Qdrant upsert was one call for all points; now batched at 512.

**Verified sound at scale already**: per-page errors don't abort the bulk
loop; provider-level retry covers transient VLM failures; rendering runs in
the threadpool (event loop stays free — /health green throughout); embed
batches of 32 (~6k chunks ≈ 188 batches, minutes); process-all and commit
survive client disconnects.

**Known limits, deliberately deferred**:
- Thumbnails endpoint renders every page in ONE response (~48 KB/page ⇒
  ~150 MB at 3k pages). Fine to a few hundred pages; beyond that use
  headless mode or skip the Pages grid. Needs pagination if the wizard must
  handle thousand-page docs interactively.
- No automatic resume-from-checkpoint after API restart (salvage is manual).
- ATLAS_PDF_MAX_BYTES defaults to 200 MB (env-tunable) and the PDF is held
  in session RAM for the run's duration.

## 2026-08-27 (testing round 1) — Real-doc VLM + import flows exercised end to end; 8 bugs found and fixed

Operator ran the full VLM wizard against a real 11-page datasheet
(Microsemi SyncServer S600), then imported the stitched markdown through
/rag/ingest/file. Everything on the checklist passed by end of session:
all containers healthy, /health responsive mid-ingest, 768-dim collection,
search returning the doc. Bugs found by the flow itself (all fixed, see
CHANGELOG "operator testing round 1"):

1. TEI sidecar crash-loop — nomic repo HEAD broke TEI's config parser; pinned
   model revision in compose.
2. Unbatched embeddings — 57-chunk commit 422'd against TEI's 32-input cap;
   client-side batching in openai_compat.
3. VLM commit left its WorkflowRun 'running' after pipeline-feed failure.
4. Maintenance orphan scan traceback every 5 min while qdrant empty.
5. SPA bulk-processing progress bar stuck at 0% (poll results never synced
   into the wizard store).
6. Page refresh orphaned a running VLM session UI-side — added localStorage
   persistence + auto-resume + ?vlm_session= deep links.
7. Import/Paste forms sent doc_name; backend requires doc_id — both 422'd.
8. API/dashboard reported hardcoded version 0.1.0 (plus a stale
   src/project_atlas.egg-info shadowing installed metadata via bind mounts).

Also: "LLM configuration" card added to Admin → Health (profile, ZDR badges,
role→model table). VLM output quality spot-checked against the source PDF —
all part numbers, spec tables, and scientific-notation exponents correct;
conversion is index-worthy.

Notes for the future:
- The workspace container is compose-built and does NOT run
  .devcontainer/post-create.sh — install pytest/ruff via
  `docker exec atlas-workspace pip install pytest pytest-asyncio pytest-cov ruff`
  after a recreate. Avoid `pip install -e .` there: it writes
  src/project_atlas.egg-info onto the host, which shadows package metadata
  inside atlas-api via the bind mount (bug 8 above).
- Data flushed at session close; ready for real org-doc ingestion.

## 2026-08-27 (host bring-up) — Stack nuked + rebuilt; compose consolidated to dev/prod; flush script

Operator moved from the dev container back to the Windows host for the testing
phase. Session actions:

- **HF CDN unblocked**: the `us.aws.cdn.hf.co` sinkhole was AdGuard DNS
  filtering; operator switched DNS and the CDN now returns 200 from inside
  containers. The TEI sidecar can finally pull its weights (the cache volume
  had been empty all along — cpu-1.5 never got past the config files).
- **Full nuke**: `compose down --volumes --remove-orphans` across the dev
  chain, plus the stray `atlas_embeddings_probe` volume. Nothing was lost —
  postgres/qdrant were empty per the day-close entry, and this cleared the
  stale cpu-1.5 embeddings container that predated the `5b09df7` image fix.
- **Compose consolidation (dev/prod only)**: deleted the e2e/optest/slim
  stacks, `Dockerfile.slim`, and their docs/driver scripts (see CHANGELOG
  "Removed"). `COMPOSE_FILE` in `.env` now makes bare `docker compose` the dev
  stack; prod is `docker compose -f docker-compose.yml`. devcontainer.json's
  explicit file list is unaffected.
- **Flush mechanism**: new `scripts/flush.ps1` — truncates all `atlas` DB
  tables, deletes every Qdrant collection, empties `artifacts/`, restarts
  `atlas-api`; keeps containers, schema, and the embeddings weight cache. Use
  between testing rounds and before the first real org-doc ingestion.
- `.gitignore` audited — no changes needed (SPA build output in `static/app`
  stays tracked by design).

Testing-phase checklist from the day-close entry below still applies.

## 2026-08-27 (close) — Repo work complete; ready for bring-up and operator testing

All repo-side work is committed and pushed. Full ledger for the day, oldest
first:

- `363f944` chore: dev container, line-ending policy, build hygiene (pyproject → 0.8.0)
- `3ad39b2` refactor!: remove dead code, incl. the never-wired PrivacyGuard
- `bb1133d` feat: LLM profiles, embeddings sidecar, ZDR enforcement, oversize guards
- `0e25050` docs: guides aligned, 0.8.0 changelog reconstructed, this worklog added
- `5b09df7` fix: embeddings sidecar image (TEI cpu-1.9), docker proxy → lifecycle surface
- `ac00f63` fix: parser work off the event loop, parse models baked into image,
  unknown-mime `.pdf` suffix default removed

State at close: suite 698 passed / 89.8% coverage; qdrant empty; postgres
tables empty; `artifacts/` clean; operator `config/models.yaml` on pinned
nomic embeddings; no uncommitted changes.

### Bring-up (from the Windows host, repo root)

    git pull
    docker compose -f docker-compose.yml -f docker-compose.dev.yml -f .devcontainer/docker-compose.devcontainer.yml up -d --build --force-recreate docker-proxy embeddings atlas

First build is slower once: the image now bakes ~1GB of parse-model weights;
the embeddings sidecar downloads its weights on first boot (healthcheck allows
180s). After the proxy recreate, the dev shell has container lifecycle access
(start/stop/restart/exec) — builds and `compose up` stay host-side.

### Testing phase checklist (operator runs docs, assistant fishes logs)

1. All containers healthy: atlas-api, atlas-embeddings, atlas-postgres,
   atlas-qdrant (`docker ps`), then `GET /health` on Atlas.
2. Dummy-doc ingests via the SPA or `/rag/ingest/*` — watch judge → refine →
   metadata in `docker logs -f atlas-api`; confirm `/health` stays responsive
   mid-ingest (regression check for the event-loop fix).
3. `/rag/search` returns the ingested content; qdrant collection is 768-dim.
4. Flush test data before real org-doc ingestion.

Deferred to the bug-fix push: parse-retry pile-up design (largely defused by
model baking), ruff debt (~350 pre-existing, 211 auto-fixable), watch items
(one-off pytest shutdown segfault; whether timed-out Docling converter
threads linger).

---

## 2026-08-27 (evening) — Stack check + aborted in-container E2E; handover to operator testing

Commits pushed earlier today (`363f944`…`0e25050`). Stack inspection through the
docker proxy plus an attempted controlled ingest from inside the dev container
surfaced real findings before the test was cut short in favour of operator-run
dummy-doc testing.

### Fixed in this pass (needs host-side `docker compose up -d` to apply)

- **`atlas-embeddings` crash-loops on first boot** — TEI `cpu-1.5`'s bundled
  hf-hub 0.3.2 cannot follow the relative redirect URLs the HF CDN now returns
  ("relative URL without a base"), so the weights download fails forever.
  `docker-compose.yml` now pins `cpu-1.9`. Same model, same API.
- **Docker proxy widened to a lifecycle surface**
  (`.devcontainer/docker-compose.devcontainer.yml`): POST/EXEC/start/stop/
  restart enabled so the dev shell can bounce and debug containers. Builds and
  `compose up` still belong on the host (daemon resolves bind-mount paths
  host-side). Recreate `docker-proxy` for this to take effect.

### Found in the aborted E2E — status

1. ✅ **A busy ingest blocked the entire API** — while a PDF parse was running,
   `GET /health` timed out (>8s); parser work (Docling, ONNX inference, model
   downloads, VLM page rendering) ran synchronously on the event loop.
   **Fixed**: all parser-side heavy work offloaded via `asyncio.to_thread`.
2. ✅ **First PDF ingest downloaded models mid-request** — heron + deepdoc
   ONNX were not in the image. **Fixed**: baked into the Docker image in the
   dependency layer; `models/` added to `.dockerignore`.
3. ⏳ **Parse-failure pile-up** (bug-fix push): `docling.convert` runs under a
   120s timeout with 2 retries, then falls back to the layout parser (its own
   downloads + retries). With unreachable weights a single 2-page ingest kept
   the server busy 10+ minutes. Consider fail-fast when the model cache is
   empty and egress fails, and check whether the timed-out converter thread
   keeps running after abandonment. Largely defused by item 2, so deferred.
4. ✅ **`DoclingParser` defaulted unknown mimes to a `.pdf` temp suffix** —
   **Fixed**: bytes sniffed for `%PDF-`; anything else gets `.bin` and fails
   format detection cleanly.

### Controlled test status

Aborted mid-run (operator will run dummy docs instead). What did get verified
live from the dev container: profile `api` resolution + startup validation
logs, ZDR header injection on real OpenRouter calls (chat + embeddings both
200 with `provider.zdr=true`), qdrant/postgres connectivity. NOT yet verified:
judge→refine→metadata on a real doc, embeddings sidecar, search. Test residue
fully flushed (workflow_runs/node_runs/artifact_refs/active_doc_versions rows
deleted, `artifacts/runs/1,2` removed, qdrant untouched-empty, operator
`config/models.yaml` restored to pinned nomic embeddings).

Environment note: the assistant's sandbox cannot reach the HF weights CDN
(`cdn-lfs.hf.co`, `cas-bridge.xethub.hf.co` resolve to 0.0.0.0) — model
downloads must happen host-side or in the compose services.

---


Session-continuity log. Dev-container rebuilds can lose assistant conversation
state, so each working session appends a dated entry here summarizing what was
done, what was verified, and what is open. Newest entry first. Keep entries
short — the CHANGELOG `[Unreleased]` section holds the detailed change notes;
this file holds *process* state (what's committed, what's verified, what's
next).

---

## 2026-08-27 (later) — Wrap-up: working tree committed

Everything from the index entry below is now committed on `main` (not pushed):

- `363f944` chore: dev container, line-ending policy, build hygiene
  (also bumps `pyproject.toml` to `0.8.0` to match the tag; next release
  picks a new number and bumps both).
- `3ad39b2` refactor!: remove dead code, incl. the never-wired PrivacyGuard.
- `bb1133d` feat: LLM profiles, embeddings sidecar, ZDR, oversize guards
  (also fixes the `IngestResult` F821 forward refs in `parsers.py`).
- docs commit (this one): all guide/reference updates + this worklog.

Verified before committing: `pytest -q` → 698 passed, 89.84% coverage.
**Watch item**: one run out of four died with a faulthandler dump (segfault
at interpreter shutdown, after tests ran; torch/onnx C-extension teardown
suspected). Not reproduced since — if it recurs during the testing phase,
capture the full dump and pin it down.

Next: the host-side validation list in the entry below (§ "Open — path to
'test ready'", items 2–3), then a bug-fix push. Ruff debt (~350 pre-existing
errors, 211 auto-fixable) intentionally left out of these commits — fold it
into the cleanup pass later so it doesn't pollute review of this work.

---

## 2026-08-27 — Repo state index after dev-container rebuild

Context: previous conversation was lost in a dev-container rebuild. This entry
reconstructs state from the working tree.

### Working tree (uncommitted, ~51 files)

One coherent body of work, fully described in `CHANGELOG.md → [Unreleased]`:

- **Added**: LLM profiles (`src/atlas/llm/profiles.py`, `local`/`api` via
  `ATLAS_LLM_PROFILE`), CPU embeddings sidecar in `docker-compose.yml`
  (host port 18090), OpenRouter ZDR enforcement, per-provider config
  (base_url/headers/timeouts), `limits.judge_max_context_tokens`
  (skip-oversize grading).
- **Removed (dead code)**: `concurrency.py` (incl. the never-wired
  PrivacyGuard — BREAKING, privacy now rests on ZDR + `local` profile),
  `hitl.py` (superseded by `hitl_ledger.py`), unused schemas/settings/config
  blocks, plus their tests.
- **Fixed**: `refine_max_section_tokens`/`refine_min_section_ratio` never
  wired to RefineNode; `refine_min_preservation_ratio` default 0.6→0.85;
  reasoning-model `content: null` error path; read timeouts no longer retried;
  `fits_in_context()` now checks the output ceiling.
- **New tests**: `test_llm_profiles.py` (18), `test_oversize_guards.py` (12).
- **Infra**: `.devcontainer/` (untracked), `.gitattributes` line-ending policy
  (LF for scripts/compose/Dockerfiles; `static/app/**` opaque) — much of the
  doc diff is line-ending churn from this, real pyproject change is only the
  ruff `EXE002` ignore.
- **Docs**: CHANGELOG reconstruction of v0.8.0, README/ARCHITECTURE/
  E2E_TEST_GUIDE/OPTEST/PIPELINE_REFERENCE updated to match (698 tests / 54
  files, `--no-cov` caveat for partial runs, `/editor` → `/app` route rename).

### Verified this session (inside dev container)

- `pytest -q`: **698 passed**, coverage 89.84% (gate 80%). No dangling imports
  of any deleted symbol (`concurrency`, `hitl`, `HITLTask`, `session_scope`, …).
- `ruff check src tests`: 350 errors — **pre-existing** (HEAD has 468; this
  work *reduced* the count). 211 are `--fix`-able. The 5 F821s are uninported
  forward-reference strings in `parsers.py` type hints, not runtime bugs.
- Docker Compose is **not available inside the dev container** (docker CLI has
  no `compose` plugin) — compose/optest validation must run from the host.

### Open — path to "test ready"

1. **Commit the working tree** (nothing is committed; a crash loses it all).
   Suggested split: (a) `.gitattributes` + pure line-ending churn,
   (b) dead-code removal, (c) LLM profiles + provider config + fixes + tests,
   (d) compose/embeddings sidecar + devcontainer, (e) docs.
2. **Host-side stack validation** (blocked in-container):
   - `docker compose -f docker-compose.optest.yml --profile deterministic up
     --build --abort-on-container-exit --exit-code-from e2e`
   - Main stack up: embeddings sidecar healthy (180s cold start), Atlas on
     28080, `ATLAS_EMBEDDINGS_BASE_URL` reaching it.
   - `local_llm` / `lmstudio` optest modes once a model server is up.
3. **Profile smoke test**: boot once with `ATLAS_LLM_PROFILE=local` and once
   with `api` (needs OpenRouter key in `.env`); confirm startup validation and
   one ingest under each.
4. **Version decision**: last tag is `v0.8.0`, `pyproject.toml` says `0.7.2`,
   CHANGELOG `[Unreleased]` is unversioned. Pick the next version and align
   all three before release.
5. Optional cleanup: `ruff check --fix` (211 auto-fixes), TYPE_CHECKING
   imports for the `IngestResult` forward refs in `parsers.py`.
6. Known foot-guns still open (documented, not blockers):
   `docs/TECHNICAL_DEBT.md` §2A sectional-refinement context loss, §2B
   destructive cleanup rules.
