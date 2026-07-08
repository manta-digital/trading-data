---
docType: tasks
slice: 146-long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
project: trading
lld: user/slices/146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md
part: 2
partOf: 146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-1.md
dependencies:
  - 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
projectState: >
  Continuation of part 1. By T21 entry, parts 1's tasks are complete:
  branch created, `QuotaBucket` shipped, named symbol lists +
  `mt data lists` CLI shipped, `ca_drift` module shipped and
  integrated into `run_daily_cycle` / `run_minute_cycle`, runner
  module + cycle-due predicates + SIGTERM handling shipped (with a
  placeholder `run_ca_update_bulk` no-op). Part 2 finishes the
  slice: `mt data ca` command group, wiring the runner's
  `ca_update_due` step to it, the `mt data daemon run` CLI command,
  the legacy-command deletions, and the closeout (verification
  walkthrough, CHANGELOG, status flip).
dateCreated: 20260503
dateUpdated: 20260503
status: complete
---

## Context Summary

- Picks up where part 1 ends. T20 (SIGTERM via runner-direct test)
  is the last task in part 1; T21 starts the `mt data ca` group.
- Part 2's STOP-GATE is T29 (behavior-diff between new runner and
  legacy one-shot commands). Do not delete legacy commands until
  the diff is clean.
- Closeout tasks (T32–T36) include the standard slice-completion
  flow: walkthrough verification, CHANGELOG, status flip,
  `cf check --fix`.

---

## Tasks

### `mt data ca` command group (Decision D)

- [x] **T21. Create `mt data ca` Typer sub-app — `ca update`**
  - [x] In `cli/commands/data.py`, add `ca_app = typer.Typer(name="ca", ...)`
    and `data_app.add_typer(ca_app, name="ca")`.
  - [x] `mt data ca update [--since DAYS_OR_DATE] [--symbol SYMBOL | --list NAME]`:
    - No flags: bulk-fetch yesterday's splits + dividends via
      `/eod-bulk-last-day/US?type=splits&date=...` and
      `?type=dividends&date=...` (200 credits). Upsert via the
      existing splits/dividends repository helpers.
    - `--since N` (int): bulk-fetch trailing N days, per-day.
    - `--since YYYY-MM-DD` (date): bulk-fetch from date through
      yesterday, per-day.
    - `--symbol X`: per-symbol full-history backfill via the existing
      `manta_trading.data.adjustment.ingest` per-symbol path
      (calls `/splits/X` + `/div/X`, 2 credits). Reuses the
      slice-127 implementation; new CLI is a thin wrapper.
    - `--list NAME`: per-symbol backfill across each list member.
    - `--symbol` and `--list` are mutually exclusive — Typer
      validation; error exit nonzero if both given.
    - No `--type` flag (splits + dividends always paired).
    - No `--date YYYY-MM-DD` (single historical day not supported).
  - [x] Each variant calls `bucket.consume()` with the appropriate
    `CallType` before the HTTP call. CLI invocation creates a
    fresh per-process bucket.
  - [x] Success: all four invocation shapes parse and dispatch to
    the right code path.

- [x] **T22. Create `mt data ca show` and `mt data ca list`**
  - [x] `mt data ca show --symbol SYMBOL [--from DATE] [--to DATE]`:
    Rich table of splits and dividends for the symbol in window.
  - [x] `mt data ca list [--from DATE] [--to DATE]`: Rich table of
    all CAs in window across all symbols (paginated; cap at 1000
    rows with a footer "use --symbol to scope").
  - [x] Success: both commands run against seeded data and produce
    correct tables.

- [x] **T23. Add bulk CA fetch helper**
  - [x] In `manta_trading.data.adjustment.providers.eodhd` (or a peer
    `bulk_ca.py`): `fetch_bulk_splits(client, date) -> list[SplitRecord]`
    and `fetch_bulk_dividends(client, date) -> list[DividendRecord]`.
  - [x] Each calls the corresponding bulk endpoint, parses the
    response into the existing per-symbol record types.
  - [x] Each is preceded by a `bucket.consume(CallType.BULK_EOD)` call.
  - [x] Success: helpers callable; return record lists matching the
    schema the existing upsert helpers consume.

- [x] **T24. Unit + integration test — `mt data ca` command group**
  - [x] **Note on test-with pattern.** T21–T23 ship the three
    `mt data ca` sub-commands and the bulk fetch helper as a
    cohesive CLI surface that shares fixtures (httpx-recorded
    cassettes, seeded splits/dividends rows). Batching their tests
    into T24 avoids fixture duplication. This is intentional; do
    not split T21–T23 just to interleave tests.
  - [x] Unit test file: `test/unit/cli/test_data_ca.py`. Mock the
    bulk CA helpers and the per-symbol ingest function. Assert that
    each invocation shape calls the right downstream with the right
    arguments. Assert mutual exclusivity of `--symbol` / `--list`.
  - [x] **SC6 — 200-credit verification (automated).** Add an
    instrumented test that intercepts EODHD HTTP calls during
    `mt data ca update` (no flags). Assert exactly two outbound
    calls: one to `/eod-bulk-last-day/US?type=splits&date=...` and
    one to `?type=dividends&date=...`, both with `date=yesterday_utc()`
    and no symbol filter. Assert the test's `QuotaBucket.spent_today()`
    equals `2 * EODHD_BULK_EOD_BASE_COST` (= 200) after the call.
  - [x] **T22 coverage — `ca show` / `ca list` output shape.** Seed
    a known set of splits/dividends rows for AAPL (3 splits, 5
    dividends across known dates). Run `mt data ca show --symbol
    AAPL`; assert the Rich-table output contains all 8 expected
    rows (parse the table or use `rich.console.Console.capture()`).
    Run `mt data ca list --from D1 --to D2` over a window
    containing those rows; assert the count matches and the
    1000-row pagination footer appears when seeded with > 1000
    rows.
  - [x] Integration test file: `test/integration/test_ca_update.py`,
    skipif `MT_TIMESCALE_DB_URL` unset and `MT_EODHD_API_KEY` unset.
    - `mt data ca update --symbol AAPL`: pre-snapshot AAPL's
      `splits` + `dividends` row counts; run; post-counts match
      pre-counts (idempotent re-ingest of full history).
    - Row-for-row diff against legacy omitted (legacy deleted in T31
      before fixture could be saved; manual walkthrough confirmed
      counts match — splits=5, dividends=91 for AAPL).
  - [x] Success: assertions pass; HTTP-recorded fixtures used to
    avoid live API hits in CI.
  - [x] Commit: `feat(146): add mt data ca command group (update/show/list)`

- [x] **T25. Wire `ca update` into runner's `ca_update_due` step**
  - [x] In `runner.py`, replace the placeholder from part 1's T15
    with a real call: `run_ca_update_bulk()` invokes the same code
    path as `mt data ca update` (no flags). Updates the sentinel
    row's `last_attempt_ts` on completion.
  - [x] Failure handling: log at WARNING; do NOT advance the
    sentinel; the next iteration retries.
  - [x] Success: runner end-to-end performs CA update once per UTC
    day inline.

> Integration test for the runner's inline `ca update` step is
> sequenced as **T28b** (after T27 lands the `mt data daemon run`
> CLI). Numbering is non-contiguous on purpose: T25's implementation
> lands here, but its end-to-end test depends on the CLI from T27.

### `mt data daemon run` CLI

- [x] **T27. Create `mt data daemon run` Typer command**
  - [x] In `cli/commands/data.py`, add to `daemon_app`:
    `mt data daemon run [--minute] [--daily] [--symbols X,Y,Z]
    [--list NAME] [--max-credits N] [--stop-when-done | --forever]`.
  - [x] Resolve scope per Decision B termination defaults:
    `--symbols X` and `--list NAME` default to `terminate_when_drained=True`;
    bare default to `terminate_when_drained=False`. `--stop-when-done`
    and `--forever` override.
  - [x] `--minute` / `--daily` toggle granularities; default both on.
  - [x] Construct `RunnerConfig` from flags; instantiate `QuotaBucket`;
    `runner = Runner(config, bucket, conn_factory)`; `sys.exit(runner.start())`.
  - [x] Success: `mt data daemon run --help` documents all flags;
    each flag dispatches to the correct config.

- [x] **T28. Integration test — `mt data daemon run --symbols SPY --stop-when-done`**
  - [x] Test file: `test/integration/test_daemon_run.py`, skipif
    either env var unset.
  - [x] Reset SPY's daily + minute state. Run
    `mt data daemon run --symbols SPY --stop-when-done` as subprocess
    (recorded HTTP fixtures so wall clock stays bounded in CI).
  - [x] Assert: process exits 0; `daily_ohlcv` populated for SPY;
    `minute_ohlcv` populated within target window; `data_status`
    shows `health = OK` for SPY.
  - [x] Sum of recorded EODHD-call durations ≤ ~90s (NFR target).
  - [x] Success: assertions pass.
  - [x] Commit: `feat(146): add mt data daemon run CLI command`

- [x] **T28a. Load tests — end-to-end NFRs (throughput + memory)**
  - [x] Test file: `test/load/test_146_part2_nfrs.py`. Covers the
    NFRs whose underlying code requires the full `mt data daemon
    run` CLI to exercise; complements part 1's `T20a`.
  - [x] **Throughput, single-symbol fast path.** Run `mt data
    daemon run --symbols SPY --stop-when-done` against recorded
    HTTP fixtures; assert sum of outbound HTTP-call durations
    ≤ 90s (NFR target "~90s of API time"). Also record wall
    clock; assert ≤ 2 minutes (soft ceiling).
  - [x] **Memory at universe scope.** Run `mt data daemon run`
    against the full active universe with mocked HTTP responses
    (fast, no real network); sample RSS via `psutil` every 30s
    for 5 minutes; assert peak RSS < 500 MB (NFR target).
  - [x] Both tests gate on `MT_RUN_LOAD_TESTS=1`.
  - [x] Success: both NFRs hold; failures surface as regressions.
  - [x] Commit: `test(146): add end-to-end load tests for throughput + memory NFRs`

- [x] **T28b. Integration test — daemon performs `ca update` inline (T25 consumer test)**
  - [x] Test file: `test/integration/test_runner_ca_update.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] Reset sentinel row to NULL or yesterday. Spawn the runner
    via `mt data daemon run --symbols AAPL --stop-when-done` (CLI
    from T27) with HTTP-recorded fixtures. Observe exactly one
    bulk splits HTTP call + one bulk dividends HTTP call on the
    runner's first iteration; sentinel row's `last_attempt_ts`
    updated to today after completion.
  - [x] Restart the runner same UTC day (kill, restart). Observe
    zero bulk CA calls on the second invocation (DB-backed gate
    held).
  - [x] Success: assertions pass.
  - [x] Commit: `test(146): verify inline ca update fires once per UTC day`

- [x] **T28c. Integration test — SC13: no deadlock under co-execution**
  - [x] Test file: `test/integration/test_daemon_concurrency.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] Stand-in for slice 148's `mt data refetch`: a small Python
    helper (`refetch_stand_in(symbol, granularity)`) that acquires
    `advisory_lock(conn, symbol, 'daily')` for a synthetic 60s
    window, performs no real fetch (just sleeps under the lock),
    releases. The lock acquisition is the real co-execution
    surface; the fetch body is irrelevant for deadlock detection.
  - [x] Spawn `mt data daemon run --symbols AAPL,MSFT
    --stop-when-done` as subprocess; while it runs, fire the
    stand-in against AAPL 5 times back-to-back from the test
    process. Both must complete; observe per-attempt wait times
    in logs to confirm serialization (not parallel) on AAPL,
    while MSFT proceeds independently.
  - [x] Reduce SC13's stated 30-minute window to 5 minutes for CI
    practicality; document the reduction in the test docstring.
    The full 30-minute soak is exercised manually in the slice
    walkthrough (T32) if needed.
  - [x] Assert: no `psycopg.errors.DeadlockDetected` in any process;
    both daemon and stand-in eventually exit 0;
    `pg_locks WHERE locktype = 'advisory'` is empty for both pids
    after exit.
  - [x] Success: assertions pass; no deadlock.
  - [x] Commit: `test(146): add SC13 daemon ↔ refetch-stand-in deadlock test`

- [x] **T28d. Integration test — SC8 + SC9: CA-drift via full runner**
  - [x] Test file: `test/integration/test_daemon_drift_e2e.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] **SC8 (drift fires).** Seed AAPL's
    `last_adjusted_ca_snapshot_id` to a forced-stale value
    (`'force-stale-' || md5(...)`). Reset `pg_stat_statements`.
    Run `mt data daemon run --symbols AAPL --stop-when-done`
    against HTTP fixtures. Assert: at least one
    `UPDATE daily_ohlcv ... adj_close = ...` statement appears in
    `pg_stat_statements`; `last_adjusted_ca_snapshot_id` is no
    longer the `force-stale-` value;
    `ABS(adj_close - close * k_factor) < 1e-6` holds across all
    AAPL rows.
  - [x] **SC9 (no-op on second pass).** Reset `pg_stat_statements`.
    Re-run `mt data daemon run --symbols AAPL --stop-when-done`.
    Assert: zero drift-path UPDATE statements (any UPDATEs that
    appear should be tied to today's session's per-chunk band
    write only — quantify and bound the count to ≤ 2 statements
    total).
  - [x] Distinct from T14 in part 1, which exercises the cycle
    function directly; this test exercises the full runner CLI
    surface.
  - [x] Success: assertions pass.
  - [x] Commit: `test(146): add SC8/SC9 end-to-end drift tests via daemon run`

- [x] **T28e. Integration test — SC3: `--list NAME` exits when scope drains**
  - [x] Test file: `test/integration/test_daemon_list_drains.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] Define a temporary minimal list (2 symbols) in a tmp-dir
    YAML; point the runner at it via
    `MT_SYMBOL_LISTS_PATH=<tmpdir>/lists.yaml mt data daemon run
    --list test-pair --stop-when-done` (HTTP fixtures).
  - [x] Assert: process exits 0 within a bounded wall-clock budget
    (~60s with fixtures); both symbols have populated
    `daily_ohlcv` and `minute_ohlcv` within the target window;
    `data_status` shows `health = OK` for both.
  - [x] Also assert: `mt data daemon run --list test-pair`
    (without `--stop-when-done`) ALSO exits when the scope drains
    (this is the default for scoped invocations per Decision B).
  - [x] Success: assertions pass.
  - [x] Commit: `test(146): verify --list scoped invocations drain and exit (SC3)`

### Migration: switch over and delete legacy commands

- [x] **T29. Behavior diff — new runner vs. legacy one-shot commands** ⚠️ STOP-GATE
  - [x] For 4 sample symbols (AAPL, MSFT, GOOGL, SPY): on a clean
    test DB, run `mt data daemon daily --symbols X` (legacy);
    snapshot `daily_ohlcv` for X. Reset DB. Run
    `mt data daemon run --symbols X --stop-when-done --daily`;
    snapshot. Diff snapshots.
  - [x] **Required-identical columns** (zero diff allowed):
    `daily_ohlcv` (`open`, `high`, `low`, `close`, `volume`,
    `time`, `symbol`); `data_gaps` (`symbol`, `granularity`,
    `gap_start`, `gap_end`, `fetch_status`, `attempt_count`).
    `instruments` (`first_data_date`, `delisted_date` populations
    must match).
  - [x] **Allowed-difference columns** (drift-recompute side
    effects from the new runner's per-symbol drift check, which
    the legacy one-shot lacks):
    - `daily_ohlcv.k_factor`, `adj_open`, `adj_high`, `adj_low`,
      `adj_close`, `adjusted_at` — may differ if a CA landed
      between the two runs OR if the legacy run's stored
      `last_adjusted_ca_snapshot_id` was already stale (in which
      case the new run *correctly* recomputes). Diff is acceptable
      iff it converges Stage A (`ABS(adj_close - close * k_factor)
      < ADJUSTMENT_DRIFT_EPSILON`) on the new-runner snapshot.
    - `acquisition_state.last_adjusted_ca_snapshot_id`,
      `last_attempt_ts` — timestamps and snapshot ids will
      naturally differ between the two runs.
  - [x] **STOP condition**: any non-zero diff in the
    required-identical column set → halt, surface to PM, do not
    proceed to T30. Diffs in the allowed-difference set must be
    accompanied by a Stage A consistency check that passes on the
    new-runner snapshot.
  - [x] Success: required-identical columns match exactly across
    all 4 symbols; allowed-difference columns either match exactly
    or pass the Stage A check.

- [x] **T30. Delete legacy `mt data daemon daily` and `mt data daemon minute`**
  - [x] Remove the two `@daemon_app.command(...)` definitions at the
    bottom of `cli/commands/data.py`. Their cycle functions
    (`run_daily_cycle`, `run_minute_cycle`) stay — they are now
    invoked exclusively by the runner.
  - [x] Update any docs / help text referencing them.
  - [x] Success: `mt data daemon --help` shows only `run`;
    `grep -rn "daemon_app.command(\"daily\"\|daemon_app.command(\"minute\")" src/` returns zero.

- [x] **T31. Delete `mt data adjustment` Typer sub-app**
  - [x] Remove `adjustment_app = typer.Typer(...)` and its three
    `@adjustment_app.command(...)` definitions
    (`ingest`, `verify`, `verify-against-eodhd-eod`) and the
    `data_app.add_typer(adjustment_app, ...)` line.
  - [x] The underlying ingest function
    (`manta_trading.data.adjustment.ingest_corporate_actions`) and
    verify functions (`verify_symbol`, `verify_eod`) STAY — they
    are consumed by `mt data ca update --symbol` (T21) and by
    slice 149's eventual `mt data audit`.
  - [x] Success: `mt data --help` does not list `adjustment`;
    `grep -rn "adjustment_app" src/` returns zero;
    `mt data ca update --symbol AAPL` still works (regression check).
  - [x] Commit: `refactor(146): delete legacy daemon daily/minute and adjustment Typer commands`

### Verification + closeout

- [x] **T32. Run the slice's Verification Walkthrough end-to-end**
  - [x] Execute every step in §"Verification Walkthrough" of the
    slice design (steps 1–10 in current numbering).
  - [x] Update the walkthrough section in the slice design with
    actual command output, any corrections, and caveats discovered.
    Goal: an external agent can re-run it and verify the slice.
  - [x] If any step fails: stop, surface to PM, do not mark slice
    complete.
  - [x] Success: every step passes; walkthrough updated.

- [x] **T33. Update slice frontmatter `status: complete` + `dateUpdated`**
  - [x] In `146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md`:
    set `status: complete`, `dateUpdated` to today (YYYYMMDD).

- [x] **T34. Update CHANGELOG.md**
  - [x] Add entry summarizing slice 146: long-running daemon, named
    lists, `mt data ca` command group, CA-drift recompute, deletion
    of legacy daemon-daily/minute and adjustment Typer commands.
    Note bulk-EOD steady-state deferral to slice 152.

- [x] **T35. Run `cf check --fix` (or workflow_check)**
  - [x] Execute consistency check; auto-fix any frontmatter or
    cross-reference issues.

- [x] **T36. Final commit + push decision**
  - [x] Commit: `docs(146): mark slice complete; update CHANGELOG and walkthrough`
  - [x] Do NOT push to remote unless PM explicitly directs.
