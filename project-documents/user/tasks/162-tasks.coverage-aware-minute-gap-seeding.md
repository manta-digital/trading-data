---
docType: tasks
slice: coverage-aware-minute-gap-seeding
project: trading-data
lld: user/slices/162-slice.coverage-aware-minute-gap-seeding.md
dependencies: [145, 146]
projectState: >
  Slices 141–161 complete; 140 initiative band reopened for 162/163/164.
  Production minute daemon is STOPPED (do not restart until this slice lands).
  The minute branch of update_data_gaps seeds one [history_start≈2004, today]
  gap row, which the most-recent-first chunk loop re-walks in ~69 paid EODHD
  chunks per long-lived symbol. The daily path already computes coverage-aware
  ranges via compute_missing_ranges. No schema/migration change in this slice.
dateCreated: 20260716
dateUpdated: 20260716
status: not_started
---

## Context Summary

- Working on slice 162: make minute gap-seeding **coverage-aware** so a restart
  seeds only genuinely-missing trading sessions instead of a full-history span.
- **Root bug:** [`update_data_gaps.py:131-141`](../../../src/manta_trading/data/gaps/update_data_gaps.py)
  emits one `GapRange(history_start, today)` for minute; for a long-lived symbol
  `history_start` ≈ 2004, so the daemon re-fetches data already present.
- **Fix (3 pieces):** (1) one grouped query on the `minute_4hour_ohlcv` cagg →
  `{symbol: set[covered_day]}`; (2) per-symbol diff of covered days vs the
  trading-session calendar → only-missing `GapRange`s; (3) thread that into the
  seed path. Plus seed-phase progress output and a `_has_any_gaps` regression test.
- **Key constraints (from design + project rules):**
  - Cagg name comes from `GRANULARITY_SOURCE[Granularity.H4]`, never a literal.
  - Reuse the daily contiguous-run grouping (`_group_into_ranges`) — promote to a
    shared helper, do not duplicate (DRY).
  - Batch query must **fail safe**: on timeout/error, skip coverage-aware seeding
    this cycle; **never** fall back to the old full-window seed; never halt the daemon.
  - Represent "index unavailable this cycle" explicitly (e.g. `None`), distinct
    from an empty index.
  - Diff at **day** granularity (a session either has intraday bars or it does not).
  - Reject seed-forward-from-`MAX(time)` (silent past-data loss).
  - The 24-month `MINUTE_HISTORY_MONTHS` NFR is a dead AlphaVantage workaround —
    do **not** implement it; remove it from the 140 arch doc (doc-only).
- **Dependencies:** slice 145 (`data_gaps`/`compute_missing_ranges` machinery),
  slice 146 (`run_minute_cycle`/`_do_minute_symbol` seed path).
- **This slice delivers:** the unblock for restarting the production minute fetch.
- **Next planned slice:** 163 (minute-cagg chunk re-sizing).
- **Verification caveat:** MCP `trading_app` cannot `SELECT` OHLCV/gap tables —
  all raw-table/cagg reads in T-final are run by the operator via `psql` as
  `postgres`/owner against `trading` (`<db-host>:5432/trading`).

---

## Tasks

- [ ] **T1 — Promote `_group_into_ranges` to a shared helper (pure refactor)**
  - [ ] In [`compute_missing_ranges.py`](../../../src/manta_trading/data/gaps/compute_missing_ranges.py),
    rename the private `_group_into_ranges` to a module-level public
    `group_sessions_into_ranges` with the **same signature and body**; keep it
    importable by other modules in `data/gaps/`.
  - [ ] Update the one internal call site in `compute_missing_ranges` to the new name.
  - [ ] Export it as appropriate for reuse (e.g. add to `data/gaps/__init__.py`
    if that is the established import surface — match the existing pattern).
  - [ ] Success: no behavior change to the daily path; grouping logic is now a
    single shared function (DRY).

- [ ] **T2 — Verify the refactor (daily path unchanged)**
  - [ ] Run `uv run pytest test/unit/data/gaps/test_compute_missing_ranges.py -q`
  - [ ] Expected: all existing `compute_missing_ranges` tests pass unmodified.
  - [ ] Run `uv run pyright src/manta_trading/data/gaps/compute_missing_ranges.py`;
    zero new errors.
  - [ ] **Commit**: `refactor(gaps): extract group_sessions_into_ranges shared helper`

- [ ] **T3 — Add the coverage-index statement-timeout constant**
  - [ ] In [`constants.py`](../../../src/manta_trading/constants.py) add a named
    constant for the coverage-index query statement timeout (a small multiple of
    the measured ~3s scan, e.g. 30s), with a docstring explaining it bounds the
    per-cycle universe-wide cagg scan. Do **not** hard-code the value at the call
    site (no magic defaults).
  - [ ] Success: constant is defined, typed, and documented; `pyright` clean.

- [ ] **T4 — Implement `build_minute_coverage_index` in new `data/gaps/minute_coverage.py`**
  - [ ] Create `src/manta_trading/data/gaps/minute_coverage.py`.
  - [ ] `build_minute_coverage_index(conn) -> dict[str, set[date]] | None`:
    - Resolve the cagg name from `GRANULARITY_SOURCE[Granularity.H4]` (import from
      `constants`) — **not** a string literal.
    - Issue the grouped query
      `SELECT symbol, date_trunc('day', time_bucket) FROM <cagg> GROUP BY symbol,
      date_trunc('day', time_bucket)` under the T3 statement timeout
      (`SET LOCAL statement_timeout`), building `{symbol: set[date]}`.
    - **Fail safe:** on the specific psycopg timeout/operational error, log at
      ERROR via `logger.exception` and **return `None`** (signals "index
      unavailable this cycle"). Do not raise; do not return `{}` for a failure
      (an empty dict is the valid "no symbol covered" state, kept distinct).
  - [ ] Success: function returns a populated dict on success, `None` on query
    failure; cagg name is sourced from `GRANULARITY_SOURCE`; parameterized/`SET
    LOCAL` SQL only; `pyright` clean.

- [ ] **T5 — Implement `compute_missing_minute_sessions` in `data/gaps/minute_coverage.py`**
  - [ ] `compute_missing_minute_sessions(conn, symbol, coverage_index, from_ts, to_ts) -> list[GapRange]`:
    1. Clamp `[from_ts, to_ts]` to lifecycle dates exactly as
       `compute_missing_ranges._clamp_to_lifecycle` does (`first_listing_date`/
       `first_data_date` lower bound, `delisted_date` upper bound). Reuse that
       helper if it can be shared cleanly; otherwise mirror its logic without
       duplicating SQL shape unnecessarily.
    2. Fetch the symbol's trading sessions over the clamped window using the
       **same** `trading_sessions ⨝ instruments` join `_fetch_sessions` uses,
       projected to session **day** (`session_open_utc::date`).
    3. `covered_days = coverage_index.get(symbol, set())`.
    4. `missing = [s for s in sessions if s.date() not in covered_days]`.
    5. Group contiguous missing sessions into `GapRange`s via the shared
       `group_sessions_into_ranges` (T1). Return the list (empty ⇒ fully covered).
  - [ ] Success: returns only-missing ranges; empty list for a fully-covered
    symbol; full-history ranges for an empty symbol; respects the delisted clamp;
    `pyright` clean.

- [ ] **T6 — Unit tests for `minute_coverage.py`**
  - [ ] Create `test/unit/data/gaps/test_minute_coverage.py`.
  - [ ] `build_minute_coverage_index`: shape/grouping correctness against a mocked
    or fixture cagg result; **`None` on simulated query timeout** (fail-safe path);
    `{}` (empty) on a genuinely-empty cagg is distinct from `None`.
  - [ ] `compute_missing_minute_sessions`, parametrized cases:
    - **past-hole**: covered days with an interior gap → ranges cover only the hole.
    - **fully-covered**: every session covered → empty list.
    - **empty symbol**: no covered days → ranges span full clamped history.
    - **delisted clamp**: `delisted_date` set → no ranges past delisting.
  - [ ] Mock the DB I/O boundary; test the diff/grouping logic with real data
    (per project testing rules).
  - [ ] Run `uv run pytest test/unit/data/gaps/test_minute_coverage.py -q`; all pass.
  - [ ] **Commit**: `feat(gaps): add coverage-aware minute session diff`

- [ ] **T7 — Add `precomputed_ranges` path to `update_data_gaps`**
  - [ ] In [`update_data_gaps.py`](../../../src/manta_trading/data/gaps/update_data_gaps.py),
    add an optional `precomputed_ranges: list[GapRange] | None = None` parameter.
  - [ ] When provided (minute path), skip the single-span short-circuit at
    lines 131-141 and insert **exactly those ranges** through the existing Step-5
    INSERT-with-carry-forward loop (carry-forward, RETRY_EXHAUSTED promotion, and
    acquisition_state update all **unchanged**).
  - [ ] When `None` (daily path and any legacy caller), behavior is **identical**
    to today — no change to the daily `compute_missing_ranges` branch.
  - [ ] Success: additive parameter; daily and `mt data refetch` call sites
    unaffected; `pyright` clean.

- [ ] **T8 — Unit tests for the `precomputed_ranges` path**
  - [ ] Extend `test/unit/data/gaps/test_update_data_gaps.py`:
    - Passing `precomputed_ranges` inserts exactly those ranges (not one span).
    - Carry-forward `attempt_count` is preserved for a re-seed over a prior status.
    - Omitting `precomputed_ranges` leaves the daily path behavior byte-for-byte
      unchanged (existing assertions still pass).
  - [ ] Run `uv run pytest test/unit/data/gaps/test_update_data_gaps.py -q`; all pass.
  - [ ] **Commit**: `feat(gaps): update_data_gaps accepts precomputed minute ranges`

- [ ] **T9 — Wire the seeder into the daemon + seed-phase progress + fail-safe**
  - [ ] In [`daemon/minute.py`](../../../src/manta_trading/data/acquisition/daemon/minute.py):
    - `run_minute_cycle`: call `build_minute_coverage_index(conn)` **once** before
      the per-symbol loop; hold the result (`dict | None`) and thread it into
      `_process_minute_symbol` / `_do_minute_symbol`.
    - `_do_minute_symbol`: when `_needs_seed` fires (trigger **unchanged**), and
      the coverage index is available, call `compute_missing_minute_sessions(...)`
      and pass the result to `update_data_gaps(..., precomputed_ranges=...)`.
    - **Fail-safe:** when the coverage index is `None` (build failed this cycle),
      **skip coverage-aware seeding** — proceed using existing gap rows only; do
      **not** emit the old `[history_start, today]` full-window seed.
  - [ ] Seed-phase progress: accumulate `symbols scanned` and `gap rows seeded`
    in `run_minute_cycle`; emit an INFO line periodically (e.g. every 250 symbols)
    and a `complete` line, per design §Seed-phase progress output. Route through
    the existing `_logger` (and respect the `-v` progress convention already used
    by `mt data pull`).
  - [ ] Success: covered symbols seed zero rows; missing sessions seed only their
    ranges; a simulated index-build failure degrades to "known gaps only" without
    a full-window re-seed; progress lines appear; `pyright` clean.

- [ ] **T10 — Daemon-level tests: coverage-aware seed + fail-safe**
  - [ ] Add tests under `test/unit/data/acquisition/daemon/` covering:
    - Seed path passes coverage-derived ranges to `update_data_gaps` (not a span).
    - `coverage_index is None` ⇒ seed path skips coverage seeding and does **not**
      call `update_data_gaps` with a full-window span (fail-safe assertion).
    - Progress accumulation produces the expected counts.
  - [ ] Run `uv run pytest test/unit/data/acquisition/daemon/ -q`; all pass.
  - [ ] **Commit**: `feat(daemon): coverage-aware minute seeding with fail-safe`

- [ ] **T11 — `_has_any_gaps` re-fire regression test**
  - [ ] Add a regression test under `test/unit/data/acquisition/daemon/` pinning
    the interaction: for a symbol **with bars** whose gap rows were deleted (so
    `_has_any_gaps` is false and `_needs_seed` fires), the re-seed recreates
    **only genuinely-missing sessions**, **not** a 2004→today span.
  - [ ] Reference design §Carry-forward / re-fire correctness and Success
    Criterion 4.
  - [ ] Run the daemon test module; the new test passes.
  - [ ] **Commit**: `test(daemon): pin _has_any_gaps re-seed to real holes only`

- [ ] **T12 — Full local test + static-analysis pass**
  - [ ] `uv run pytest test/ -q` — all pass; zero new failures (daily path
    behavior unchanged).
  - [ ] `uv run ruff check src/ test/` — clean.
  - [ ] `uv run pyright src/ test/` — zero errors.
  - [ ] A failure here is a **STOP** condition — do not proceed to production
    verification with a red suite.

- [ ] **T13 — Remove the obsolete `MINUTE_HISTORY_MONTHS` NFR (doc-only)**
  - [ ] In [`140-arch.data-quality-operations.md`](../architecture/140-arch.data-quality-operations.md),
    remove the `MINUTE_HISTORY_MONTHS = 24` constant (≈ line 1029) and the minute
    `history_months = 24` default in the Target window section (≈ lines 157-162),
    replacing with a one-line note that minute history is full-to-`EODHD_INTRADAY_HORIZON`,
    narrowable via `MT_MINUTE_HISTORY_START` (per slice 162 §History window).
  - [ ] Do **not** change `_resolve_minute_history_start` or any code — this is a
    doc reconciliation only.
  - [ ] Success: the arch doc no longer contradicts the shipped operator-floor model.
  - [ ] **Commit**: `docs(arch): remove obsolete 24-month minute-history NFR`

- [ ] **T14 — Operational-fix re-audit (confirming pass)**
  - [ ] Walk the four fixes per design §Operational-Fix Re-Audit and confirm no
    behavior regression against the new seeder:
    - `_has_any_gaps` trigger — now correct (pinned by T11).
    - EODHD 404→EMPTY, `httpx.TimeoutException` retry, `PoolTimeout` WARNING —
      unaffected (orthogonal / below the gap layer / process-boundary handler).
  - [ ] Success: re-audit confirms three no-change verdicts and one test-covered
    verdict; note the outcome in the slice walkthrough. No code change expected.

- [ ] **T15 — Production verification walkthrough (operator-assisted)**
  - [ ] Run the slice-design Verification Walkthrough end to end against `trading`
    (operator runs the `psql`/cagg reads; MCP `trading_app` cannot read those
    tables). Capture concrete outputs:
    - Coverage-index EXPLAIN (~3s, single Finalize HashAggregate over Gather).
    - A **fully-covered** symbol: delete its minute gap rows, run one scoped
      `mt data pull --granularity minute --symbols <sym> -v`, confirm **near-zero
      chunks** (not ~69) and **zero/near-zero** new gap rows.
    - A **partially-covered** symbol with a known interior hole: only the hole is
      seeded.
    - Seed-phase progress lines appear (`mt data pull --granularity minute -v |
      grep 'minute seed:'`).
    - Record the coverage-index **memory footprint** on the full universe (design
      Risk item) — if it exceeds a comfortable bound, note the batched-index
      fallback (query already supports `WHERE symbol = ANY(...)`); do not change
      the design unless it actually exceeds the bound.
  - [ ] Fill the concrete symbols/counts/EXPLAIN back into the slice-design
    Verification Walkthrough so an external agent can replay it verbatim.
  - [ ] **Do not restart the production minute daemon** as part of this task —
    that is a separate PM go/no-go after this slice merges.
  - [ ] A walkthrough step that does not behave as described is a **STOP**
    condition — confer with the Project Manager before marking the slice complete.

- [ ] **T16 — Final commit + slice closeout**
  - [ ] Update the slice-design frontmatter `status` → `complete` and
    `dateUpdated`; ensure the Verification Walkthrough reflects captured output.
  - [ ] Update `CHANGELOG.md` per `project-guides/templates/changelog-format.md`.
  - [ ] Run `workflow_check` (or `cf check`) with fix; resolve any findings.
  - [ ] **Commit**: `docs: close out slice 162 — coverage-aware minute gap-seeding`
