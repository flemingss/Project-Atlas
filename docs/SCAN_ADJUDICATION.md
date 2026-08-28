# Adjudication — Agent Scan Report (2026-08-28)

Every claim below was re-verified against source. Verdicts are mine; the
underlying scan is credited where it was right, and corrected where it wasn't.

**Headline:** the scan's §4 (VLM/session hot path) is **6-for-6 correct**, and it
caught a real regression introduced by the durability commit (`ac52367`) hours
earlier. The weakest section is §1 item 1, whose conclusion is wrong — though
the observation underneath it points at a different, real problem.

**Verdict key:** `CONFIRMED` — reproduced from source · `PARTIAL` — real issue,
wrong framing or severity · `REJECTED` — claim does not hold.

---

## Tier A — live regressions, fix before anything else

All six are in code shipped today. Items 1 and 2 are operator-blocking.

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | `process_page` leaves the session `PROCESSING` forever; `process_all` then 409s permanently | **CONFIRMED** | `api_vlm_ingest.py` — `s.set_status(PROCESSING)` before the try, never reset on any exit path; `process_all_pages` guards on `s.status == PROCESSING` |
| A2 | Restart mid-bulk bricks the session | **CONFIRMED** | `store.rehydrate()` restores session status verbatim; a session saved as `processing` comes back unstartable |
| A3 | Stitched markdown not restored on rehydrate | **CONFIRMED** | `vlm_sessions.stitched_markdown` is written but `rehydrate()` never reads it back into `s.stitched`; `commit_session` requires it |
| A4 | Operator page corrections never persisted | **CONFIRMED** | `update_page_result` assigns `s.page_results[n]` directly, bypassing `set_page_result` → no writer hook, no ledger row |
| A5 | DELETE during a bulk loop resurrects the session row | **CONFIRMED** | `save_session` upserts (creates when absent); the loop's terminal `set_status` runs after cancellation |
| A6 | Non-atomic `source.pdf` write | **CONFIRMED** | `pdf_path.write_bytes()` — a crash mid-write leaves a truncated PDF that rehydrate cannot render |

**A1 deserves emphasis.** It is pre-existing, but the durability commit made its
blast radius worse: `set_status` now persists `PROCESSING` to the ledger, so the
stuck state survives a restart. Previously a restart cleared it — by destroying
the session. Fixing one failure mode extended another.

**A4 is the quiet one.** It silently discards *human* corrections, which are
more expensive than the VLM output the durability work was built to protect.

---

## Tier B — real, high value, pre-existing

| # | Claim | Verdict | Notes |
|---|---|---|---|
| B1 | `FakeQdrantStore.search` returns a dummy hit when nothing matches | **CONFIRMED** | `tests/helpers.py`. The sharpest pre-existing finding in the report: **no test can assert an empty result set**, so a broken tenant/project filter — silently returning nothing, or everything — cannot be caught. Test-integrity rot; cheap to fix, and some existing assertions may be resting on the fallback |
| B2 | Whole document materialized in RAM before first upsert | **CONFIRMED** | `runner.py:492` embeds all texts in one call, `:573` accumulates every point, `:603` upserts once. The real 3000-page blocker. (Client-side *HTTP* embed batching added earlier fixed a 422, not this) |
| B3 | Docker image ignores the lock files | **CONFIRMED** | `Dockerfile:57` `pip install .` — CI tests `requirements-dev.lock` while the image resolves pyproject ranges fresh. **CI green does not imply the image is what was tested.** `docling` is unpinned (`pyproject.toml:25`) |
| B4 | Admin token in `?token=` query param | **CONFIRMED** | `web/src/services/shared.ts:11` — tokens in URLs leak to history, referrers and logs. The localStorage half is a defensible trade for a local appliance; the query param is not |
| B5 | No Alembic; `create_all` only | **CONFIRMED** | `db_init.py`. More pressing now: today's commit added three tables. `create_all` creates but never **alters** — the next change to an existing column has no migration path |
| B6 | No type-check gate; 28 unenforced `# type: ignore` | **CONFIRMED** | No mypy/pyright in `pyproject.toml` or `ci.yml` |

---

## Tier C — real but overstated, or cheaper than framed

| # | Claim | Verdict | Correction |
|---|---|---|---|
| C1 | Docling 2000-page cap blocks 3000-page docs | **PARTIAL** | Real, but `atlas_pdf_max_pages` is a setting — an env var, not a code change. The genuine defect is the **asymmetry**: the layout parser enforces no cap at all, so the two paths disagree about what is too big |
| C2 | Hardcoded credentials in prod compose | **PARTIAL** | A hardcoded local Postgres password on a single-host appliance is low risk. The real item in that file is `ATLAS_ENV: ${ATLAS_ENV:-dev}` — the **prod** compose defaulting to dev |
| C3 | Coverage gate only covers `atlas.pipeline` | **PARTIAL** | True. Whole-package coverage would mostly add noise; extending it to `atlas.vlm_ingest` is the move that would actually have caught Tier A |
| C4 | `print()` in `_diag` | **CONFIRMED but now obsolete** | That `print` existed *because* `configure_logging()` was never called. Logging was fixed today, so the workaround should now be deleted — the scan found the symptom after the cause was already gone |
| C5 | 502s leak `str(e)` | **CONFIRMED** | Real, low effort |
| C6 | Registry may hold 50 PDFs in RAM | **PARTIAL** | Bounded now by LRU + cold release, and only reachable with 50 concurrent sessions. Low priority |
| C7 | `personal_configs/` in the tree | **CONFIRMED, reframed** | `personal_configs/pipeline.yml` is tracked (since `4a275ea`). Scanned: **no secrets** — consistent with the earlier history audit. Hygiene, not exposure |
| C8 | Container runs as root | **CONFIRMED** | No `USER` in the Dockerfile |

---

## Tier D — rejected

| # | Claim | Verdict | Why |
|---|---|---|---|
| D1 | No ruff `select` → "ignore list may be hollow" | **REJECTED (conclusion)** | Tested directly against a probe file: ruff's **default** rule set already includes `B`, `C4`, `S110`, `BLE001`, `EXE002`. Every ignore entry suppresses a rule that is genuinely enabled. The list is not decorative, and the registered-debt narrative holds. |

**But the observation underneath D1 is worth acting on, for a different reason.**
With no explicit `select`, the enforced rule set *is whatever the installed ruff
version defaults to* — an upgrade can silently widen or narrow the gate. Proof
that this already bites: `line-length = 100` is configured, yet `E501` is not
enabled, so nothing enforces it. Pin the gate with an explicit `select`; do it
for reproducibility, not because the ignores are fake.

---

## Notes on scan quality

Worth recording, since the point of the run was to evaluate the harness:

- **§4 (CodeReviewer / GLM) was the standout** — 6-for-6 on freshly written code
  it had no prior context for, including two operator-blocking defects and one
  subtle upsert-resurrection race. That section justified the exercise on its own.
- **§1 (tech debt) was the weakest** — one rejected conclusion, several severity
  inflations, and it flagged `print()` in `_diag` without noticing the cause had
  been removed the same day.
- **Structural artifacts:** the report has no §2 (numbering jumps 1 → 3), and §1
  ends with a stub line reading `**Confirmed:** message.` Something was dropped
  in synthesis. Worth checking whether a source report was lost rather than
  merged — a silently dropped section is the failure mode to watch for in a
  fan-out harness.
- **Self-marked hypotheses were honest.** Both flagged items needed exactly the
  verification they asked for, and one (D1) turned out wrong — the marking did
  its job.

---

## Recommended order

**Now — Tier A.** Live regressions in shipped code; A1/A2 block normal use.
Root cause for A1–A3 is one thing: session status is written on entry but not
reconciled on exit or on restore. Fix it as a lifecycle invariant, not three
patches.

**Next — B1, B3.** Both attack the same weakness: *the gates do not test what
ships.* B1 lets filter regressions pass; B3 lets the image drift from what CI
verified. Highest value per unit effort in the whole report.

**Then — B2** (streaming ingest, the real 3000-page work), **B5** (Alembic,
before more schema churn), **B4/C5** (token and error-leak hardening).

**Last — C1, C3, C4, C6, C8, D1-as-reproducibility**, and B6 if the type-ignore
count keeps growing.

---

## Resolution log (2026-08-28)

Tiers A and B were worked in full. What each change actually turned out to be:

### Tier A — one root cause, not six bugs

Status was doing double duty as durable description *and* concurrency lock, and
was written on entry but never reconciled on exit or restore. Split into
`status` (durable, descriptive) and `bulk_active` (in-memory, process-scoped) —
an in-memory lock is released by a restart automatically, which is exactly what
"is a loop running right now?" should mean. `save_session` now refuses to
persist a transient status at all, so A2 is impossible by construction rather
than merely fixed.

Proven live: single page then bulk returns 200 (was 409 forever); a restarted
session comes back `complete` and startable; commit with no markdown succeeds
from the restored stitch (was 400).

Also closed while in there: `DELETE` left the checkpoint directory on disk —
the unbounded-growth risk the scan listed but did not rank.

### Tier B — the gates now test what ships

- **B1** removed the fake store's manufactured hit. Exactly one test depended on
  it, and it was the one named `applies_filters`: it searched without ingesting
  and asserted the result was non-empty. Rewritten to ingest under a known
  scope, assert real hits, and assert a **different tenant sees nothing** — the
  assertion the fabrication had made impossible.
- **B2** commit now embeds and upserts in windows of `COMMIT_WINDOW_CHUNKS`
  (256). Peak memory is a function of the window, not the document. Verified
  live at 320 chunks across two windows against real Qdrant and TEI, plus a
  unit test asserting windowed and single-window commits store byte-identical
  results.
- **B3** the image installs `requirements.lock` instead of resolving pyproject.
  This immediately exposed the drift it was meant to prevent: the running image
  had `huggingface_hub` **1.29.0** while CI tested **0.36.2**.
- **B4** the `?token=` bootstrap is consumed once and erased from the URL via
  `replaceState`. The read-and-store logic had been copy-pasted into four
  modules; it is now one function.
- **B5** Alembic adopted. A pre-Alembic database is *stamped* at the baseline
  rather than migrated, which is safe by construction — the baseline was
  autogenerated from the same models `create_all` was building. Both paths were
  tested on scratch databases (fresh → 13 tables at head; adoption → stamped,
  zero DDL, data preserved, idempotent) before the live database was touched.
- **B6** mypy gate added, clean at 82 files with stub-driven debt registered in
  `pyproject.toml`.

### What the type gate found on its first run

`huggingface_hub` 1.x removed `local_dir_use_symlinks`, which the deepdoc model
downloader still passed — to `snapshot_download` (whose `TypeError` was
swallowed into a misleading "falling back" warning) and then to
`hf_hub_download` on the fallback path, where it was not caught at all.
Runtime model download was broken outright. It went unnoticed because the image
bakes those weights in at build time, and because the lock and the image had
drifted onto different versions of the library.

That is two findings converging on one bug: B3's drift is what let the
incompatible version reach production, and B6's gate is what saw it.
