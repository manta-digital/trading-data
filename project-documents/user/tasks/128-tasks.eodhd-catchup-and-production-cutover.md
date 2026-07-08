---
docType: task-breakdown
slice: 128
sliceName: eodhd-catchup-and-production-cutover
parent: user/slices/128-slice.eodhd-catchup-and-production-cutover.md
project: trading
dateCreated: 20260427
dateUpdated: 20260430
dependencies: [121, 122, 123, 124, 125, 127, 131]
projectState: Slice 127 closed 20260426 — EODHD minute provider, adjustment writer, Stage A verifier all green on AAPL+NVDA. NVDA 2024-06-07→2024-07-25 gap discovered post-merge and acknowledged unfillable by EODHD support. Branch main is clean.
status: complete
deferredTo: 144
closureNote: |
  Slice 128 closed 20260430 with the EODHD daily provider, daemon
  freshness fix (Fix A), and provider-filter wiring shipped, plus
  the 10.3 dry-run evidence collected. The dry-run surfaced two
  real correctness bugs (#9 Saturday-classified-as-FAIL in Stage B,
  #10 stale k_factor on MSFT), which made honest production cutover
  sign-off impossible on this slice. Rather than ship a cutover
  built on broken adjustment math, slice 128 closes here and the
  remaining 10.4 / 10.5 cutover work is absorbed into slice 144's
  daemon refactor (which lands the corrected adjustment pipeline
  and the gap-driven backfill loop the dry-run revealed we need).
  The architectural redesign that resulted (140-arch + 140-slices
  + data-correctness-architecture reference doc) is the most
  valuable artifact this slice produced.
---

# Tasks: Slice 128 — EODHD Catch-up and Production Cutover

## Context

Slice 128 brings the post-127 system to a state where reliable, verifiable, gap-detected minute data flows continuously — first against the test environment, then to .144 once gating signals are clean. Six in-scope themes:

1. Daily daemon owns corporate-action ingest (per-symbol cycle: OHLCV → splits → dividends → checkpoint; CA failure non-blocking).
2. `ICorporateActionsProvider` protocol seam, `EODHDCorporateActionsProvider` first implementation, env-var dispatch mirroring slice 127's minute-provider seam.
3. Coverage check (`mt data minute coverage`) — provider-agnostic, computes expected vs actual bar counts via `TradingCalendar`, persists to new `coverage_gaps` table, surfaces NVDA-class gaps that Stage A is structurally blind to.
4. Stage B verifier (`mt data adjustment verify-against-eodhd-eod`) — cross-checks stored `k_factor` against `/eod`-published `adjusted_close / close` per day.
5. Universe-scale backfill (`mt data minute backfill`) — drives the steady-state minute-update path with universe-iteration resumability, quota guard, and operational mutual-exclusion with the minute daemon.
6. Production deployment to .144 — reuses slice 126's systemd templates and runbook structure; two hard gates (PM-confirmed backup, ≥24h test dry-run with coverage scan green and Stage A+B passing on AAPL plus 4 other symbols).

`bar_flags` validation column was deferred in iteration 4 of slice review; it lands in the slice that introduces a populating validator. Three migrations in this slice (012 coverage_gaps, 013 backfill_state, 014 NVDA inaugural row), all on the TimescaleDB host alongside `acquisition_state`.

### Key paths

**New code:**
- `src/manta_trading/data/adjustment/providers/__init__.py` — `ICorporateActionsProvider` protocol, `CorporateActionsProviderName` enum, `build_corporate_actions_provider` helper
- `src/manta_trading/data/adjustment/providers/eodhd.py` — `EODHDCorporateActionsProvider`
- `src/manta_trading/data/coverage/__init__.py` — coverage scan module
- `src/manta_trading/data/coverage/scanner.py` — scan algorithm, persistence
- `src/manta_trading/data/acquisition/minute/backfill.py` — universe-scale backfill orchestrator + quota guard
- `test/unit/test_corporate_actions_provider.py`
- `test/unit/test_eodhd_corporate_actions_provider.py`
- `test/unit/test_coverage_scanner.py`
- `test/unit/test_minute_backfill.py`
- `test/unit/test_verify_against_eodhd_eod.py`
- `test/integration/test_daily_daemon_ca_ingest.py`
- `test/integration/test_coverage_persistence.py`
- `deploy/systemd/mt-daily-daemon.service`
- `deploy/systemd/mt-minute-daemon.service`
- `deploy/systemd/journald-manta-trading.conf`
- `project-documents/user/runbooks/production-deploy.md`

**Schema migrations (slice 150's framework):**
- `src/manta_trading/market/schema/migrations/minute.py` — append `012_coverage_gaps`, `013_backfill_state`, `014_nvda_inaugural_gap`

**Touched code:**
- `src/manta_trading/config/__init__.py` — `corporate_actions_provider`, `eodhd_daily_limit`
- `src/manta_trading/data/adjustment/ingest.py` — refactor to implement `ICorporateActionsProvider`
- `src/manta_trading/data/acquisition/daily/orchestrator.py` — per-symbol cycle gains splits + dividends ingest steps
- `src/manta_trading/cli/commands/data.py` — `mt data minute coverage`, `mt data minute backfill`, `mt data adjustment verify-against-eodhd-eod`; extend `mt data minute status`
- `src/manta_trading/data/acquisition/events.py` — register new action types
- `project-documents/user/architecture/120-arch-adjustment-policy.md` — provider compatibility contract section, outlier-handling Non-Goal section
- `project-documents/user/architecture/140-arch.data-quality-operations.md` — slice 128 handoff note (create stub if absent)
- `CHANGELOG.md`

### CLI surfaces this slice creates or modifies

- `mt data minute coverage [--symbol|--all] --from --to [--threshold N] [--json] [--persist]` — new
- `mt data minute backfill --universe NAME --since DATE [--max-symbols N] [--quota-fraction F]` — new
- `mt data adjustment verify-against-eodhd-eod [--symbol] [--from] [--to] [--tolerance F] [--json]` — new
- `mt data minute status` — extended with universe-progress section when backfill active

### Out-of-scope reminders

Explicitly **not** in this task list (per slice scope):

- Containerization (Docker/Compose).
- Polygon/Finnhub provider evaluation or implementation.
- Monitoring, alerting, dashboards, log shipping (Prometheus, Grafana, PagerDuty).
- Secrets manager (Vault, SOPS).
- Real-time / WebSocket tier.
- Strategy-side data-access layer (`data.minute_bars(...)`).
- Outlier removal, smoothing, forward-fill at storage time.
- `mt data minute coverage triage` UX (manual SQL is fine for now).
- `bar_flags` column (deferred to validator-introducing slice).
- Per-environment provider override / A-B comparison.
- Automatic recompute of adjusted columns when new corporate actions arrive (Stage A and Stage B surface drift).

---

## Phase 0 — Branch and setup

- [x] **0.1 Create slice branch and verify clean working tree** (effort: 1)
  - [x] Confirm working directory is `/Users/manta/source/repos/manta/trading`
  - [x] Confirm `git status` is clean and current branch is `main`
  - [x] Create branch: `git checkout -b 128-eodhd-catchup-and-production-cutover`
  - [x] Success: branch exists, no uncommitted changes

- [x] **0.2 Add config fields for corporate-actions provider and EODHD daily limit** (effort: 1)
  - [x] In `src/manta_trading/config/__init__.py`, add `corporate_actions_provider` (str, default `"eodhd"`, env `MT_CORPORATE_ACTIONS_PROVIDER`)
  - [x] Add `eodhd_daily_limit` (int, default `100_000`, env `MT_EODHD_DAILY_LIMIT`)
  - [x] Update existing config tests in `test/unit/test_config.py` to assert new fields parse from env and from defaults
  - [x] Success: `pytest test/unit/test_config.py` passes; `mt --help` does not error

- [x] **0.3 Commit: chore — config fields for slice 128**

---

## Phase 1 — Schema migrations

- [x] **1.1 Add migration `minute.012_coverage_gaps`** (effort: 2)
  - [x] Append to `src/manta_trading/market/schema/migrations/minute.py`
  - [x] Schema per slice design §Scope item 4: `(symbol TEXT, gap_start TIMESTAMPTZ, gap_end TIMESTAMPTZ, source TEXT, detected_at TIMESTAMPTZ, resolution_status TEXT, notes TEXT, PRIMARY KEY (symbol, gap_start, source))`
  - [x] Add CHECK constraint enumerating valid `resolution_status` values: `'unknown'`, `'provider_confirmed_unfillable'`, `'retry_pending'`, `'resolved'`
  - [x] Define values via a Python `StrEnum` (`CoverageGapStatus`) referenced by both migration and scanner — no magic strings
  - [x] Success: migration list includes 012; `mt data migrate apply --db minute` against test DB creates table; describe shows expected columns and PK

- [x] **1.2 Add migration `minute.013_backfill_state`** (effort: 2)
  - [x] Schema: `(universe TEXT PRIMARY KEY, cursor_symbol TEXT, since_date DATE, started_at TIMESTAMPTZ, last_progress_at TIMESTAMPTZ, daily_calls_used INTEGER NOT NULL DEFAULT 0, daily_calls_window_start TIMESTAMPTZ NOT NULL)`
  - [x] Success: migration applies cleanly; PK on `universe`

- [x] **1.3 Add migration `minute.014_nvda_inaugural_gap`** (effort: 1)
  - [x] Single INSERT into `coverage_gaps` per slice design §Scope item 5
  - [x] Use `ON CONFLICT DO NOTHING` so re-running migration framework against an already-seeded DB does not fail
  - [x] `resolution_status` references the `CoverageGapStatus.PROVIDER_CONFIRMED_UNFILLABLE` enum value (no inline literal)
  - [x] Success: row visible in `coverage_gaps` after `mt data migrate apply --db minute`

- [x] **1.4 Tests for new migrations** (effort: 2)
  - [x] Add `test/unit/test_minute_migrations_128.py` (or extend existing migration tests)
  - [x] Test: applying 012 then 013 then 014 in sequence produces expected schema
  - [x] Test: applying 014 twice is idempotent (no exception, no duplicate)
  - [x] Test: inserting with an invalid `resolution_status` value violates the CHECK constraint
  - [x] Success: `pytest test/unit/test_minute_migrations_128.py` green

- [x] **1.5 Commit: feat(schema) — coverage_gaps, backfill_state, NVDA inaugural row**

---

## Phase 2 — `ICorporateActionsProvider` protocol and EODHD implementation

- [x] **2.1 Define `ICorporateActionsProvider` protocol** (effort: 2)
  - [x] Create `src/manta_trading/data/adjustment/providers/__init__.py`
  - [x] Define `ICorporateActionsProvider` Protocol with methods: `fetch_splits(symbol: str) -> list[Split]`, `fetch_dividends(symbol: str) -> list[Dividend]` (use existing dataclasses if defined in slice 127; else introduce them in this module)
  - [x] Define `CorporateActionsProviderName` StrEnum: `EODHD = "eodhd"`
  - [x] Define `build_corporate_actions_provider(settings) -> ICorporateActionsProvider` dispatching on `settings.corporate_actions_provider`; raise `ValueError` with message naming valid options if unrecognized (no silent fallback)
  - [x] Success: module imports cleanly; `build_corporate_actions_provider` returns the EODHD impl when env unset (default); raises on unknown name

- [x] **2.2 Refactor existing EODHD ingest behind the protocol** (effort: 3)
  - [x] Move the per-symbol fetch logic in `src/manta_trading/data/adjustment/ingest.py` into a class `EODHDCorporateActionsProvider` at `src/manta_trading/data/adjustment/providers/eodhd.py`
  - [x] Keep DB-write logic in `ingest.py` — provider returns parsed records; ingest module persists them. Clean producer/consumer split
  - [x] `mt data adjustment ingest` CLI continues to work unchanged (operator path retained)
  - [x] Success: existing slice-127 ingest tests still pass; the CLI command behaves identically

- [x] **2.3 Tests for protocol seam and EODHD provider** (effort: 2)
  - [x] `test/unit/test_corporate_actions_provider.py` — `build_corporate_actions_provider` returns EODHD by default, raises on unknown name, no string comparisons in dispatch logic
  - [x] `test/unit/test_eodhd_corporate_actions_provider.py` — given a captured `/splits/AAPL` fixture, `fetch_splits` returns expected dataclass rows; same for `/div/AAPL`. Use existing slice-127 fixtures if present; else capture small samples
  - [x] Test: HTTP 4xx propagates as `ProviderPermanentError`; HTTP 429 honored with `Retry-After`; HTTP 5xx retried per slice design §Error handling
  - [x] Success: tests green

- [x] **2.4 Commit: refactor(adjustment) — extract ICorporateActionsProvider protocol**

---

## Phase 3 — Daily daemon owns corporate-action ingest

- [x] **3.1 Extend daily orchestrator per-symbol cycle** (effort: 3)
  - [x] In `src/manta_trading/data/acquisition/daily/orchestrator.py`, after the existing `update daily OHLCV` step, add `ingest_splits(symbol)` and `ingest_dividends(symbol)` steps using the configured `ICorporateActionsProvider`
  - [x] Per Decision 14: CA-ingest failure (after retries) does NOT block checkpoint advance. Log at ERROR, mark per-symbol `ca_ingest_status='failed'` for the cycle, continue
  - [x] Track consecutive-failure count per symbol; escalate to ERROR-level logging at 3 consecutive failures (default)
  - [x] Cycle order: OHLCV → splits → dividends → checkpoint (regardless of CA outcome)
  - [x] Success: orchestrator unit tests show all four steps invoked in order; CA failure does not prevent checkpoint advance

- [x] **3.2 Emit structured events for CA ingest** (effort: 1)
  - [x] In `src/manta_trading/data/acquisition/events.py`, register action types `ca_ingest_splits`, `ca_ingest_dividends` on `ActionType` (or equivalent) StrEnum — no magic strings
  - [x] Orchestrator emits one event per CA fetch attempt (success and failure paths) with fields per slice design §Structured event emission table
  - [x] Success: emitted JSONL events include all required fields (`run_id`, `symbol`, `granularity='daily'`, `provider='eodhd'`, `action`, `status`, `rows_fetched`, `time_range`, `duration_ms`, `error`, `timestamp`)

- [x] **3.3 Tests for daemon-owned CA ingest** (effort: 3)
  - [x] Unit: `test/unit/test_daily_orchestrator.py` (extend) — assert per-symbol cycle invokes splits + dividends in order; checkpoint advances even when CA provider raises
  - [x] Unit: emitted events for both success and failure paths contain mandated fields
  - [x] Integration: `test/integration/test_daily_daemon_ca_ingest.py` — run daemon against test DB on AAPL for one cycle; assert splits/dividends rows updated and watermark advanced
  - [x] Success: tests green

- [x] **3.4 Commit: feat(daily-daemon) — ingest splits and dividends per symbol**

---

## Phase 4 — Coverage scan module and CLI

- [x] **4.1 Implement coverage scanner module** (effort: 3)
  - [x] Create `src/manta_trading/data/coverage/scanner.py` with `scan_coverage(symbols, from_date, to_date, threshold_override=None) -> list[CoverageRow]`
  - [x] Use existing `TradingCalendar` from `src/manta_trading/data/base/trading_calendar.py`: `is_trading_day(date)`, `get_expected_bar_count(date)`
  - [x] For each `(symbol, trading_date)`: compute expected_min_bars = override OR `int(get_expected_bar_count(date) * 0.8)`; SELECT count(*) from `minute_ohlcv`; classify `present | partial | gap` per slice design §Coverage check algorithm
  - [x] Define classification labels via `CoverageStatus` StrEnum — no magic strings
  - [x] Collapse contiguous `gap`/`partial` runs into gap ranges for output and persistence
  - [x] Success: function returns list with correct status for AAPL 2020-08-25→2020-09-04 (all present) and NVDA 2024-06-07→2024-07-25 (mostly gap)

- [x] **4.2 Implement coverage persistence (DELETE-then-INSERT within scanned range)** (effort: 2)
  - [x] Add `persist_coverage_gaps(scan_results, scan_from, scan_to, source)` to scanner module
  - [x] Within a transaction: DELETE rows where `symbol=? AND source=? AND gap_start >= scan_from AND gap_end <= scan_to`, then INSERT one row per contiguous gap range
  - [x] Handles the three boundary scenarios per slice design §Persistence semantics (shrink, split, merge)
  - [x] Success: integration test in 4.4 verifies all three scenarios

- [x] **4.3 Implement `mt data minute coverage` CLI command** (effort: 2)
  - [x] In `src/manta_trading/cli/commands/data.py`, add subcommand under `minute_app`
  - [x] Flags: `--symbol SYM` (mutually exclusive with `--all`), `--all`, `--from DATE` (required), `--to DATE` (required), `--threshold N` (optional integer floor), `--json`, `--persist`
  - [x] Default output: Rich table, per-symbol summary `(expected_days, present_days, partial_days, gap_days, gap_ranges[])`
  - [x] Exit 1 if any gaps found, 0 otherwise
  - [x] When `--persist` (always when `--all`), call `persist_coverage_gaps`
  - [x] Success: command appears in `mt data minute --help`; runs against test DB; exit codes correct

- [x] **4.4 Tests for coverage scanner and persistence** (effort: 3)
  - [x] `test/unit/test_coverage_scanner.py` — synthetic minute_ohlcv rows for AAPL: full day → `present`; partial day → `partial`; missing day → `gap`. Verify threshold override behavior
  - [x] `test/unit/test_coverage_scanner.py` — verify early-close days use `get_expected_bar_count` not a hardcoded constant
  - [x] `test/integration/test_coverage_persistence.py` — exercise the three boundary scenarios:
    - [x] Scenario 1 (shrink): seed gap row 2024-06-07→2024-07-25; rescan with subset of dates filled; assert single row remains with smaller `gap_end`
    - [x] Scenario 2 (split): seed contiguous gap; rescan after middle dates filled; assert two rows with non-overlapping ranges
    - [x] Scenario 3 (merge): seed two adjacent gap rows; rescan after the boundary day becomes gappy; assert single row collapses them
  - [x] Test: DELETE clause respects scan-range bounds (entries outside `--from`/`--to` for same symbol untouched)
  - [x] Success: all scenarios pass

- [x] **4.5 Commit: feat(coverage) — scanner module, CLI, and persistence**

---

## Phase 5 — Stage B verifier (`verify-against-eodhd-eod`)

- [x] **5.1 Implement Stage B verifier logic** (effort: 2)
  - [x] Add module `src/manta_trading/data/adjustment/verify_eod.py`
  - [x] Function `verify_against_eodhd_eod(symbol, from_date, to_date, tolerance) -> list[VerifyRow]`
  - [x] For each date in scope: fetch EODHD `/eod/{symbol}` once for the full range, compute `published_k = adjusted_close / close` per day, fetch stored `k_factor` from minute table, assert `abs(stored - published) <= tolerance`
  - [x] Default tolerance: `0.0001` (matches Stage A)
  - [x] Success: returns per-day rows with stored_k, published_k, diff, status

- [x] **5.2 Implement `mt data adjustment verify-against-eodhd-eod` CLI command** (effort: 2)
  - [x] Subcommand under existing `adjustment_app`
  - [x] Flags: `--symbol SYM` (required for now; `--all` deferred), `--from DATE`, `--to DATE`, `--tolerance F`, `--json`
  - [x] Output: per-day Rich table or JSON
  - [x] Exit 0 only if all green; exit 1 if any FAIL; exit 2 on permanent provider failure or transient retries exhausted
  - [x] Emit structured event `verify_eod` per slice design §Structured event emission
  - [x] Success: command appears in `mt data adjustment --help`; behaves per spec

- [x] **5.3 Tests for Stage B verifier** (effort: 2)
  - [x] `test/unit/test_verify_against_eodhd_eod.py` — given captured `/eod/AAPL` fixture and a stub minute store, all-green case returns expected statuses
  - [x] Synthetic-drift case: stub minute returns `k_factor` outside tolerance for one day; verifier returns FAIL for that day, PASS for others
  - [x] Provider-permanent-failure case: stub `/eod` returns 404; CLI exits 2
  - [x] Test: `verify_eod` event emitted on success and failure paths
  - [x] Success: tests green

- [x] **5.4 Commit: feat(adjustment) — Stage B verify-against-eodhd-eod**

---

## Phase 6 — Universe-scale backfill orchestrator and CLI

- [x] **6.1 Implement quota guard helper** (effort: 2)
  - [x] In `src/manta_trading/data/acquisition/minute/backfill.py`, add `QuotaGuard` class tracking `daily_calls_used` against `quota_fraction × eodhd_daily_limit`
  - [x] State persisted to `backfill_state.daily_calls_used` and `daily_calls_window_start`
  - [x] On `record(n)`: increment counter; if cap reached, sleep until next UTC midnight, emit `quota_sleep` event, then reset counter and emit `quota_window_advance`
  - [x] Window advance is event-time (UTC midnight), not wall-clock based on start
  - [x] Success: unit test in 6.4 covers cap-reached behavior

- [x] **6.2 Implement backfill orchestrator** (effort: 3)
  - [x] `BackfillOrchestrator` class with `run(universe, since_date, max_symbols, quota_fraction)`
  - [x] Loads universe from `InstrumentRegistry`; resumes at `backfill_state.cursor_symbol` if set; else starts at first symbol
  - [x] Per symbol: invoke the existing steady-state minute-update path (`update_symbol(symbol, from_ts=since_or_watermark)`) so `acquisition_state` watermarks advance per Decision 7
  - [x] After each symbol: persist `cursor_symbol = next_symbol`, `last_progress_at = now`
  - [x] Reuse the existing slice-125 semaphore-bounded concurrency pool — do not introduce new concurrency code
  - [x] On `Ctrl-C` (SIGINT): clean shutdown, cursor preserved
  - [x] On per-symbol permanent failure: log ERROR, advance cursor, continue (per slice design data-flow diagram)
  - [x] Emit `backfill_symbol` event per attempt
  - [x] Success: end-to-end run on a 5-symbol mini-universe completes; mid-run interrupt preserves cursor; resume picks up where left off

- [x] **6.3 Implement `mt data minute backfill` CLI command** (effort: 2)
  - [x] Subcommand under `minute_app`
  - [x] Flags: `--universe NAME` (required), `--since DATE` (required), `--max-symbols N`, `--quota-fraction F` (default `0.8`)
  - [x] On completion: invoke `mt data minute coverage --all --persist` from within the command (or document operator runs it as the next step) — slice design Phase-5 verification implies the latter; choose the latter to keep commands single-concern
  - [x] Success: command appears in `mt data minute --help`; runs against test DB

- [x] **6.4 Tests for backfill orchestrator and quota guard** (effort: 3)
  - [x] `test/unit/test_minute_backfill.py` — `QuotaGuard` cap-reached path triggers sleep and emits `quota_sleep` + `quota_window_advance` events (use a clock-injection seam to avoid real sleep)
  - [x] Unit: `BackfillOrchestrator` resumes at `cursor_symbol`; first invocation with empty state starts at universe[0]
  - [x] Unit: per-symbol permanent failure advances cursor (does not stall iteration)
  - [x] Unit: SIGINT during a symbol leaves `cursor_symbol` pointing at the in-progress symbol (resume re-runs that symbol; idempotent thanks to UNIQUE constraint)
  - [x] Unit: `backfill_symbol` event emitted with required fields including `universe` and `cursor_position`
  - [x] Success: tests green

- [x] **6.5 Commit: feat(minute) — universe-scale backfill orchestrator and CLI**

---

## Phase 7 — `mt data minute status` extended with universe-progress section

- [x] **7.1 Extend status command output** (effort: 2)
  - [x] In `src/manta_trading/cli/commands/data.py`, modify `mt data minute status` handler
  - [x] When a row exists in `backfill_state`, render an additional section: `X / Y symbols complete; coverage NN.N% of expected bars; quota used Z% of daily limit; est. completion: D days at current rate`
  - [x] Reads from `acquisition_state` (per-symbol watermarks), `backfill_state` (universe iteration progress), and a coverage-summary aggregation (count of rows in `coverage_gaps` per symbol vs total symbols)
  - [x] Support `--json` for scripted monitoring
  - [x] Success: command shows backfill section when backfill is active; omits it cleanly when no backfill row exists

- [x] **7.2 Tests for status extension** (effort: 1)
  - [x] `test/unit/test_data_cli_minute_status.py` (extend or create) — assert backfill section appears with mock `backfill_state` row; absent without
  - [x] JSON output includes a top-level `backfill` key when active
  - [x] Success: tests green

- [x] **7.3 Commit: feat(cli) — extend minute status with backfill progress**

---

## Phase 8 — Architecture and policy documentation

- [x] **8.1 Update adjustment ADR with provider compatibility contract** (effort: 1)
  - [x] Edit `project-documents/user/architecture/120-arch-adjustment-policy.md`
  - [x] Add section "Provider compatibility contract" with the table from slice design §Provider compatibility contract (capabilities × required × why)
  - [x] Add section "Outlier handling — Non-Goal" stating: no outlier removal, smoothing, or forward-fill at storage time, ever. Document reasoning per Decision 10
  - [x] Add section "Stage B is provider-coupled by design" recording why `verify-against-eodhd-eod` is named after its source
  - [x] Success: file edited; `dateUpdated` bumped to 20260427 or later

- [x] **8.2 Add Initiative 140 handoff note** (effort: 1)
  - [x] If `project-documents/user/architecture/140-arch.data-quality-operations.md` does not exist, create a stub with required frontmatter
  - [x] Add a section recording: slice 128 ships operational coverage checking and EOD cross-validation as a baseline that 140 inherits; enumerate artifacts (CLI commands, `coverage_gaps` table schema, event types `ca_ingest_splits`/`ca_ingest_dividends`/`verify_eod`/`backfill_symbol`/`quota_sleep`/`quota_window_advance`); list analytical extensions explicitly out of scope for 128 (historical gap-rate trending, dashboards, multi-provider cross-validation, automated triage workflows)
  - [x] Success: handoff section present and dated

- [x] **8.3 Commit: docs(arch) — provider compatibility contract and 140 handoff note**

---

## Phase 9 — systemd units, journald drop-in, runbook

- [x] **9.1 Author systemd unit files** (effort: 2)
  - [x] Create `deploy/systemd/mt-daily-daemon.service` and `deploy/systemd/mt-minute-daemon.service` rendered from slice-126 templates
  - [x] Both: `EnvironmentFile=/etc/manta-trading.env`, `User=manta`, `Group=manta`, `WorkingDirectory=/opt/manta-trading`, `Restart=on-failure`, `RestartSec=5s`
  - [x] `ExecStart` invokes the appropriate `mt data daily daemon` / `mt data minute daemon` command
  - [x] Success: files present; `systemd-analyze verify` (if available) clean

- [x] **9.2 Author journald drop-in** (effort: 1)
  - [x] `deploy/systemd/journald-manta-trading.conf` setting per-service log volume limits
  - [x] Success: file present with documented values

- [x] **9.3 Author production runbook** (effort: 3)
  - [x] Create `project-documents/user/runbooks/production-deploy.md`
  - [x] Phase 0: Hard Gate A — PM-confirmed minute-data backup. Operator pings PM, awaits explicit confirmation, checks the box. **Nothing proceeds until this is checked.**
  - [x] Phase 1: Hard Gate B — confirm ≥24h test dry-run results: coverage scan green or `coverage_gaps` populated only with known/triaged entries (NVDA inaugural visible at minimum); Stage A and Stage B both passing on AAPL plus ≥4 other symbols; quota guard demonstrably engaged at ≥1 fraction-of-cap threshold during a backfill
  - [x] Phase 2: set service-user variable
  - [x] Phase 3: prepare host (one-time): create service user, /opt and /var directories
  - [x] Phase 4: install code (clone repo, `uv sync`)
  - [x] Phase 5: drop in `/etc/manta-trading.env` (root-owned, mode 0640) with required vars (MT_EODHD_API_KEY, MT_ALPHAVANTAGE_API_KEY, MT_MARKET_DB_URL, MT_TIMESCALE_DB_URL, MT_MINUTE_PROVIDER=eodhd, MT_CORPORATE_ACTIONS_PROVIDER=eodhd, MT_LOG_LEVEL=INFO, MT_LOG_FORMAT=json)
  - [x] Phase 6: apply migrations against production DBs; spot-check the inaugural NVDA row landed
  - [x] Phase 7: render and install systemd units, journald drop-in
  - [x] Phase 8: start daily service; observe via `journalctl -u mt-daily-daemon -f` for 5+ minutes; confirm CA ingest steps in logs
  - [x] Phase 9: gated minute service start
  - [x] Phase 10: failure recovery sanity check (`systemctl kill`, observe restart and resume)
  - [x] Phase 11: log volume check after 24h
  - [x] Phase 12 (backfill, optional, gated): explicit prerequisite — `sudo systemctl stop mt-minute-daemon` BEFORE invoking `mt data minute backfill`. Re-enable daemon only after backfill exits cleanly. Daily daemon may run throughout. Reference Decision 18
  - [x] Success: runbook present, all phases enumerated as checkboxes, both hard gates first-class with explicit operator-confirms-X language

- [x] **9.4 Mark slice 126 superseded** (effort: 1)
  - [x] In `project-documents/user/slices/126-slice.production-deployment.md`, update frontmatter: `supersededBy: 128`, `status: superseded`
  - [x] Success: 126's frontmatter recorded the supersedence

- [x] **9.5 Commit: docs(deploy) — systemd units, journald drop-in, .144 runbook**

---

## Phase 10 — CHANGELOG and final verification

- [x] **10.1 Add CHANGELOG entry** (effort: 1)
  - [x] Under `[Unreleased]`, add `### Added`, `### Changed` sections summarizing slice 128 deliverables (CA-ingest in daily daemon, ICorporateActionsProvider seam, `mt data minute coverage`, Stage B verifier, `mt data minute backfill`, status extension, three migrations, systemd units, runbook)
  - [x] Success: entry present

- [x] **10.2 Full unit + integration test pass** (effort: 1)
  - [x] Run `pytest test/unit test/integration` from project root
  - [x] All tests pass; pre-existing failures noted in slice-127 followups remain noted (not regressed beyond baseline)
  - [x] Success: green run; no new failures introduced by slice 128
    - Unit: 1049 passed, 13 skipped (re-confirmed after EODHD enum addition)
    - Integration: 78 passed, 1 failed, 41 skipped, 1 deselected (slow `test_daemon_survives_provider_failure` runs alone in ~4 min)
    - Single remaining integration failure is pre-existing on main (`testNewsIntegration::test_agent_command` — MarketDB context-manager bug, predates slice 128 per commit 4a2d045)
    - Side-fixes landed in this task to clear pre-existing breakage so the slice-128 baseline is clean: schema migration counts bumped 11→14, instrument_registry tests updated for `register_instrument` tuple return, freshness-dates relativised, daemon cleanup fixtures de-providered to flush stale-AV rows, AV-specific real-network tests skipped wholesale, AV-via-stub tests re-pointed at EODHD via canonical `ProviderType.EODHD`

- [~] **10.3 Test-environment dry-run** (effort: 3) — partial; surfaced blocking bugs
  - [x] Daemon run + Fix A applied (work-queue freshness `last_attempt_ts` vs `last_success_ts`)
  - [x] Coverage scan attempted; found `--all --json` produces empty stdout on empty-DB edge case (cosmetic, not blocking)
  - [x] Stage A verification on AAPL/MSFT/GOOGL/NVAX over Q1-Q2 2024: all PASS internal consistency
  - [x] Stage B verification on same 4 symbols: AAPL/GOOGL/NVAX clean (10 false-FAILs are Saturdays — issue #9); MSFT 134/134 FAIL with constant 2.09e-3 drift (issue #10 — k_factor staleness)
  - [x] EODHD reliability probe: 4/5 large-cap symbols clean for Q1-Q2 2024; NVDA cuts off at 2024-06-07 (split date — vendor bug, reported); TSLA 2024-H1 missing entirely (vendor data hole, reported)
  - [x] Evidence captured in `project-documents/user/research/slice-128-dry-run.md`
  - [x] ~~Backfill demo with `--quota-fraction 0.05`~~ — deferred to slice 144 (the new daemon's refetch path)
  - **Outcome:** dry-run successful in the sense that it caught real correctness bugs before production cutover. Issues #9 and #10 filed; #10 is a blocker for honest sign-off and is fixed by slice 143's `compute_k_factor`.

- [x] **10.4 Production deployment to .144** — **deferred to slice 144**
  - [x] The original cutover would deploy a daemon with the stale-k_factor bug. Not acceptable.
  - [x] Slice 144's daemon refactor lands the corrected adjustment pipeline (band-based UPDATE per ex-date band, CA-detection on snapshot_id mismatch, no per-bar Python). That daemon is the one that gets deployed to .144.
  - [x] Runbook at `project-documents/user/runbooks/production-deploy.md` will be revised in slice 144 to reference the new daemon's behaviors.

- [x] **10.5 Commit and merge** — partial; merge happens with narrower scope
  - [x] CHANGELOG entry added for what shipped
  - [x] Original "merge slice 128" → narrowed to "fast-forward merge of branch carrying slice 128 deliverables + 140 architectural redesign"
  - [x] Slice 128 frontmatter set to `status: closed_narrower_than_originally_scoped`, `deferredTo: 144`
  - [x] Successor slices 141-147 take over the production-cutover and dry-run-validation responsibilities, on a more honest foundation

---

## Appendix — Cross-reference to slice success criteria

Each numbered success criterion in [128-slice.eodhd-catchup-and-production-cutover.md](../slices/128-slice.eodhd-catchup-and-production-cutover.md) §Success criteria is covered as follows:

| Slice SC | Covered by tasks |
|---|---|
| 1. Daily daemon CA ingest | 3.1, 3.3, 10.4 |
| 2. ICorporateActionsProvider seam | 2.1, 2.2, 2.3 |
| 3. `mt data minute coverage` CLI | 4.1, 4.3, 4.4 |
| 4. `coverage_gaps` table + NVDA row | 1.1, 1.3, 1.4 |
| 5. `verify-against-eodhd-eod` CLI | 5.1, 5.2, 5.3 |
| 6. `mt data minute backfill` CLI | 6.1, 6.2, 6.3, 6.4 |
| 7. `backfill_state` table | 1.2 |
| 8. `mt data minute status` extended | 7.1, 7.2 |
| 9. (removed iter 4) | n/a |
| 10. systemd units + runbook | 9.1, 9.2, 9.3 |
| 11. Adjustment ADR updated | 8.1 |
| 12. CHANGELOG entry | 10.1 |
| 13. ≥24h test dry-run with green signals | 10.3 |
| 14. Initiative 140 handoff note | 8.2 |
| 15. Structured events for new I/O paths | 3.2, 4.x (events fired in scanner if applicable), 5.3, 6.4 |
