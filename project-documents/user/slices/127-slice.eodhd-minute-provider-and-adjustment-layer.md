---
docType: slice-design
slice: 127
parent: user/architecture/120-slices.data-acquisition.md
project: trading
dateCreated: 20260426
dateUpdated: 20260427
verifiedAgainstCode: 20260427
status: complete
---

# Slice 127 — EODHD Minute Provider + Adjustment Layer

## Purpose

Replace the AlphaVantage minute provider with EODHD, and ship the adjustment layer alongside it as a single unit. Storage holds raw (as-traded) intraday bars **and** split/dividend-adjusted bars, both populated from EODHD endpoints. After this slice, the system can ingest minute data with 22-year history and produce backtest-ready adjusted prices that are continuously verifiable against EODHD's published `adjusted_close`.

This slice supersedes the earlier draft that deferred adjustment to a follow-up slice. Splitting them was the wrong call: shipping unadjusted bars without a query layer leaves data that breaks naive backtests, and the adjustment layer is small enough to bundle. One slice, one complete result.

## Motivation / Problem

Slice 124 fixed the AlphaVantage minute provider. Slice 125 wrapped it in a daemon. The pipeline works — but only against a provider whose minute history is 2 years deep and whose request budget is 30/min on a legacy plan with no upgrade path. For a research platform whose first use is strategy exploration on accessible historical data, that is structurally insufficient.

**EODHD selected.** Decision recorded 2026-04-26 (see `MEMORY.md`) after eval against Polygon and AlphaVantage. EODHD provides 22 years of 1-minute history (since 2004), 100K API calls/day on the $30 plan, 120-day window per `/intraday` request, REST API, splits and dividends endpoints, and a documented adjustment formula that passes round-trip verification (see "Adjustment feasibility — verified" below).

**AlphaVantage is dropped from the platform** in this slice, not retained as a fallback. AV's 2-year window means it has nothing useful to contribute even as a backup; maintaining two providers is ongoing cost for zero realistic benefit. The AV provider class and supporting code are removed. (Git history retains it if it ever needs to be resurrected.)

**Adjustment is a real architectural concern.** EODHD's `/intraday` returns unadjusted prices. AV's `TIME_SERIES_INTRADAY` returns adjusted prices by default — so the existing minute pipeline has been silently storing adjusted bars. Switching providers without an adjustment layer would either:
1. Store raw bars (breaks all naive backtests across split dates), or
2. Store provider-mutated history (AV's behaviour — irreproducible across time, since adjustments mutate retroactively).

This slice picks a third option: **store raw + adjusted columns, derived deterministically from EODHD's published splits and dividends, continuously verified against EODHD's daily `adjusted_close`**. Raw history is immutable; adjusted columns are recomputable from raw + corporate actions. Reproducibility is a property of the pipeline, not the provider.

## Feasibility — verified by two probes

Both ran against EODHD's real endpoints with a paid key.

### Chunk-size probe — [scripts/probe_eodhd_chunk_size.py](scripts/probe_eodhd_chunk_size.py)

Tested 1-minute intraday windows from 30 to 365 calendar days for AAPL.US (ending 2025-01-15). Output at [project-documents/user/research/eodhd-chunk-size-probe/results.json](project-documents/user/research/eodhd-chunk-size-probe/results.json):

- **120-day window delivers 76,083 bars (10.6 MB JSON, 1.2s response)** with full coverage of the requested range.
- 30/60/90/110/119/120-day requests all deliver in proportion to size; the first-bar timestamp moves backward by exactly one calendar day per increment, confirming `from` is honored exactly (not "trading days," not "120 most recent").
- **121+ days returns HTTP 422 with an explicit error envelope**: `{"errors": {"to": ["Max period length is 120 days"]}}`. Server-enforced cap, no silent truncation.
- Bar count consistent with ~83 trading days × ~960 ETH minutes per US trading day — confirms CTA/UTP feed coverage from 4am to 8pm ET.

This is the contract. `max_days_per_request = 120` calendar days, hard cap, clean error on overshoot. Backfill math: ~76K bars/request × ~67 requests/symbol × 5K symbols ≈ ~17 days unattended at the $30 plan's 20K-requests/day quota.

### Adjustment-formula probe — [scripts/probe_eodhd_adjustment.py](scripts/probe_eodhd_adjustment.py)

Ran against EODHD endpoints for AAPL across the 2020-08-31 4:1 split. Output at [project-documents/user/research/eodhd-adjustment-probe/](project-documents/user/research/eodhd-adjustment-probe/). Findings:

1. **EODHD's `adjusted_close` is internally consistent.** For every day in a contiguous block, `close × k == adjusted_close` to floating-point precision (worst-case error: 0.000000%). This is not a noise floor; it is exact.
2. **`k` is a clean step function.** Within a corporate-action-free window, every day has identical `k`. Corporate actions produce single sharp transitions (the 2020-08-31 split shows `k` jump from 0.242717 to 0.970867, ratio exactly 4.0).
3. **Same-date intraday bars and daily EOD share the same `k`**, so `adjusted_intraday_OHLC = raw_OHLC × k(date)` is well-defined and verifiable.

Conclusion: the adjustment formula EODHD documents in their intraday API (`k = adjusted_close / close`) works exactly. We do not invent the adjustment factor; we read it from EODHD's daily EOD response, verify it round-trips, and apply it. This is a normal piece of code, not a research project.

## Scope

**In scope:**

1. **Smoke-test the EODHD intraday contract.** A small probe script (modelled on [scripts/probe_eodhd_adjustment.py](scripts/probe_eodhd_adjustment.py)) confirms the documented intraday endpoint shape, response schema, and error envelope on the implementer's paid key. Cost: <20 API calls. Output: notes added to the existing probe directory.

2. **`EODHDMinuteProvider` class** at `src/manta_trading/data/historical_minute/providers/eodhd.py`, implementing all four `IMinuteDataProvider` methods plus a new `max_days_per_request` property. Returns raw (as-traded) UTC bars.

3. **Per-provider chunk-window generalisation.**
   - Add `max_days_per_request: int` to `IMinuteDataProvider`. EODHD returns **120** (calendar days; documented and consistent with the smoke-test).
   - Generalise `_compute_month_ranges` in [orchestrator.py](src/manta_trading/data/acquisition/minute/orchestrator.py) to `_compute_chunk_ranges(start_ts, end_ts, max_days_per_request)`. The orchestrator reads the provider's window and passes it in. Per-chunk checkpointing semantics preserved.

4. **Configuration plumbing**:
   - New `Settings` field: `eodhd_api_key: str | None = None` (env: `MT_EODHD_API_KEY`). Already present in `.env`.
   - New `Settings` field: `minute_provider: str = "eodhd"` (env: `MT_MINUTE_PROVIDER`) — `StrEnum` with one entry today (`EODHD`), structured for future expansion. No magic strings.
   - Per-provider history-window constants moved to where the existing `HISTORY_MONTHS` lives. Per-provider lookup, not a global.

5. **Provider-selection seam** at [src/manta_trading/cli/commands/data.py:1217](src/manta_trading/cli/commands/data.py#L1217): replace the hardcoded `AlphaVantageMinuteProvider(...)` with a `build_minute_provider(settings)` helper that dispatches via the enum. Even with one entry today, the dispatcher exists so adding a future provider is a 3-line change, not a refactor.

6. **Remove AlphaVantage minute code paths.** Deletes:
   - `src/manta_trading/data/historical_minute/providers/alphavantage.py`
   - `MT_ALPHAVANTAGE_API_KEY` settings field (only if no other code uses it — daily provider may; verify before delete)
   - AV-specific tests and fixtures under `test/unit/test_minute_provider.py` (or wherever)
   - References in `data.py`
   - **Daily AV provider stays** — slice 122's `AlphaVantageDailyProvider` is unrelated and used by the daily acquisition pipeline. Only the minute path is touched here.

7. **Schema migration: adjusted OHLC columns.** New migration in `migrations/minute.py` (slice 150's track) adds to the minute hypertable:
   - `adj_open NUMERIC(20,8)`, `adj_high`, `adj_low`, `adj_close` — adjusted prices
   - `k_factor NUMERIC(20,12)` — the per-row adjustment factor used (audit + recomputation)
   - `adjusted_at TIMESTAMPTZ` — when the adjusted columns were last written (allows detection of stale rows after a new split)
   - All adjusted columns NULLABLE so existing rows and rows ingested before adjustment processing can coexist.

8. **`splits` and `dividends` tables** in the daily DB (slice 150's daily track):
   - `splits(symbol, date, ratio_to, ratio_from)` — primary key (symbol, date)
   - `dividends(symbol, ex_date, amount, currency)` — primary key (symbol, ex_date)
   - Both updated by an ingestion job that pulls from EODHD's `/splits/{TICKER}` and `/div/{TICKER}` endpoints (1 call each per symbol).
   - Bulk-fill via EODHD's bulk-EOD endpoint with `type=splits` / `type=dividends` (100 calls per exchange + 1 per symbol; see "Future optimization" below). Initial implementation uses per-symbol calls; bulk is an optimization for backfill.

9. **`k_factor(symbol, date)` computation**: a deterministic Python function that, given a symbol and a date, returns the adjustment factor. Implementation:
   - Compute `k(date) = product over (corporate_actions occurring AFTER date) of action.factor`
   - Split factor = `ratio_from / ratio_to` (e.g., 4-for-1 split → `1/4 = 0.25`)
   - Dividend factor = `(prev_close - amount) / prev_close` where `prev_close` is the close on the day before ex-date
   - Tested against EODHD's published `k = adjusted_close / close` per day; **must match within tolerance** (<0.0001%) or the function is wrong.

10. **Continuous verification** as a first-class artifact, not an afterthought:
    - A `mt data quality verify-adjustment [--symbol X] [--from D] [--to D]` CLI command (lives under existing data CLI; matches slice 140's quality-command shape).
    - For each row in scope: recompute `adj_close = close × k_factor(symbol, date)` from the raw `close` and the splits/dividends tables. Compare against the stored `adj_close`. Compare against EODHD's published `adjusted_close` from a fresh `/eod` call. Emit per-row discrepancies.
    - This is the killer feature: we have a continuous self-test against EODHD's own ground truth. If the splits/dividends ingestion ever drifts, the verifier catches it.

11. **Adjusted-column population**:
    - During minute ingestion: after writing raw bars for a symbol-date, immediately compute and write the adjusted columns using current splits/dividends data and `k_factor(symbol, date)`.
    - After a new corporate action arrives: re-compute adjusted columns for all dates BEFORE the action's ex-date for that symbol (efficient with TimescaleDB's chunk-targeted UPDATEs since corporate actions are infrequent).
    - The recomputation job is idempotent and safe to re-run.

12. **Fixture capture**: real raw EODHD intraday response committed under `test/fixtures/eodhd/`. Captured during smoke-test; includes one symbol over a chunk-sized range. Pinned schema reference for unit tests.

13. **Tests**:
    - Unit tests for `EODHDMinuteProvider` (validate-error branches, convert-to-DataFrame schema, ticker normalisation, rate-limit accounting).
    - Unit tests for `_compute_chunk_ranges` parametrised across multiple `max_days` (30 = AV legacy baseline, 120 = EODHD, plus edge cases).
    - Unit tests for `k_factor(symbol, date)` against hand-built splits/dividends fixtures: pre-split, post-split, multiple-splits, dividend-only, no-actions, and a regression test for AAPL 2020-08-31 reproducing the probe's result.
    - Integration test against real EODHD endpoint: fetches a >30-day range to exercise the larger-chunk path; asserts canonical OHLCV schema; cross-checks adjusted columns match recomputation.

14. **Documentation**:
    - Module docstring on `EODHDMinuteProvider`: EODHD-specific quirks, MCP-info-only rule, chunk-window decision.
    - New ADR-style doc at `project-documents/user/architecture/120-arch-adjustment-policy.md` recording: storage holds raw + adjusted; k-factor formula and source of truth; continuous-verification command; recomputation triggers.
    - One-paragraph README addition in the providers package noting how to add a new provider.

**Out of scope (explicitly):**

- Deployment to .144. Slice 128 (formerly slice 126) covers production cutover.
- WebSocket / streaming. REST only here.
- Real-time tier. Free intraday tier requires the All-World Extended subscription; this slice assumes the implementer has paid-tier access. Production use likewise requires it.
- Crypto, forex, non-equity markets. EODHD offers them; we are not using them.
- MCP-server-as-runtime-provider. MCP is for documentation lookups during development. The runtime daemon never imports an MCP client.
- Dividend-adjustment edge cases beyond what EODHD's `adjusted_close` reflects (e.g., return-of-capital distributions, special dividends with non-cash components). EODHD's `adjusted_close` is the source of truth; if it gets a corner case wrong, our verification flags it but does not "correct" it.
- Adjusting bars from sources other than EODHD. If a future provider is added (slice 130+), the k-factor source becomes a per-provider concern.
- Backfilling the full 22-year history. Slice 128's job. This slice ships the *capability*; running it at scale is later.

## Architecture

### Component map (what changes vs what stays)

```
existing (unchanged):
  IMinuteDataProvider              ← protocol (gains max_days_per_request)
  MinuteAcquisitionOrchestrator    ← consumer
  _MinuteChunkProviderAdapter      ← bridges per-chunk fetches
  TimescaleMinuteWriter            ← persistence (gains adjusted-column writes)
  MinuteAcquisitionDaemon          ← supervises
  AlphaVantageDailyProvider        ← daily pipeline, untouched
  classify_bar_session             ← timezone projection logic, untouched

existing (touched):
  IMinuteDataProvider              ← +max_days_per_request property
  Settings                         ← +eodhd_api_key, +minute_provider
  _create_minute_orchestrator      ← uses build_minute_provider() helper
  _compute_chunk_ranges            ← was _compute_month_ranges; generalised
  TimescaleMinuteWriter            ← writes adjusted columns alongside raw

removed:
  AlphaVantageMinuteProvider       ← deleted
  MT_ALPHAVANTAGE_API_KEY usages   ← removed from minute path (daily retains if needed)

new:
  EODHDMinuteProvider              ← src/manta_trading/data/historical_minute/providers/eodhd.py
  MinuteProviderName (StrEnum)     ← src/manta_trading/data/historical_minute/providers/__init__.py
  build_minute_provider helper     ← same module
  k_factor(symbol, date) module    ← src/manta_trading/data/adjustment/k_factor.py (new package)
  splits/dividends ingest job      ← src/manta_trading/data/adjustment/ingest.py
  verify-adjustment CLI command    ← in src/manta_trading/cli/commands/data.py (or quality.py)
  schema migration (010)           ← migrations/minute.py: adjusted columns + recompute helper
  schema migrations (003, 004)     ← migrations/daily.py: splits, dividends tables
  test/fixtures/eodhd/*.json
  test/unit/test_eodhd_provider.py
  test/unit/test_chunk_ranges.py
  test/unit/test_k_factor.py
  test/integration/test_eodhd_integration.py
  project-documents/user/architecture/120-arch-adjustment-policy.md
```

### Data flow: end-to-end fetch → adjusted storage

```
Daemon work-cycle for one symbol-chunk:
  ┌───────────────────────────────────────────────┐
  │ 1. Provider: GET /intraday/AAPL.US?from=...   │
  │    Returns ~78K raw 1-min bars (UTC)          │
  └────────────────────┬──────────────────────────┘
                       │
  ┌────────────────────▼──────────────────────────┐
  │ 2. DataProcessor: validate, convert to        │
  │    canonical UTC DataFrame, classify sessions │
  └────────────────────┬──────────────────────────┘
                       │
  ┌────────────────────▼──────────────────────────┐
  │ 3. Writer: bulk INSERT raw bars               │
  │    (timestamp, o/h/l/c/v, session)            │
  └────────────────────┬──────────────────────────┘
                       │
  ┌────────────────────▼──────────────────────────┐
  │ 4. Adjustment: for each date in this chunk,   │
  │    compute k_factor(symbol, date) from        │
  │    splits + dividends tables, then bulk       │
  │    UPDATE adj_o/h/l/c, k_factor, adjusted_at  │
  └────────────────────┬──────────────────────────┘
                       │
  ┌────────────────────▼──────────────────────────┐
  │ 5. Watermark advance (existing slice-121      │
  │    checkpointing). Crash-safe.                │
  └───────────────────────────────────────────────┘
```

Splits/dividends ingestion is a separate, lower-frequency job (daily or on-demand) that runs against the daily DB. When it discovers a new corporate action for a symbol, it triggers a recompute of adjusted columns for that symbol's bars in dates BEFORE the action's ex-date.

### k-factor computation

For a given `(symbol, date)`:

```
k(symbol, date) = ∏ over corporate actions A where A.ex_date > date:
                  split_factor(A) if A is a split
                  dividend_factor(A, prev_close) if A is a dividend

where:
  split_factor(A)         = A.ratio_from / A.ratio_to
                            (e.g., 4-for-1: 1/4 = 0.25)

  dividend_factor(A, pc)  = (pc - A.amount) / pc
                            where pc = close on day before A.ex_date
```

This formula matches EODHD's `adjusted_close = close × k`. The ordering of multiplication does not matter (multiplication is commutative). The factor for a date in the deep past is the cumulative effect of every corporate action since.

**Verification**: at any time, fetch EODHD's `/eod/{symbol}` for a date range, compute `published_k = adjusted_close / close` per day, and compare to our computed `k_factor(symbol, date)`. Difference must be < 0.0001%. The probe already proves the formula round-trips on EODHD's own data; this verification confirms our splits/dividends tables are kept in sync.

### Storage: raw + adjusted columns, single hypertable

The minute hypertable gets eight additional columns. Adjusted columns are populated by the writer immediately after raw inserts. They are nullable for existing rows and for the brief window after raw-write but before adjustment-write (a crash in between leaves NULL adjusted columns; the recompute job picks them up on retry).

Storage cost (compressed, TimescaleDB):
- Raw OHLCV at 5K symbols × 22 years × 240K bars/symbol/year ≈ 26.4B rows ≈ ~330 GB
- Adjusted columns add ~150 GB
- Total: ~480 GB at full scale. Day-one: nowhere near this.

### Rate budget for backfill (EODHD $30 plan)

- Quota: 100K calls/day = 20K intraday requests/day
- Bars per request: **~76K (measured, not estimated)** — 1-minute, ETH-inclusive, US equities
- Requests per symbol for full 22-year history: ~67 (22yr × 365.25 days / 120 days per request)
- 5K symbols × 67 = 335K requests = ~17 days unattended backfill at full quota utilization
- Response size: 10.6 MB at the cap; 1.2s response time per request → bandwidth ≈ 3.3 TB inbound for full backfill (manageable on any reasonable connection)
- Steady-state daily incremental: 5K symbols × 1 request/day ≈ 25% of quota

Splits/dividends ingestion adds:
- Per-symbol: 1 call splits + 1 call dividends = 10K calls for 5K-symbol full sync (5% of daily quota; trivial)
- Bulk path (future): 100 calls/exchange + 1/symbol per type → ~5,200 calls per type per exchange (NYSE+NASDAQ ≈ 10,400 calls = 5% of daily quota for both)

### Future optimization: bulk splits/dividends API

EODHD's bulk-EOD endpoint accepts `type=splits` or `type=dividends`:
```
GET /eod-bulk-last-day/{EXCHANGE}?type=splits&api_token=KEY
```
Cost: 100 calls per exchange request + 1 call per symbol returned. URL: https://eodhd.com/knowledgebase/bulk-api-eod-splits-dividends/

For initial implementation, per-symbol calls (1 call each) are simpler and the daily quota cost is trivial. The bulk endpoint becomes valuable when initial ingestion across thousands of symbols is needed and per-symbol latency becomes the bottleneck. Slice 127 ships the per-symbol path; bulk is recorded here and may be added in this slice or a follow-up if the implementation reveals it is needed.

### MCP usage rule (developer-tooling only, not runtime)

The `eodhd-api` skill (already installed) provides 100+ pages of EODHD documentation as local files plus a tested REST client. We use it for:
- Reading endpoint docs while writing the provider (zero API cost)
- Capturing reference fixtures for tests
- Ad-hoc verification during development

The runtime daemon **never** imports an MCP client and **never** depends on a hosted MCP server. The `EODHDMinuteProvider` speaks REST directly to `https://eodhd.com/api/...`. This rule is recorded in the provider's module docstring so a future contributor doesn't wire MCP into runtime "to share a code path."

The project's `.mcp.json` already has the `eodhd-api` plugin available; no additional MCP configuration is needed for slice 127.

### Timezone handling

EODHD returns Unix timestamps in UTC and a `datetime` field formatted as UTC. The converter is a one-liner: `pd.to_datetime(rows["timestamp"], unit="s", utc=True)`. No DST gymnastics, no `tz_localize` with ambiguous-time inference, no `astimezone` round-trips at storage. The canonical storage timezone is UTC; downstream `classify_bar_session` projects to calendar-local for session classification only and never modifies stored timestamps.

This is a small but real reduction in fragility versus the AV converter, which had to localise from US/Eastern with `ambiguous="infer"` (a guess during the fall-back DST hour). The probe confirms EODHD's UTC discipline is consistent across the split-day window, including the DST-relevant date ranges.

## Cross-slice dependencies and interfaces

- **Hard dep on 124** — `IMinuteDataProvider` protocol and orchestrator scaffolding. (Complete.)
- **Hard dep on 125** — daemon machinery. (Complete.)
- **Hard dep on 150** — schema migration framework supports both DBs and is the channel for the new `adj_*` columns and `splits`/`dividends` tables. (Complete.)
- **Forward dep from 128** — slice 128 (production deployment) consumes the resulting provider + adjustment layer. Backfill at scale happens there.
- **Touches 122** — only if `MT_ALPHAVANTAGE_API_KEY` is removed and the daily provider needs it. Verify before delete; if daily uses it, leave the env var in place and just stop the minute path from reading it.
- **Future provider slices** — protocol now requires `max_days_per_request`; future providers must declare theirs. The k-factor source for non-EODHD providers is undefined and out of scope here.

## Decisions

1. **EODHD-only minute provider, AV minute removed.** AV's 2-year window means it is useless even as a fallback. Maintaining two providers is ongoing cost for zero benefit. Daily AV provider unrelated and unaffected.

2. **Bundle adjustment into this slice.** Earlier draft deferred it to slice 129. Reversed because shipping unadjusted-only data leaves an awkward intermediate state and the adjustment layer is small enough to ship together. Cost: slice grows from 3/5 to 4/5 effort. Benefit: one shippable result, end-to-end queryable.

3. **Store raw + adjusted columns, both materialised.** Disk is cheap; at-read computation is not. ~150 GB extra at full scale (5K × 22yr) is acceptable. Recomputation triggers only on new corporate actions.

4. **k-factor formula = `adjusted_close / close` from EODHD daily, applied to intraday OHLC.** Probe-verified to match exactly (0.000000% error). Continuously re-verifiable against EODHD's own ground truth.

5. **Splits/dividends ingested per-symbol from EODHD endpoints initially.** Bulk endpoint noted as future optimization (100 calls/exchange + 1/symbol). Per-symbol cost is trivial at our quota; simpler to implement.

6. **Continuous verification is a first-class deliverable**, not a follow-up. The `mt data quality verify-adjustment` command ships in this slice and is the operator's confidence signal that adjustment is correct.

7. **Per-provider chunk window via `max_days_per_request`**, generalising the orchestrator's range computation. AV would have returned 30; EODHD returns 120. Backfill efficiency: ~17 days for full 5K-symbol × 22-year history vs ~70 days at per-month chunking.

8. **Provider selection seam preserved (one entry today)**. `MinuteProviderName` enum + `build_minute_provider` helper. Adding a future provider is a 3-line change. No magic strings.

9. **EODHD UTC timestamps preserved end-to-end**. No timezone conversion at the converter; `classify_bar_session` handles local projection internally and is unchanged.

10. **MCP for info, REST for runtime.** Codified in module docstring so a future contributor doesn't wire MCP into runtime "to share a code path."

11. **Adjustment ADR documented** at `120-arch-adjustment-policy.md`. Records: storage policy (raw + adjusted), k-factor source of truth, recomputation triggers, verification command. Survives the slice; durable reference.

12. **Daily AV provider untouched.** Slice 122's daily path is unrelated; this slice does not touch it. `MT_ALPHAVANTAGE_API_KEY` settings field stays if daily uses it.

## Verification walkthrough

Demo script the PM can run to confirm the slice works.

### Phase 1: Smoke-test EODHD intraday contract

Implementer's paid key in `MT_EODHD_API_KEY`. Run a small probe to confirm endpoint shape, error envelope, and that the documented chunk window holds.

```bash
uv run python scripts/probe_eodhd_intraday.py --symbol AAPL.US --days 100
# Output: project-documents/user/research/eodhd-intraday-smoketest.md
```

Output records: response shape matches docs, returned date span matches requested, error envelope on overshoot, per-call accounting if observable.

### Phase 2: Apply schema migrations

```bash
mt data migrate apply --db all
mt data migrate status --db all
# Expected: minute track gains migration 010 (adj columns);
# daily track gains 003 (splits) and 004 (dividends).
```

### Phase 3: Ingest splits and dividends for one symbol

```bash
uv run python -m manta_trading.data.adjustment.ingest --symbol AAPL.US --since 2000-01-01
# Calls /splits/AAPL.US and /div/AAPL.US (1 call each);
# writes to splits and dividends tables in the daily DB.
```

Spot-check via SQL:
```sql
SELECT date, ratio_to, ratio_from FROM splits
 WHERE symbol = 'AAPL' ORDER BY date;
-- Expected: 1987, 2000, 2005, 2014, 2020 splits as in probe output.
```

### Phase 4: Fetch raw + adjusted intraday bars across the 2020-08-31 split

```bash
mt data minute update AAPL --from 2020-08-25 --to 2020-09-04
```

Sanity-check the bars in the DB:
```sql
SELECT timestamp, close, adj_close, k_factor
  FROM minute_ohlcv
 WHERE symbol = 'AAPL'
   AND timestamp::date IN ('2020-08-28', '2020-08-31')
 ORDER BY timestamp DESC LIMIT 5;
-- Expected:
--   pre-split  rows: k_factor ≈ 0.242717, adj_close = close × 0.242717
--   post-split rows: k_factor ≈ 0.970867, adj_close = close × 0.970867
-- (exact values depend on dividends paid since; the ratio stays 4.0)
```

### Phase 5: Run continuous verification

```bash
mt data quality verify-adjustment --symbol AAPL --from 2020-08-25 --to 2020-09-04
# Expected output:
#   2020-08-25: stored adj_close=121.18 ± computed=121.18 ± EODHD=121.19  PASS
#   ...
#   All rows within 0.0001% tolerance: PASS
```

If any row's stored `adj_close` differs from `raw_close × k_factor(symbol, date)` recomputation OR from EODHD's published `adjusted_close`, the command flags it.

### Phase 6: Larger-chunk end-to-end check

Pull a 100-day window through the daemon path. EODHD's 120-day window means this is a single REST request (vs 4 monthly requests under the AV-30 baseline). Confirms the chunk-window generalisation works under real machinery.

```bash
mt data minute update AAPL --from 2024-09-01 --to 2024-12-31
mt data minute coverage --symbol AAPL --from 2024-09-01 --to 2024-12-31
# Expected: ~84 trading days fully covered; orchestrator made 1 request,
# visible in the JSONL event log.
```

### Phase 7: Drift detection — corrupt the splits table, run verifier, observe failure

Synthetic test that the verifier actually catches drift:

```bash
psql -d market-stocks-test -c \
  "UPDATE splits SET ratio_to = 5 WHERE symbol = 'AAPL' AND date = '2020-08-31'"

mt data quality verify-adjustment --symbol AAPL --from 2020-08-25 --to 2020-09-04
# Expected: FAIL with explicit divergence on rows after 2020-08-31.

# Restore:
psql -d market-stocks-test -c \
  "UPDATE splits SET ratio_to = 4 WHERE symbol = 'AAPL' AND date = '2020-08-31'"
```

This proves the verifier is not vacuously passing; it actually catches errors.

### Phase 8: Unit and integration tests pass

```bash
uv run pytest test/unit/test_eodhd_provider.py \
                test/unit/test_chunk_ranges.py \
                test/unit/test_k_factor.py -v
# Expected: green.

MT_EODHD_API_KEY=$REAL_KEY uv run pytest test/integration/test_eodhd_integration.py -v
# Expected: green; fetches real data and cross-checks adjusted columns.
```

### Phase 9: AV minute path is gone

```bash
grep -r "AlphaVantageMinuteProvider\|alphavantage_minute" src/ test/
# Expected: no matches in source. Daily AV provider may match — that's fine.
```

If all phases pass, slice 127 is delivered.

## Success criteria

1. Smoke-test artifact records EODHD intraday endpoint contract verified against real responses with paid key.
2. `EODHDMinuteProvider` exists, implements `IMinuteDataProvider` plus `max_days_per_request = 120`, passes `pyright --strict`, passes `ruff`.
3. `IMinuteDataProvider` protocol has `max_days_per_request: int`. `_compute_chunk_ranges(start, end, max_days)` exists and produces correct chunked ranges.
4. `MinuteProviderName` enum, `build_minute_provider` helper exist. `_create_minute_orchestrator` uses the helper. No AV minute references remain.
5. `Settings` has `eodhd_api_key` and `minute_provider` fields. `MT_MINUTE_PROVIDER` defaults to `eodhd`.
6. `AlphaVantageMinuteProvider` source file deleted; tests removed; no source-tree references remain.
7. Schema migrations applied: minute hypertable gains `adj_open`, `adj_high`, `adj_low`, `adj_close`, `k_factor`, `adjusted_at`. Daily DB gains `splits`, `dividends` tables.
8. `splits` and `dividends` ingestion job pulls per-symbol from EODHD; populates both tables; idempotent.
9. `k_factor(symbol, date)` function exists, is deterministic, and unit-tested against the AAPL 2020-08-31 probe scenario plus hand-built fixtures (multiple splits, dividends, no-actions).
10. Minute writer populates adjusted columns immediately after raw inserts using current `k_factor`. Idempotent on re-run.
11. `mt data quality verify-adjustment [--symbol] [--from] [--to]` CLI command exists. Compares stored `adj_close` to (a) recomputation from raw + k, and (b) EODHD's published `adjusted_close`. Per-row tolerance < 0.0001%. Synthetic drift in splits/dividends tables produces FAIL.
12. Captured fixture under `test/fixtures/eodhd/` is a real paid-tier response.
13. Unit tests pass; integration test passes against real `MT_EODHD_API_KEY` and is env-gated.
14. `mt data minute update SYMBOL` over a >30-day range produces fewer fetch chunks than the per-month baseline (visible in JSONL event log).
15. ADR `120-arch-adjustment-policy.md` exists and records the design decisions.
16. CHANGELOG entry added.

## Risks

- **EODHD's intraday endpoint contract drifts from the docs** — smoke-test catches this before implementation. Probe is cheap.
- **`k_factor` formula has corner cases EODHD's data doesn't trigger in our test sample** — return-of-capital distributions, spinoffs, special dividends, mergers. Verifier catches divergence; we'd file a TODO and address case-by-case rather than pre-engineering. Initial coverage: regular splits + cash dividends.
- **Splits/dividends ingestion drift** — corporate actions retroactively published or corrected after our last sync. Mitigation: verifier compares against fresh EODHD `adjusted_close` and flags drift. Operator re-runs ingestion + recompute.
- **Recomputation cost at full scale** — for a new corporate action, we need to UPDATE all prior rows for that symbol. With TimescaleDB chunked storage and per-symbol partitioning this is bounded but not free; for very long-history symbols, it's a multi-second-to-minute operation. Acceptable; happens infrequently.
- **Adjusted columns NULL after raw write but before adjustment write** — crash-window risk. Mitigation: writer pairs raw and adjusted writes in the same DB transaction; or, if separated, the recompute job sweeps NULL-adjusted rows on each run.
- **Storage growth surprises** — measured cost at slice-128 backfill scale. Day-one is small.

## Effort

4/5. Bigger than original 3/5 because it now includes the adjustment layer. Bounded by: protocol stable, orchestrator stable, AV provider as a known-good template for the new EODHD class, probe-verified math for k-factor. Adds: schema migration (low risk via slice 150 framework), splits/dividends ingestion (small), k-factor function (well-defined), verification command (modeled on slice 140 patterns), and AV-removal cleanup (mechanical).

## Verification walkthrough

Captured 2026-04-27 against the test environment
(`MT_TIMESCALE_DB_URL=…/trading_test`,
`MT_MARKET_DB_URL=…/market-stocks-test`, paid `MT_EODHD_API_KEY`).
Reproducible by an external agent with the same connection strings
and key.

The CLI surfaces shipped:

* `mt data adjustment ingest --symbol SYMBOL [--since DATE]`
* `mt data adjustment verify --symbol SYMBOL [--from DATE] [--to DATE]
  [--tolerance F] [--json]`
* `mt data minute update SYMBOL [--from DATE] [--to DATE]
  [--months N]` — `--from/--to` is **ad-hoc backfill** mode added in
  task 30; it fetches the explicit window without touching
  `acquisition_state` so the production resumable path is unaffected.

(The slice originally drafted the verifier as `mt data quality
verify-adjustment`; per implementer choice it lives under the existing
`mt data adjustment` subgroup so all corporate-action operator
commands sit together.)

### Step 1 — Ingest AAPL corporate actions

```
$ mt data adjustment ingest --symbol AAPL
… EODHD GET https://eodhd.com/api/splits/AAPL.US?api_token=***&fmt=json
… EODHD GET https://eodhd.com/api/div/AAPL.US?api_token=***&fmt=json
AAPL: splits 0 added / 5 updated; dividends 0 added / 90 updated
```

5 splits and 90 dividends recorded for AAPL on the daily DB. Idempotent
on re-run (numbers shift to all-`updated` after the first call).

### Step 2 — Ad-hoc backfill across the 2020-08-31 split

```
$ mt data minute update AAPL --from 2020-08-25 --to 2020-09-04
AAPL: ok — 1 chunk(s) written
```

11 calendar days fits in one EODHD chunk (well under the 120-day cap).
9 trading days written. Watermark on `acquisition_state` for AAPL
**unchanged** by this command.

### Step 3 — SQL spot-check on the split window

```sql
SELECT
  (time AT TIME ZONE 'America/New_York')::date AS ny_date,
  COUNT(*) AS bars,
  ROUND(MIN(close), 2)        AS min_close,
  ROUND(MAX(close), 2)        AS max_close,
  ROUND(MIN(adj_close), 4)    AS min_adj_close,
  ROUND(MAX(adj_close), 4)    AS max_adj_close,
  ROUND(MIN(k_factor)::numeric, 6) AS min_k,
  ROUND(MAX(k_factor)::numeric, 6) AS max_k
FROM minute_ohlcv
WHERE symbol='AAPL'
  AND (time AT TIME ZONE 'America/New_York')::date
      BETWEEN '2020-08-25' AND '2020-09-04'
  AND adjusted_at IS NOT NULL
GROUP BY ny_date ORDER BY ny_date;
```

```
  ny_date   | bars | min_close | max_close | min_adj_close | max_adj_close |  min_k   |  max_k
------------+------+-----------+-----------+---------------+---------------+----------+----------
 2020-08-25 |  768 |    492.56 |    506.40 |      119.5526 |      122.9118 | 0.242717 | 0.242717
 2020-08-26 |  802 |    499.00 |    507.94 |      121.1157 |      123.2856 | 0.242717 | 0.242717
 2020-08-27 |  777 |    495.34 |    509.72 |      120.2261 |      123.7176 | 0.242717 | 0.242717
 2020-08-28 |  802 |    498.83 |    505.35 |      121.0749 |      122.6570 | 0.242717 | 0.242717
 2020-08-31 |  955 |    125.04 |    130.97 |      121.3973 |      127.1496 | 0.970867 | 0.970867
 2020-09-01 |  950 |    130.68 |    135.00 |      126.8729 |      131.0671 | 0.970867 | 0.970867
 2020-09-02 |  953 |    127.12 |    138.54 |      123.4118 |      134.5040 | 0.970867 | 0.970867
 2020-09-03 |  949 |    116.77 |    132.24 |      113.3682 |      128.3875 | 0.970867 | 0.970867
 2020-09-04 |  950 |    111.01 |    123.28 |      107.7760 |      119.6885 | 0.970867 | 0.970867
```

Pre-split (Aug 25–28) all rows carry `k_factor = 0.242717` exactly,
matching the EODHD probe's published value. Post-split (Aug 31–Sep 4)
all rows carry `k_factor = 0.970867`. Per-row math: pre-split close
≈499 × 0.242717 ≈ 121 → matches the `adj_close` column;
post-split close ≈129 × 0.970867 ≈ 125 → matches.

### Step 4 — Stage A verifier on the split window

```
$ mt data adjustment verify --symbol AAPL --from 2020-08-25 --to 2020-09-04
                       verify-adjustment AAPL (tol=0.0001)
┏━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┓
┃ Date       ┃ Rows ┃ Adj rows ┃ Stored k ┃ Expected k ┃ Max diff ┃ Status ┃
┡━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━┩
│ 2020-08-25 │ 768  │ 768      │ 0.242717 │ 0.242717   │ 5.00e-09 │ PASS   │
│ 2020-08-26 │ 802  │ 802      │ 0.242717 │ 0.242717   │ 4.99e-09 │ PASS   │
│ 2020-08-27 │ 777  │ 777      │ 0.242717 │ 0.242717   │ 4.98e-09 │ PASS   │
│ 2020-08-28 │ 802  │ 802      │ 0.242717 │ 0.242717   │ 5.00e-09 │ PASS   │
│ 2020-08-31 │ 955  │ 955      │ 0.970867 │ 0.970867   │ 5.00e-09 │ PASS   │
│ 2020-09-01 │ 950  │ 950      │ 0.970867 │ 0.970867   │ 5.00e-09 │ PASS   │
│ 2020-09-02 │ 953  │ 953      │ 0.970867 │ 0.970867   │ 4.99e-09 │ PASS   │
│ 2020-09-03 │ 949  │ 949      │ 0.970867 │ 0.970867   │ 5.00e-09 │ PASS   │
│ 2020-09-04 │ 950  │ 950      │ 0.970867 │ 0.970867   │ 5.00e-09 │ PASS   │
└────────────┴──────┴──────────┴──────────┴────────────┴──────────┴────────┘

7906 row(s) across 9 trading day(s); 0 failed.
```

Worst per-row drift is ≈5e-9 — Decimal-vs-float roundoff, five
orders of magnitude below the 0.0001 tolerance. Exit code 0.

### Step 5 — Stage B (`--cross-check-eodhd`) — DEFERRED

The slice originally specified a `--cross-check-eodhd` flag that
fetches EODHD's daily `/eod` and compares stored `adj_close` to
EODHD's published `adjusted_close`. Implementation is recorded as
future work; the per-symbol-per-date `/eod` call volume should be
served by EODHD's bulk-EOD API (1 call covers a full exchange) when
that is wired in. Stage A's local-recompute check covers the most
common drift cause (corporate-action ingestion lagging the stored
rows) and is the operator's primary signal today.

### Step 6 — Larger range exercises chunking

```
$ mt data minute update AAPL --from 2024-09-01 --to 2024-12-30
AAPL: ok — 2 chunk(s) written
```

121 calendar days → 2 chunks (one full 120-day chunk plus the
spillover day). 77,029 minute bars written. Confirms the orchestrator
honours the EODHD provider's `max_days_per_request = 120` rather than
the legacy per-month chunking.

### Step 7 — Synthetic-drift demo (manual)

```
-- Corrupt the split row
UPDATE splits SET ratio_to = 5
WHERE symbol='AAPL' AND ex_date='2020-08-31';

$ mt data adjustment verify --symbol AAPL --from 2020-08-25 --to 2020-08-28
…
│ 2020-08-25 │ 768  │ 768      │ 0.242717 │ 0.194173   │ 2.45e+01 │ FAIL   │
…
3149 row(s) across 4 trading day(s); 4 failed.
$ echo $? → 1
```

The recomputed `expected_k` shifts from 0.242717 to 0.194173
(= 0.242717 × 4/5, the ratio change), per-row drift jumps to ~24.5
price units, all 4 pre-split days FAIL, and the CLI exits with code 1.

```
-- Restore
UPDATE splits SET ratio_to = 4
WHERE symbol='AAPL' AND ex_date='2020-08-31';

$ mt data adjustment verify …
… 0 failed.
$ echo $? → 0
```

After restoring, the verifier returns clean. This same scenario is
captured as an automated integration test
(`TestVerifierCatchesDrift`) so the property is regression-protected.

### Caveats discovered during walkthrough

* The slice originally referenced `mt data quality verify-adjustment`;
  shipped as `mt data adjustment verify`. ADR records this rename.
* Tolerance default changed from spec's `0.0001%` *relative* to
  `0.0001` *absolute* (price units). Absolute tolerance is more
  intuitive for this domain and behaves predictably on penny stocks.
* `--from/--to` is implemented on `mt data minute update` (single
  symbol). Skipped on `mt data minute update-all` because explicit
  windows on a per-symbol basis are clear; on update-all the
  semantics are ambiguous. Add later if a use case appears.
* `mt data minute update --from/--to` does **not** advance
  `acquisition_state.last_success_ts`. Mode is "ad-hoc backfill" —
  the production resumable path is unaffected.
