# Worklog

## 2026-08-28 (flush verification + DR) — flush.ps1 proven; backup/restore now exists

Operator asked to verify `flush.ps1`. Because that destroys the real CANES
corpus, took a full backup first — which turned into closing the DR gap.

- **flush.ps1 verified against every claim**: all `atlas` tables truncated with
  the 10-table schema intact, all Qdrant collections deleted, `artifacts/`
  emptied, `atlas-api` restarted, containers up, 522MB embeddings weight cache
  preserved. Post-flush the stack is fully functional.
- **New `scripts/backup.ps1` + `scripts/restore.ps1`** (Qdrant snapshot +
  `pg_dump --clean` + `artifacts/` copy). Proven twice: flush→restore
  reproduced counts and **identical search scores**, and a backup→restore
  no-op over live data was clean. This is the DR story that had been listed as
  missing since the scale audit. `backups/` gitignored.
- **Real bug found while verifying**: search 500'd whenever the Qdrant
  collection did not exist — i.e. every fresh deployment before its first
  commit, and any stack straight after a flush. Now returns no hits.

Two things that *looked* like bugs and were not, worth remembering:
1. Post-flush ingest reporting `chunks_upserted: 0` — the repetitive synthetic
   text failed the judge's quality gate and routed to HITL, which is correct;
   documents awaiting review are deliberately not indexed. Check
   `workflow_runs.status = 'hitl'` before suspecting the indexer.
2. Danger Zone reset/restore returning 401 "Admin token not configured" —
   those two endpoints alone use the strict admin dependency and refuse to run
   without `ATLAS_ADMIN_TOKEN`, by design, so a dev stack cannot wipe itself.

Caution learned: **`/admin/self-test` is not read-only.** It ingests `e2e-*`
documents, creates runs, and cycles config versions against the live stack. It
polluted the live corpus (78 → 95 points) before being cleaned out by hand; it
now sits behind a confirmation dialog.

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

---

## 2026-08-28 — VLM ingest: durability moved onto the ledger

### The bug the operator hit

A VLM session finished 28 of 31 pages (`status=complete`), sat unattended for
79 minutes, and was gone on return — `GET /api/editor/vlm-ingest/<sid>` → 404.
Auto-resume could not help, because there was nothing left to attach to.

### Why it happened — five band-aids over one wrong foundation

The in-memory `SessionRegistry` was the *system of record*. Every mechanism
around it existed to compensate for that, and each one patched the wound left
by the previous:

| Failure | Compensation added |
|---|---|
| dict lost on restart | write-through `page_XXXX.md` checkpoints |
| TTL from `created_at` killed long jobs | re-key TTL to `last_activity` |
| `last_activity` needs traffic | make the **client poll** the keep-alive |
| poll dies on refresh | localStorage + `?vlm_session=` resume |
| resume cannot survive eviction | ← the reported bug |

React Query does not fire `refetchInterval` in a backgrounded tab, and leaving
the ingest page unmounts the hook entirely. So the liveness signal stopped in
exactly the circumstance the TTL was meant to detect, and a *memory-management
policy* destroyed hours of paid VLM output.

Compounding it: `configure_logging()` had **zero callers**, so under uvicorn no
`atlas.*` logger had a handler and the eviction left no trace at all.

### The fix — the registry is now a cache, not the record

Atlas already had a durable job substrate (`workflow_runs` / `node_runs` /
`artifact_refs` + per-concern ledger modules); VLM ingest was the one subsystem
that opted out. It now uses the same pattern.

- **`vlm_sessions` / `vlm_page_results`** — session state and per-page outcomes,
  written the moment each page settles (`LedgerSessionWriter`).
- **`vlm_page_cache`** — content-addressed memo keyed on source hash, page,
  DPI, crop, masks, prompt and model. A re-run is a cache hit, so a failure
  costs only the in-flight page. Measured: 22.7s → **0.06s**, byte-identical.
- **Source PDF persisted** next to the checkpoints, so a released session
  rehydrates *fully* — previews and re-processing included, not results-only.
- **`SessionRegistry`** gained a `loader`; a miss rehydrates from the ledger.
  Eviction is pure RAM reclaim and skips sessions that are actively
  `PROCESSING` (their bulk loop holds a live reference — releasing one would
  let a later request rehydrate a second object and fork progress).
- **Capacity pressure evicts LRU** instead of refusing to start new work.
- **`GET /sessions`** now lists from the ledger, powering a "Resume an
  in-progress document" list in the UI — no longer dependent on one
  localStorage key surviving.
- **`configure_logging()` is called at startup**, so `atlas.*` logs are visible.

### Invariant this pins

> A cache eviction policy must never be able to destroy business state.
> If it cost real money or hours to compute, it is durable before it is
> acknowledged.

### Verification

- Full process restart mid-session → `GET` returns 200 with results intact
  (strictly harder than the TTL eviction that caused the bug).
- Cache key proven to discriminate on all seven output-determining inputs and
  to be stable under float noise — a false hit would be a correctness bug.
- 718 tests pass (21 new in `tests/test_vlm_ingest_durability.py`); `ruff` and
  the `tsc` contract gate clean.

### Deleted, not added

`touch()`-as-liveness, TTL-as-abandonment, client-poll-as-keep-alive and the
localStorage lifeline are all gone as *load-bearing* mechanisms.

### Known, unrelated

`tests/test_cleanup_rules_import_export.py::test_export_empty` is flaky
(~1 in 5 full-suite runs, `KeyError`; passes in isolation). Pre-existing —
worth a separate look since it can redden CI at random.

---

## 2026-08-28 — Agent-scan adjudication, and the debt it turned up

A read-only multi-agent scan (4 subagents, mixed models) was run against the
repo. Every claim was re-verified against source before being acted on. Open
items live in `TECHNICAL_DEBT.md` §6; what was fixed is below.

### Session lifecycle — one root cause, not six bugs

The scan found six defects in the VLM session code shipped earlier the same
day. Five shared a cause: **session status was doing double duty** as a durable
description of where a document had got to *and* as a concurrency lock, written
on entry but never reconciled on exit or on restore.

The worst had been made worse by the durability commit. `process_page` set the
session to `PROCESSING` and never cleared it, so "Process all" returned 409
forever after a single interactive page. That was pre-existing — but once
status became durable, the wedge survived restarts. Previously a restart
cleared it, by destroying the session. Fixing one failure mode had extended
another.

Split into `status` (durable, descriptive) and `bulk_active` (in-memory,
process-scoped). An in-memory lock is released by a restart automatically,
which is exactly what "is a loop running right now?" should mean. `save_session`
now refuses to persist a transient status at all, so the unstartable-after-
restart case is impossible by construction rather than merely fixed.

Also closed: operator hand-corrections were memory-only and silently lost on
cache release; `DELETE` during a bulk loop could recreate the rows it had just
removed, and left the checkpoint directory on disk forever; `source.pdf` was
written non-atomically.

### The gates did not test what shipped

Three findings with one shape:

*   `FakeQdrantStore.search` returned a manufactured hit whenever a filter
    matched nothing, making "search returns empty" untestable — the exact shape
    of a scoping bug. Removing it broke one test, and it was the one named
    `applies_filters`: it searched without ingesting and asserted non-empty.
*   The Docker image resolved `pyproject.toml` ranges while CI installed the
    lock, so a green build said nothing about what shipped. Now installs
    `requirements.lock`.
*   No type-check gate. Added mypy, clean across 82 files, with stub-driven
    debt registered the way ruff's is.

**These converged on a live bug.** `huggingface_hub` 1.x removed
`local_dir_use_symlinks`, which the deepdoc downloader still passed — to
`snapshot_download`, whose `TypeError` was swallowed into a misleading
"falling back" warning, and then to `hf_hub_download` on the fallback path,
where it was not caught at all. Runtime model download was broken outright.
It went unnoticed because the image bakes those weights in at build time, and
because the image and the lock had drifted onto **different versions of the
library** (1.29.0 vs 0.36.2). The drift is what let the incompatible version
reach the appliance; the type gate is what saw it.

### Commit streams; schema has migrations

Commit embedded every chunk and built every point before a single upsert, so
peak memory scaled with the document — gigabytes of vectors resident before
the first byte reached Qdrant at the 3,000-page target. Now windowed at 256
chunks. Verified live at 320 chunks across two windows against real Qdrant and
TEI, plus a test asserting windowed and single-window commits store identical
results.

Alembic adopted. `create_all` can only add tables, never alter one, so the
first change to an existing table had no path to production. Pre-Alembic
databases are *stamped* at the baseline rather than migrated — safe by
construction, since the baseline was autogenerated from the same models
`create_all` was building. Both paths were tested on scratch databases before
the live one was touched.

### Notes on the scan itself

Recorded because the run was also a test of the harness:

*   The **code-review agent was 6-for-6** on freshly written code it had no
    prior context for, including two operator-blocking defects and a subtle
    row-resurrection race.
*   The **tech-debt agent was weakest** — one conclusion rejected outright
    after direct testing, several severity inflations, and it flagged a
    `print()` workaround without noticing its cause had been removed the same
    day.
*   **Structural artifacts worth watching:** the synthesised report had no §2
    (numbering jumped 1 → 3) and one section ended mid-sentence. Something was
    dropped rather than merged. A silently lost section is the failure mode to
    guard against in a fan-out.
*   **Self-marked hypotheses were honest** — both needed exactly the
    verification they asked for, and one turned out wrong. The marking worked.

---

## 2026-08-28 — Docling: real coverage, and a way to judge upgrades

### Docling was never actually tested

Every Docling test in the suite monkeypatched `parse_document_path`, so they
covered the wiring — artifact persistence, error codes, fidelity flags — while
never running Docling itself. A version bump, a model change, or a
pipeline-option regression would have passed all of them.

`tests/test_docling_e2e.py` runs the real converter against PDFs built at test
time with known ground truth. It asserts a **quality floor, not exact output**:
Docling's markdown changes between releases, and pinning the string would make
every upgrade look like a regression. Fixtures are generated rather than
committed — a checked-in PDF drifts from what the assertions claim it contains,
and the real manuals are production material.

Marked `integration` and skipped unless the models are already cached, so it
never triggers a large download inside CI.

### Three things writing those tests turned up

*   **`pdf_parser.table_extraction` was inert.** Documented in
    `pipeline.yaml.example`, read by nothing. Now wired to Docling's
    `do_table_structure`, with a test that fails if it goes dead again. On a
    ruled table: **on** ≈4.5s with real columns, **off** ≈0.65s with the table
    collapsed onto one line.
*   **TableFormer normalises cell text** — `1E-11` comes back as `1e-11`. Table
    content is not byte-exact and must never be compared strictly.
*   **Limits were enforced after the Docling import.** An oversized document
    with Docling unavailable reported a missing dependency instead of the limit
    it violated. Preflight uses PyMuPDF, so the guard now runs first.

### Measuring instead of guessing

`scripts/ingest_quality.py` (logic in `atlas.eval.ingest_quality`) measures a
parse — chars, headings, tables, timing, and recall against optional
per-document ground truth — writes JSON, and diffs two runs, exiting non-zero
on regression. It is meant to *gate* a parser upgrade, not describe one.

It earned that on first use: pointed at a Docling upgrade it caught both
fixtures dropping from 100% recall to a hard parse failure.

### Where the Docling upgrade actually stands

Pinned `2.76.0`; latest `2.123.0`, 53 releases on. Established: installing the
new version **into the existing image** — by `--upgrade` or by exact pin —
leaves a half-upgraded tree (`docling.pipeline` present,
`docling.document_converter` gone) and every parse fails. **Not** established:
whether a clean `pip-compile` resolve of 2.123.0 parses correctly, or at what
quality. Nobody has run that. Details and the evaluation procedure are in
`TECHNICAL_DEBT.md` §7.

The failure mode is worth remembering: a broken Docling reports "Docling is not
installed", and with `backend: auto` the pipeline falls back to the layout
parser and **succeeds at lower quality**. A silent quality drop behind a
misleading message.

### Open: an intermittent full-suite failure

Not fixed, and deliberately not guessed at.

*   Reproduces roughly 1 run in 3–5 of the **full** suite; never in isolation
    (10 clean runs of the Docling E2E file alone, and `test_export_empty`
    passes alone too).
*   It moves between tests. It first appeared as
    `test_cleanup_rules_import_export.py::test_export_empty`; after the Docling
    tests were added it landed on
    `test_docling_e2e.py::test_docling_recovers_headings_and_body_from_born_digital_pdf`.
    Both traces bottom out in a heavy lazy module import compiling regexes.
*   An earlier reading of this as a `KeyError` was wrong — that was a
    source-context line (`except KeyError:` inside `re/__init__.py`), not a
    raise. The actual exception has not been captured cleanly yet.
*   Leading hypothesis, untested: resource pressure during the full suite,
    which loads torch, Docling and onnxruntime across many app fixtures.

Repro:

```
for i in $(seq 8); do python -m pytest -q --no-cov --tb=long || break; done
```

Worth resolving because it can redden CI at random, and because a test that
fails for environmental reasons trains people to re-run rather than read.
