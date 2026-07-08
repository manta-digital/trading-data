---
docType: review
layer: project
reviewType: arch
slice: data-acquisition
project: squadron
verdict: FAIL
sourceDocument: project-documents/user/architecture/120-arch.data-acquisition.md
aiModel: claude-sonnet-4-6
status: complete
dateCreated: 20260404
dateUpdated: 20260404
findings:
  - id: F001
    severity: fail
    category: consistency
    summary: "AlphaVantageMinuteProvider does not actually paginate month history"
    location: `src/manta_trading/data/historical_minute/providers/alphavantage.py:296-310`
  - id: F002
    severity: fail
    category: completeness
    summary: "Atomic state + data write is impossible for daily acquisition"
    location: **Technical Considerations — "Graceful shutdown and resume"**
  - id: F003
    severity: concern
    category: technology
    summary: "RateLimiter holds asyncio.Lock during sleep — serialises concurrent symbol fetches"
    location: `src/manta_trading/util/ratelimiter.py:17-42`
  - id: F004
    severity: concern
    category: completeness
    summary: "No enforced shared rate limit budget between daily and minute daemons"
    location: **Technical Considerations — "Shared rate limit across daemons"**
  - id: F005
    severity: concern
    category: consistency
    summary: "acquire_symbol gather-all-then-write pattern breaks per-month progress tracking"
    location: `src/manta_trading/data/historical_minute/service.py:124-185`
  - id: F006
    severity: concern
    category: completeness
    summary: "\"Caught up\" state transition is undefined for both daemons"
    location: **Envisioned State — Daily Acquisition Daemon / Minute Acquisition Daemon**
  - id: F007
    severity: concern
    category: completeness
    summary: "CLI one-shot commands and daemon watermarks own the same state — interaction unspecified"
    location: **Architectural Principles — "CLI is the baseline, daemon is the target"**
  - id: F008
    severity: concern
    category: completeness
    summary: "Acquisition state schema lacks a specified uniqueness constraint"
    location: **Envisioned State — "Acquisition state tables"**
  - id: F009
    severity: concern
    category: completeness
    summary: "`IDailyDataProvider` interface shape is entirely deferred"
    location: **Envisioned State — "Provider interface layer"**
  - id: F010
    severity: concern
    category: antipattern
    summary: "`RECENT_DAYS = 100` duplicated in two methods — violates stated project rule"
    location: `src/manta_trading/market/marketservice.py:566, 596`
  - id: F011
    severity: note
    category: completeness
    summary: "Daemon lifecycle management decision is unresolved"
    location: **Envisioned State — "CLI integration"**
---

# Review: arch — slice 120

**Verdict:** FAIL
**Model:** claude-sonnet-4-6

## Findings

### [FAIL] AlphaVantageMinuteProvider does not actually paginate month history

The architecture document, in both **Current State** and **Related Work**, designates `AlphaVantageMinuteProvider` as "modern" code that can be reused. That endorsement is wrong.

`_fetch_month()` calls `TIME_SERIES_INTRADAY` with `outputsize=full` but passes **no month parameter** (`year1month1`, `year1month2`, etc. — required by the `TIME_SERIES_INTRADAY_EXTENDED` endpoint that supports historical pagination). Every call in the loop inside `fetch_minute_data()` sends an identical request and receives the same most-recent data. The `_calculate_month_ranges()` scaffold is present, but its output is never used in the API parameters.

This means the entire premise of "month-based pagination that fills 2-year history" does not exist in the actual code. The document also states the module docstring says "CSV format responses" while the implementation parses JSON — the two endpoints use different response formats, confirming the wrong endpoint is being called.

Any slice that reuses this provider for historical backfill will silently produce duplicate recent data rather than historical coverage. This needs to be called out explicitly as a rewrite, not a reuse.

---

### [FAIL] Atomic state + data write is impossible for daily acquisition

The document states: "The acquisition state table must be updated atomically with data writes — either in the same transaction (if same database) or with a 'data written, state pending' recovery pattern."

This is undeliverable for daily OHLCV. From `100-arch.data-storage.md` (and confirmed by `MarketDB`'s `conninfo` and `TimescaleMinuteDataDB`'s `conninfo`): daily OHLCV lives on PostgreSQL 16 at `<prototype-host>`, minute OHLCV on TimescaleDB at `<db-host>`. The document proposes acquisition state lives "in the database alongside the data it describes" — but "alongside daily data" means `.95` and "alongside minute data" means `.144`. PostgreSQL does not support cross-server transactions.

If daily state is on `.95` and minute state is on `.144`, the CLI must hold two separate DB connections to query combined status. If all state is centralised on one host, writes to the other are no longer in the same transaction. The "same transaction" branch of the atomicity promise is structurally blocked.

The "data written, state pending" recovery pattern is viable but demands at-least-once delivery semantics and idempotent writes to the data tables (deduplication on upsert). Neither requirement is called out anywhere in the document.

---

### [CONCERN] RateLimiter holds asyncio.Lock during sleep — serialises concurrent symbol fetches

The **Technical Considerations** section recommends using `asyncio.gather` or a semaphore-bounded task pool for concurrent symbol fetching, with 3 concurrent symbols as the default for minute data.

`RateLimiter.__aenter__()` acquires `self.lock` and then calls `await asyncio.sleep(time_to_wait)` while holding the lock. Because only one coroutine can hold the lock at a time, any concurrent callers block at `async with self.lock`. Concurrent symbol fetches via `asyncio.gather` will queue behind this lock, making them effectively sequential. The rate limiter already exists and is designated "modern code to reuse" — but this bug means the concurrency gains from `asyncio.gather` are lost precisely at the point where concurrency matters most (when the rate limit is being approached).

The fix is to release the lock before sleeping and re-acquire it afterward. The architecture should note this rather than presenting the rate limiter as ready for multi-symbol concurrency.

---

### [CONCERN] No enforced shared rate limit budget between daily and minute daemons

The document's proposed mechanism for shared rate limiting is temporal: "let the daily daemon run first with the full rate budget… once daily is current, the minute daemon gets most of the budget." This is an operational procedure, not a system mechanism.

If both daemons run simultaneously — which will happen after the initial catch-up, as daily daemon wakes on its cycle while the minute daemon is mid-run — both instantiate independent `RateLimiter` objects against the same AlphaVantage API key. AlphaVantage enforces the 30 req/min limit account-wide; the local limiters have no visibility into each other's calls. The result is API-level rate-limit errors that neither daemon's rate limiter predicted.

The document proposes that "daily needs very few requests once current" — true in steady state, but the "daily catches up fast" assumption means the two daemons will overlap for hours after initial deployment. The architecture must specify an enforcement mechanism (a shared rate-limit process, a coordinator table, or a deliberate sequencing policy with detection logic), not just state an intent.

---

### [CONCERN] acquire_symbol gather-all-then-write pattern breaks per-month progress tracking

The document lists "Minute orchestrator hardening" as a slice that will add "proper progress tracking and resume capability." It doesn't acknowledge that the current `acquire_symbol` implementation structurally defeats this: all months are fetched into `all_dataframes`, then `pd.concat()` + `write_minute_data_bulk()` writes everything at once. If month 18 of 24 fails, nothing is written and the entire symbol restarts.

To achieve per-symbol-per-month watermarks (the core resumability promise), the write pattern must change to: fetch-month → validate → write-month → update-state-watermark → next-month. This is a meaningful redesign of the inner loop, not just a configuration change. The slice description should make this explicit so it isn't discovered as scope expansion mid-implementation.

---

### [CONCERN] "Caught up" state transition is undefined for both daemons

Both daemon descriptions use "Sleeps when caught up" as a terminal condition without defining it:

- **Daily daemon**: Does "caught up" mean every symbol has data within 1 trading day? 2 days? What happens to symbols that haven't traded recently (low-volume or delisted symbols that still appear in the symbol list)?
- **Minute daemon**: Does "caught up" mean all gaps are filled up to the intraday cutoff boundary? What threshold triggers re-waking? How does the daemon handle the rolling 2-year history limit as older data becomes permanently unavailable?

Without these definitions, the daemon's main loop has no concrete exit condition from the "sleep" state and the "staleness" detection logic (referenced in the daily daemon description: "identifies stale symbols (no data for N days)") has no value for N. These must be pinned at architecture level, not left to slice implementation.

---

### [CONCERN] CLI one-shot commands and daemon watermarks own the same state — interaction unspecified

The document states that `mt data daily update SYMBOL` must work independently of the daemon. But the resumability architecture requires that every successful fetch updates the per-symbol watermark in the acquisition state table. If the CLI command writes to that table, it participates in daemon state. If it doesn't, the daemon may re-fetch data the CLI already wrote, or overwrite CLI-written data with a conflicting watermark.

Three coherent models exist (CLI writes state; CLI bypasses state; CLI is a thin wrapper over the same orchestrator the daemon uses), and they have different implications for the slice designs. The document doesn't pick one. This will be resolved ad-hoc in the first daily slice, likely inconsistently with the minute slice.

---

### [CONCERN] Acquisition state schema lacks a specified uniqueness constraint

The proposed schema is: `(symbol, granularity, provider, last_success_ts, last_attempt_ts, status, error_message, retry_count, run_id)`. No primary key or unique constraint is specified. The natural watermark key is `(symbol, granularity, provider)` — one row per symbol-granularity-provider triplet. Without a declared constraint, implementations may:

- INSERT a new row on every run instead of UPSERT-ing the watermark
- Create multiple rows per symbol under different `run_id`s, making watermark reads ambiguous
- Leave orphaned in-progress rows when the daemon crashes mid-write

The schema section should explicitly declare the unique key and specify the UPSERT semantics expected on each state write.

---

### [CONCERN] `IDailyDataProvider` interface shape is entirely deferred

The document says `IDailyDataProvider` will be "new, modeled on existing `IMinuteDataProvider`." The minute provider interface has four methods: `fetch_minute_data`, `get_rate_limits`, `validate_response`, `convert_to_standard_format`. Applied naively to daily data:

- `fetch_minute_data` → `fetch_daily_data` with start/end date: fine
- `validate_response(raw_data: dict)` → AlphaVantage daily returns a DataFrame via the existing `getDailyOHLCV` call chain, not a raw dict. Mapping to the protocol requires deciding whether the provider returns raw API data or normalised data.
- `convert_to_standard_format` → may be a no-op if the API layer already normalises.

The `marketservice.py` replacement slice depends on this interface being defined first, but its shape has non-trivial implications for how `marketservice.py` is decomposed. Deferring it entirely to a slice is a risk; the architecture should sketch the method signatures.

---

### [CONCERN] `RECENT_DAYS = 100` duplicated in two methods — violates stated project rule

`marketservice.py` defines `RECENT_DAYS = 100` as a local variable inside both `_determineOutputSizeByGap()` and `_determineOutputSizeByGapInfo()`. The project's `CLAUDE.md` explicitly prohibits this: "Never scatter comparison values across code. If a value is used in conditionals, switch cases, or lookups, define it once." The architecture document identifies `marketservice.py` as legacy code to replace but does not flag this as one of the issues requiring cleanup. The replacement should define this as a config value or constant, not duplicate it.

---

### [NOTE] Daemon lifecycle management decision is unresolved

The document offers two daemon lifecycle models: CLI-managed (`mt data daemon start daily`) or systemd-managed externally. These have meaningfully different implications for process supervision, log capture, restart-on-failure behaviour, and how the "graceful shutdown on SIGTERM" requirement is tested. Deferring this to "or daemons run as systemd services" leaves the first daemon slice with an unresolved deployment question. This doesn't block architecture approval but should be resolved before the daemon slice is written.
