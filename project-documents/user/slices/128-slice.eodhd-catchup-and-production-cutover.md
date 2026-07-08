---
docType: slice-design
slice: 128
parent: user/architecture/120-slices.data-acquisition.md
project: trading
dateCreated: 20260427
dateUpdated: 20260430
status: complete
supersedes: 126
deferredTo: 144
reviewIteration: 4
closureNote: |
  Closed 20260430 with EODHD daily provider, daemon Fix A
  (work-queue freshness on last_attempt_ts), and provider-filter
  wiring shipped. The 10.3 dry-run was completed and surfaced two
  real correctness bugs (#9, #10). Production cutover (10.4) is
  deferred to slice 144's daemon refactor, which lands on the
  corrected adjustment foundation. The architectural redesign
  (140-arch + 140-slices + data-correctness-architecture)
  emerging from this slice is the foundation for slices 141-147.
---

# Slice 128 — EODHD Catch-up and Production Cutover

## Purpose

Bring the post-127 system to a state where **reliable, verifiable, gap-detected** minute data flows continuously — first against the test environment (production DB credentials are not available locally; they live in the deployment env), then to prod-host once gating signals are clean. This slice closes the gap between "the pipeline works on a developer machine for one symbol" and "the pipeline runs unattended at universe scale, knows what's missing, and can be trusted by a backtest."

It supersedes the deferred [slice 126](126-slice.production-deployment-to.md) — that slice's systemd templates and runbook structure are reused; the rest is rewritten for the post-127 reality.

## Motivation / Problem

Slice 127 delivered raw + adjusted minute storage with a continuous Stage A verifier. As shipped, it works end-to-end on AAPL and (now verified) NVDA — except for a real, EODHD-acknowledged 7-week gap in NVDA intraday around the 2024-06-10 split. That single discovery, made one day after slice 127 closed, reshapes what slice 128 has to do:

1. **Stage A is structurally blind to provider data gaps.** It verifies that what is *stored* is internally consistent with the splits/dividends tables. A symbol-date with zero rows is silently consistent with itself. The NVDA gap demonstrates this concretely: Stage A passes on a database missing 7 weeks of data because nothing inconsistent is present, just absent.

2. **The platform's reliability claim depends on detection independent of the provider.** EODHD's data-quality response to the NVDA gap was acknowledgment + "very rare" + "can't be filled currently." That is workable, but only if the platform measures the gap rate empirically rather than trusting the claim. The coverage check is the measurement instrument.

3. **Adjustment ingest is currently manual** (`mt data adjustment ingest --symbol X` is operator-driven). For production, a producer for splits/dividends has to exist and run automatically; the natural owner is the daily daemon, since corporate actions are daily-frequency data and the daily daemon already owns the daily DB connection.

4. **Universe-scale backfill needs visibility and resumability beyond per-symbol.** Slice 121 made single-symbol acquisition resumable. A 22-year × 5K-symbol fill needs operator-visible progress and a quota guard to avoid blowing the daily EODHD limit on day one.

5. **Production deployment from deferred slice 126** is still owed. The systemd/env-file/runbook story didn't change; what changed is what the daemons need to do once they're running.

This slice ships the platform-level capabilities (coverage, Stage B, daemon-owned corporate-action ingest, universe-scale backfill, gap log) that make production cutover meaningful. The cutover itself becomes the last phase of the slice, not its centerpiece.

## Scope

**In scope:**

1. **Daily daemon owns corporate-action ingest.** Per-symbol cycle becomes:
   `update daily OHLCV → ingest splits → ingest dividends → checkpoint`. Reuses slice 121's orchestrator pattern; same retry, same rate budget, same status surface. The manual `mt data adjustment ingest` CLI remains for ad-hoc operator use but is no longer the production path.

2. **`ICorporateActionsProvider` protocol.** Today's per-symbol EODHD ingester (`src/manta_trading/data/adjustment/ingest.py`) becomes `EODHDCorporateActionsProvider` implementing the protocol. New env var `MT_CORPORATE_ACTIONS_PROVIDER` (default `eodhd`) selects the implementation through a `build_corporate_actions_provider(settings)` helper, mirroring the slice 127 minute-provider seam. Even with one implementation today, the seam exists so a future Polygon or AlphaVantage CA provider is a 3-line change.

3. **Coverage check, first-class CLI command.**
   - `mt data minute coverage --symbol|--all --from --to [--json] [--threshold N]`
   - Uses the existing `TradingCalendar` class ([src/manta_trading/data/base/trading_calendar.py](../../../../src/manta_trading/data/base/trading_calendar.py)) — `is_trading_day(date)` to enumerate NYSE trading days in range; `get_expected_bar_count(date)` to derive the per-day expected-bar floor (already accounts for early closes). No new external dependency required; the `trading_calendars` table is seeded by migration 007 (slice 102).
   - For each `(symbol, trading_date)`, counts stored bars on `minute_ohlcv`. Flags dates where row count < threshold. Default threshold derived from `get_expected_bar_count(date) × 0.8` (so a 390-bar regular-hours day fails at <312 stored bars, an early-close 210-bar day fails at <168). `--threshold N` overrides with an absolute floor.
   - Output: Rich table by default; JSON via `--json`; per-symbol summary `(expected_days, present_days, partial_days, gap_days, gap_ranges[])`.
   - Exit 1 if any gaps found, 0 otherwise — composable in pipelines.
   - Provider-agnostic: scans stored data against the local `trading_calendars` table, doesn't call any external provider.

4. **`coverage_gaps` table** on the TimescaleDB host (alongside `acquisition_state`, per the architecture's centralization principle for acquisition state). Schema:
   ```
   coverage_gaps (
     symbol TEXT,
     gap_start TIMESTAMPTZ,
     gap_end TIMESTAMPTZ,
     source TEXT,                    -- 'eodhd', 'alphavantage', etc.
     detected_at TIMESTAMPTZ,
     resolution_status TEXT,         -- 'unknown' | 'provider_confirmed_unfillable' | 'retry_pending' | 'resolved'
     notes TEXT,
     PRIMARY KEY (symbol, gap_start, source)
   )
   ```
   The coverage-check CLI populates this table with `--persist` (or always when run via `--all`); operator triages each entry via a separate `mt data minute coverage triage` workflow (out of scope this slice — manual SQL is fine for now).

5. **NVDA gap as inaugural `coverage_gaps` entry.** Migration includes a one-time INSERT (acknowledged trade-off: this conflates schema evolution with operational data seeding. Acceptable for a single inaugural entry that documents a known case and demonstrates the table format; future bulk seed data should use a separate seed mechanism, not a migration):
   ```
   ('NVDA', '2024-06-07T23:59:00Z', '2024-07-25T08:00:00Z', 'eodhd',
    NOW(), 'provider_confirmed_unfillable',
    'EODHD support 2026-04-27: missing from sources, unfillable. Verified by probe.')
   ```
   Documents the known case, demonstrates the table format, and prevents future operator confusion ("why does NVDA have this gap?").

6. **Stage B verification — `mt data adjustment verify-against-eodhd-eod`.**
   - New CLI command (separate from Stage A; explicitly named after its source of truth).
   - For each date in scope: fetch EODHD `/eod/{symbol}` for the range, compute `published_k = adjusted_close / close` per day, compare against the minute table's stored `k_factor` for the same date, assert agreement within tolerance (default `0.0001` absolute, matching Stage A).
   - Per-day Rich table or `--json`; exit code 0/1.
   - Provider-coupled by design — Stage B's value *is* cross-checking against EODHD. A future Polygon-equivalent would be a parallel command, not a generalization. This is recorded in the adjustment ADR.
   - Quota cost: 1 call per symbol per range (the `/eod` endpoint covers whatever date range is requested in one call). Trivial.

7. **Universe-scale backfill — `mt data minute backfill`.**
   - `mt data minute backfill --universe active --since DATE [--max-symbols N] [--quota-fraction F]`
   - Iterates the active-instrument universe (existing `InstrumentRegistry`), runs the per-symbol minute-update path against each symbol from `--since` forward, persists universe-iteration state in a new `backfill_state` table on the TimescaleDB host so a restart resumes at the next unfetched symbol (not just at the next chunk within the current symbol).
   - **Quota guard:** caps daily EODHD request count at `quota_fraction × MT_EODHD_DAILY_LIMIT` (default fraction 0.8, default limit 100_000). When the cap is reached, the backfill sleeps until the next day's window. Avoids surprise overage.
   - Operator can `Ctrl-C` or `systemctl stop` and the next invocation resumes cleanly.
   - **Concurrency:** reuses the existing per-symbol concurrency bound (slice 125's semaphore-bounded pool); no new concurrency code.
   - **Mutual exclusion with the minute daemon (operational policy).** Backfill and the steady-state minute daemon must not run concurrently. The runbook's backfill phase requires the minute daemon to be stopped (`systemctl stop mt-minute-daemon`) before `mt data minute backfill` is invoked, and re-enabled only after backfill completes. Reasoning: (a) the backfill quota guard tracks only its own outbound calls; concurrent daemon traffic would not be counted, risking EODHD daily-limit overrun; (b) concurrent UPSERTs on the same `acquisition_state` row could cause stale-watermark reads and redundant fetches. The policy is operationally enforced (runbook), not via a lock table — the cost of a lock-table mechanism is not justified for a one-time-per-deployment operation. **Failure mode if violated:** quota overrun on the EODHD account (operator-visible, recoverable on quota reset), plus some redundant fetches that cost time but produce idempotent writes (no data corruption). See Decision 18.

8. **Backfill visibility — `mt data minute status` extended.**
   - Existing per-symbol watermark display retained.
   - New universe-progress section when a backfill is active:
     `X / Y symbols complete; coverage NN.N% of expected bars; quota used Z% of daily limit; est. completion: D days at current rate`.
   - Reads from `acquisition_state` (per-symbol watermarks), `backfill_state` (universe iteration), and a coverage-summary aggregation.
   - JSON output via `--json` for scripted monitoring.

9. **Validation-flags column deferred** (was: add `bar_flags INTEGER NOT NULL DEFAULT 0` now). Removed from this slice per iteration-4 review (F002): adding a speculative column with no reader or writer encodes an exception in shipped schema and risks normalizing premature schema expansion. The future migration cost on a populated hypertable is metadata-only on PG14 anyway, so the deferral cost is real but small. The column lands in the slice that introduces a validator populating it. **No outlier deletion, no smoothing, no forward-fill — ever — at storage time** remains a Non-Goal regardless of when the column appears.

10. **Production deployment to prod-host.** Reuses [126's](126-slice.production-deployment-to.md) systemd templates and runbook structure, updated for the post-127 environment:
    - **HARD GATE: PM-confirmed production minute-data backup is required before any prod-host migration or daemon invocation.** Per the architecture's external operational constraint and the slice 125 backup gate, the production minute hypertable contains irreplaceable historical data. The new tables created by this slice (coverage_gaps, backfill_state) are pure additions, but any DDL on the production environment is gated by the backup confirmation. Runbook Phase 0 is operator-confirms-backup-is-current; nothing else proceeds until it is checked.
    - **Test-environment dry-run is the second hard prerequisite.** Run both daemons against test DBs on the developer machine for ≥24h continuously; confirm coverage scan green (or `coverage_gaps` populated only with known/triaged entries); confirm Stage A and Stage B both passing on AAPL plus ≥4 additional tested symbols. Only then does the production deploy proceed.
    - Two systemd units: `mt-daily-daemon.service` and `mt-minute-daemon.service` (templates already drafted in slice 126).
    - `/etc/manta-trading.env` carrying `MT_EODHD_API_KEY`, `MT_ALPHAVANTAGE_API_KEY` (daily AV provider still uses it), `MT_MARKET_DB_URL`, `MT_TIMESCALE_DB_URL`, `MT_MINUTE_PROVIDER=eodhd`, `MT_CORPORATE_ACTIONS_PROVIDER=eodhd`, `MT_LOG_LEVEL=INFO`, `MT_LOG_FORMAT=json`.
    - Runbook at `project-documents/user/runbooks/production-deploy.md` (new file; structurally similar to deferred 126's plan, with the two hard gates above as Phase 0 and Phase 1).
    - journald drop-in for log volume limits.

11. **Provider compatibility contract documented.** New section in [120-arch-adjustment-policy.md](../architecture/120-arch-adjustment-policy.md) (the existing adjustment ADR) recording:
    - Minute provider must return raw (unadjusted) intraday OHLCV.
    - Daily provider used for `prev_close` lookup must return raw close. Adjusted close optional but enables Stage B.
    - Corporate-action source must supply complete splits and dividends per symbol.
    - Providers that return only adjusted prices on either endpoint (Yahoo) are incompatible.
    - Stage B verifier is named after its source (`verify-against-eodhd-eod`); switching providers means writing a parallel verifier or accepting Stage A only.

12. **CHANGELOG entry** under `[Unreleased]` with the standard Added/Changed/Removed sections.

**Out of scope (explicitly):**

- **Containerization** (Docker, Compose). Future slice; will supersede the systemd approach when it lands.
- **Polygon / Finnhub provider evaluation.** Triggered only if the universe coverage scan after backfill shows >5% of symbol-days with gaps, or any gap >30 trading days on a top-100 large-cap symbol. Separate slice if it happens.
- **Monitoring, alerting, dashboards, log shipping.** Local journald + CLI status commands only. No Prometheus, no Grafana, no PagerDuty. Future ops slice if operational evidence justifies.
- **Secrets manager** (Vault, SOPS, etc.). Root-owned mode-0640 env file is sufficient for single-host. Future when there's more than one host.
- **Real-time / WebSocket tier.** REST polling only. Databento or EODHD WebSocket later.
- **Strategy-side data-access layer** (the `data.minute_bars(..., on_missing="raise"|"skip"|...)` shape). That belongs in a slice 130-ish layer; this slice provides only the storage-side detection (coverage_gaps table) that such a layer would consume.
- **Outlier removal, smoothing, forward-fill at storage time.** Documented as anti-pattern in the adjustment ADR. The validation-flags column flags; it does not mutate.
- **Coverage-gap triage workflow.** Manual SQL inspection of `coverage_gaps` is fine for now. A `mt data minute coverage triage` UX is future work.
- **Per-environment provider override / A-B comparison.** Both daemons resolve provider from a single env var. Running EODHD and Polygon in parallel for comparison would need either two env files or a small dispatcher extension; out of scope here.
- **Automatic recompute of adjusted columns when a new corporate action arrives.** The current writer applies `k_factor` at write-time using whatever splits/dividends are in the daily DB at that moment. If a new split for AAPL is ingested tomorrow, *future* writes apply it correctly, but *historical* rows already in `minute_ohlcv` retain the old `k_factor`. Slice 127's adjustment ADR notes this; addressing it (an `mt data adjustment recompute` command) is a follow-up slice. Stage A and Stage B both surface the drift if it occurs.

## Architecture

### Component map (what changes vs what stays)

```
existing (unchanged):
  IMinuteDataProvider              ← protocol
  EODHDMinuteProvider              ← slice 127
  MinuteAcquisitionOrchestrator    ← slice 124
  TimescaleMinuteWriter            ← slice 127 (writes raw + adjusted)
  MinuteAcquisitionDaemon          ← slice 125
  AlphaVantageDailyProvider        ← slice 122
  DailyAcquisitionOrchestrator     ← slice 122
  DailyAcquisitionDaemon           ← slice 123
  AdjustmentContext                ← slice 127
  k_factor function                ← slice 127
  Stage A verifier                 ← slice 127 (mt data adjustment verify)

existing (touched):
  daily orchestrator per-symbol cycle
                                   ← gains splits/dividends ingest steps
  daily daemon                     ← per-symbol cycle now includes CA ingest
  Settings                         ← +corporate_actions_provider, +eodhd_daily_limit
  mt data minute status            ← extended with universe-progress section
  120-arch-adjustment-policy.md    ← adds provider compatibility contract
  src/manta_trading/data/adjustment/ingest.py
                                   ← refactored behind ICorporateActionsProvider

new:
  ICorporateActionsProvider        ← src/manta_trading/data/adjustment/providers/__init__.py
  EODHDCorporateActionsProvider    ← src/manta_trading/data/adjustment/providers/eodhd.py
  build_corporate_actions_provider helper
  CorporateActionsProviderName     ← StrEnum
  coverage check module            ← src/manta_trading/data/coverage/
  mt data minute coverage CLI      ← under existing minute_app
  mt data adjustment verify-against-eodhd-eod CLI
                                   ← under existing adjustment_app
  mt data minute backfill CLI      ← under existing minute_app
  backfill orchestrator            ← src/manta_trading/data/acquisition/minute/backfill.py
  schema migration: coverage_gaps table  ← migrations/minute.py (012)
  schema migration: backfill_state table ← migrations/minute.py (013)
  schema migration: NVDA inaugural gap   ← migrations/minute.py (014)
  systemd units, journald drop-in        ← deploy/systemd/ (rendered from 126's templates)
  production-deploy.md runbook       ← project-documents/user/runbooks/

unchanged but now used in production:
  AlphaVantageMinuteProvider       ← stays dormant; not started in production
```

### Data flow: daily daemon per-symbol cycle (post-128)

```
For each symbol in active universe:
  ┌─────────────────────────────────────────────────────┐
  │ 1. AlphaVantageDailyProvider.fetch(symbol)          │
  │    → daily OHLCV bars                               │
  └─────────────────────┬───────────────────────────────┘
                        │
  ┌─────────────────────▼───────────────────────────────┐
  │ 2. MarketDB writes raw + adjusted daily rows        │
  │    → dailyohlcvadjusted (raw close + adjusted close)│
  └─────────────────────┬───────────────────────────────┘
                        │
  ┌─────────────────────▼───────────────────────────────┐
  │ 3. EODHDCorporateActionsProvider.ingest(symbol)     │
  │    → splits, dividends                              │
  │    GET /splits/{symbol} + GET /div/{symbol}         │
  │    UPSERT into splits, dividends tables             │
  │    On failure (after retries): log ERROR, mark      │
  │    ca_ingest_status='failed' for this cycle, do     │
  │    NOT block step 4. (See Decision 14.)             │
  └─────────────────────┬───────────────────────────────┘
                        │
  ┌─────────────────────▼───────────────────────────────┐
  │ 4. acquisition_state checkpoint advance             │
  │    (existing slice-121 pattern, crash-safe)         │
  │    Watermark advances regardless of step-3 outcome. │
  └─────────────────────────────────────────────────────┘
```

The minute daemon's cycle is unchanged from slice 127. It reads splits/dividends from the daily DB at write time; the daily daemon is now the producer that keeps those tables fresh. **Stale CA data is recoverable**: a CA-ingest failure on cycle N is retried on cycle N+1 (next per-symbol pass). Stage A and Stage B both surface the resulting drift if staleness persists across multiple cycles.

### Data flow: backfill mode

```
mt data minute backfill --universe active --since 2004-01-01:

  ┌─────────────────────────────────────────────────────┐
  │ Universe iterator: active symbols, ordered          │
  │ resume from backfill_state.cursor if set            │
  └─────────────────────┬───────────────────────────────┘
                        │
  ┌─────────────────────▼───────────────────────────────┐
  │ For each symbol:                                    │
  │   - quota guard: if daily_calls > cap, sleep until  │
  │     UTC midnight                                    │
  │   - existing minute-update path with --from=since,  │
  │     --to=now (uses slice 127's ad-hoc backfill mode │
  │     OR steady-state mode; decision below)           │
  │   - on success: backfill_state.cursor = next symbol │
  │   - on permanent failure: log, mark, advance        │
  └─────────────────────┬───────────────────────────────┘
                        │
  ┌─────────────────────▼───────────────────────────────┐
  │ When iteration complete:                            │
  │   - run mt data minute coverage --all --persist     │
  │   - report summary                                  │
  └─────────────────────────────────────────────────────┘
```

**Decision: backfill drives the steady-state minute-update path, NOT the slice-127 ad-hoc `--from/--to` mode.** Reasoning: ad-hoc mode bypasses `acquisition_state` (intentionally — it was designed for one-off pulls). For universe-scale backfill we *want* watermarks to advance, so subsequent steady-state runs of the minute daemon resume from where backfill left off. The backfill command essentially calls `update_symbol(symbol, from_ts=since_or_watermark)` per symbol with normal state-advancement.

### Coverage check algorithm

```
Input: symbol(s), date range, optional --threshold override
Output: list of (symbol, date, expected_min_bars, actual_bars, status)

calendar = TradingCalendar('NYSE', conninfo)

For each symbol × date in range:
  if not calendar.is_trading_day(date): skip
  expected_bars = calendar.get_expected_bar_count(date)   # accounts for early closes
  expected_min_bars = override OR int(expected_bars * 0.8)
  actual_bars = SELECT count(*) FROM minute_ohlcv
                  WHERE symbol = ? AND time::date = ?
  status = 'present' if actual_bars >= expected_min_bars
         else 'partial' if actual_bars > 0
         else 'gap'

When run with --persist:
  # Atomic per-symbol replacement within the scanned range.
  # Required for correctness under shrinking, splitting, and merging gaps
  # (UPSERT alone produces stale/overlapping entries in those cases).
  BEGIN;
    DELETE FROM coverage_gaps
      WHERE symbol = ? AND source = ?
        AND gap_start >= ? AND gap_end <= ?;          -- the scanned range
    INSERT INTO coverage_gaps (symbol, gap_start, gap_end, source, detected_at, ...)
      VALUES (...) FOR EACH contiguous run of 'gap'/'partial' dates;
  COMMIT;
```

The threshold is a configurable floor, not strict equality, because session lengths vary (early closes around holidays, half-days, ETH-inclusive runs). Defaulting to 80% of `get_expected_bar_count(date)` adapts per-day rather than a hardcoded constant. `--threshold N` provides an operator escape hatch when the calendar's expected count is wrong for some symbol class.

**Persistence semantics under changing gap boundaries.** The DELETE-then-INSERT pattern (vs. plain UPSERT) is required for correctness in three scenarios that any real coverage scan eventually encounters:

1. **Gap shrinks.** A previously gappy range fills partially after a provider fix. Old row had `gap_end = J`; new row should have `gap_end = J' < J`. UPSERT on `(symbol, gap_start, source)` would update the same row; the DELETE-then-INSERT also handles it correctly.
2. **Gap splits.** A previously contiguous gap fills in the middle, producing two separate gaps with different `gap_start` values. UPSERT-only would insert the new row but leave the old row spanning the now-filled range — stale/incorrect data. DELETE-then-INSERT replaces the old row with the two new ones.
3. **Gaps merge.** Two adjacent gap rows merge when a previously-present day becomes gappy. UPSERT-only would leave both rows; DELETE-then-INSERT collapses them into one.

The DELETE clause is bounded to the scanned range (`gap_start >= scan_from AND gap_end <= scan_to`) so a `--from 2024-06-01 --to 2024-08-01` scan does not erase entries outside that window for the same symbol. Operator-set fields (`resolution_status`, `notes`) on a row whose boundaries change are reset to defaults — a deliberate trade-off; if an operator triaged an entry and the gap boundaries shift, re-triage is required. Future enhancement (Initiative 140 territory): preserve `resolution_status='provider_confirmed_unfillable'` across boundary changes by matching on overlap rather than exact key.

### Error handling and failure modes for new HTTP paths

Three new EODHD HTTP paths are introduced by this slice: `GET /splits/{symbol}` (CA ingest), `GET /div/{symbol}` (CA ingest), and `GET /eod/{symbol}` (Stage B verifier). Each must classify and handle failure modes explicitly.

**Common transport-layer rules** (apply to all three; reuse the existing `EODHDMinuteProvider` httpx client patterns):

- **Connection timeout:** `connect_timeout=10s`. On expiry, classify transient and retry up to 3 times with exponential backoff (1s, 2s, 4s).
- **Read timeout:** `read_timeout=30s` for `/splits` and `/div` (small payloads); `60s` for `/eod` (potentially multi-year range). On expiry, transient — same retry as above.
- **Connection hang (TCP open, no headers):** governed by `connect_timeout`. No special handling.
- **Peer disconnect mid-response (partial body):** httpx raises `RemoteProtocolError` or similar. Classify transient — retry. Do not attempt to use the partial body.
- **HTTP 4xx (except 429):** classify permanent. Log at ERROR with full URL (api_token already masked by `_CREDENTIAL_SAFE_LOGGERS` from slice 127), do not retry, propagate as a `ProviderPermanentError` for the caller to handle per-context.
- **HTTP 429 (rate limit):** transient. Honor `Retry-After` header if present; otherwise exponential backoff up to 60s, max 5 retries.
- **HTTP 5xx:** transient. Same retry as 429 minus the `Retry-After` priority.

**Per-endpoint specifics:**

- **CA ingest (`/splits`, `/div`):** caller is the daily daemon's per-symbol cycle. Permanent failure (404 on a delisted ticker, 4xx malformed) → log, mark CA ingest for symbol as `failed`, **continue the daily cycle** (the OHLCV write already happened; CA staleness is recoverable next cycle). Transient-retries-exhausted → same handling as permanent: log, mark, continue. Stale CA data is surfaced by Stage A drift if it persists across multiple cycles.
- **Stage B (`/eod`):** caller is the verifier CLI command. Permanent failure → exit 2 with diagnostic message (the operator can re-run; this is not silent). Transient-retries-exhausted → exit 2 same as permanent. Partial-success (some symbols verified, one failed) → tabulate per-symbol results, exit 1 if any verification failed *or* errored, exit 0 only if all green.

**No silent fallback.** Per project rules, no swallowed exceptions on these paths. Every failure is logged at WARNING (transient, will retry) or ERROR (permanent or retries-exhausted).

**Structured event emission** for every new fetch attempt, mirroring the established slice-121 pattern at [src/manta_trading/data/acquisition/events.py](../../../../src/manta_trading/data/acquisition/events.py). Each of the three new HTTP paths and the backfill orchestrator emit one event per attempt to the existing `JsonlEventSink`, with these fields populated per the architecture's required schema:

| Operation | `action` value | Notes on payload |
|---|---|---|
| CA ingest splits | `ca_ingest_splits` | `granularity='daily'`, `provider='eodhd'`, `rows_fetched`=splits count, `time_range`=`(since, now)` |
| CA ingest dividends | `ca_ingest_dividends` | same shape, dividends count |
| Stage B verifier `/eod` | `verify_eod` | `granularity='daily'`, `rows_fetched`=days verified, `error`=mismatch count if any |
| Backfill per-symbol cycle | `backfill_symbol` | wraps the existing minute-update event chain; adds `universe`, `cursor_position` to the structured payload |

Backfill quota-cap-reached and cap-window-rollover are also emitted as events (`action='quota_sleep'`, `action='quota_window_advance'`) so an external observer can reconstruct the backfill timeline from the event log alone.

### Provider compatibility contract (recorded in adjustment ADR)

The minimum a provider stack must supply for the slice 127 + 128 adjustment + verification pipeline to function:

| Capability | Required | Why |
|---|---|---|
| Raw intraday OHLCV | Yes | The bars we store; must be unadjusted so `adj = raw × k` is meaningful |
| Raw daily close | Yes | `prev_close` for the dividend factor in `k_factor()` |
| Splits, complete | Yes | Multiplicative factor in `k` |
| Dividends, complete with `amount` and `ex_date` | Yes | Dividend factor in `k` |
| Daily `adjusted_close` | Optional | Enables Stage B cross-check; not load-bearing |
| Bulk endpoints | Optional | Backfill optimization; not load-bearing |

Providers known to satisfy: EODHD (✓), AlphaVantage daily (✓ for daily data; minute window 2yr makes it incompatible for primary minute service), Polygon (✓ via `adjusted=false` selectability), Finnhub (✓ if daily-raw confirmed).

Providers known to fail: Yahoo (intraday is adjusted-only).

### Backfill rate budget (EODHD $30 plan)

Reusing slice 127's measured numbers:
- Quota: 100K calls/day = 20K intraday requests/day.
- Per-symbol full 22-year history: ~67 requests at the 120-day chunk window.
- 5K active symbols × 67 = 335K requests = ~17 days at full quota utilization.
- Default `--quota-fraction 0.8` → 16K requests/day → ~21 days. Conservative; leaves headroom for the daily daemon's corporate-action ingest (10K calls one-time, then near-zero steady-state).

The quota guard is a soft check (in-process counter, persisted to `backfill_state`). EODHD's hard rate limits (per-second, per-minute) are enforced separately by the existing `RateLimiter` and remain unchanged.

## Cross-slice dependencies and interfaces

- **Hard dep on 121** — `acquisition_state` schema and orchestrator pattern. (Complete.)
- **Hard dep on 122, 123** — daily provider and daily daemon (now extended with CA ingest). (Complete.)
- **Hard dep on 124, 125** — minute orchestrator and minute daemon. (Complete.)
- **Hard dep on 127** — `EODHDMinuteProvider`, adjustment layer, Stage A verifier, splits/dividends tables. (Complete.)
- **Hard dep on 150** — schema migration framework for both DBs. (Complete.)
- **Supersedes 126** — reuses systemd templates and runbook structure; 126 marked `supersededBy: 128` in its frontmatter when 128 ships.
- **Forward dep from a hypothetical "Polygon evaluation" slice (129+)** — `ICorporateActionsProvider` protocol introduced here is the seam that slice consumes. No code change required at that point; just a new provider class and a config flip.
- **Forward dep from a strategy-side data-access slice (130+)** — `coverage_gaps` table is the data contract that layer reads to distinguish "gap present" from "data not yet fetched."

## Decisions

1. **Daily daemon owns corporate-action ingest, not a separate cron/timer or an enhancement to the minute daemon.** Corporate actions are daily-frequency by nature; the daily daemon already runs against the daily DB; the producer/consumer separation is clean (daily produces splits/dividends, minute consumes them). Adding a separate scheduler fragments supervision for no benefit.

2. **`ICorporateActionsProvider` introduced now even with one implementation.** Same pattern as slice 127's `MinuteProviderName` enum + `build_minute_provider`. The cost is trivial; the benefit is that a future provider swap is a 3-line change rather than a refactor. Aligns with project rules on "no magic strings, structured for future expansion."

3. **Coverage check is provider-agnostic and operates on stored data only.** It compares stored bars against a market calendar, not against any provider's claim. This is the only check that catches the NVDA-class failure mode (Stage A is structurally blind to it). It works identically against EODHD, Polygon, or any future provider.

4. **`coverage_gaps` and `backfill_state` live on the TimescaleDB host alongside `acquisition_state`.** Reasoning: the architecture document explicitly centralizes "all acquisition state (watermarks, run status, error tracking)" on the TimescaleDB host regardless of where the data itself is written. `acquisition_state` already lives there (verified in [src/manta_trading/cli/commands/data.py:66-79](../../../../src/manta_trading/cli/commands/data.py)). Coverage gaps and backfill state are operational metadata about acquisition progress — same category. Co-locating with `acquisition_state` keeps the operational-state surface in one place and matches the architecture's centralization principle. (An earlier draft of this slice placed them on the daily DB; corrected during review per F004.)

5. **NVDA inaugural entry baked into the migration.** Documents the known case, demonstrates the table format, prevents re-discovery. The migration is reversible; if the gap is ever fixed by EODHD, an operator updates `resolution_status = 'resolved'`.

6. **Stage B verifier is named after its source (`verify-against-eodhd-eod`), not generalized.** Stage B is intrinsically provider-coupled — its value *is* cross-checking against a specific authoritative source. A future Polygon equivalent would be `verify-against-polygon-eod`, parallel command. Cleaner than a generic interface that hides which source it trusts.

7. **Backfill drives the steady-state minute-update path, not the slice-127 ad-hoc `--from/--to` path.** Watermarks must advance so subsequent steady-state daemon runs resume cleanly from the backfill cursor. Ad-hoc mode (designed for one-off operator pulls) is left untouched and remains available for that use case.

8. **Quota guard is soft (in-process counter persisted to `backfill_state`), not enforced via a separate rate-limit dimension on `RateLimiter`.** The existing per-second and per-minute limits stay in `RateLimiter`. The daily quota is a higher-level budget, naturally tracked at the backfill orchestrator level. Crossing the cap triggers a sleep-until-UTC-midnight; not a hard error.

9. **Validation-flags column added now even though no validator populates it.** Migration cost is trivial; later migration cost is non-trivial (active hypertable). "Future-proof in advance only when cost is near-zero" is the rule of thumb being applied. **One-time exception, not a pattern.** The architecture's principle is "reuse what exists, rewrite what's broken" — speculative columns generally don't align with that. The justification here rests specifically on the cost asymmetry between adding the column to an empty/test hypertable now vs. adding it later to a production hypertable holding 22 years of irreplaceable data. Future schema additions in this repo should require a populated reader/writer at the time of the migration, not deferred to a hypothetical later slice.

10. **No outlier handling, smoothing, or forward-fill at storage time. Ever.** Documented as anti-pattern in the adjustment ADR. The reasoning: outlier-ness is strategy-defined and time-varying; raw data must be immutable for reproducibility; standard cleaning techniques (Z-score, ffill) actively *delete* real corporate events and *fabricate* fake trades. The validation-flags column flags rows for downstream consideration; it does not mutate them.

11. **AV stays dormant in production.** `MT_MINUTE_PROVIDER=eodhd` is the default; AV minute provider is not wired for production (its 2-year window cannot meaningfully complement EODHD). The file remains on disk per slice 127's "deletion deferred" decision. Daily AV provider continues as the sole daily OHLCV source.

12. **Test-environment dry-run is a hard prerequisite for prod-host cutover.** The runbook's first phase requires running both daemons against test DBs locally for ≥24h, with coverage scan green (or `coverage_gaps` populated as expected — e.g., the NVDA entry visible) and Stage A + Stage B both passing. Only then does the deploy proceed. This is operational discipline encoded in process; the runbook makes it explicit so it doesn't get skipped.

13. **No automatic recompute on new corporate actions.** Out of scope; deferred to a follow-up slice. Stage A and Stage B both surface the drift if it occurs, so silent corruption is impossible — the operator just has to act.

14. **CA-ingest failure does not block the daily-OHLCV checkpoint.** When the daily daemon fetches OHLCV successfully but corporate-action ingest fails (after retries), the checkpoint still advances. Reasoning: (a) the OHLCV row is independently useful (charting, daily strategies); (b) CA staleness is recoverable on the next cycle; (c) blocking the watermark would compound a transient EODHD outage into a multi-symbol stall. The CA ingest is marked `failed` with a per-symbol counter; if a symbol's CA ingest fails N consecutive cycles (default 3), it is escalated to ERROR-level logging and surfaced in `mt data daily status`. Stage A and Stage B catch persistent drift downstream regardless.

15. **Coverage check and Stage B placement (provisional, with Initiative 140 acknowledged).** The architecture document assigns "is our data correct" to Initiative 140 (data quality operations) and "fetch what's needed" to Initiative 120 (this initiative). The coverage check and Stage B verifier are arguably 140 territory. They are placed in slice 128 because production cutover cannot proceed without them — Stage A is structurally blind to provider data gaps (NVDA case proves this), and Stage B is the only signal of provider-side adjusted-close drift. **The capabilities ship here as operational necessity. Their analytical extensions** — historical gap-rate trending, dashboards, multi-provider cross-validation, automated triage workflows — **remain Initiative 140's responsibility.** The migration path: 140 may later move ownership of `coverage_gaps` to its own schema or provide a richer query surface; the storage model (gap rows on the TimescaleDB host) and CLI command shape (`mt data minute coverage ...`) are designed to be 140-compatible. No code change required at handover.

    **Formal handoff tracked.** A note is added to Initiative 140's architecture document ([140-arch.data-quality-operations.md](../architecture/140-arch.data-quality-operations.md), if present, or its successor) recording that slice 128 ships an operational baseline of coverage checking and EOD cross-validation, and that 140's eventual scope inherits these as starting points rather than greenfield work. Success criterion 14 confirms the architecture-doc handoff note exists.

16. **Production minute-data backup gate is first-class** (per architecture's external operational gate from slice 125 and reviewer F005). The `bar_flags` migration is an `ALTER TABLE` on the production `minute_ohlcv` hypertable, which holds irreplaceable historical data. **No production migration in this slice may proceed until PM-confirms minute-data backup.** This gate is restated explicitly in scope item 10 and as Phase 0 of the runbook (see Verification Walkthrough Phase 7), not buried in a parenthetical. The gate applies even though slice 127 already deployed schema changes to test — it is binding for the production cutover specifically.

17. **(removed iteration 4)** Decision originally covered the `bar_flags` migration's metadata-only behavior on PG14. With the column itself deferred (Decision 9), this decision is no longer applicable.

18. **Backfill-daemon mutual exclusion via operational policy, not lock tables.** The simpler design (runbook gate + operator discipline) is chosen over a `backfill_lock` table or a shared-quota-counter mechanism for two reasons: (a) backfill is a one-time-per-deployment operation, not a recurring workflow — engineering investment in mutual-exclusion infrastructure does not amortize; (b) the failure mode if the policy is violated is bounded: EODHD quota overrun (recoverable on next-day reset) plus some redundant fetches that produce idempotent writes thanks to the slice 127 dedup migration (`UNIQUE (symbol, time)`), so no data corruption is possible. If a future use case introduces recurring backfills (e.g., repointing universe definition, new asset class onboarding), upgrade to a lock table at that point. Captured in the runbook as a Phase 8 prerequisite and in scope item 7.

## Verification walkthrough

Demo script the PM (or a future operator) can run to confirm the slice delivers. All commands run against test environment unless explicitly noted.

### Phase 1: Schema migrations

```bash
mt data migrate apply --db all
mt data migrate status --db all
# Expected: minute track gains migrations 012 (coverage_gaps), 013 (backfill_state),
# 014 (NVDA inaugural coverage_gaps row).
# Daily track gains no new migrations in this slice.
```

Spot-check the inaugural NVDA gap entry:
```sql
SELECT symbol, gap_start, gap_end, resolution_status, notes
  FROM coverage_gaps WHERE symbol = 'NVDA';
-- Expected: one row with the 2024-06-07 → 2024-07-25 range, provider_confirmed_unfillable.
```

### Phase 2: Daily daemon owns corporate-action ingest

```bash
mt data daily update AAPL
# Expected log lines (in order):
#   "fetched daily OHLCV (X rows)"
#   "ingested splits (N updated)"
#   "ingested dividends (M updated)"
#   "checkpoint advanced for AAPL"
```

Run twice; confirm idempotency. Run on a symbol with no recent corporate actions; confirm zero-add behavior with no errors.

```bash
# Run the daily daemon for 5 minutes, observe:
mt data daily daemon &
sleep 300; kill $!
mt data daily status
# Expected: per-symbol watermarks advanced, splits/dividends counts present.
```

### Phase 3: Coverage check against known-good and known-gap symbols

```bash
mt data minute coverage --symbol AAPL --from 2020-08-25 --to 2020-09-04
# Expected: 9 trading days, all "present", exit 0.

mt data minute coverage --symbol NVDA --from 2024-06-01 --to 2024-08-01
# Expected: 22 trading days, of which ~5 present (Jun 3-7), ~16 gap (Jun 10 - Jul 24);
#   one entry surfaces matching the inaugural coverage_gaps row.
#   Exit code 1.

mt data minute coverage --symbol NVDA --from 2024-06-01 --to 2024-08-01 --json
# Expected: same content, JSON shape.

mt data minute coverage --symbol NVDA --from 2024-06-01 --to 2024-08-01 --persist
# Expected: re-running produces no new rows in coverage_gaps (NVDA gap already there);
#   the existing row's detected_at is updated.
```

### Phase 4: Stage B verifier

```bash
# Pre-condition: AAPL minute data and corporate actions ingested (Phases 1-2 cover this).
mt data adjustment verify-against-eodhd-eod --symbol AAPL --from 2020-08-25 --to 2020-09-04
# Expected: per-day table showing stored k vs published k, per-day diff < 0.0001, all PASS, exit 0.
# Quota cost: 1 EODHD call (the /eod range request).
```

Synthetic-drift demo (mirrors slice 127's Phase 7, but for Stage B):
```bash
# Corrupt one stored adj_close
psql "$MT_TIMESCALE_DB_URL" -c \
  "UPDATE minute_ohlcv SET adj_close = adj_close * 1.5
     WHERE symbol='AAPL' AND time::date = '2020-08-31' LIMIT 1;"

mt data adjustment verify-against-eodhd-eod --symbol AAPL --from 2020-08-25 --to 2020-09-04
# Expected: 2020-08-31 row FAILs with diff well above 0.0001; exit 1.

# Restore (slice 127 has integration tests that handle this in tearDown).
```

### Phase 5: Backfill on a 5-symbol mini-universe

```bash
# Define a mini-universe in the registry (or use an existing test set).
mt data minute backfill --universe test_5 --since 2024-01-01 --quota-fraction 0.05
# Expected:
#   - Per-symbol log: chunks fetched, watermark advanced.
#   - Quota guard logged at fraction-of-cap intervals.
#   - On Ctrl-C mid-run: backfill_state.cursor persists.

# Restart and observe resume:
mt data minute backfill --universe test_5 --since 2024-01-01 --quota-fraction 0.05
# Expected: resumes at the symbol where Ctrl-C hit (not at symbol 0).
```

```bash
mt data minute status --json
# Expected: backfill section present, with X/Y symbols complete and quota-used percentage.
```

### Phase 6: Coverage scan after backfill

```bash
mt data minute coverage --all --from 2024-01-01 --to 2024-12-31 --persist
# Expected: most rows "present"; any gaps populated into coverage_gaps for triage.
# Outputs a summary: total expected days, total gap days, top-N symbols by gap-day count.
```

This is the slice's empirical answer to "what is EODHD's gap rate across our universe?" — the question that motivates the "switch providers?" decision later.

### Phase 7: Production deployment — two hard gates first

**Hard Gate A — PM-confirmed production minute-data backup.** Per the architecture's external operational constraint. The `minute_ohlcv` hypertable holds irreplaceable historical data; the `bar_flags` ALTER TABLE migration is DDL on that hypertable. **No production migration runs until backup is confirmed current.** Operator pings PM and waits for explicit confirmation; this is recorded in the runbook with a checkbox.

**Hard Gate B — Phases 1-6 above run cleanly against test for ≥24h continuously.** Coverage scan green (or `coverage_gaps` populated only with known/triaged entries — at minimum the NVDA inaugural row visible). Stage A and Stage B both passing on AAPL plus ≥4 additional symbols. Quota guard demonstrably engaged at ≥1 fraction-of-cap threshold during a backfill.

Only after both gates clear does the operator follow [project-documents/user/runbooks/production-deploy.md](../runbooks/production-deploy.md), which includes:
- Phase 0: confirm Hard Gate A (backup) — explicit checkbox, requires PM sign-off.
- Phase 1: confirm Hard Gate B (test dry-run results) — explicit checkbox, references the test-environment evidence.
- Phase 2: set service-user variable.
- Phase 3: prepare host (one-time): create service user, /opt and /var directories.
- Phase 4: install code: clone repo, `uv sync`.
- Phase 5: drop in `/etc/manta-trading.env` (root-owned, mode 0640).
- Phase 6: apply migrations against production DBs (`mt data migrate apply --db all`); spot-check the inaugural NVDA row landed.
- Phase 7: render and install systemd units, journald drop-in.
- Phase 8: start daily service; observe via `journalctl -u mt-daily-daemon -f` for 5+ minutes; confirm CA ingest steps in logs.
- Phase 9: gated minute service start.
- Phase 10: failure recovery sanity check (`systemctl kill`, observe restart and resume).
- Phase 11: log volume check after 24h.
- Phase 12 (backfill, optional, gated): when ready to run universe-scale backfill, **stop the minute daemon first** (`sudo systemctl stop mt-minute-daemon`), invoke `mt data minute backfill --universe active --since 2004-01-01 --quota-fraction 0.8` (potentially under `screen`/`tmux` since runtime is ~21 days at full universe), and re-enable the daemon (`sudo systemctl start mt-minute-daemon`) only after backfill exits cleanly. The daily daemon may run throughout (it consumes a different rate budget). See Decision 18 for rationale.

### Phase 8: AV minute path stays dormant in production

```bash
# On prod-host, confirm:
sudo journalctl -u mt-minute-daemon | grep -i "alphavantage" || echo "no AV references in minute daemon logs"
# Expected: no matches in minute daemon logs. Daily daemon may match (daily AV provider in use).

env | grep MT_MINUTE_PROVIDER
# Expected: MT_MINUTE_PROVIDER=eodhd
```

If all phases pass, slice 128 is delivered.

## Success criteria

1. Daily daemon's per-symbol cycle includes `ingest_splits` and `ingest_dividends` steps; resume-on-restart preserves cycle position. Verifiable by `mt data daily daemon` running for ≥30min and per-symbol logs showing all three steps.

2. `ICorporateActionsProvider` protocol exists; `EODHDCorporateActionsProvider` implements it; `build_corporate_actions_provider(settings)` dispatches via `MT_CORPORATE_ACTIONS_PROVIDER`. New env var defaults to `eodhd`.

3. `mt data minute coverage` CLI command exists with `--symbol|--all`, `--from`, `--to`, `--threshold`, `--json`, `--persist` flags. Provider-agnostic (no provider call). Exit code 0/1 on clean/gappy. Populates `coverage_gaps` when `--persist`.

4. `coverage_gaps` table exists on the TimescaleDB host (alongside `acquisition_state`) with the schema specified. NVDA inaugural row present after migration `minute.014` runs. Re-running coverage scan does not duplicate rows.

5. `mt data adjustment verify-against-eodhd-eod` CLI command exists. Per-day comparison of stored `k_factor` vs `/eod`-published `k`. Tolerance default 0.0001 absolute. Exit 0/1. Synthetic drift produces FAIL.

6. `mt data minute backfill --universe X --since DATE` CLI command exists. Resumes at universe-iteration granularity from `backfill_state`. Quota-guard configurable via `--quota-fraction`. Drives the steady-state minute-update path (watermarks advance).

7. `backfill_state` table exists on the TimescaleDB host (alongside `acquisition_state`); persists `(universe, cursor_symbol, since_date, started_at, last_progress_at, daily_calls_used, daily_calls_window_start)`.

8. `mt data minute status` extended with universe-progress section when a backfill is active. JSON output supports `--json`.

9. **(removed iteration 4)** — `bar_flags` deferred to a future slice that populates it.

10. systemd units (`mt-daily-daemon.service`, `mt-minute-daemon.service`) and journald drop-in checked into `deploy/systemd/`. Production runbook at `project-documents/user/runbooks/production-deploy.md` covers test-dry-run prerequisite, install, gated minute-start, failure recovery, log volume check.

11. Adjustment ADR ([120-arch-adjustment-policy.md](../architecture/120-arch-adjustment-policy.md)) updated with provider compatibility contract section and outlier-handling Non-Goal section.

12. CHANGELOG entry under `[Unreleased]` recording all of the above.

13. After running Phase 1-6 of the verification walkthrough against the test environment for ≥24h continuously, the system is in a state to be deployed to prod-host — meaning: coverage scan green or coverage_gaps populated only with known/triaged entries; Stage A and Stage B both passing on AAPL and at least 4 other tested symbols; quota guard demonstrably engaged at ≥1 fraction-of-cap threshold during a backfill.

14. **Initiative 140 handoff note recorded.** A paragraph added to the Initiative 140 architecture document (or, if 140 has no doc yet, a stub document at `project-documents/user/architecture/140-arch.data-quality-operations.md`) recording that slice 128 ships operational coverage checking and EOD cross-validation as a baseline that 140 inherits. The note enumerates the artifacts (CLI commands, `coverage_gaps` table schema, event types) that 140 absorbs and lists the analytical extensions explicitly out of scope for 128.

15. **Structured event emission verified for new I/O paths.** For each of the four new event types (`ca_ingest_splits`, `ca_ingest_dividends`, `verify_eod`, `backfill_symbol`) plus the two backfill control events (`quota_sleep`, `quota_window_advance`), a unit test asserts the event is emitted on the success path and on at least one failure path with the architecture-mandated fields populated (`run_id`, `symbol`, `granularity`, `provider`, `action`, `status`, `rows_fetched`, `time_range`, `duration_ms`, `error`, `timestamp`).

## Risks

- **Universe coverage scan reveals a high gap rate (>5% symbol-days) and the EODHD-vs-Polygon decision becomes urgent.** Mitigation: this slice produces the *measurement*. Decision to switch is a separate slice. The cost of slice 128 is not wasted if a switch happens — `ICorporateActionsProvider`, `coverage_gaps`, the coverage-check CLI, and the backfill orchestrator are all provider-agnostic.

- **Backfill quota guard is too conservative or too aggressive.** Mitigation: `--quota-fraction` is a CLI flag, not a hardcode. Operator can tune. Default 0.8 is a starting point; observed daily-call patterns will refine.

- **Daily-daemon CA ingest hits an EODHD quirk we haven't seen on AAPL/NVDA** (e.g., a symbol with thousands of dividends, or a delisted symbol that errors). Mitigation: existing slice-127 ingest already handled the AAPL 90-dividend case and per-symbol error-and-continue is the existing daemon pattern. Continue, log, advance.

- **`coverage_gaps` table grows unboundedly under universe scan.** At 5K symbols × 22yr × 252 trading days ≈ 27.7M expected rows, but `coverage_gaps` only stores *gap ranges* (contiguous gap runs collapsed into single rows). Even pessimistically (1% gap rate, average gap span 5 days) this is ~10K rows. Trivial.

- **Production cutover surfaces an environment-specific bug** (path, permission, env-var name) not caught in test dry-run. Mitigation: runbook's Phase 4 (`mt data migrate status` against production DBs) and Phase 6 (5-min observation before declaring success) catch most of these. If a code-level bug surfaces, it's a tiny follow-up slice — do not bundle code work into this one.

- **The "≥24h test dry-run" gate gets skipped in operator haste.** This is a process discipline risk, not an engineering risk. The runbook makes it explicit and verifiable; the PM should treat it as non-negotiable.

## Effort

3/5. Larger than deferred slice 126's 2/5 (which was deployment-only) because of the platform-level additions (coverage check, Stage B verifier, daemon-owned CA ingest, backfill orchestrator + quota guard, status extension, four migrations, validation-flags column). Bounded by: orchestrator pattern stable, provider seams already proven by slice 127, systemd templates already drafted by slice 126, market calendar integration likely already present, no novel algorithms.
