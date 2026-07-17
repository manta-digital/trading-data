---
docType: tasks
slice: urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency
project: trading-data
lld: user/slices/166-slice.urgent-diagnose-and-fix-pathological-minute-ohlcv-query-latency.md
dependencies: [156, 160]
projectState: >
  Slice 162 (coverage-aware minute gap-seeding) merged to main. Production
  minute daemon is STOPPED (separate PM go/no-go, not part of this slice).
  minute_ohlcv is a 126 GB compressed hypertable with 25,256 four-hour chunks;
  trivial single-symbol MIN/MAX runs 10m47s. TimescaleDB 2.23.0 / PostgreSQL
  17.7. This slice diagnoses and fixes the table's chunk pathology.
dateCreated: 20260717
dateUpdated: 20260717
status: not_started
---

## Context Summary

- Working on the **166 — pathological `minute_ohlcv` query latency** slice.
- Root-cause hypothesis (design §Root-Cause Analysis): 25,256 four-hour chunks
  force any un-pruned query to open all chunks; the same interval fragments
  compression batches (85 GB TOAST). Fix = re-chunk to 7 days, in place.
- **Evidence before fix** (project rule): Phase A captures real `EXPLAIN`
  before any table mutation, and a scratch-hypertable rehearsal answers three
  open questions (batch rewrite, mixed windows, job collision) that the
  remediation depends on.
- **Two PM gates:** (1) end of Phase A — confirm root cause, select
  remediation option A/B/C, confirm backup point; (2) any fallback escalation.
- This slice delivers a usable `minute_ohlcv` and unblocks slices 163/164/182.
- Key constraints (carried from design + standing project guidance):
  - Do **not** restart the production minute daemon (separate PM decision).
  - Do **not** run unbounded `minute_ohlcv` reads casually — the Phase A
    `EXPLAIN (ANALYZE)` on the 10m47s query is a **deliberate, one-time**
    diagnostic cost, run knowingly.
  - Chunk interval defined **once** as a constant; no scattered `'7 days'`.
  - Per-window transactions; the merge driver must be resumable (Ctrl-C safe).
- Next planned slice after 166: **163** (minute-cagg chunk re-sizing), which
  builds on the healthy source table this slice produces.

## Anchors (verified 2026-07-17, do not re-derive)

- Migrations are dicts in `MINUTE_MIGRATIONS`
  (`src/manta_trading/market/schema/migrations/minute.py:473`). Each has
  `id` / `description` / `sql` **or** `python_fn` (+ optional
  `requires_autocommit`). Highest existing id is `042`; **next is `043`**.
  Registration is automatic via list membership (`migrations/__init__.py`);
  append procedure is in `migrations/README.md`.
- The 4-hour `create_hypertable` call is at
  `migrations/minute.py:531` (`chunk_time_interval => INTERVAL '4 hours'`).
- Constants live in `src/manta_trading/constants.py`.
- CLI maintenance-command template: `data extend` (slice 144) at
  `cli/commands/data.py:840` — resumable, idempotent, typed exit codes.
- `caggs_app` sub-app (`cli/commands/data.py:59`) already holds `refresh` /
  `status`; the merge driver command belongs here as `caggs merge-chunks` (it
  is minute-hypertable maintenance adjacent to the existing cagg tooling).
- Whole-`test/` collection is broken (missing `__init__.py`); run tests
  **per-subpackage**. `uv run pytest/mypy/ruff` require `--extra dev`.

---

## Phase A — Diagnose (evidence before any table mutation)

- [ ] **A1. Capture `EXPLAIN` (no ANALYZE) of the single-symbol MIN/MAX query.**
  - [ ] Run `EXPLAIN (VERBOSE, COSTS)` of
        `SELECT MIN(time), MAX(time) FROM minute_ohlcv WHERE symbol = '<sym>'`
        against prod `trading` DB (choose an active symbol, e.g. AAPL).
  - [ ] Record **planning time** and plan shape (expect a many-thousand-way
        chunk `Append`). Do **not** run ANALYZE in this task.
  - [ ] Success: EXPLAIN text captured verbatim into a Phase A scratch note
        for later transcription into the design's root-cause record.

- [ ] **A2. Capture `EXPLAIN (ANALYZE, BUFFERS)` of the same query — one
      deliberate diagnostic run.**
  - [ ] Run once, knowingly accepting the ~10m cost, on prod. Capture actual
        total time, per-node timing, and buffer counts.
  - [ ] Identify where execution time concentrates (chunk-open/plan overhead
        vs decompression vs heap reads).
  - [ ] Success: ANALYZE output captured; a one-line conclusion states whether
        the chunk-count hypothesis is **confirmed** or **contradicted**.

- [ ] **A3. Sample lock-table pressure during A2.**
  - [ ] From a second connection while A2 runs, sample
        `SELECT count(*) FROM pg_locks WHERE pid = <A2 backend pid>`.
  - [ ] Success: peak lock count recorded (expected in the thousands,
        corroborating why `max_locks_per_transaction` was raised to 2048).

- [ ] **A4. Build a scratch hypertable mirroring `minute_ohlcv` compression +
      one attached cagg.**
  - [ ] Create a throwaway hypertable with 4-hour chunks, `segmentby=symbol`,
        `orderby=time DESC`, populated with enough synthetic multi-symbol data
        to produce dozens of small chunks; compress the older chunks; attach
        one continuous aggregate with a refresh policy.
  - [ ] Success: scratch table exists with ≥40 compressed chunks and a
        working cagg; teardown SQL noted for cleanup.

- [ ] **A5. Rehearse `merge_chunks` on the scratch table — answer the three
      gating questions.** (Design §Phase A step 4.)
  - [ ] **Batch rewrite:** merge a window of adjacent compressed chunks;
        compare per-batch row counts and TOAST size before/after. Record
        whether batches are rebuilt, carried over fragmented, or whether merge
        requires decompress-first (and if so, the transient size delta).
  - [ ] **Mixed windows:** attempt a merge over a window containing both a
        compressed and an uncompressed chunk; record the behavior (succeeds /
        errors / requires uniform state).
  - [ ] **Job collision:** trigger a cagg refresh (or compression) against a
        chunk mid-merge; record whether it blocks, errors, or corrupts, to
        confirm the Phase C job-pause is necessary and sufficient.
  - [ ] Verify the cagg still refreshes and returns correct results after the
        merge.
  - [ ] Success: all three questions answered in writing; each answer maps to
        a concrete Phase C decision (recompress pass yes/no, mixed-window
        handling, job-pause confirmed). Scratch table torn down.

- [ ] **A6. Consult TimescaleDB 2.23 `merge_chunks` docs to corroborate the
      rehearsal.**
  - [ ] Use context7 (`/timescale/timescaledb`) and/or official docs for
        `merge_chunks` restrictions on compressed and cagg-attached
        hypertables; note any documented constraint the rehearsal did not
        surface.
  - [ ] Success: doc findings recorded alongside A5; any conflict between docs
        and rehearsal flagged for the A7 gate.

- [ ] **A7. Transcribe Phase A evidence into the design's root-cause record and
      reach the PM decision gate.** (Design §Success Criterion 6.)
  - [ ] Append A1–A3 EXPLAIN/lock evidence to the design doc under a
        "Root-Cause Record" heading; state hypothesis confirmed or corrected.
  - [ ] Commit: `review: record slice 166 Phase A root-cause evidence`.
  - [ ] **PM gate (stop here):** PM confirms (a) root cause matches
        hypothesis, (b) remediation Option **A / B / C** selected per the A5
        rehearsal, (c) a DB snapshot/backup point exists before any bulk
        mutation. Do not proceed to Phase B until confirmed.
  - [ ] Success: gate outcome recorded in the design doc; if EXPLAIN
        contradicted the hypothesis, this slice's design is revised with the
        PM rather than proceeding (design §Risk "Hidden second bottleneck").

---

## Phase B — Migration & config (future chunks + doc truth)

- [ ] **B1. Add `MINUTE_OHLCV_CHUNK_INTERVAL` constant.**
  - [ ] Add to `constants.py` a single `timedelta`/interval constant for the
        target interval (7 days per design §Target chunk interval, unless the
        A7 gate selected otherwise). Document its origin (slice 166) in a
        comment.
  - [ ] Success: constant exists; `grep -rn "4 hours\|INTERVAL '7 days'"` shows
        no competing literal is introduced.

- [ ] **B2. Reference the constant from the `create_hypertable` migration.**
  - [ ] Edit `migrations/minute.py:531` so the `chunk_time_interval` derives
        from `MINUTE_OHLCV_CHUNK_INTERVAL` (interpolated into the migration
        SQL), not a hardcoded `INTERVAL '4 hours'`. A cold-start DB must create
        chunks at the new interval from the first run.
  - [ ] Success: the create-hypertable migration no longer contains a literal
        `'4 hours'`; the value traces to the constant.

- [ ] **B3. Add migration `043` — `set_chunk_time_interval` for the existing
      hypertable.**
  - [ ] Append a dict to `MINUTE_MIGRATIONS` (id `043_minute_chunk_interval_7d`,
        clear `description` with manual-revert note) calling
        `set_chunk_time_interval('minute_ohlcv', <constant>)`. This governs
        **future** chunks only and is safe/idempotent regardless of the
        remediation option chosen.
  - [ ] Success: `mt data migrate status` lists `043`; applying it against a
        DB where it already ran is a no-op.

- [ ] **B4. Test: migration + cold-start chunk interval.** (test-with B2/B3)
  - [ ] Add a test (dev/fixture DB or a migration-level test in the existing
        schema-migration test module) asserting that after the migration chain
        runs, `minute_ohlcv`'s dimension `time_interval` equals the constant.
  - [ ] Success: test passes via per-subpackage `uv run --extra dev pytest`;
        it fails if the interval reverts to 4 hours.

- [ ] **B5. Update architecture docs stating the old interval.** (Design
      §Phase B 6a; review F004.)
  - [ ] Update `user/architecture/100-arch.data-storage.md:67`
        ("`minute_ohlcv` (4hr chunks)") and the chunk-sizing rationale at `:124`
        ("1hr vs 4hr") to the new interval, noting slice 166 as the change.
  - [ ] `grep -rn "4hr\|4 hour\|4-hour" project-documents/user/architecture/`
        and fix any other doc restating minute chunks as 4-hour.
  - [ ] Success: no architecture doc still asserts a 4-hour `minute_ohlcv`
        interval; grep is clean.

- [ ] **B6. Commit Phase B.**
  - [ ] `feat: re-chunk minute_ohlcv to 7-day interval (migration 043)` plus
        the doc update. Buildable checkpoint before the bulk operation.

---

## Phase C — Execute remediation (resumable bulk merge)

- [ ] **C1. Implement the merge driver as `mt data caggs merge-chunks`.**
  (Design §Merge driver.)
  - [ ] Add a `caggs_app` command modeled on `data extend` (slice 144):
        enumerate current `minute_ohlcv` chunks from the Timescale catalog,
        group into target 7-day windows, and merge one window per transaction.
  - [ ] Skip windows already a single chunk (idempotent — safe to re-run).
  - [ ] Skip **and log** windows containing any uncompressed chunk (the
        trailing chunks inside `compress_after`), per design; handling matches
        the A5 mixed-window answer.
  - [ ] Emit `merged W/<total> windows` progress; stop cleanly on first error
        with the failing window identified and a non-zero exit code.
  - [ ] Provide `--dry-run` (report the window plan and counts; mutate
        nothing). No other configuration surface.
  - [ ] Pre-flight: assert the minute background jobs are paused (see C3);
        refuse to run otherwise. Job IDs resolved from the catalog at runtime,
        **not** hardcoded.
  - [ ] Success: `--dry-run` prints a coherent window plan (~600 windows over
        ~25k chunks) and mutates nothing.

- [ ] **C2. Test: merge driver on the scratch/fixture hypertable.**
  (test-with C1)
  - [ ] Reusing the A4-style scratch setup (or a fixture), assert: (a)
        `--dry-run` mutates nothing; (b) a real run reduces chunk count for a
        multi-window range; (c) re-running is a no-op (idempotent); (d) a
        deliberate mid-run interrupt leaves a valid, partially-merged table and
        a subsequent run completes the remainder; (e) pre-flight refuses when a
        job is unpaused.
  - [ ] Success: all assertions pass per-subpackage; **no test touches the
        126 GB prod table.**

- [ ] **C3. Pause minute-family background jobs (operational).**
  (Design §Phase C step 8; review F002.)
  - [ ] `alter_job(<id>, scheduled => false)` for the minute cagg refresh
        policies (jobs 1002, 1003, 1007, 1008) and the minute columnstore
        policy (1009) — IDs resolved from
        `timescaledb_information.jobs` at runtime, not assumed.
  - [ ] Success: `SELECT job_id FROM timescaledb_information.jobs WHERE
        scheduled = false` lists exactly the five minute-family jobs.

- [ ] **C4. Confirm backup point, then run the merge against prod.**
  - [ ] Verify the PM-confirmed snapshot/backup (A7 gate) is in place.
  - [ ] Run `mt data caggs merge-chunks` against prod (daemon stopped, jobs
        paused). Deliberately Ctrl-C once early and resume, proving
        resumability on the real table.
  - [ ] Success: chunk count for `minute_ohlcv` falls to ~1,200; progress log
        shows all windows processed; the interrupted run resumed cleanly.

- [ ] **C5. Conditional recompression pass.** (Design §Phase C step 10;
      review F003.)
  - [ ] **Only if** the A5 rehearsal showed merged chunks retain fragmented
        batches: recompress each merged chunk so batches rebuild at proper
        size. If rehearsal showed merge already rebuilds batches, record that
        and skip this task explicitly.
  - [ ] Success: sampled merged chunks report full-size compression batches;
        or a recorded note that recompression was unnecessary per A5.

- [ ] **C6. Resume paused jobs and confirm catch-up.**
  (Design §Phase C step 11; review F002.)
  - [ ] `alter_job(<id>, scheduled => true)` for all five jobs paused in C3.
  - [ ] Confirm cagg refresh policies catch up over their normal windows and
        the columnstore policy re-engages.
  - [ ] Success: `SELECT job_id FROM timescaledb_information.jobs WHERE
        scheduled = false` returns **zero rows** (design §Success Criterion 9).

- [ ] **C7. `ANALYZE minute_ohlcv` and re-check row-count sanity.**
  - [ ] Run `ANALYZE`; re-check `approximate_row_count('minute_ohlcv')` — the
        pre-fix 64.2 B figure should correct to a plausible value.
  - [ ] Success: corrected row count recorded for the design's root-cause
        record.

- [ ] **C8. Commit Phase C tooling.**
  - [ ] `feat: add resumable minute_ohlcv chunk-merge maintenance command`
        (driver + tests). The prod run itself is operational, not a code
        commit, but its outcome is recorded in Phase D.

---

## Phase D — Verify (prove the fix; record evidence)

- [ ] **D1. Re-run the three T15 queries and capture before/after.**
  (Design §Success Criteria 1, 2; Verification Walkthrough 1–2.)
  - [ ] Single-symbol MIN/MAX: expect **low seconds** (was 10m47s), with a
        fresh `EXPLAIN (ANALYZE, BUFFERS)` showing the collapsed chunk count.
  - [ ] Universe-wide `NOT EXISTS` probe: expect ~3–20 s (was 8m8s).
  - [ ] Success: both timings and the post-fix EXPLAIN captured.

- [ ] **D2. Measure `data_status` full-universe latency against the NFR.**
  (Design §Success Criterion 8; review F001.)
  - [ ] Time `SELECT count(*) FROM data_status` (or `mt data status`) before
        and after remediation. The view's `bars_summary` CTE full-scans
        `minute_ohlcv`, so it must be dramatically faster post-fix.
  - [ ] Restate the 140-arch NFR ("view latency stays sub-second at
        full-universe scope") against the measured result.
  - [ ] Success: before/after latency recorded. **If** post-fix latency still
        misses sub-second, record the actual and **raise to the PM** whether a
        cagg-backed `bars_summary` rewrite is a follow-up slice — do not
        silently leave the NFR unmet nor widen this slice into a view redesign.

- [ ] **D3. Integrity checks — no data loss.** (Design §Success Criteria 4, 5.)
  - [ ] For ≥3 sampled symbols, confirm bounded-window `count(*)`, `MIN(time)`,
        `MAX(time)` are identical to pre-merge captures (capture the baselines
        before C4 if not already recorded).
  - [ ] Confirm the 162 grouped coverage query returns identical results and
        all four minute caggs refresh and serve identical query results.
  - [ ] Success: every integrity comparison matches exactly; any mismatch
        halts and is escalated.

- [ ] **D4. Storage re-measurement.** (Design §Success Criterion 7.)
  - [ ] `hypertable_detailed_size('minute_ohlcv')` and compression stats;
        confirm total drops materially from 126 GB (expected ~30–40 GB) and
        TOAST far below 85 GB, and that ~1,200 chunks are compressed.
  - [ ] Success: actual sizes recorded; if TOAST did not collapse, cross-check
        against the C5 recompression decision.

- [ ] **D5. Cold-start verification.** (Design §Verification Walkthrough 7.)
  - [ ] On a fixture/dev DB, run `mt data init` and confirm `minute_ohlcv`'s
        `chunk_time_interval` equals the new constant (guards B2/B3 end-to-end).
  - [ ] Success: fresh DB creates 7-day chunks from the migration chain.

- [ ] **D6. Finalize root-cause record and close out the slice.**
  - [ ] Append D1–D5 evidence (timings, EXPLAINs, sizes, corrected row count)
        to the design's Root-Cause Record; set the design's Verification
        Walkthrough from "draft" to final with the real numbers.
  - [ ] Commit: `docs: record slice 166 verification results and close out`.
  - [ ] Check off slice 166 in `140-slices.data-quality-operations.md`.
  - [ ] Success: design doc carries the full before/after evidence; the two
        prerequisite consumers (163, 164) can proceed on a healthy table.

---

## Coverage Check (design element → task)

- Phase A diagnosis (EXPLAIN, lock sampling) → A1–A3
- Scratch rehearsal + 3 gating questions → A4–A6
- PM decision gate → A7
- Constant + create-hypertable reference → B1, B2
- Migration 043 (`set_chunk_time_interval`) → B3, B4
- Architecture-doc truth (F004) → B5
- Resumable merge driver → C1, C2
- Background-job pause/resume (F002) → C3, C6
- Prod merge run + resumability → C4
- Conditional recompression (F003) → C5
- ANALYZE / row-count sanity → C7
- T15 query re-run + EXPLAIN → D1
- `data_status` NFR (F001) → D2
- No-data-loss + cagg integrity → D3
- Storage reclaim → D4
- Cold-start → D5
- Root-cause record + slice close → A7, D6

Effort: 3/5. Risk: High (prod bulk mutation), mitigated by rehearsal,
resumable per-window execution, and the A7 backup/decision gate.
