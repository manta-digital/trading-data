---
docType: slice-design
slice: 141-universe-rebuild-from-eodhd-instruments-schema-migration
project: trading
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [131-slice.unified-schema-migration-tracking-across-both-databases]
interfaces: [142-slice.schema-migration-and-cold-start, 144-slice.daemon-refactor]
dateCreated: 20260430
dateUpdated: 20260430
status: complete
---

# Slice Design: 141 — Universe Rebuild from EODHD + Instruments Schema Migration

## Overview

Rebuild the `instruments` registry from EODHD's bulk symbol-list endpoints and migrate
the `instruments` schema to support life-cycle dates and EODHD-typed classification. The
slice is **bar-non-destructive**: bars, acquisition state, and gap tables are not
touched. The one destructive action on `instruments` is the AV-orphan delete (D10) —
rows in the AV baseline that EODHD doesn't know about. It populates the universe and
adds the columns that slice 142's `data_status` view, slice 144's daemon, and slice 145's
status command will read.

## Value

Architectural enablement. Four concrete deliverables:

1. The new universe (~57.6k symbols) is in place before slice 142 TRUNCATEs the bar
   tables. Without this, slice 142 wipes data while pointing at a stale instrument set.
2. `data_status`'s `effective_start = COALESCE(first_listing_date, first_data_date)`
   contract becomes implementable: this slice creates `first_listing_date` and populates
   it from Finnhub for the symbols Finnhub knows.
3. `eodhd_type` and `delisted_at_eodhd` give the daemon and `data_status` view the
   classification they need to reason about each symbol without re-deriving type from the
   provider on every read.
4. **Authoritative `venue` (NYSE / NASDAQ / NYSE_ARCA / BATS / NYSE_MKT) is preserved
   and extended.** The existing AV-seeded ~8k rows already carry correct `venue`; the
   rebuild *overlays* EODHD universe coverage on top without losing this. New symbols
   that Finnhub knows get `venue` from `/stock/profile2.exchange`. Symbols Finnhub
   doesn't know fall back to a conservative default. This keeps the
   per-exchange CTE in slice 142's `data_status` view meaningful.

The operator-visible behavior change is `mt data instruments rebuild` returning correct
counts and a populated `instruments` table. `--symbol AAPL` keeps working at the CLI
surface — operators look up by symbol; canonical_id remains an internal disambiguator.

## Technical Scope

**In scope:**
- Schema migration on `instruments`: add `first_listing_date`, `first_data_date`,
  `delisted_date`, `eodhd_type`, `delisted_at_eodhd`, `eodhd_exchange`. Drop `active`.
  Update consumer code.
- New CLI: `mt data instruments rebuild [--dry-run] [--skip-finnhub]`.
- EODHD bulk symbol-list fetcher (active US, delisted US, INDX filtered to USA).
- Type filter to v1 universe: `Common Stock | ETF | Preferred Stock | INDEX`.
- Idempotent upsert into `instruments` keyed by `canonical_id`.
- Optional Finnhub `/stock/profile2` enrichment that populates **both**
  `first_listing_date` *and* authoritative `venue` (NASDAQ / NYSE / NYSE_ARCA / BATS /
  NYSE_MKT) plus `trading_calendar_id` (60/min, resumable).
- Pre-flight halts on Forbidden / unexpected EODHD bulk-endpoint shape.

**Explicitly out of scope:**
- Populating `first_data_date` or `delisted_date` (slice 144 does that as side effect of
  daily backfill).
- TRUNCATE of any bar table (slice 142).
- Touching `acquisition_state`, `data_gaps`, `coverage_gaps`, or `data_status` (slice 142).
- Any adjustment / k_factor work (slice 143).
- Daemon changes (slice 144).
- Deleting symbols that disappear from EODHD between runs (audit-trail decision; future
  work).
- Cleaning up the legacy `mt data daily symbols` AlphaVantage path
  (`cli/commands/data.py:960`). Out of scope here, slated for slice 144 cleanup.

## Dependencies

### Prerequisites
- Slice 150 (schema migration framework with `minute` and `daily` tracks). Already
  complete; provides the migration runner this slice ships its DDL through.
- EODHD plan permits `/exchange-symbol-list/{US,INDX}` — verified by probe 2026-04-30.
- Finnhub `/stock/profile2` permits IPO date lookup — verified for AAPL.
- `MT_FINNHUB_API_KEY` and `MT_EODHD_API_KEY` present in environment.

### Interfaces Required
- `manta_trading.market.schema.runner.apply_migrations` (slice 150).
- `manta_trading.data.base.instrument_registry.InstrumentRegistry` (existing — extended
  here).
- A small EODHD HTTP client. The existing
  `manta_trading.data.acquisition.daily.providers.eodhd` module is daily-bar-shaped
  (per-symbol `/eod`); the bulk symbol-list call is a different endpoint family. Add a
  thin `EodhdSymbolListClient` rather than overloading the daily-bar provider.
- New `manta_trading.api.finnhub` module — does not yet exist; this slice creates it (one
  endpoint: `/stock/profile2`).

## Architecture

### Component Structure

```
src/manta_trading/
  data/
    universe/                                 ← new package
      __init__.py
      eodhd_symbol_list_client.py             ← EODHD bulk symbol-list client
      eodhd_classification.py                 ← eodhd_type filter + EodhdType StrEnum
      venue_mapping.py                        ← Finnhub exchange string → internal venue + calendar
      finnhub_ipo_client.py                   ← Finnhub /stock/profile2 (returns ipo + exchange)
      rebuild.py                              ← orchestrator: fetch → filter → upsert → enrich
    base/
      instrument_registry.py                  ← extend Instrument dataclass + writes
  api/
    finnhub/
      __init__.py
      finnhubapi.py                           ← thin httpx client for /stock/profile2
  market/
    schema/
      migrations/
        minute.py                             ← add migration 015_instruments_lifecycle_columns
  cli/
    commands/
      data.py                                 ← new `instruments rebuild` subcommand
```

The orchestrator in `rebuild.py` is the entire procedure end-to-end. It is the only
component that knows the full pipeline; the clients and classification module are pure
helpers.

### Data Flow

```
EODHD /exchange-symbol-list/US           ┐
EODHD /exchange-symbol-list/US?delisted=1│ ──► fetch (3 calls)
EODHD /exchange-symbol-list/INDX (USA)   ┘
            │
            ▼
   eodhd_classification.filter_v1_universe()
   - keep Type ∈ {Common Stock, ETF, Preferred Stock, INDEX}
   - tag delisted_at_eodhd = (was-from-delisted-list)
   - record raw eodhd_exchange ∈ {'US', 'INDX'}
            │
            ▼
   For each EODHD row:
     symbol            = Code
     existing          = SELECT * FROM instruments WHERE symbol = Code
     IF existing exists (AV-seeded with NYSE/NASDAQ/etc):
        canonical_id   = existing.canonical_id   -- keep authoritative venue
        venue          = existing.venue          -- (already correct)
        trading_calendar_id = existing.trading_calendar_id
     ELSE:
        canonical_id   = symbol + '.' + eodhd_exchange  -- temporary; e.g. 'XYZ.US'
        venue          = eodhd_exchange                  -- 'US' | 'INDX'
        trading_calendar_id = 'NYSE' if eodhd_exchange='US' else NULL  -- conservative
            │
            ▼
   InstrumentRegistry.upsert_eodhd_universe()
   - ON CONFLICT (canonical_id) DO UPDATE SET
       eodhd_type, eodhd_exchange, delisted_at_eodhd, currency, ...
   - existing rows: venue / trading_calendar_id / canonical_id NEVER overwritten
   - new rows: inserted with conservative defaults above
            │
            ▼
   DELETE AV orphans (eodhd_type IS NULL, see D10)
   - count + sample reported first
   - 5-second Ctrl-C window before DELETE
            │
            ▼
   Apply migrations 016 (SET NOT NULL + CHECK on eodhd_type, eodhd_exchange)
                    017 (DROP active)
            │
            ▼
   IF NOT --skip-finnhub:
   for each row WHERE first_listing_date IS NULL OR venue = 'US':
       Finnhub /stock/profile2 → { ipo, exchange }
       UPDATE instruments SET
         first_listing_date = ipo,
         venue              = map_finnhub_exchange(exchange),
         trading_calendar_id = derive_calendar(venue),
         canonical_id       = symbol + '.' + venue       -- promote from temp
   - rate limit: 60/min via token bucket
   - resumable: each iteration re-selects rows still needing enrichment
            │
            ▼
   Print summary counts
```

### State Management

Persistent state is `instruments`. No external cursor file; the resumability of the
Finnhub enrichment loop is inherent — re-running picks up where it left off because the
loop only looks at `first_listing_date IS NULL` rows.

The slice does **not** introduce any new tracking table for the rebuild itself (no
"rebuild_runs" log). If the operator wants history of when a rebuild ran, that's
`schema_migrations.applied_at` for the schema migration plus git history of the CLI
invocation.

## Technical Decisions

### D1. New columns are added in **one** migration

```
015_instruments_lifecycle_columns
  ALTER TABLE instruments
    ADD COLUMN IF NOT EXISTS first_listing_date  DATE,
    ADD COLUMN IF NOT EXISTS first_data_date     DATE,
    ADD COLUMN IF NOT EXISTS delisted_date       DATE,
    ADD COLUMN IF NOT EXISTS eodhd_type          TEXT,
    ADD COLUMN IF NOT EXISTS eodhd_exchange      TEXT,
    ADD COLUMN IF NOT EXISTS delisted_at_eodhd   BOOLEAN NOT NULL DEFAULT FALSE;
```

`eodhd_exchange` stores the raw `Exchange` field from EODHD's bulk endpoint (`'US'` or
`'INDX'`). It is data, not classification — kept distinct from `venue` (which is the
authoritative trading venue: NASDAQ, NYSE, NYSE_ARCA, BATS, NYSE_MKT). See D5.

`eodhd_type` is added nullable in this migration and tightened to `NOT NULL` in a
**second** migration (`016_instruments_eodhd_type_not_null`) that runs after the rebuild
populates it. This avoids a chicken-and-egg with existing rows. Slice 142 removes the
ability to live with NULL `eodhd_type` permanently by treating an unset value as a
pre-flight failure.

### D2. Drop `active` in the same migration

```
017_instruments_drop_active
  ALTER TABLE instruments DROP COLUMN IF EXISTS active;
```

Consumers updated within this slice (see Migration Plan). `data_status` view (slice 142)
derives the same boolean as
`delisted_at_eodhd = false AND delisted_date IS NULL`.

**Rationale:** keeping `active` around as an unused column invites silent re-introduction.
The arch's "no silent fallbacks" principle applies to schema too.

### D3. `eodhd_type` is a free-text TEXT, not an enum

Four allowed values today: `Common Stock | ETF | Preferred Stock | INDEX`. A CHECK
constraint enforces the set:

```sql
ALTER TABLE instruments
  ADD CONSTRAINT instruments_eodhd_type_check
  CHECK (eodhd_type IS NULL OR eodhd_type IN
    ('Common Stock', 'ETF', 'Preferred Stock', 'INDEX'));
```

Constraint is added in `016_instruments_eodhd_type_not_null` together with the
`SET NOT NULL`. The four allowed values are also defined as a Python `StrEnum` in
`manta_trading.data.universe.eodhd_classification.EodhdType`; the migration text and the
enum derive from the same source list (one definition, multiple references — per
project's no-magic-strings rule).

**Why TEXT + CHECK rather than a dedicated PG ENUM:** PG ENUMs require ALTER TYPE for
extension, which creates schema-migration friction whenever EODHD adds a Type. TEXT +
CHECK + Python enum gives the same correctness with cleaner evolution.

### D4. `canonical_id` derivation — symbol-first lookup, venue is the disambiguator

**Operator surface:** the operator types `--symbol AAPL`. `InstrumentRegistry.get_by_symbol`
(existing) returns the unique active row for that symbol. `canonical_id` is for internal
disambiguation only — needed when the same symbol appears on multiple venues (rare for
US equities, common for cross-listings). The shape rule is **`{symbol}.{venue}`** —
matching the existing AV-seeded convention (`AAPL.NASDAQ`, `GE.NYSE`).

**Rebuild rule:** the rebuild does **not** synthesize new canonical_ids when a row
already exists. For each EODHD bulk-list row:

- If a row with `symbol = EODHD.Code` already exists in `instruments`, it keeps its
  `canonical_id`, `venue`, and `trading_calendar_id`. The rebuild only updates the
  EODHD-sourced fields (`eodhd_type`, `eodhd_exchange`, `delisted_at_eodhd`, `currency`).
- If no row exists (new symbol from EODHD's universe), one is inserted with
  `canonical_id = "{symbol}.US"` and `venue = 'US'` — a **temporary** placeholder. The
  Finnhub enrichment loop later promotes it to the authoritative venue (NASDAQ / NYSE /
  NYSE_ARCA / BATS / NYSE_MKT) and rewrites both `canonical_id` and `venue` in one
  transaction.
- Index entries (`eodhd_exchange = 'INDX'`) are inserted with
  `canonical_id = "{symbol}.INDX"` and `venue = 'INDX'`. These are not promoted by
  Finnhub.

After rebuild + Finnhub enrichment, the universe looks like:

```
AAPL  | NASDAQ     | AAPL.NASDAQ      | trading_calendar_id=NASDAQ
JPM   | NYSE       | JPM.NYSE         | trading_calendar_id=NYSE
SPY   | NYSE_ARCA  | SPY.NYSE_ARCA    | trading_calendar_id=NYSE   (ETFs use NYSE calendar)
SPX   | INDX       | SPX.INDX         | trading_calendar_id=NYSE
XYZ   | US         | XYZ.US           | trading_calendar_id=NYSE   (Finnhub didn't know it)
```

The AV-seeded rows for `AAPL`/`MSFT`/etc are **preserved unchanged** — the rebuild is an
overlay, not a replacement.

### D5. `venue` authority: existing row > Finnhub > conservative default

**EODHD's bulk endpoint does not distinguish NYSE vs NASDAQ.** It returns
`Exchange = 'US'` for every US equity (verified 2026-04-30); per-symbol `/fundamentals`
would disambiguate but is Forbidden on this plan. So the rebuild cannot use EODHD alone
to populate `venue` correctly for new symbols.

**Three sources of truth for `venue`, in priority order:**

1. **Existing row's `venue`** — for symbols already in `instruments` (the AV-seeded
   ~8k baseline plus anything previously enriched). The rebuild does not overwrite. This
   keeps `AAPL → NASDAQ`, `JPM → NYSE`, etc. correct from day one.
2. **Finnhub `/stock/profile2.exchange`** — for symbols Finnhub knows. Mapped through
   `manta_trading.data.universe.venue_mapping.map_finnhub_exchange()` to the project's
   internal venue values (`'NASDAQ'`, `'NYSE'`, `'NYSE_ARCA'`, `'BATS'`, `'NYSE_MKT'`).
   Mapping table is centralized; if Finnhub returns an unknown exchange string, the
   loop logs a warning and leaves the row at `venue = 'US'` (the conservative default
   below). No silent fallback to a wrong venue.
3. **Conservative default `venue = 'US'`, `trading_calendar_id = 'NYSE'`** — for new
   EODHD symbols when (a) `--skip-finnhub` is in effect, (b) Finnhub does not know the
   symbol (typical for delisted-pre-Finnhub or obscure tickers), or (c) Finnhub returns
   an unknown exchange. NYSE is the conservative US-equity calendar — it has the same
   trading hours and holidays as NASDAQ, so calendar-driven gap detection produces
   correct results. The `venue = 'US'` value is a marker that this row's venue has not
   been authoritatively determined; slice 145's `mt data status` can surface these as a
   diagnostic if useful.

**Why this preserves existing data:** the AV-seeded rows all have authoritative `venue`
(NASDAQ / NYSE / NYSE_ARCA / BATS / NYSE_MKT — verified 2026-04-30 with row counts
3579 / 2144 / 1449 / 626 / 212). Source 1 means rebuild is purely additive for these
rows. The new EODHD rows (mostly delisted symbols and indices) come in with `venue='US'`
or `venue='INDX'` and get promoted by Finnhub.

**Slice 142 view-join contract.** The architecture's pseudocode for `data_status`
joins `exchange_completed_close ec ON ec.exchange = i.venue`. A row with the transient
`venue='US'` would not match any row in `trading_calendar` (which only contains
authoritative exchange names: NASDAQ, NYSE, etc.) and would be **excluded from the
view entirely** — wrong, because the operator needs to see those rows in status.

**Resolution:** slice 142's view definition must join on `i.trading_calendar_id`
rather than `i.venue`:

```sql
JOIN exchange_completed_close ec ON ec.exchange = i.trading_calendar_id
```

This is the right join semantically anyway: the view wants the calendar, not the
venue, and `trading_calendar_id` is the authoritative pointer to the calendar (an
ETF on `NYSE_ARCA` already uses the `NYSE` calendar via this column). For
`venue='US'` rows, `trading_calendar_id='NYSE'` (the conservative default set by the
rebuild), so they match and appear in the view.

This slice records the contract; slice 142 honors it. The `140-arch` doc is updated
in lockstep so arch and slices agree.

**Slice scope:** rebuild + Finnhub-default-on. After this slice runs successfully with
Finnhub credentials present, the residual `venue='US'` rows should be a small minority
(low thousands at most — the EODHD-knows-but-Finnhub-doesn't tail). Quantifying that
minority is one of the success criteria.

### D6. Finnhub enrichment is opt-out, but never blocks the schema migration

`mt data instruments rebuild` runs Finnhub enrichment by default. `--skip-finnhub`
suppresses it. The schema migration (015 → 016 → 017) and the EODHD upsert always
complete regardless of Finnhub state — Finnhub is enrichment, not a hard dependency.

Three modes the operator might run:

| Invocation | EODHD upsert | Migrations 015/016/017 | Finnhub enrichment |
|---|---|---|---|
| `rebuild` (default) | yes | yes | yes (~17h, resumable) |
| `rebuild --skip-finnhub` | yes | yes | no |
| `rebuild` with Finnhub Forbidden mid-run | yes | yes | partial; logs the failure and exits with code 0 once EODHD half is committed |

The third mode is the key change from the prior draft: a Finnhub outage or quota
exhaustion does not abort the schema migration. EODHD half commits, migrations apply,
operator can re-run the Finnhub half later (the loop is resumable via
`WHERE first_listing_date IS NULL OR venue = 'US'`).

`--skip-finnhub` exists for two operator cases: (1) Finnhub credentials not present,
(2) operator wants the EODHD half done now and Finnhub later. Both produce identical
DB state — `venue = 'US'` for new EODHD-only rows, `first_listing_date NULL`, AV-seeded
rows untouched.

### D7. No cursor file for Finnhub resumability

Resumability is achieved by selecting
`WHERE first_listing_date IS NULL OR venue = 'US'` each iteration. The loop is
naturally idempotent. A cursor file would just be a denormalized cache that can drift
from the table. Re-running the command after a kill/restart picks up exactly where it
left off because the `IS NULL OR venue='US'` filter is the cursor.

The composite predicate ensures rows that are missing **either** signal get a Finnhub
visit. A row that already has `first_listing_date` but is still on `venue='US'` (e.g.,
a Finnhub-unknown symbol whose IPO date came from somewhere else in the future) still
gets re-attempted; if Finnhub still doesn't know it, the row stays as-is.

### D8. Rate limiting is a token bucket, one per provider

Two clients in this slice (EODHD bulk, Finnhub). Each gets its own
`asyncio.Semaphore` + token-bucket implementation in
`manta_trading.api.<provider>.rate_limit.py`. The EODHD bulk client only makes 3 calls
total per rebuild (well under 1000/min), so its limiter is essentially a no-op; it still
exists for symmetry and to preempt accidental burst. Finnhub limit is 60/min — the
limiter actively gates.

### D9. Pre-flight checks halt EODHD on failure; Finnhub failures are non-fatal

Two pre-flights at the start of `mt data instruments rebuild`:

1. **EODHD (fatal).** Probe `/exchange-symbol-list/US?api_token=...&fmt=json`; assert
   200 + array shape with `Code` field. On 403/Forbidden, raise `EodhdAccessError` and
   exit with non-zero **before** any DB mutation. On unexpected schema, raise
   `EodhdSchemaError`. EODHD is the universe source — without it there is no rebuild.
2. **Finnhub (warn-and-degrade).** If Finnhub enrichment is not `--skip-finnhub`, probe
   `/stock/profile2?symbol=AAPL`; assert `ipo` field present. On 403 or non-200, log a
   warning (`"Finnhub pre-flight failed: <reason>; proceeding with EODHD only,
   --skip-finnhub semantics"`) and continue. The schema migration still runs.

No fallback to "partial rebuild on partial EODHD endpoints." If EODHD bulk endpoints
don't respond as expected, the operator hears about it and the DB is untouched. Finnhub
is enrichment, treated as best-effort.

### D10. AV-orphan policy: delete orphans, report count

After the EODHD upsert completes, some pre-existing AV-seeded rows may not have been
visited by any EODHD bulk-list row — these symbols are present in the AV baseline but
absent from EODHD's active and delisted US lists (private secondaries, OTC, foreign
ADRs, etc.). After upsert, these rows have `eodhd_type IS NULL` while all
EODHD-visited rows have `eodhd_type` populated. Migration `016` (`SET NOT NULL`)
would fail on these orphans.

**Decision:** delete AV orphans. The rebuild orchestrator runs, after the EODHD
upsert and **before** migration 016:

```sql
DELETE FROM instruments WHERE eodhd_type IS NULL;
```

Rationale:

- We've cancelled AlphaVantage as a primary provider. We have no API key to backfill
  these symbols and no operator command targeting them.
- Slice 142 TRUNCATEs the bar tables anyway. Any bars these orphans might
  theoretically have are evaporating regardless.
- Holding orphans for hypothetical-future OTC support is exactly the
  "design-for-imaginary-future" anti-pattern the project explicitly avoids.

**Safety rail:** before deleting, the orchestrator counts orphans and reports them
in the rebuild summary:

```
AV orphans (in AV baseline, absent from EODHD): N9
  Sample (first 20): TICKER1, TICKER2, ...
Deleting orphans...
```

If `N9` is unexpectedly large (operator can read the count and decide), the operator
can `Ctrl-C` between the report and the delete — the orchestrator pauses for 5
seconds with a confirmation gate before issuing the DELETE. This is the only
destructive action in this slice; everything else is non-destructive overlay.

A `--keep-av-orphans` flag is **not** provided. If the operator wants to inspect
orphans, they read the report and Ctrl-C; if they want to keep them, they extend
the slice. Pre-existing decisions don't deserve flags they will never use.

### D11. HTTP retry / timeout policy is centralized and explicit

Both new HTTP clients (`EodhdSymbolListClient`, Finnhub client) use the same
`manta_trading.api.http_retry.RetryPolicy`:

| Parameter | Value | Rationale |
|---|---|---|
| Connect timeout | 10s | Reject slow DNS / TCP handshake quickly |
| Read timeout | 30s | EODHD bulk responses are 5-10MB; 30s covers slow links |
| Total retries | 3 | One transient blip is normal; three is overkill |
| Retry backoff | 1s, 2s, 4s (exponential) | No jitter; this is a single-process tool |
| Retryable errors | Connect timeout, read timeout, peer reset, 502, 503, 504, 429 | All transient; all warrant retry |
| Non-retryable | 400, 401, 403, 404, 5xx other than above, malformed JSON | Configuration / quota / contract problem; retrying won't help |
| Circuit breaker | None | Single-process tool; if 3 retries don't work, fail loud and let the operator decide |

After 3 retries against EODHD bulk endpoints, raise `EodhdAccessError` and abort
(EODHD is fatal). After 3 retries against Finnhub for one symbol, log a warning,
mark that symbol as "Finnhub-failed" in the run summary, and continue (Finnhub is
best-effort, per D6/D9).

The retry policy is one module so we don't grow two divergent retry behaviors.

### Patterns and Conventions

- Type filter set lives in `EodhdType` StrEnum, **referenced** by the SQL CHECK
  constraint generator (same pattern as `coverage_gaps_resolution_status_check` in the
  existing minute migrations file). One definition, two consumers.
- All HTTP I/O uses `httpx.AsyncClient`. Mirrors the existing EODHD daily-bar provider's
  conventions — same retry-with-backoff wrapper.
- Idempotency is tested by re-running the orchestrator twice in a row in an integration
  test and asserting the second run is a no-op (zero rows changed).
- Errors at clients re-raise with provider-tagged subclasses (`EodhdAccessError`,
  `FinnhubAccessError`); the CLI surface catches at the top and converts to typer exit
  codes.

## Implementation Details

### Migration Plan

This is a refactoring slice in the schema-migration sense. Three migrations land:

| ID | Description | Reversible? |
|---|---|---|
| `015_instruments_lifecycle_columns` | Add `first_listing_date`, `first_data_date`, `delisted_date`, `eodhd_type`, `eodhd_exchange`, `delisted_at_eodhd` (all nullable initially; `delisted_at_eodhd` defaults to FALSE NOT NULL). | Forward-only. |
| `016_instruments_eodhd_type_not_null` | Add CHECK constraint on `eodhd_type`; SET NOT NULL. Pre-condition: rebuild has populated all rows. | Forward-only. |
| `017_instruments_drop_active` | Drop `active` column. Pre-condition: all consumers no longer reference it. | Forward-only. |

Migrations 016 and 017 are run **after** rebuild has populated the table for the first
time **and** after AV orphans are deleted. The CLI orchestrates this:

```
mt data instruments rebuild
  → run migration 015 (idempotent, no-op if already applied)
  → fetch + filter + upsert  (populates eodhd_type for EODHD-known symbols)
  → count AV orphans (eodhd_type IS NULL); print + sample
  → 5-second confirmation gate
  → DELETE AV orphans            (D10 — only remaining rows are EODHD-visited)
  → run migration 016 (NOT NULL + CHECK on eodhd_type, eodhd_exchange)
  → run migration 017 (DROP active)
  → optional Finnhub enrichment
```

The order is encoded in the orchestrator. The migration file just defines the SQL; the
orchestrator's job is to call them at the right point in the pipeline. Migration 016
will fail if any orphan was missed — that's the safety net.

#### Consumer updates for `DROP active`

Search for `\.active\b` produces these consumer sites (verified 2026-04-30):

1. `src/manta_trading/data/base/instrument_registry.py` — `Instrument.active` field,
   `_INSTRUMENT_COLS` includes it, several queries filter `WHERE active = TRUE`,
   `register_instrument` accepts it as a parameter, `list_instruments` has
   `active_only` flag.
   - **Update:** remove `active` from `Instrument` dataclass, add the new lifecycle
     fields. `_INSTRUMENT_COLS` updated. Query predicates change from
     `WHERE active = TRUE` to
     `WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL`. `list_instruments`'s
     `active_only` flag preserved as parameter for caller compatibility, but its meaning
     becomes the new boolean expression.
2. `src/manta_trading/cli/commands/data.py:976` — `mt data daily symbols` command,
   AlphaVantage path. **Not updated by this slice.** This command is dead code in the
   EODHD era; it's removed as part of slice 144's daemon cleanup.
3. `src/manta_trading/cli/commands/data.py:2095, 2118` — `mt data instruments list`
   command emits `i.active`.
   - **Update:** replace with derived boolean (same expression as above) and rename the
     output column. Or remove the column entirely from the table — operator can use
     `eodhd_type` and `delisted_at_eodhd` to reason about state. **Decision:** keep a
     "Listed" column rendered from `(NOT delisted_at_eodhd) AND (delisted_date IS NULL)`.
4. `src/manta_trading/api/alphavantage/alphavantageapi.py` — uses an `active` parameter
   on `getSymbolListing`; that's the AlphaVantage API's own parameter, not a column
   reference. No change.
5. `src/manta_trading/tasks/taskqueue.py` and `market/symbol_list_manager.py` — these
   reference an `active` field on AlphaVantage symbol records. Distinct from
   `instruments.active`. No change.
6. `src/manta_trading/cli/commands/provider.py` and `cli/commands/status.py` — verified
   to not reference `instruments.active` directly.

#### Verification that behavior is preserved

The `instruments` table after rebuild is a **strict superset** of the AV-seeded baseline:

- Every (symbol, venue, canonical_id, trading_calendar_id) tuple from the AV-seeded
  rows is preserved. Verified by snapshot diff in the walkthrough (steps 1a vs 3a).
- Every previously-`active=TRUE` AV-seeded row is `delisted_at_eodhd=FALSE` and
  `delisted_date IS NULL` after rebuild — the derived "is listed" boolean produces the
  same value. Consumers that previously filtered `WHERE active = TRUE` and now filter
  `WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL` see the same rows.
- New rows added by rebuild (mostly delisted-pre-AV symbols and indices) are additive.
  Existing consumers that listed instruments will see *more* rows but never different
  rows.

Bar tables are not touched by this slice. Slice 142 wipes them; this slice does not.

### CLI Specification

```
mt data instruments rebuild [OPTIONS]

  Rebuild the instrument registry from EODHD bulk symbol-list endpoints.

  Steps:
    1. Pre-flight EODHD bulk endpoints.
    2. Apply migration 015_instruments_lifecycle_columns.
    3. Fetch active US, delisted US, USA-relevant indices.
    4. Filter to v1 universe (Common Stock, ETF, Preferred Stock, INDEX).
    5. Upsert into instruments (idempotent).
    6. Apply migrations 016, 017.
    7. (Unless --skip-finnhub) enrich first_listing_date from Finnhub.

Options:
  --dry-run         Print counts without DB mutation. Skips migrations
                    016/017 and Finnhub.
  --skip-finnhub    Skip the Finnhub IPO-date enrichment loop.
  --json            Emit JSON summary instead of Rich table.
  --help            Show this message and exit.
```

Output (Rich table by default):

```
Universe Rebuild Summary
┌──────────────────┬────────┬──────────┐
│ Type             │ Active │ Delisted │
├──────────────────┼────────┼──────────┤
│ Common Stock     │ 31,402 │  16,481  │
│ ETF              │  6,889 │   1,073  │
│ Preferred Stock  │  1,387 │     507  │
│ INDEX            │     50 │       0  │
├──────────────────┼────────┼──────────┤
│ TOTAL            │ 39,728 │ 18,061   │
└──────────────────┴────────┴──────────┘
Inserted:  N1
Updated:   N2
Unchanged: N3
AV orphans deleted (in AV baseline, absent from EODHD): N4
  Sample (first 20): TICKER1, TICKER2, ...

Finnhub enrichment:
  Eligible (first_listing_date NULL): N5
  Populated:                          N6
  Not found in Finnhub:               N7
  Errors (logged):                    N8
```

`--json` returns the same structure under canonical keys.

### Database / Storage Schema

Final shape of `instruments` after this slice:

```
instruments
  instrument_id        BIGSERIAL PK
  canonical_id         VARCHAR(64) UNIQUE NOT NULL
  symbol               VARCHAR(32) NOT NULL
  asset_class          VARCHAR(32) NOT NULL
  venue                VARCHAR(32) NOT NULL
  currency             VARCHAR(8)  NOT NULL DEFAULT 'USD'
  tick_size            NUMERIC(18,8)
  lot_size             NUMERIC(18,8)
  trading_calendar_id  VARCHAR(32)
  adjustment_policy    VARCHAR(32)
  metadata             JSONB DEFAULT '{}'
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
  -- new in slice 141
  first_listing_date   DATE NULL
  first_data_date      DATE NULL          -- populated by slice 144
  delisted_date        DATE NULL          -- populated by slice 144
  eodhd_type           TEXT NOT NULL CHECK (eodhd_type IN ('Common Stock','ETF','Preferred Stock','INDEX'))
  eodhd_exchange       TEXT NOT NULL CHECK (eodhd_exchange IN ('US','INDX'))
  delisted_at_eodhd    BOOLEAN NOT NULL DEFAULT FALSE
  -- removed in slice 141
  -- active            BOOLEAN  (dropped)
```

`venue` (existing column) is the **authoritative trading venue** — `'NASDAQ'`, `'NYSE'`,
`'NYSE_ARCA'`, `'BATS'`, `'NYSE_MKT'` (existing AV-seeded values) plus `'INDX'` (new for
indices) plus `'US'` (transient placeholder for new EODHD-only rows that Finnhub didn't
disambiguate). `eodhd_exchange` is the **raw EODHD provider field** — `'US'` or `'INDX'`
— kept as data, not used for routing.

### API Contracts

External APIs consumed (read-only):

**EODHD bulk symbol list**
```
GET /api/exchange-symbol-list/{US|INDX}?api_token=...&fmt=json[&delisted=1]
→ 200 OK: [{ Code, Name, Country, Exchange, Currency, Type, Isin }, ...]
→ 403 Forbidden: account does not include this endpoint
→ 401 Unauthorized: bad token
```
Three calls per rebuild: `US`, `US?delisted=1`, `INDX` (post-filtered to `Country == 'USA'`).

**Finnhub stock profile**
```
GET /api/v1/stock/profile2?symbol=...&token=...
→ 200 OK: { country, currency, exchange, ipo, name, ... }
   (ipo may be missing or empty string for symbols Finnhub doesn't track)
→ 429 Too Many Requests: rate limit exceeded (handle as transient)
→ 403 Forbidden: free tier exhausted / blocked endpoint
```

This slice exposes no new external API. It exposes one new CLI surface.

## Integration Points

### Provides to Other Slices

- **To slice 142:** populated `instruments` table with `eodhd_type`,
  `delisted_at_eodhd`, and the lifecycle columns (`first_listing_date` populated where
  Finnhub knows; `first_data_date`, `delisted_date` left NULL). Slice 142's pre-flight
  asserts `eodhd_type IS NOT NULL` for every row — guaranteed by D10's orphan delete
  plus migration 016's NOT NULL constraint.
- **To slice 142 (view-join contract):** the `data_status` view must join the
  `exchange_completed_close` CTE on `i.trading_calendar_id`, **not** on `i.venue`.
  Reason: a small minority of rows end at `venue='US'` (the conservative placeholder
  for EODHD-known + Finnhub-unknown symbols, see D5); these have
  `trading_calendar_id='NYSE'` and need to appear in status. Joining on `venue`
  would exclude them entirely. The arch's pseudocode is being amended in lockstep
  to reflect this.
- **To slice 144:** the contract that `first_listing_date` may exist (Finnhub-sourced)
  and `first_data_date`/`delisted_date` are slice 144's job to populate from MIN/MAX bar
  date during backfill.
- **To slice 145 (`mt data status`):** `effective_start = COALESCE(first_listing_date,
  first_data_date)` is well-defined for any row with at least one of those populated.

### Consumes from Other Slices

- Slice 150's migration runner. The slice fails if migration 015's predecessors aren't
  applied; this is detected by the runner, not duplicated here.
- The existing `manta_trading.api.alphavantage` rate-limit module pattern. Mirror it for
  the new `manta_trading.api.finnhub` module.

## Success Criteria

### Functional Requirements

1. `mt data instruments rebuild` against the test DB populates ~57.6k rows
   distributed across `eodhd_type ∈ {Common Stock, ETF, Preferred Stock, INDEX}` with
   counts within 5% of the values verified by 2026-04-30 probe (Common Stock ~48k, ETF
   ~7.9k, Preferred Stock ~1.9k, INDEX ~50).
2. `--dry-run` prints the same counts without mutating the DB. Verified by running it
   twice against an empty `instruments` table and asserting row count remains 0.
3. Re-running `mt data instruments rebuild` with no upstream change is a no-op:
   `inserted = 0`, `updated = 0`. Verified by running the full command twice
   back-to-back.
4. `mt data instruments rebuild --skip-finnhub` populates everything except
   `first_listing_date` (NULL) and any new EODHD-only row's `venue` (left at the
   transient `'US'`).
5. `mt data instruments rebuild` (no flag) populates `first_listing_date` for at least
   one well-known symbol (AAPL → 1980-12-12) and promotes its `venue` to the
   authoritative value (`'NASDAQ'`). Full enrichment is ~17 hours and is resumable;
   functional verification uses a small subset.
6. **AV-seeded venues are preserved.** After `mt data instruments rebuild` (any mode),
   `instruments` rows with pre-existing `venue ∈ {NASDAQ, NYSE, NYSE_ARCA, BATS,
   NYSE_MKT}` retain their venue and `canonical_id`. Verified by snapshot of (symbol,
   venue, canonical_id) for the AV-seeded baseline before and after — must match
   exactly.
7. **`--symbol` lookup works at the CLI surface.** A pre-existing CLI command that
   accepts `--symbol AAPL` (e.g., `mt data instruments list --symbol AAPL` if it has
   that form, or any subsequent slice's `--symbol` flag) returns the AAPL row without
   the operator needing to type `AAPL.NASDAQ`. Verified by inspecting
   `InstrumentRegistry.get_by_symbol('AAPL')` returns a row.
8. EODHD `403 Forbidden` on the pre-flight halts the run with non-zero exit code and
   does NOT touch the DB.
9. **Finnhub `403 Forbidden` does NOT halt the run.** EODHD upsert and migrations
   015/016/017 still complete; Finnhub enrichment is skipped with a logged warning.
   Final exit code is 0.
10. **AV orphans are deleted; report shows count.** A symbol present in the AV
    baseline but absent from EODHD's bulk lists ends with `eodhd_type IS NULL` after
    upsert. The orchestrator counts these, prints sample, pauses 5s, then DELETEs.
    Verified: pre-rebuild AV baseline count vs post-rebuild
    `SELECT COUNT(*) FROM instruments WHERE eodhd_type IS NULL` should be 0; the
    orphan count in the run summary equals the difference.
11. **Symbol absent from EODHD between two consecutive runs.** A symbol present in
    EODHD on run 1 but absent from EODHD on run 2 ends as an orphan after run 2 and
    is deleted by run 2. (Operator wants old data preserved? Out of scope — they
    extend the slice. The rebuild's job is to mirror EODHD's universe.)
12. Migration `017_instruments_drop_active` succeeds; no consumer code references the
    `active` column after the slice. Verified by `grep -rn 'instruments\.active\|"active"\|\.active\b'`
    in the source tree returning only AlphaVantage symbol-list residue (out-of-scope
    here).
13. **Residual `venue='US'` rows are a small minority** after a Finnhub-enabled run.
    Quantified: `SELECT COUNT(*) FROM instruments WHERE venue='US'` should be
    significantly smaller than `WHERE venue IN ('NASDAQ','NYSE','NYSE_ARCA','BATS','NYSE_MKT')`.
    No fixed threshold — operator confirms the ratio looks reasonable. Helps gauge
    Finnhub coverage of EODHD's universe.

### Technical Requirements

- All new modules pass `ruff check` and `pyright --strict` per project settings.
- Each new client module has a unit test covering: success, 403, malformed JSON,
  rate-limit retry. Tests use `respx` (httpx mocking) — no live calls.
- An integration test runs the full orchestrator against a containerized empty
  `instruments` table with EODHD/Finnhub stubbed by recorded fixtures (cassettes), and
  asserts (a) row counts within probe values, (b) re-run is no-op, (c) migration 017
  applied.
- One real-world fixture: a recorded `/exchange-symbol-list/US` response with at least
  100 representative rows including each of the four kept Types and at least one
  filtered-out Type (e.g., `Mutual Fund`) — proves the filter actually filters.

### Integration Requirements

- After this slice completes, slice 142's pre-flight check
  (`eodhd_type IS NOT NULL` on every row) passes. Verified by manually invoking slice
  142's pre-flight script (created in slice 142, but the contract here is "every row
  has `eodhd_type` set").
- After this slice completes, slice 144's backfill code can read
  `instruments.first_listing_date` for symbols where it exists and use it as
  `target_start = max(first_listing_date, today - history_months)`.

### Verification Walkthrough

End-to-end demo script. This is the script the operator runs once to sign off on slice
141.

```bash
# Prereqs
set -a; source .env; set +a

# 0. Verify env
echo "$MT_EODHD_API_KEY" | head -c 8
echo "$MT_FINNHUB_API_KEY" | head -c 8
psql "$MT_TIMESCALE_DB_URL" -c "SELECT 1"

# 1. Confirm clean baseline. Slice 150 should already have migrations 001-014
#    applied. Migrations 015-017 are not yet present.
mt data migrate status --db minute
# Expect: 014_nvda_inaugural_gap = applied; 015,016,017 = pending.

# 1a. Snapshot AV-seeded baseline so we can verify it survives the rebuild.
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, venue, canonical_id, trading_calendar_id
  FROM instruments
  ORDER BY symbol, venue
" > /tmp/instruments-pre.txt
wc -l /tmp/instruments-pre.txt
# Expect: ~8000 rows (the AV-seeded baseline).

# 2. Dry run
mt data instruments rebuild --dry-run
# Expect: prints type-count breakdown, no DB mutation.
psql "$MT_TIMESCALE_DB_URL" -c "SELECT COUNT(*) FROM instruments"
# Expect: count is unchanged from step 1a (~8000).

# 3. Skip-Finnhub run (proves schema migration completes without Finnhub)
mt data instruments rebuild --skip-finnhub
# Expect: prints type-count breakdown plus inserted/updated/unchanged.
# Reports AV orphan count + sample, 5s gate, then DELETEs them.
# Migrations 015, 016, 017 applied after orphan delete. Exit code 0.

mt data migrate status --db minute
# Expect: 015, 016, 017 = applied.

# 3-orphan. Verify orphans were deleted; eodhd_type populated everywhere.
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT COUNT(*) FROM instruments WHERE eodhd_type IS NULL;
"
# Expect: 0.

psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT eodhd_type, COUNT(*)
  FROM instruments
  WHERE delisted_at_eodhd = FALSE
  GROUP BY eodhd_type
  ORDER BY 2 DESC;
"
# Expect: Common Stock ~31k, ETF ~6.9k, Preferred Stock ~1.4k, INDEX ~50.

# 3a. Verify AV venues survived the upsert.
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, venue, canonical_id, trading_calendar_id
  FROM instruments
  WHERE symbol IN ('AAPL','MSFT','GE','JPM')
  ORDER BY symbol;
"
# Expect:
#   AAPL | NASDAQ | AAPL.NASDAQ | NASDAQ
#   GE   | NYSE   | GE.NYSE     | NYSE
#   JPM  | NYSE   | JPM.NYSE    | NYSE
#   MSFT | NASDAQ | MSFT.NASDAQ | NASDAQ

# 3b. Verify new EODHD-only rows came in with the transient venue='US'.
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT venue, COUNT(*) FROM instruments GROUP BY venue ORDER BY 2 DESC;
"
# Expect: NASDAQ ~3579, NYSE ~2144, NYSE_ARCA ~1449, BATS ~626,
#         NYSE_MKT ~212 (unchanged from baseline);
#         US = ~50000 (new EODHD-only rows, transient placeholder);
#         INDX ~50.

psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT
    COUNT(*) FILTER (WHERE first_listing_date IS NULL) AS no_ipo,
    COUNT(*) FILTER (WHERE first_listing_date IS NOT NULL) AS has_ipo
  FROM instruments;
"
# Expect: has_ipo = 0 (we skipped Finnhub).

# 4. Idempotency check
mt data instruments rebuild --skip-finnhub
# Expect: inserted = 0, updated = 0.

# 5. Finnhub enrichment, smoke run
# (Full run is ~17h; smoke-test for ~90s — the loop is resumable.)
timeout 90 mt data instruments rebuild || true
# Expect: process exits cleanly. Some venue='US' rows promoted to NASDAQ/NYSE/etc.
# Some first_listing_date populated.

psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT symbol, venue, canonical_id, first_listing_date
  FROM instruments
  WHERE symbol = 'AAPL';
"
# Expect: AAPL | NASDAQ | AAPL.NASDAQ | 1980-12-12.

# 6. Resumability
mt data instruments rebuild
# Expect: continues enrichment from where step 5 left off; no duplicate work
# on already-populated rows.

# 7. Active column gone
psql "$MT_TIMESCALE_DB_URL" -c "
  SELECT column_name FROM information_schema.columns
  WHERE table_name = 'instruments'
  ORDER BY ordinal_position;
"
# Expect: 'active' NOT in list. eodhd_type, eodhd_exchange, delisted_at_eodhd,
# first_listing_date, first_data_date, delisted_date ARE in list.

# 8. Failure-mode demo: EODHD pre-flight halts cleanly
MT_EODHD_API_KEY=bad mt data instruments rebuild
# Expect: pre-flight fails with EodhdAccessError, exit code != 0, DB unchanged.

# 9. Failure-mode demo: Finnhub failure does NOT halt
# (On a fresh DB, drop Finnhub key and run.)
MT_FINNHUB_API_KEY=bad mt data instruments rebuild
# Expect: warning logged, EODHD upsert + migrations 015/016/017 complete,
# exit code 0. No first_listing_date populated; new EODHD rows remain at
# venue='US' awaiting a future Finnhub run.
```

The slice is signed off when each `# Expect:` is observed.

### Actual Results (recorded 2026-04-30 during Phase 6 verification)

Steps 1–4, 7, 8 confirmed working as expected. Notable actual values:

```
EODHD bulk fetch:
  Active US:    51,807 rows
  Delisted US:  54,119 rows
  INDX (USA):    1,660 rows fetched, 906 retained after Country='USA' filter

Filter v1 universe (post Mutual Fund / FUND / etc removed):
  Common Stock:     active 18,920  delisted 29,048
  ETF:              active  5,501  delisted  2,379
  Preferred Stock:  active    725  delisted  1,201
  INDEX:            active    908  delisted      0
  TOTAL:            58,682

After full skip-finnhub run:
  inserted: 58,651 (first run: 4 AV-seed pre-existing → 58655 - 4)
  unchanged on idempotent re-run: 58,628 (54 updated reflect EODHD bulk eodhd_exchange variation between runs)
  orphans_deleted: 27 (test-leftovers + de-duped)
  Total instruments rows: 58,655
  eodhd_type IS NULL: 0
  Migrations 015/016/017 all applied.
  active column dropped.

AV-seeded venue preservation (snapshot):
  AAPL | NASDAQ | AAPL.NASDAQ | NASDAQ | Common Stock
  GE   | NYSE   | GE.NYSE     | NYSE   | Common Stock
  JPM  | NYSE   | JPM.NYSE    | NYSE   | Common Stock
  MSFT | NASDAQ | MSFT.NASDAQ | NASDAQ | Common Stock

Venue distribution (skip-finnhub):
  US     57,745  ← transient; will reduce after Finnhub enrichment runs
  INDX      906
  NASDAQ      2  (AV-seeded)
  NYSE        2  (AV-seeded)

Failure-mode verification:
  MT_EODHD_API_KEY=bad → exit code 1, EodhdAccessError, DB unchanged.
  (Finnhub bad-key path is exercised by integration test
   test_rebuild_finnhub_403_does_not_halt against respx mock.)
```

The Finnhub-enabled path was exercised by integration tests
(`test_rebuild_finnhub_403_does_not_halt`, `test_rebuild_inserts_rows`); the
~17-hour live Finnhub enrichment was not run as part of this verification —
slice is signed off without that step. Operator can re-run
`mt data instruments rebuild` (without `--skip-finnhub`) at any time to
populate `first_listing_date` and promote `venue='US'` rows.

## Risk Assessment

### Technical Risks

1. **EODHD bulk endpoint plan change** — if the plan loses access to
   `/exchange-symbol-list/{US,INDX}` between probe (2026-04-30) and execution, the slice
   cannot proceed. Mitigation: pre-flight check fails loudly; operator decision on
   how to proceed.
2. **Finnhub IPO data quality** — Finnhub may return non-ISO date strings or empty for
   some symbols. Treated as "not found in Finnhub" rather than fatal. The 17-hour run
   produces a list of un-enriched symbols at the end.
3. **Finnhub `exchange` mapping completeness** — Finnhub returns exchange strings like
   `"NASDAQ NMS - GLOBAL MARKET"`, `"NEW YORK STOCK EXCHANGE, INC."`, etc. The mapping
   table in `manta_trading.data.universe.venue_mapping` must cover the strings Finnhub
   actually returns. Mitigation: log unknown strings and leave the row at `venue='US'`
   rather than guessing. Operator can extend the mapping table when unknown strings
   show up. No silent fallback to a wrong venue.
4. **Symbol collision across venues** — same symbol on two venues (e.g., `BRK.A` on
   NYSE and `BRK-A` aliased somewhere else) creates two `instruments` rows. The
   rebuild's "match existing by `symbol`" rule may match the wrong one when there are
   multiple. Mitigation: scope the existence check to `WHERE symbol = X AND
   delisted_at_eodhd = FALSE`, and if more than one row matches, log a warning and
   skip that EODHD row (don't pick arbitrarily). Operator resolves manually. This case
   is rare for US equities; AV-seeded baseline contains no symbol-duplicates per
   verification 2026-04-30.

## Implementation Notes

### Development Approach

Suggested implementation order:

1. Migration `015` + extend `Instrument` dataclass + `InstrumentRegistry` writes for
   the new columns. Tests pass against a fresh DB.
2. `EodhdSymbolListClient` with unit tests (respx). No DB integration.
3. `eodhd_classification` filter + `EodhdType` StrEnum. Pure-function tests.
4. `venue_mapping.py` — Finnhub exchange string → `(venue, trading_calendar_id)` pure
   function. Unit-tested against recorded Finnhub responses for a representative set of
   exchanges (NASDAQ, NYSE, NYSE_ARCA, BATS, NYSE_MKT, plus at least one unknown to
   prove the warn-and-leave-as-`'US'` path).
5. `manta_trading.api.finnhub.finnhubapi` with unit tests. Token-bucket included.
6. Orchestrator `rebuild.py` ties everything together. The
   "match-existing-by-symbol" rule lives here; integration test covers preservation of
   AV-seeded venues plus promotion via Finnhub.
7. CLI surface `mt data instruments rebuild`. Smoke test in dev DB.
8. Update consumers of `instruments.active` (instrument_registry queries, CLI list).
9. Migration `017_instruments_drop_active`. Re-run integration tests.
10. Verification walkthrough end-to-end against test DB, including the AV-baseline
    snapshot diff (step 1a vs 3a).

Each step is committable. The slice does not leave the system in a broken state at any
intermediate commit because migrations 016/017 only apply after the orchestrator ran;
the schema-runner pattern allows partial application.

### Special Considerations

- **17-hour Finnhub run**: must be safe to interrupt. The token bucket enforces 60/min
  ceiling; killing the process and resuming hits the rate limit a second time the same
  minute. Acceptable given Finnhub's free-tier limits and the rarity of restarts.
- **No secrets in commits**: both API keys are environment variables. Fixtures used in
  tests have keys redacted to `REDACTED`. `.env` is already in `.gitignore`.
- **TRUNCATE is NOT in this slice**: explicitly verify by reading the rebuild source
  and confirming there is no `TRUNCATE` statement anywhere. Slice 142 owns the wipe.

---

## Implementation Deviations (recorded 2026-04-30)

### Deviation 1: EODHD bulk-list returns authoritative venue per row

**LLD assumption (D3, D5):** EODHD `/exchange-symbol-list/US` returns `Exchange='US'`
for every US equity, requiring Finnhub `/stock/profile2.exchange` to disambiguate
NYSE vs NASDAQ.

**Reality (verified during Phase 6 by running `mt data instruments rebuild --skip-finnhub`
against the live API):** the bulk endpoint returns the authoritative per-row
Exchange value: NASDAQ (5675), NYSE (3362), NYSE ARCA (2646), BATS (1122),
PINK (9460), OTCQB (1076), NMFQS (26059), OTCGREY (764), OTCQX (551),
OTCMKTS, OTCCE, OTCBB, AMEX, NYSE MKT, NYSEARCA, OTC, plus `'US'` (for ~319
indices/ETFs) and `'INDX'` (for the INDX endpoint).

**Implementation impact:**
- Migration 016's CHECK constraint `eodhd_exchange IN ('US', 'INDX')` (per D3)
  was wrong and would fail against real data. The CHECK on `eodhd_exchange`
  was **removed** from migration 016. `eodhd_type` still has its CHECK
  (`Common Stock | ETF | Preferred Stock | INDEX`) — that constraint holds.
- D5's "transient `venue='US'` for new EODHD rows" still applies in this
  slice (operator-visible behavior unchanged); a future slice can lean into
  the eodhd_exchange data to populate `venue` directly without Finnhub.
- D6/D7's Finnhub enrichment is still useful for IPO date (`first_listing_date`)
  and as a venue-disambiguation backstop.

**Status:** Slice 141 ships with this deviation noted. Future slice (likely 144
during daemon cleanup) should revisit using `eodhd_exchange` directly for venue
population to eliminate most `venue='US'` transients.

## Future Work

### FW1: Batch the Step-6 upsert

**Tracked:** [GitHub issue #11](https://github.com/manta-digital/trading/issues/11)

**Observation (recorded 2026-05-01):** Step 6 of the orchestrator (`InstrumentRegistry.upsert_eodhd_universe`) executes one `cur.execute` per row, producing ~58k DB round trips. Observed runtime is 60s–4min depending on network/DB load. The bulk of the rebuild time is this loop; the 3 EODHD HTTP fetches finish in ~3s combined.

**Proposed fix:** Replace the per-row loop in `upsert_eodhd_universe` with `executemany` (psycopg3 supports `RETURNING` over batches via `cursor.executemany`) or, for larger reductions, a single VALUES-list INSERT with `ON CONFLICT ... RETURNING (xmax = 0)`. Either path should bring Step 6 to a few seconds.

**Why deferred:** Slice 141's correctness criteria (idempotency, venue preservation, orphan delete) are met at current speed; rebuild is operator-triggered, not on a hot path. Worth doing before any cadence > weekly. Slated for follow-up; not a blocker.

---

**Status:** ready for Project Manager review.
