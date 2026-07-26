---
docType: tasks
slice: cagg-freshness-assertion-for-derived-data-readers
project: trading-data
lldReference: project-documents/user/slices/168-slice.cagg-freshness-assertion-for-derived-data-readers.md
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [163]
interfaces: [162, 167]
dateCreated: 20260726
dateUpdated: 20260726
status: complete
---

# Tasks: Cagg freshness assertion for derived-data readers

## Context summary

A TimescaleDB refresh policy only reconsiders the last `start_offset` of data,
so any interruption longer than that leaves a hole that resuming the policy
**never heals**. Slice 163's Phase D hit this in production: job 1003
(`minute_4hour_ohlcv` refresh) was paused for restructuring and left paused; the
daemon's coverage index reads that exact cagg, so its leading edge froze while
raw `minute_ohlcv` kept growing, and ~349 of 4,198 symbols were re-seeded every
cycle for four days. It was silent — gap rows land under `ON CONFLICT DO
NOTHING`, so nothing errored.

163 added a `preflight()` guard covering exactly one path: maintenance tooling
refusing to repair a cagg while the coverage-index cagg's refresh is paused. The
other causes — crashed job, policy failing every fire, out-of-band `alter_job`,
restart mid-maintenance — never pass through maintenance tooling.

This slice adds `assert_cagg_fresh(conn, view_name)` in the **reader** path and
wires its first consumer, `build_minute_coverage_index`. Slice 167 is a
downstream hard dependent and consumes the helper unchanged.

### What already exists (verified in tree)

- `src/manta_trading/market/maintenance/` — `cagg_parity.py`, `cagg_repair.py`,
  `rechunk.py`. The new module goes here.
- `cagg_repair.py` already reads `timescaledb_information.jobs` (`job_id`,
  `proc_name`, `hypertable_name`, `scheduled`) — `hypertable_name` carries the
  *view* name for cagg refresh jobs. Reuse this shape; do not re-derive it.
- `cagg_parity._TimeoutConnection` — statement-timeout + backend-cancel
  discipline, plus `RepairError` in `cagg_repair.py` as the error-class
  precedent.
- `constants.py`: `GRANULARITY_SOURCE`, `MINUTE_OHLCV_TABLE`,
  `MINUTE_CAGG_GRANULARITIES`, `MINUTE_STALENESS_THRESHOLD` (`timedelta(days=1)`),
  `MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT`.
- `data/gaps/minute_coverage.py:34` `build_minute_coverage_index` already
  returns `None` on `psycopg.OperationalError` and its caller already skips
  coverage-aware seeding. The guard reuses that path — it does not invent one.
- `test/integration/test_rechunk_driver.py` builds a **scratch hypertable with
  its own cagg and its own refresh policy**, dropped per test. This is the
  isolation mechanism for the induced-staleness tests: pause the *scratch*
  policy, never a production job.
- Unit tests for maintenance live in `test/unit/market/`.

### Non-negotiables from the design

- Indeterminate freshness is **stale** (D3). Never fall back to a full-window
  seed (that reintroduces the 22-year re-seed 162 exists to prevent).
- Never auto-remediate (D4) — no `refresh_continuous_aggregate` in a read path.
- Threshold is `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)`. The ceiling
  is **required**: daily caggs use 21/90/270-day offsets, so a daily cagg
  stalled 100 days passes every `start_offset`-relative check.
- Staleness must be **induced**, never mocked, in the acceptance tests.

---

## Task 1 — Constants

- [x] **1.1 Add `MAX_COVERAGE_SOURCE_STALENESS` to `constants.py`**
  - [x] Value `timedelta(days=1)`, typed `timedelta`, beside the other staleness
        constants.
  - [x] Docstring states: one ceiling serves both the acquisition path and 167's
        status path; a derived read older than a full trading day is stale for
        either purpose. Note it is a full refresh cycle above every minute
        policy's `start_offset`, so it never fires on a healthy cagg.
  - Success: importable; no magic number appears at any call site.
  - Effort: 1

- [x] **1.2 Add `CAGG_FRESHNESS_CACHE_TTL` to `constants.py`**
  - [x] Value `timedelta(seconds=60)`, typed `timedelta`.
  - [x] Docstring states it is two orders of magnitude below
        `MAX_COVERAGE_SOURCE_STALENESS`, so a cached verdict can never mask a
        lag the uncached check would catch.
  - Success: importable; referenced only by the freshness module.
  - Effort: 1

---

## Task 2 — `FreshnessVerdict` and the staleness signals

- [x] **2.1 Create `src/manta_trading/market/maintenance/cagg_freshness.py`**
  - [x] Module docstring citing slice 168 and the 163 incident.
  - [x] `class StalenessSignal(StrEnum)` with one member per D1 signal plus the
        indeterminate causes: `LAG_EXCEEDS_THRESHOLD`, `NOT_SCHEDULED`,
        `LAST_SUCCESS_TOO_OLD`, `LAST_RUN_FAILED`, `NO_JOB_ROW`, `PROBE_FAILED`.
        No bare strings anywhere in dispatch or logging.
  - [x] `@dataclass(frozen=True) class FreshnessVerdict` with: `view_name: str`,
        `is_fresh: bool`, `signals: tuple[StalenessSignal, ...]`,
        `lag: timedelta | None`, `threshold: timedelta | None`,
        `detail: str` (human-readable, for the ERROR log).
  - Success: `mypy`/`pyright` clean; enum covers every branch used later.
  - Effort: 2

- [x] **2.2 Unit-test the verdict type and enum**
  - [x] `test/unit/market/test_cagg_freshness.py`: a fresh verdict reports
        `is_fresh=True` with empty `signals`; a stale one carries every signal
        that fired.
  - [x] Assert the enum has exactly the six expected members (guards against a
        signal being added without test coverage).
  - Success: tests pass.
  - Effort: 1

- [x] **Commit**: `feat(maintenance): add cagg freshness verdict type and staleness constants`

---

## Task 3 — Catalog read

- [x] **3.1 Implement the job-catalog read**
  - [x] `_read_refresh_job(conn, view_name) -> _JobRow | None` querying
        `timescaledb_information.jobs` joined to `job_stats` for: `job_id`,
        `scheduled`, `start_offset` (a.k.a. the refresh config offset),
        `last_run_status`, `last_successful_finish`.
  - [x] Match on `hypertable_name = view_name` for cagg refresh jobs, following
        the `cagg_repair.py` precedent — reuse that filter, do not re-derive it.
  - [x] Parameterized query (never f-string interpolation of `view_name`).
  - [x] Returns `None` when no row matches; the caller turns that into
        `NO_JOB_ROW`.
  - Success: one round trip supplies all four D1 catalog inputs.
  - Effort: 3

- [x] **3.2 Unit-test the catalog read against fakes**
  - [x] Fake cursor returning a populated row → parsed `_JobRow` with correct
        types (`start_offset` as `timedelta`, `scheduled` as `bool`).
  - [x] Empty result → `None`.
  - [x] Assert the SQL passes `view_name` as a bound parameter, not inlined.
  - Success: tests pass.
  - Effort: 2

---

## Task 4 — Edge probes and threshold

- [x] **4.1 Implement the two `max()` edge probes**
  - [x] `_cagg_max(conn, view_name)` and `_raw_max(conn, source_table)`,
        resolving the source table via `GRANULARITY_SOURCE` — never a hardcoded
        name (D5 / 163 precedent).
  - [x] Each probe sets an explicit `statement_timeout` (F001), sized to a small
        multiple of the measured probe cost (~0.19 s cagg, ~0.75 s raw). Put the
        timeout in `constants.py` if a suitable one is not already there; do not
        inline a literal.
  - Success: probes return `datetime | None`; timeout is set on every path.
  - Effort: 2

- [x] **4.1a Unit-test the probe timeout discipline**
  - [x] Assert `_cagg_max` and `_raw_max` each set `statement_timeout` on the
        cursor **before** executing the probe — on every code path, including
        the early-return/`None` paths. Use a recording fake that captures the
        statement order; assert the timeout precedes the `max()` query.
  - [x] Assert the timeout value comes from the constant, not an inline literal.
  - [x] Assert `view_name` / table name reach the probe through
        `GRANULARITY_SOURCE` resolution rather than string interpolation of
        caller input.
  - Rationale: D3 requires that "a hung catalog or edge query degrades to a
    refusal rather than stalling the caller." Task 5.3 covers the `except`
    branch once an error is raised; this covers whether the bound that raises
    it is actually configured. Missing `statement_timeout` on probe queries is
    the root-cause class of the 2026-07-20 prod incident.
  - Success: tests fail if any `SET statement_timeout` is removed.
  - Effort: 2

- [x] **4.2 Implement threshold resolution**
  - [x] `_resolve_threshold(start_offset) -> timedelta` returning
        `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)`.
  - [x] Handle `start_offset is None` (policy with no offset) by falling back to
        the ceiling alone.
  - Success: pure function, no I/O, trivially testable.
  - Effort: 1

- [x] **4.3 Unit-test threshold resolution including the 270-day case**
  - [x] Parametrized: `start_offset` of 1 h / 1 day / 21 days / 90 days /
        **270 days** each resolve to `min(offset, ceiling)`.
  - [x] **Regression test for design criterion 3**: a daily cagg with a 270-day
        `start_offset` stalled 100 days is judged **stale**. This test must
        **fail** if the ceiling is removed from `_resolve_threshold` — add a
        comment saying so explicitly.
  - [x] `start_offset is None` → ceiling.
  - Success: tests pass; removing the `min()` breaks the 270-day case.
  - Effort: 2

- [x] **Commit**: `feat(maintenance): add cagg catalog read, edge probes, and staleness threshold`

---

## Task 5 — `assert_cagg_fresh` core

- [x] **5.1 Implement the uncached evaluation**
  - [x] `_evaluate(conn, view_name) -> FreshnessVerdict` OR-ing all four D1
        signals: `raw_max - cagg_max > threshold`, `NOT scheduled`,
        `now() - last_successful_finish > threshold`,
        `last_run_status <> 'Success'`.
  - [x] Collect **every** signal that fired, not the first — the ERROR log names
        all of them.
  - Success: any single signal produces `is_fresh=False`; a healthy cagg
    produces `is_fresh=True` with empty `signals`.
  - Effort: 3

- [x] **5.2 Implement indeterminate-freshness handling (F001)**
  - [x] No job row for the view → **trip** with `NO_JOB_ROW`. A cagg with no
        refresh policy is never self-healing — the strongest form of the
        incident, not an exemption.
  - [x] View name not a cagg / not in `GRANULARITY_SOURCE` → raise `ValueError`.
        This is a caller bug and must **not** be absorbed into a refusal.
  - [x] Probe timeout, connection loss, any other `psycopg.Error` → **trip** with
        `PROBE_FAILED`, logged via `logger.exception`, never propagated into the
        reader's own error path.
  - [x] Add an inline comment at each `except` explaining why trapping is
        correct here (per the project exception rule).
  - Success: every new I/O path has an explicit, tested outcome.
  - Effort: 3

- [x] **5.3 Unit-test each signal in isolation (design criterion 2)**
  - [x] Four tests, one per D1 signal, each with the other three healthy.
  - [x] Three tests for the indeterminate modes: no job row → trip; non-cagg
        name → `ValueError`; injected `psycopg.Error` → trip with
        `PROBE_FAILED`.
  - [x] A fully healthy fixture → `is_fresh=True` (no false positive,
        criterion 4).
  - Success: eight tests pass; each fails if its signal is removed.
  - Effort: 3

- [x] **Commit**: `feat(maintenance): evaluate cagg freshness signals, fail safe on indeterminate`

---

## Task 6 — TTL verdict cache (D6)

- [x] **6.1 Implement the cache**
  - [x] `assert_cagg_fresh(conn, view_name) -> FreshnessVerdict` wrapping
        `_evaluate`, memoizing per **view name only** for
        `CAGG_FRESHNESS_CACHE_TTL`.
  - [x] Cache **stale** verdicts on the same terms as fresh ones — the cache must
        never convert a refusal into a pass.
  - [x] Process-local module state. Use an injectable clock (parameter or module
        seam) so expiry is testable without sleeping.
  - [x] Docstring: not for maintenance decisions — 163's `preflight()` remains
        the uncached, always-probing maintenance guard.
  - [x] Expose a cache-reset helper for tests.
  - Success: warm calls issue zero probe queries.
  - Effort: 3

- [x] **6.2 Unit-test the cache in both directions (design criterion 8)**
  - [x] Warm call issues **no** probe queries — assert by **query count** on a
        counting fake, not by timing.
  - [x] A cached **stale** verdict still refuses on the second call.
  - [x] Advancing the injected clock past the TTL causes a re-probe.
  - [x] Distinct view names do not share a cache entry.
  - Success: four tests pass.
  - Effort: 2

- [x] **Commit**: `feat(maintenance): cache cagg freshness verdicts with short TTL`

---

## Task 7 — Wire the first consumer

- [x] **7.1 Guard `build_minute_coverage_index`**
  - [x] In `src/manta_trading/data/gaps/minute_coverage.py`, call
        `assert_cagg_fresh` for `GRANULARITY_SOURCE[Granularity.H4]` before the
        coverage query.
  - [x] On trip: log ERROR naming the cagg, the measured lag, and which signals
        fired, then `return None` — reusing the existing `None` contract
        (documented at lines 37–52). Do not change the function signature or the
        caller.
  - [x] Never fall back to a full-window seed.
  - Success: existing `None`-on-failure semantics preserved; caller untouched.
  - Effort: 2

- [x] **7.2 Unit-test the consumer wiring**
  - [x] Guard trips → `build_minute_coverage_index` returns `None`, the coverage
        query never executes, and the ERROR names the cagg and lag.
  - [x] Guard passes → existing behavior byte-for-byte unchanged (index built as
        before).
  - Success: tests pass; the healthy path shows no behavior change.
  - Effort: 2

- [x] **Commit**: `feat(gaps): guard coverage-index build on cagg freshness`

---

## Task 8 — Induced-staleness integration tests (design criterion 1)

> A test that only asserts the helper was *called* does **not** satisfy this.
> Staleness is induced against a **scratch** hypertable + cagg + refresh policy
> built and dropped per test, following `test/integration/test_rechunk_driver.py`.
> Never pause a production job.

- [x] **8.1 Build the scratch fixture**
  - [x] `test/integration/test_cagg_freshness.py` with a scratch hypertable, its
        own cagg, and its own refresh policy; dropped on teardown.
  - [x] `pytestmark = pytest.mark.skipif(not TIMESCALE_URL, ...)` per the
        existing integration convention.
  - Success: fixture builds and tears down cleanly; leaves no scratch objects.
  - Effort: 3

- [x] **8.2 Induced-staleness acceptance test**
  - [x] Baseline: coverage build succeeds and seeds normally.
  - [x] Pause the **scratch** refresh policy, advance raw past `start_offset`,
        then assert: the guard trips, seeding is skipped, the ERROR names the
        cagg and the measured lag, and **no gap rows were written**.
  - Success: reproduces the 163 incident shape and refuses (criterion 7).
  - Effort: 3

- [x] **8.3 Granularity-agnostic test (design criterion 6)**
  - [x] Exercise the helper against a minute cagg and a daily cagg with no
        signature change.
  - Success: both pass; confirms 167 can consume it as-is.
  - Effort: 2

- [x] **8.3a Induced-slowness test — the timeout actually fires**
  - [x] Force a probe to exceed its `statement_timeout` against the scratch DB
        (e.g. temporarily set the probe timeout to a very small value, or block
        the probe with a competing lock / `pg_sleep`).
  - [x] Assert the call **returns a stale verdict with `PROBE_FAILED`** within a
        bounded wall-clock time rather than hanging the caller, and that the
        backend does not remain running afterward.
  - Rationale: pairs with 4.1a — that test proves the bound is configured, this
    proves it converts to a refusal in a live database (D3's stated property).
  - Success: bounded refusal, no hung caller, no orphaned backend.
  - Effort: 2

- [x] **8.4 Healthy-path and probe-cost check (criteria 4, 5)**
  - [x] Healthy scratch cagg passes with no false positive.
  - [x] Record probe timings against the ~1 s envelope. A single recorded
        measurement, not a benchmarking harness.
  - Success: no false positive; timings recorded in the task notes.
  - Effort: 2

- [x] **8.5 Record the load-test deferral (review F003)**
  - [x] **No `test/load/` task is added to this slice**, and the omission is a
        decision, not an oversight. Success criterion 8's closing clause
        ("repeated calls across a full-universe read amortize to well under the
        sub-second consumer NFR") describes behavior on a call path this slice
        does not create: 168's only consumer is
        `build_minute_coverage_index`, which calls the helper **once per daemon
        cycle**, where the ~1 s uncached cost is already proven negligible
        against a ~23 s index build. Full-universe repeated calls only exist
        once slice 167 wires `bars_summary`.
  - [x] Slice 167 owns that coverage: its D5 / success criterion 6 already
        specifies a CI-gated load test asserting full-universe read latency
        < 1 s. Duplicating it here would test a call pattern no shipped code in
        this slice performs.
  - [x] What 168 *does* own is the cache mechanism that makes amortization
        possible — covered by 6.2 (query-count assertions, stale-cached-still-
        refuses, TTL expiry, per-view isolation).
  - Rationale: follows the slice-166 D2 precedent of recording *why* no load
    test was added rather than silently omitting one.
  - Success: decision recorded; no load-test task in this slice.
  - Effort: 1

- [x] **Commit**: `test(maintenance): induced-staleness integration tests for cagg freshness guard`

---

## Task 9 — Close-out

- [x] **9.1 Quality gates**
  - [x] `ruff` clean, `mypy`/`pyright` zero errors on all touched files.
  - [x] Full unit suite for `test/unit/market/` and `test/unit/data/` passes
        (run per-subpackage — whole-`test/` collection is broken by a missing
        `__init__.py`, a known pre-existing issue).
  - Effort: 1

- [x] **9.2 Amend the 140-arch Constants section**
  - [x] Add `MAX_COVERAGE_SOURCE_STALENESS` and `CAGG_FRESHNESS_CACHE_TTL` with
        rationale, per review finding F003.
  - Effort: 1

- [x] **9.3 Verification walkthrough**
  - [x] Execute the slice design's walkthrough and record results.
  - [x] Confirm all eight success criteria are met; note any that are not.
  - Effort: 2

> Merging the slice branch is a workflow action, not an implementation task, so
> it is not tracked here. Commit discipline is covered by the per-task
> checkpoints above and the project convention in Notes.

---

## Notes

- **Do not auto-remediate.** A catch-up `refresh_continuous_aggregate` from
  inside a read path makes a heavy write the side effect of a read. Runbook R2
  stays authoritative for remediation (D4).
- **Slice 167 is a hard dependent** and consumes `assert_cagg_fresh` unchanged.
  Any signature change here is a change to 167's contract.
- The raw-edge probe is a bounded `max(time)` index scan, not an expression
  aggregate over compressed chunks — it is not the query shape behind the
  2026-07-20 prod incident. The `statement_timeout` discipline applies anyway,
  and is verified from both directions: 4.1a proves the bound is configured on
  every path, 8.3a proves it converts to a refusal in a live database.
- **Commit per task**, not batched at the end (project convention; slice 162
  precedent). Checkpoints follow Tasks 2, 4, 5, 6, 7, and 8.
- Design rules: journal 20260725 ADR, rule 3 (`start_offset` is a maintenance
  budget; long pauses are not self-healing) and rule 4 (silent-and-harmless is
  the hardest failure to find).
