# Worklog

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
