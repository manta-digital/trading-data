---
docType: tasks
slice: 146-long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute
project: trading
lld: user/slices/146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute.md
part: 1
partOf: 146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md
dependencies:
  - 145-slice.daemon-refactor-data-gaps-driven-backfill-advisory-locking-band-based-adj-writes
projectState: >
  Slice 145 complete and merged to main. data_gaps-driven daemon cycle
  functions exist as `manta_trading.data.acquisition.daemon.daily.run_daily_cycle`
  and `...minute.run_minute_cycle`; both write correct adj_* on initial
  fetch via band-based UPDATE and serialize on advisory locks. CLI
  surface is `mt data daemon daily [--symbols X]` and
  `mt data daemon minute [--symbols X]` (one-shot). Adjustment ingest
  exists as `mt data adjustment ingest --symbol X --type {splits|dividends}`.
  Verify commands exist as `mt data adjustment verify` and
  `verify-against-eodhd-eod`. EODHD CA endpoints already wired:
  `manta_trading.data.adjustment.providers.eodhd` for per-symbol
  `/splits/{ticker}` + `/div/{ticker}`. `compute_k_factor`,
  `current_ca_snapshot`, `compute_snapshot_id` all live in
  `manta_trading.data.adjustment.{k_factor,context}`. Band-based
  UPDATE writer in `adjustment.band_writer`. Constants
  `EODHD_DAILY_QUOTA`, `EODHD_PER_MINUTE_BURST`,
  `EODHD_INTRADAY_CALL_COST`, `EODHD_EOD_CALL_COST`,
  `EODHD_BULK_EOD_BASE_COST` already added 2026-05-03.
  `acquisition_state.last_adjusted_ca_snapshot_id` column exists
  (slice 142) and is populated on initial fetch (slice 145).
  Bulk-EOD steady-state was deferred to slice 152 — slice 146 keeps
  per-symbol `/eod` for the daily path.
  Branch: create `146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute`
  from main before T1.
dateCreated: 20260503
dateUpdated: 20260504
status: complete
---

## Context Summary

- Four components: (1) long-running daemon `mt data daemon run` with
  token-bucket throttling, SIGTERM handling, scoped-exit defaults;
  (2) named symbol lists from `config/symbol-lists.yaml`;
  (3) `mt data ca` command group replacing `mt data adjustment`;
  (4) CA-drift detection + band recompute integrated into each cycle's
  per-symbol iteration.
- The slice **wraps** slice 145's cycle functions; it does not change
  their internals except to add the per-symbol drift check at the top
  of each symbol's iteration (Decision C).
- No new schema migrations. Once-per-day `mt data ca update` gate
  uses a sentinel row in `acquisition_state` (Decision G).
- Test tiers: unit (mocked, default), integration (live test DB,
  skipif `MT_TIMESCALE_DB_URL` unset), HTTP-recorded (skipif
  `MT_EODHD_API_KEY` unset; uses VCR-style cassettes or an
  instrumented httpx transport).
- Migration: build alongside, switch over (T26), then delete the
  legacy `mt data daemon daily/minute` and `mt data adjustment` Typer
  commands (T27).
- All findings from the 2026-05-03 review are already absorbed into
  the slice design; this task list reflects the post-review shape.

---

## Tasks

- [x] **T1. Create slice branch and verify pre-state**
  - [x] From `main`: `git checkout -b 146-slice.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute`
  - [x] Confirm `mt data daemon daily --symbols AAPL` runs successfully
    on the test DB (sanity that slice 145 baseline holds).
  - [x] Confirm `acquisition_state.last_adjusted_ca_snapshot_id` is
    populated for at least one symbol (sample: `SELECT symbol, last_adjusted_ca_snapshot_id
    FROM acquisition_state WHERE last_adjusted_ca_snapshot_id IS NOT NULL LIMIT 5;`).
  - [x] Success: branch created; pre-state verified; no commit yet.

### Token bucket (Decision A)

- [x] **T2. Create `manta_trading.data.acquisition.quota` module**
  - [x] New file `src/manta_trading/data/acquisition/quota.py`.
  - [x] Define `CallType(StrEnum)` with members `EOD`, `INTRADAY`,
    `BULK_EOD`. Map each to its credit cost via a module-level
    `dict[CallType, int]` populated from `manta_trading.constants`
    (`EODHD_EOD_CALL_COST`, `EODHD_INTRADAY_CALL_COST`,
    `EODHD_BULK_EOD_BASE_COST`). No magic numbers.
  - [x] `class QuotaBucket` with two sub-windows: `minute_window`
    (capacity = `EODHD_PER_MINUTE_BURST`, refill 1000/60s) and
    `day_window` (capacity = `EODHD_DAILY_QUOTA`, refill 100k/86400s
    rolling).
  - [x] `consume(call_type: CallType) -> None` blocks until both
    windows have capacity for `cost_for(call_type)`. Synchronous
    blocking via `time.sleep` is fine — the runner is single-threaded.
  - [x] `cost_for(call_type: CallType) -> int` reads the module dict.
  - [x] `spent_today() -> int` returns the current 24h-rolling spend
    (for progress logging and the `--max-credits` gate).
  - [x] Inject a clock for testability: constructor takes
    `now: Callable[[], float] = time.monotonic`.
  - [x] Success: module imports cleanly; `QuotaBucket()` instantiable;
    no business-logic dependencies beyond `manta_trading.constants`.

- [x] **T3. Unit test — `QuotaBucket`**
  - [x] Test file: `test/unit/data/acquisition/test_quota.py`.
  - [x] `consume` of cost ≤ remaining capacity returns immediately
    (mocked clock; no `time.sleep`).
  - [x] `consume` past `EODHD_PER_MINUTE_BURST` in 60 simulated
    seconds blocks the next call until the window refills.
  - [x] `consume` past `EODHD_DAILY_QUOTA` in 86400 simulated
    seconds blocks until the day window refills.
  - [x] `cost_for(CallType.BULK_EOD)` returns `EODHD_BULK_EOD_BASE_COST`.
  - [x] `spent_today()` reflects credits consumed in the rolling
    24h window; spend more than 24h ago does not count.
  - [x] Clock-jump-backwards (NTP correction): bucket does not
    over-grant beyond `EODHD_PER_MINUTE_BURST`.
  - [x] Success: all assertions pass; no real `time.sleep`.
  - [x] Commit: `feat(146): add QuotaBucket with two-window throttling`

### Named symbol lists (Decision E)

- [x] **T4. Create `manta_trading.data.lists` module**
  - [x] New file `src/manta_trading/data/lists.py`.
  - [x] `load_lists(config_path: Path) -> dict[str, list[str]]` parses
    `config/symbol-lists.yaml`, resolving `source: file:...` references
    relative to the config file's directory.
  - [x] `resolve_list(name: str, config_path: Path) -> list[str]`
    returns the symbol list for `name`; raises
    `ListNotFoundError` (a custom exception) on unknown name. No
    silent fallback to the full universe.
  - [x] `intersect_with_active(symbols: list[str], conn) -> list[str]`
    intersects against `instruments WHERE delisted_at_eodhd = false`.
    Logs at WARNING for symbols in the list but absent from
    instruments.
  - [x] `refresh_sp500(conn, eodhd_client, snapshot_path: Path) -> int`
    calls `/fundamentals/GSPC.INDX`, extracts `Components` array,
    writes one ticker per line to `snapshot_path`. Returns count
    written. On malformed payload, raises and does not write.
  - [x] Success: module imports cleanly; YAML parsing works for the
    arch-spec schema (top-level `lists:` map with `description`,
    `symbols:` list, or `source:` string).

- [x] **T5. Create `config/symbol-lists.yaml` with `priority1` + `priority2`**
  - [x] New file `config/symbol-lists.yaml`.
  - [x] `priority1`: hand-picked 10 symbols
    `[SPY, QQQ, AAPL, MSFT, NVDA, GOOGL, META, TSLA, AMZN, BRK-B]`.
  - [x] `priority2`: `source: file:config/lists/sp500-snapshot.txt`.
  - [x] Add `config/lists/sp500-snapshot.txt` as a placeholder
    (single-line comment header explaining it's populated by
    `mt data lists refresh-sp500`).
  - [x] Add `config/symbol-lists.yaml` and `config/lists/` to
    `.gitignore` reverse-include if they were previously ignored;
    confirm they will commit.
  - [x] Success: files exist; `config/symbol-lists.yaml` parses
    cleanly via `yaml.safe_load`.

- [x] **T6. Unit test — `manta_trading.data.lists`**
  - [x] Test file: `test/unit/data/test_lists.py`.
  - [x] `load_lists` parses inline-symbols and file-source forms.
  - [x] `resolve_list("priority1", ...)` returns the inline list.
  - [x] `resolve_list("priority2", ...)` returns the contents of
    `sp500-snapshot.txt` (use a temp-dir fixture).
  - [x] `resolve_list("nonexistent", ...)` raises `ListNotFoundError`.
  - [x] `intersect_with_active` filters out symbols absent from a
    mocked `instruments` cursor; logs WARNING.
  - [x] `refresh_sp500` writes the snapshot file when
    `Components` is well-formed; raises and leaves the file
    untouched on malformed payload.
  - [x] Success: all assertions pass.
  - [x] Commit: `feat(146): add named symbol lists (config + lists module)`

- [x] **T7. Add `mt data lists` Typer sub-app**
  - [x] In `src/manta_trading/cli/commands/data.py`, add
    `lists_app = typer.Typer(name="lists", help=...)` and
    `data_app.add_typer(lists_app, name="lists")`.
  - [x] `mt data lists ls` — prints all defined list names with member
    counts (Rich table).
  - [x] `mt data lists show NAME` — prints resolved symbols, one per
    line.
  - [x] `mt data lists refresh-sp500` — calls `refresh_sp500()`,
    prints count written, exits 0; nonzero on malformed payload.
  - [x] All commands resolve `config/symbol-lists.yaml` from the
    project's config dir (use the existing config-resolution helper
    if one exists; otherwise hard-code relative to `cwd`).
  - [x] Success: `mt data lists --help` lists all three subcommands;
    `mt data lists ls` runs against the committed config.

- [x] **T8. Integration test — `mt data lists` CLI**
  - [x] Test file: `test/integration/test_cli_lists.py`, skipif
    `MT_TIMESCALE_DB_URL` unset.
  - [x] `mt data lists ls` exits 0 and includes `priority1` in output.
  - [x] `mt data lists show priority1` prints all 10 hand-picked
    symbols.
  - [x] `mt data lists show nonexistent` exits nonzero.
  - [x] `mt data lists refresh-sp500` against a recorded HTTP fixture
    writes the expected file.
  - [x] Success: all assertions pass.
  - [x] Commit: `feat(146): add mt data lists CLI sub-app`

### CA-drift detection (Decision C)

- [x] **T9. Create `manta_trading.data.acquisition.daemon.ca_drift` module**
  - [x] New file `src/manta_trading/data/acquisition/daemon/ca_drift.py`.
  - [x] `class DriftCheckResult` (frozen dataclass): `bool drift_detected`,
    `int bands_recomputed`, `str | None new_snapshot_id`.
  - [x] `check_and_recompute(conn, symbol, granularity) -> DriftCheckResult`:
    1. Read `acquisition_state.last_adjusted_ca_snapshot_id` for
       `(symbol, granularity)`.
    2. Compute `current = current_ca_snapshot(conn, symbol)` (slice 143).
    3. If `stored is None`: return `DriftCheckResult(False, 0, current.snapshot_id)`
       — slice 145's fetch path will populate it on next bar write;
       no recompute needed.
    4. If `stored == current.snapshot_id`: return
       `DriftCheckResult(False, 0, None)` — no-op fast path.
    5. Else: determine recompute range
       `[min(changed_ca.ex_date), now()]`; call slice 145's
       band-based UPDATE writer (`adjustment.band_writer`) over that
       range; refresh affected cagg ranges (delegate to a helper —
       see T10); update `acquisition_state.last_adjusted_ca_snapshot_id =
       current.snapshot_id` only if recompute + cagg refresh both
       succeed; return `DriftCheckResult(True, n_bands, current.snapshot_id)`.
  - [x] Acquires `advisory_lock(conn, symbol, granularity)` for the
    full recompute path. Caller already inside a transaction; this
    function does not start its own.
  - [x] On cagg refresh failure: log at ERROR; do NOT advance
    `last_adjusted_ca_snapshot_id`; return `DriftCheckResult(True, n, None)`
    so the caller knows recompute fired but state didn't advance.
  - [x] Success: module imports cleanly; calls slice 143/145 modules
    only.

- [x] **T10. Add cagg-range-refresh helper**
  - [x] In `ca_drift.py` (or a peer `cagg_refresh.py` if it grows):
    `refresh_caggs_in_range(conn, symbol, granularity, start_ts, end_ts) -> None`.
  - [x] For minute granularity: invoke
    `CALL refresh_continuous_aggregate('<cagg_name>', start_ts, end_ts)`
    for each cagg defined in slice 150's eventual setup. For now
    (slice 150 not landed): refresh the existing minute caggs
    (5min, 15min, hourly, 4hour, daily, weekly, monthly v2) — list
    them in a module constant `MINUTE_CAGGS` to avoid magic strings.
  - [x] For daily granularity: no caggs project from `daily_ohlcv`
    in current schema; the function is a no-op for daily (but the
    daemon still calls it uniformly — clean abstraction boundary).
  - [x] Success: function exists; cagg names live in one module-level
    list, not scattered.

- [x] **T11. Unit test — `ca_drift.check_and_recompute`**
  - [x] Test file: `test/unit/data/acquisition/daemon/test_ca_drift.py`.
  - [x] Fixtures cover:
    - `stored is None` → `drift_detected=False`, `bands_recomputed=0`.
    - `stored == current` → `drift_detected=False`,
      `bands_recomputed=0` (no UPDATE issued — verify mock band_writer
      not called).
    - `stored != current` → `drift_detected=True`, band_writer called
      with expected `(start, end)` range,
      `last_adjusted_ca_snapshot_id` advanced.
    - Cagg refresh raises → state NOT advanced;
      `DriftCheckResult.new_snapshot_id is None`.
  - [x] Success: all assertions pass.

- [x] **T12. Integration test — drift recompute end-to-end**
  - [x] Test file: `test/integration/test_ca_drift.py`, skipif
    `MT_TIMESCALE_DB_URL` unset.
  - [x] Setup: pick a symbol with daily history (AAPL); seed
    `acquisition_state.last_adjusted_ca_snapshot_id = 'force-stale-' || md5(...)`.
  - [x] Capture pre-state: count `daily_ohlcv` rows for AAPL with
    `adj_close` populated; record sample `(close, k_factor, adj_close)` triples.
  - [x] Call `check_and_recompute(conn, 'AAPL', 'daily')`.
  - [x] Assert: `drift_detected=True`; `bands_recomputed >= 1`;
    `last_adjusted_ca_snapshot_id` no longer matches the
    `force-stale-` prefix; `ABS(adj_close - close * k_factor) < 1e-6`
    holds for every AAPL row.
  - [x] Second call: `drift_detected=False`, `bands_recomputed=0`.
  - [x] Success: all assertions pass.
  - [x] Commit: `feat(146): add CA-drift detection + band recompute`

- [x] **T13. Integrate drift check + add `should_continue` hook into cycle functions**
  - [x] In `acquisition/daemon/daily.py` and
    `acquisition/daemon/minute.py`, at the top of each symbol's
    iteration (inside the existing per-symbol loop, after the advisory
    lock acquisition but before the fetch), call
    `ca_drift.check_and_recompute(conn, symbol, granularity)`.
  - [x] Log at INFO when `drift_detected=True` with bands recomputed
    count; DEBUG when no-op.
  - [x] If `check_and_recompute` raises, classify per the existing
    cycle's transient-failure handling (treat as one symbol's
    transient failure; do not crash the cycle).
  - [x] **Add a `should_continue: Callable[[], bool] | None = None`
    parameter** to both `run_daily_cycle` and `run_minute_cycle`.
    At the top of each per-symbol iteration (before the drift
    check), if `should_continue is not None and not should_continue()`,
    break out of the loop cleanly. Default `None` preserves
    backward compatibility: existing callers (slice 145's one-shot
    CLI commands) work unchanged. The runner (T19) supplies a
    real callback that flips on SIGTERM. Bundling here avoids
    touching the cycle signatures twice.
  - [x] Success: both cycle functions still pass slice 145's existing
    integration tests; `should_continue=None` path is a no-op.

- [x] **T14. Integration test — drift check fires inside cycle**
  - [x] Test file: `test/integration/test_cycle_with_drift.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] Seed AAPL's `last_adjusted_ca_snapshot_id` to a stale value;
    run `run_daily_cycle(symbols=['AAPL'])`.
  - [x] Assert: cycle completes successfully;
    `last_adjusted_ca_snapshot_id` advanced; `daily_ohlcv` adj_*
    consistent (Stage A).
  - [x] Run `run_daily_cycle(symbols=['AAPL'])` a second time;
    assert no band-UPDATE fires from the drift path (count via
    `pg_stat_statements` reset/diff or via mocked band_writer call
    counter).
  - [x] Success: assertions pass.
  - [x] Commit: `feat(146): integrate CA-drift check into daily + minute cycles`

### Long-running runner (Decisions B, F, G)

- [x] **T15. Create `manta_trading.data.acquisition.daemon.runner` module**
  - [x] New file `src/manta_trading/data/acquisition/daemon/runner.py`.
  - [x] `class RunnerConfig` (frozen dataclass): `scope` (Literal
    `ALL_ACTIVE` | concrete list), `granularities` (set of
    `daily`/`minute`), `max_credits: int | None`,
    `terminate_when_drained: bool`.
  - [x] `class Runner`:
    - `__init__(config, bucket: QuotaBucket, conn_factory: Callable[[], Connection])`.
    - `start() -> int` — main loop; returns process exit code.
  - [x] Main loop body matches slice design "Long-running loop"
    pseudocode:
    1. If `should_exit()` → break.
    2. If `ca_update_due()` → `run_ca_update_bulk()` (T18 wires this
       to the actual ca-update function once T18 lands; for now,
       call a placeholder that logs and no-ops).
    3. If `daily_cycle_due()` → invoke `run_daily_cycle(scope)` from
       slice 145; passes `bucket` via a thread-local or contextvars
       handoff so EODHD-client wrappers can call `bucket.consume()`
       per call (see T16).
    4. If `minute_cycle_due()` → same with `run_minute_cycle`.
    5. Else → `sleep_until_next_due_event()`.
  - [x] `should_exit()` checks: SIGTERM flag,
    `bucket.spent_today() >= max_credits`, scope-drained-and-
    `terminate_when_drained=True`.
  - [x] `daily_cycle_due()` / `minute_cycle_due()`: see T17.
  - [x] Per-cycle progress log: symbols processed, credits spent
    today, est. completion if scope is bounded.
  - [x] Success: module imports cleanly; `Runner(...).start()` exists
    and returns an int.

- [x] **T16. Wire `QuotaBucket` into the EODHD HTTP client**
  - [x] In the existing EODHD provider modules
    (`data/acquisition/daily/...`, `data/acquisition/minute/...`,
    `data/adjustment/providers/eodhd/...`): before each outbound
    HTTP call, call `bucket.consume(<CallType>)`.
  - [x] The bucket is supplied via a `contextvars.ContextVar` set by
    the runner's main loop. CLI one-shot commands (legacy and the
    new `mt data ca update` per-symbol path) set their own bucket
    instance per invocation.
  - [x] If no bucket is set in the context, raise — never silently
    skip throttling. (One exception: existing one-shot commands
    that are about to be deleted in part 2 T30 may use a no-op
    bucket during the transition.)
  - [x] Success: every outbound EODHD call is preceded by a
    `bucket.consume()` (verify via grep: every `httpx.get(...)` /
    `client.get(...)` to an EODHD URL has a `consume` call within
    the same function).

- [x] **T16a. Harden EODHD HTTP retry: 429 + peer-disconnect**
  - [x] In the EODHD HTTP wrapper(s) used by the providers, add
    explicit handling per slice design §"Failure Modes / EODHD HTTP":
    - **429 Too Many Requests.** Log at WARNING. Sleep the
      response's `Retry-After` header value (parse as seconds
      integer or HTTP-date); fall back to 60s if the header is
      absent or unparseable. Retry. After `MAX_RETRY_COUNT`
      consecutive 429s on the same call, log at ERROR with the
      message "EODHD 429 escalation — token bucket likely
      misconfigured" and raise a `QuotaBucketMisconfiguredError`
      that the runner catches and converts to a nonzero exit.
    - **Peer disconnect mid-send** (`httpx.RemoteProtocolError`,
      `httpx.ReadError`, `json.JSONDecodeError` inside the
      response-body parse path). Discard the partial response
      bytes — do not parse, do not persist. Classify as
      transient_failure and retry per slice 145's existing
      backoff/`MAX_RETRY_COUNT` policy. Token bucket is NOT
      refunded for the dropped call.
  - [x] Both behaviors live in one place (the shared HTTP wrapper),
    not duplicated across daily/minute/CA providers.
  - [x] Success: a junior AI can grep the wrapper and find one
    explicit `except` per failure mode.

- [x] **T16b. Unit test — 429 retry + peer-disconnect handling**
  - [x] Test file: `test/unit/data/acquisition/test_eodhd_retry.py`.
  - [x] Mock `httpx` transport. Fixtures cover:
    - Single 429 with `Retry-After: 1` → sleep ~1s (mocked
      sleep), retry once, succeed.
    - Single 429 with no `Retry-After` → sleep 60s (mocked),
      retry once, succeed.
    - `MAX_RETRY_COUNT` consecutive 429s → raises
      `QuotaBucketMisconfiguredError`; runner-level test (T20 or
      its peer) verifies this propagates to nonzero exit.
    - `httpx.RemoteProtocolError` mid-response → classified as
      transient, retried per slice 145 policy, no partial JSON
      persisted (verify via mocked downstream parser not called
      on the dropped chunk).
    - `json.JSONDecodeError` on truncated body → same classification
      as RemoteProtocolError.
  - [x] Success: every assertion passes; no real `time.sleep`.
  - [x] Commit: `feat(146): harden EODHD HTTP — 429/Retry-After + peer-disconnect`

- [x] **T17. Cycle-due predicates**
  - [x] In `runner.py` (or a peer `due.py` if it grows):
    `daily_cycle_due(state) -> bool`:
    - True if last daily cycle's start was on a prior UTC day AND
      the current UTC time is past `00:00 + LATE_BAR_GRACE_PERIOD`.
    - State is a `RunnerState` dataclass tracking `last_daily_cycle_start_utc`,
      `last_minute_cycle_start_utc`, `last_ca_update_utc_date`.
  - [x] `minute_cycle_due(state) -> bool`:
    - True if no minute cycle is currently running AND the last cycle's
      end was at least 1 minute ago AND any scope member has an
      actionable minute gap.
  - [x] `ca_update_due(state, conn) -> bool`:
    - Read sentinel row from `acquisition_state` keyed by
      `('__bulk_ca__', 'daily')`; check its `last_attempt_ts`. True
      if `last_attempt_ts.date() < today_utc()` AND current UTC time
      is past `00:00 + LATE_BAR_GRACE_PERIOD`. The DB-backed gate
      survives daemon restarts (Decision G).
    - **Missing-row / NULL handling.** If the sentinel row does not
      exist (first-ever daemon run on this DB) or its
      `last_attempt_ts IS NULL`, treat as "never updated" and return
      `True` (subject to the same grace-period gate). Do NOT call
      `.date()` on `None`. The runner's CA-update step (T25) is
      responsible for inserting/updating the row on completion;
      `ca_update_due` is read-only.
  - [x] `sleep_until_next_due_event(state) -> None`: sleep until the
    soonest-due event; cap at 60s so SIGTERM is responsive.
  - [x] Success: predicates are pure functions of state + clock;
    unit-testable without a real loop.

- [x] **T18. Unit test — runner predicates and main-loop control**
  - [x] Test file: `test/unit/data/acquisition/daemon/test_runner.py`.
  - [x] Inject mock clock + mock conn factory + mock cycle functions.
  - [x] `daily_cycle_due` returns True after UTC-day rollover + grace,
    False before grace, False when last cycle was already today.
  - [x] `ca_update_due`: True when the sentinel row's
    `last_attempt_ts.date() < today_utc()`; False when fresh; True
    when the row is missing entirely; True when the row exists but
    `last_attempt_ts IS NULL`. None of the missing/NULL cases raise.
  - [x] `should_exit`: SIGTERM flag → True; `max_credits` exhausted
    → True; scope drained + `terminate_when_drained=True` → True;
    bare invocation never exits.
  - [x] Main loop with `terminate_when_drained=True` and a scope
    that drains in one iteration: returns 0 after one pass.
  - [x] Success: all assertions pass.
  - [x] Commit: `feat(146): add long-running runner with cycle-due predicates`

- [x] **T19. SIGTERM / SIGINT handling in runner**
  - [x] In `runner.py`: install signal handlers in `start()` that set
    a `should_exit` flag.
  - [x] When invoking `run_daily_cycle` / `run_minute_cycle`, pass
    `should_continue=lambda: not self._should_exit` (the
    `should_continue` parameter was added to both cycle functions
    in T13). Cycle exits cleanly between symbols on flag flip.
  - [x] Restore previous signal handlers on `start()` exit (so
    pytest doesn't inherit them).
  - [x] Success: SIGTERM during a multi-symbol cycle finishes the
    current symbol then returns from `start()`.

- [x] **T20. Integration test — SIGTERM clean shutdown (runner direct)**
  - [x] Test file: `test/integration/test_runner_sigterm.py`,
    skipif `MT_TIMESCALE_DB_URL` unset.
  - [x] Test calls `Runner(...).start()` directly in a thread
    against a bounded scope of 5 symbols. Main thread waits 30s,
    then `os.kill(os.getpid(), signal.SIGTERM)` to exercise the
    handler. (Subprocess-via-CLI variant is exercised in part 2,
    after T27 lands the `mt data daemon run` CLI.)
  - [x] Assert: `start()` returns 0 within one symbol's processing
    time; `pg_locks` shows no leaked advisory locks held by the
    runner's connection.
  - [x] Success: assertions pass.
  - [x] Commit: `feat(146): add SIGTERM handling to runner`

- [x] **T20a. Load tests — NFRs covered in part 1 scope**
  - [x] Test file: `test/load/test_146_part1_nfrs.py`. Per project
    `python.md` rules: any code on concurrency/network paths
    needs at least one load test. This task covers the NFRs whose
    underlying code lands in part 1.
  - [x] **Token bucket overhead.** Run 100k `bucket.consume(EOD)`
    calls in a tight loop with mocked clock so no real waits
    fire; assert mean time per call < 1ms, p99 < 5ms (NFR target
    "Token bucket overhead: < 1ms per consume()").
  - [x] **List resolution latency.** Define a list with the full
    SP500 snapshot (~500 symbols) plus one inline list; call
    `resolve_list(...)` 100 times and intersect with a seeded
    13k-symbol `instruments` table; assert wall-clock median
    < 100ms per call (NFR target "List resolution latency:
    < 100ms").
  - [x] **SIGTERM-to-exit latency.** Reuse T20's harness with a
    longer-running cycle (use mocked cycle that sleeps per symbol
    for 5s); send SIGTERM at a known point; assert `start()`
    returns within 1.2× the symbol's processing time (NFR target
    "SIGTERM-to-exit latency: ≤ one symbol's processing time").
  - [x] All three load tests gate on `MT_RUN_LOAD_TESTS=1` so they
    don't run by default in fast unit-test loops; CI must enable
    them for slices touching these paths.
  - [x] Success: all three assertions hold; failures surface as
    NFR regressions, not silent drift.
  - [x] Commit: `test(146): add load tests for token bucket, list resolution, SIGTERM latency`

> **Throughput (~90s API time SPY backfill) and memory (RSS < 500
> MB at full universe) NFRs are deferred to part 2's load-test
> task** — both require the `mt data daemon run` CLI (T27) to
> exercise end-to-end. Part 2 must include a load-test task
> covering them.

---

**End of part 1.** Continue with T21 onwards in
[146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md](146-tasks.long-running-daemon-named-lists-mt-data-ca-ca-drift-recompute-2.md):
`mt data ca` command group, `mt data daemon run` CLI, legacy-command
deletions, and slice closeout.

