---
docType: slice-design
slice: kalshi-provider-foundation
project: trading
parent: user/architecture/260-slices.kalshi-event-contract-data.md
dependencies: []
interfaces: [262, 263, 264, 265, 266]
effort: 3
dateCreated: 20260824
dateUpdated: 20260824
status: complete
---

# Slice Design: Kalshi Provider Foundation (261)

## Overview

Foundation slice for Initiative 260 (Kalshi event-contract data). Delivers the three things every later slice stands on:

1. **Provider registration** — `ProviderType.KALSHI` and a `ProviderProfile` with `AuthType.NONE` in the existing provider registry.
2. **API client** — an httpx-based client for Kalshi's `trade-api/v2` market-data surface: shared-budget rate limiting, cursor pagination, transient/permanent error taxonomy, Pydantic response models, optional authenticated request signing (the PM holds a funded, verified Kalshi account; authenticated operation is planned near-term for its documented rate budget), and recorded real-response fixtures for every consumed endpoint.
3. **Schema** — a `kalshi` migration track on the TimescaleDB host creating the catalog tables (series, events, markets with lifecycle status and settlement fields) and collection-state tables (per-surface watermarks, awaiting-settlement set).

This slice also discharges the architecture's discovery mandate: Kalshi's current endpoint surface and `/historical/*` cutoff behavior were verified against published documentation (docs.kalshi.com, checked 2026-08-24) and are recorded in **Discovery Findings** below. The findings gate conditional slice 266 (Historical Backfill).

No collection logic runs in this slice — catalog sync arrives in 262. This slice ends with a tested client, an applied schema, and a registered provider.

## Discovery Findings (verified against docs.kalshi.com, 2026-08-24)

These findings satisfy the architecture requirement that endpoint surface and cutoff behavior be *discovered, not assumed*. Fixture recording during implementation re-verifies the response shapes against the live API.

### Base URLs and authentication

- Documented production base URLs: `https://external-api.kalshi.com/trade-api/v2` (primary in current docs) and `https://api.elections.kalshi.com/trade-api/v2` (alternate). Demo environments exist (`external-api.demo.kalshi.co`, `demo-api.kalshi.co`).
- **Every market-data endpoint this initiative consumes is unauthenticated** (OpenAPI `security: []`), including the public `/historical/*` market-data endpoints. This confirms `AuthType.NONE` and the architecture's no-credentials constraint.

### Live endpoint surface (market data)

| Endpoint | Pagination | Notes |
|---|---|---|
| `GET /series` | **none — full list in one response** | Filters: `category`, `tags`, `min_updated_ts`, `include_product_metadata`, `include_volume`. Series carry `last_updated_ts`. |
| `GET /series/{series_ticker}` | n/a | Single series. |
| `GET /events` | cursor; `limit` 1–200, default 200 | Filters: `status` (unopened/open/closed/settled), `series_ticker`, `tickers`, `min_close_ts`, `min_updated_ts`; `with_nested_markets` embeds Market objects. |
| `GET /events/{event_ticker}` | n/a | Single event. |
| `GET /markets` | cursor; `limit` 0–1000, default 100 | Filters: `status` (unopened/open/**paused**/closed/settled), `event_ticker`, `series_ticker` (requires `mve_filter=exclude`), `tickers`, `min/max_created_ts`, `min/max_close_ts`, `min/max_settled_ts`, `min_updated_ts`. **`min_updated_ts` is incompatible with all other filters except `mve_filter=exclude`** — directly constrains 262's incremental-sync design. |
| `GET /markets/{ticker}` | n/a | Single market. |
| `GET /series/{series_ticker}/markets/{ticker}/candlesticks` | none — range query | Required: `start_ts`, `end_ts`, `period_interval` ∈ {1, 60, 1440} minutes. Optional `include_latest_before_start`. Candles keyed by `end_period_ts`; nested `yes_bid`/`yes_ask`/`price` OHLC objects (price fields nullable), `volume_fp`, `open_interest_fp`. Series ticker is part of the path — the catalog supplies it. |
| Batch candlesticks (`Batch Get Market Candlesticks`) | n/a | Up to 100 market tickers per request, ≤10,000 candles total. Not consumed in 261; recorded as a 264 optimization option. |
| `GET /markets/trades` | cursor; `limit` 1–1000, default 100 | Filters: `ticker`, `min_ts`, `max_ts`, `is_block_trade`. Trade fields: `trade_id`, `ticker`, `count_fp`, `yes_price_dollars`, `no_price_dollars`, `taker_outcome_side`, `taker_book_side`, `created_time`, `is_block_trade`. (`taker_side` is deprecated.) |

Money and quantity are served as **fixed-point decimal strings** (`*_dollars`, `*_fp` with 2 decimals) — the legacy integer-cents representation is superseded. Storage uses `NUMERIC`; parsing uses `Decimal`.

**Served market status vocabulary differs from the filter vocabulary (Phase 6 finding, live survey 2026-08-24, 1,000 markets per filter).** The `status` *field* in market objects does not carry the documented filter values. Observed mapping, filter → served: `unopened` → `initialized`; `open` → `active`; `paused` → `inactive` (plus some `closed`); `closed` → `determined` (result known, `settlement_ts` absent) and `closed`; `settled` → `finalized` (result and `settlement_ts` present). Consequence: `MarketStatus` (stored values; drives the `kalshi.markets.status` CHECK) has the six served values `initialized`, `active`, `inactive`, `closed`, `determined`, `finalized`, and a separate `MarketStatusFilter` enum carries the five documented query values. Settled time is served as `settlement_ts`, alongside `settlement_value_dollars`. `Market.status` is parsed as a plain string so an unknown future value fails at the CHECK (per Technical Decision 7), not by poisoning a whole page at parse time.

### Recording cross-check (Phase 6, fixtures recorded 2026-08-24)

What the committed fixtures under `test/fixtures/kalshi/` confirmed or corrected against the findings above:

- **Confirmed:** `GET /series` is unpaginated (98 series in one body, no `cursor` key); `/events`, `/markets`, `/markets/trades` paginate by `cursor` and a page that has more data always carries one (the recorded second pages do too — the iterator's termination on an absent/empty cursor is unit-tested, not fixture-driven); `/historical/cutoff` serves exactly the four ISO-8601 fields listed above; money/quantity fields are fixed-point strings throughout; the served market status vocabulary is as recorded in the paragraph above (`finalized` on the settled pages, `active` on the open page).
- **Corrected/added:** `GET /events/{event_ticker}?with_nested_markets=true` nests the markets *inside* the `event` object and serves an empty top-level `markets` list (the client folds either placement onto `Event.markets`). Candlestick `price` objects carry `mean_dollars` in addition to OHLC and `previous_dollars`; the `yes_bid`/`yes_ask` objects do not. A settled market's series ticker is not on the market object — it comes from its event (the catalog supplies it, as the candlestick path requires).
- **Optional columns finalized for `kalshi_002_catalog`** (every key observed across the recorded pages; anything not listed stays in `raw`): series — `frequency`, `title`, `category`, `tags`, `settlement_sources`, `fee_type`, `fee_multiplier`, `contract_url`, `contract_terms_url`, `last_updated_ts` (`product_metadata` only with `include_product_metadata`); events — `title`, `sub_title`, `category`, `mutually_exclusive`, `strike_period`, `strike_date` (documented; absent from recordings), `collateral_return_type`, `available_on_brokers`, `settlement_sources`, `last_updated_ts`; markets — `market_type`, `title`, `subtitle`, `yes_sub_title`, `no_sub_title`, `rules_primary`, `rules_secondary`, lifecycle `created_time`/`open_time`/`close_time`/`expiration_time`/`expected_expiration_time`/`latest_expiration_time`/`updated_time`, settlement `result`/`expiration_value`/`can_close_early`/`settlement_ts`/`settlement_value_dollars`, economics `notional_value_dollars`/`last_price_dollars`/`previous_price_dollars`/`yes_bid_dollars`/`yes_ask_dollars`/`no_bid_dollars`/`no_ask_dollars`/`liquidity_dollars`/`volume_fp`/`volume_24h_fp`/`open_interest_fp`, plus `strike_type`, `price_level_structure`, `is_provisional`, `mve_collection_ticker`.

### Historical tier and cutoff behavior

The live/historical split took effect **2026-02-19** (per the API changelog). Behavior:

- `GET /historical/cutoff` (unauthenticated) returns ISO-8601 cutoff timestamps: `market_settled_ts` (markets settled before this time — and their candlesticks — are served only by the historical endpoints), `trades_created_ts` (trades filled before this time), `orders_updated_ts` and `market_positions_last_updated_ts` (portfolio data; irrelevant here). The cutoff is a moving boundary — exactly the "cutoff as data, discovered not hardcoded" posture the architecture requires.
- Public, unauthenticated historical market-data endpoints exist: `GET /historical/markets`, `GET /historical/markets/{ticker}` , `GET /historical/markets/{ticker}/candlesticks`, `GET /historical/trades` (same shape and cursor pagination as live trades). Note the historical candlesticks path has **no series_ticker segment**, unlike the live path.
- `GET /historical/fills`, `/historical/orders`, `/historical/positions` are portfolio-scoped (authenticated) and out of scope.

**Gate decision for slice 266:** recoverable public data *does* exist behind `/historical/*` — settled markets, their candlesticks, and old trades are all retrievable without credentials. **Slice 266 (Historical Backfill) is confirmed viable and should proceed** once 264/265 land. The client's `get_historical_cutoff()` lands in this slice (262's status surface and 266 both need it); the remaining `/historical/*` fetch methods land in 266 with their own fixtures.

### Rate limits

- Kalshi replaced its per-second scheme with a **token-bucket model on 2026-04-23**: separate read/write budgets; most requests cost 10 tokens. The lowest authenticated tier (Basic — default for any account holder) refills 200 read tokens/sec ≈ 20 reads/sec. Per-endpoint costs are queryable only via an authenticated account endpoint.
- **The unauthenticated budget is not documented.** Consequence: in public mode the client's operating budget is a conservative local config value, not a documented ceiling. Default: **300 requests/minute (5/sec sustained)**, defined once (see Technical Decisions) and configurable. A `429` carries no penalty per the docs; the client treats it as transient and backs off.
- In authenticated mode the budget is documented (Basic: ~20 reads/sec); the authenticated default runs below that ceiling (see Technical Decisions).

### Authentication mechanism (authenticated tier)

Verified against the API-keys documentation: keys are created in Kalshi account settings and issued as an **RSA private key plus a Key ID** (the private key is shown once and never stored by Kalshi). Each authenticated request carries three headers — `KALSHI-ACCESS-KEY` (key ID), `KALSHI-ACCESS-TIMESTAMP` (milliseconds), `KALSHI-ACCESS-SIGNATURE` — where the signature is **RSA-PSS (SHA-256, MGF1-SHA256, digest-length salt)**, base64-encoded, over the concatenation `timestamp + HTTP method + request path` with **query parameters excluded** from the signed path. The same market-data endpoints serve both modes; responses are identical, so fixtures are mode-independent.

## Value

Architectural enablement: every subsequent 260 slice (262 catalog sync, 263 pass, 264 candles, 265 trades, 266 backfill) consumes this client and this schema. The discovery findings also convert 266 from "conditional" to "confirmed viable," and the fixture set gives the whole initiative real-format test coverage from day one (per the project's parsing rules).

## Technical Scope

**In scope:**
- `ProviderType.KALSHI` + `ProviderProfile` registration (`src/manta_trading/providers/types.py`, `profiles.py`).
- New package `src/manta_trading/data/kalshi/`: async client, Pydantic models, constants (endpoint paths, rate defaults, enums).
- Client methods for: series list, series, events (paged), event, markets (paged), market, market candlesticks, trades (paged), historical cutoff.
- Optional authenticated mode: RSA-PSS request signing when credentials are configured, public mode otherwise; the two modes select different rate budgets.
- Recorded real-response fixtures under `test/fixtures/kalshi/` for every method above, plus representative error responses; a manual recording script.
- `kalshi` migration track (`src/manta_trading/market/schema/migrations/kalshi.py`, registered in `TRACKS`): PostgreSQL schema `kalshi` with catalog tables (`series`, `events`, `markets`) and collection-state tables (`sync_state`, `awaiting_settlement`, `market_candle_state`).
- `--track` option on `mt data migrate apply|status` so the kalshi track can be applied and inspected (default `minute`, preserving current behavior).
- Unit tests (mock transport + fixtures) and integration tests (throwaway DB migration apply/idempotence/teardown).

**Out of scope:** any collection or sync logic (262+), candle/trade *data* tables (264/265 create them alongside their collectors), `/historical/*` fetch methods other than the cutoff (266), pass command and systemd wiring (263), `mt data kalshi status` (262), websocket/orderbook anything.

## Dependencies

### Prerequisites
- Initiative 900 foundation (complete): provider registry, Typer CLI, config, logging.
- Initiative 100 storage (complete): migration framework (`market/schema/runner.py`, `TRACKS`), TimescaleDB host database, role split (`trading_app` DML-only / `trading_migrate` DDL) per slice 913.
- Test infrastructure (complete): `MT_TIMESCALE_TEST_URL` throwaway-database fixtures in `test/conftest.py`.
- External: Kalshi public API reachable for one-time fixture recording.

### Interfaces Required
- `manta_trading.providers.errors` — the existing `ProviderTransientError` / `ProviderPermanentError` taxonomy (reused, not extended).
- `manta_trading.util.ratelimiter.RateLimiter` — the existing sliding-window async limiter.
- `manta_trading.market.schema.runner.apply_migrations` / `list_migration_state` — track-agnostic; no runner changes.

## Architecture

### Component Structure

```
src/manta_trading/data/kalshi/
  __init__.py
  constants.py    # base URL default, endpoint paths, rate-budget default,
                  # MarketStatus (served) / MarketStatusFilter / EventStatusFilter /
                  # CandlePeriod / Surface enums — the single source for every
                  # comparison value in the domain
  models.py       # Pydantic response models (external boundary)
  client.py       # KalshiClient — httpx.AsyncClient + RateLimiter + retry

src/manta_trading/market/schema/migrations/kalshi.py   # KALSHI_MIGRATIONS
src/manta_trading/providers/{types,profiles}.py        # registry additions
test/fixtures/kalshi/*.json                            # recorded real responses
scripts/record_kalshi_fixtures.py                      # manual recorder
```

The client is a thin, honest transport layer: it fetches, validates, types, and raises. Sync strategy, watermarks, and persistence live in later slices — the client never touches the database.

### Data Flow

```
KalshiClient.method()
  → RateLimiter (one instance per client — the shared budget across all
    surfaces the architecture requires; 262–266 all call through one client)
  → httpx.AsyncClient GET (bounded retry on transient failures)
  → error mapping (complete over httpx.HTTPError — see client contract):
    every httpx.TransportError subclass (DNS failure, connection refused,
    TLS failure, connect/read/write/pool timeout, peer disconnect
    mid-response, protocol errors) and HTTP 429/5xx
      → ProviderTransientError (after retries exhausted);
    any other 4xx → ProviderPermanentError
  → Pydantic validation → typed model (validation failure → ProviderPermanentError)
  → caller (paged endpoints: async iterator follows `cursor` until absent/empty)
```

## Technical Decisions

1. **Same `trading` database, new PostgreSQL schema `kalshi`** — not a separate database. *(PM-approved, with direction: the Kalshi domain is expected to grow and eventually separate for zero blast-radius crossover with equities.)* The architecture requires the TimescaleDB *host*; a dedicated PG schema on the existing `trading` database gives table isolation and clean namespacing (Kalshi's natural table names — `series`, `events`, `markets` — would be collision-prone in `public`) while reusing the existing connection URLs, role split, pools, and backup coverage. Hypertable promotion later works identically on non-public schemas. **Extraction discipline (binding on all 260 slices):** the `kalshi` schema must stay fully self-contained — no foreign keys, joins, views, or code paths that cross into `public` tables — so the eventual move to a dedicated database is a schema dump/restore plus a URL change, never an untangling.

2. **Migration IDs are prefixed `kalshi_NNN_*`** — the kalshi track shares the `trading` database's `schema_migrations` ledger with the minute track, so IDs must be globally unique within that database. The track's first entry is the standard `001_schema_migrations` bootstrap (identical SQL to the other tracks): already recorded on the production database (no-op there), and it lets the unchanged runner bootstrap a bare throwaway database in tests.

3. **Reuse, don't invent** — errors: the existing `ProviderTransientError`/`ProviderPermanentError` hierarchy; rate limiting: the existing `util.ratelimiter.RateLimiter`; retry: the bounded exponential-backoff shape already used by `data/adjustment/providers/_http.fetch_with_retry` (reused directly if its signature fits, otherwise mirrored in the client). No new frameworks.

4. **Two rate budgets, each defined once, selected by mode** — `kalshi/constants.py` defines `KALSHI_PUBLIC_RATE_LIMIT = RateLimit(requests_per_minute=300)` (conservative — the unauthenticated budget is undocumented) and `KALSHI_AUTHENTICATED_RATE_LIMIT = RateLimit(requests_per_minute=1000)` (≈83% of the documented Basic-tier read budget of ~20 reads/sec, leaving headroom for token-cost exceptions). The client selects the constant matching its mode; a config override (read at CLI wiring time, 262+) can raise or lower either. No other rate number appears anywhere.

4a. **Authenticated support ships in this slice, as an optional mode** — the PM holds a funded, verified account and authenticated operation is planned near-term, so the signing layer is foundation work, not a retrofit (it lives in the same client file either way). Credentials are `MT_KALSHI_API_KEY_ID` and `MT_KALSHI_PRIVATE_KEY_PATH` (a path to the PEM file — private key material never goes in an env var or the repo). Mode selection is explicit, not a silent fallback: both set → authenticated; neither set → public; **exactly one set → hard error at client construction**. The selected mode is logged at construction. The architecture's constraint stands: the collector must keep working with no credentials configured. Signing uses the `cryptography` package (RSA-PSS/SHA-256) — one new direct dependency, the standard library for this and not otherwise in the tree. The provider profile keeps `AuthType.NONE`: the registry field records what the provider *requires* (market data requires nothing); optional signing is a client capability, and a new `AuthType` member is only warranted if a required-auth surface is ever adopted.

5. **Pydantic models, lenient by policy** — models declare the fields the initiative consumes as required, parse `*_dollars`/`*_fp` strings to `Decimal`, and set `extra="allow"` so new upstream fields never break collection (capture-before-it-disappears beats strictness here; genuinely malformed payloads still fail validation loudly).

6. **Catalog rows keep the raw payload** — `series`/`events`/`markets` rows carry a `raw JSONB` column holding the full API object. Rationale: the initiative's purpose is capture before the data becomes unobtainable; column modeling covers what we query, `raw` preserves what we didn't anticipate. Candles/trades (264/265) will not carry raw — they are fully structured.

7. **Lifecycle status enforced from one enum** — `MarketStatus` StrEnum in `kalshi/constants.py` with the values the API actually *serves* — `initialized`, `active`, `inactive`, `closed`, `determined`, `finalized` (Discovery Findings: the documented five-value vocabulary is the *filter* vocabulary, carried by the separate `MarketStatusFilter` enum); the migration derives the CHECK constraint from `MarketStatus` (the `acquisition_state` precedent). An undocumented new status fails the upsert loudly — correct per fail-explicit; admitting a new value is a one-line enum change plus a small migration.

8. **`mt data migrate --track`** — `mt data migrate apply|status` gain a `--track` option whose choices come from `TRACKS.keys()` (no string scatter), defaulting to `minute` so existing behavior and the 913 credential rules (apply uses `MT_TIMESCALE_MAINTENANCE_URL`, status the app URL) are unchanged. Both tracks target the same database, so no new connection plumbing.

## Implementation Details

### Provider registry

- `ProviderType.KALSHI = "kalshi"` in `types.py`.
- Profile in `profiles.py`: `name="kalshi"`, `base_url="https://external-api.kalshi.com/trade-api/v2"` (documented primary; overridable at client construction — the alternate host is a config change, not a code change), `api_key_env=None`, `auth_type=AuthType.NONE` (records what the provider *requires*; see Technical Decision 4a), `rate_limit=KALSHI_PUBLIC_RATE_LIMIT` with a comment that the unauthenticated tier budget is undocumented and this is our conservative operating budget, not Kalshi's ceiling.

### Client contract (`kalshi/client.py`)

Async methods, all returning validated models; paged endpoints get both a single-page call and an `iter_*` async generator that follows the cursor:

- `get_series_list(...)`, `get_series(series_ticker)`
- `get_events(...)` / `iter_events(...)`, `get_event(event_ticker)`
- `get_markets(...)` / `iter_markets(...)`, `get_market(ticker)`
- `get_market_candlesticks(series_ticker, ticker, start_ts, end_ts, period_interval)`
- `get_trades(...)` / `iter_trades(...)`
- `get_historical_cutoff()`

Filter parameters mirror the documented query parameters (Discovery Findings table) — no invented abstractions over them. The client owns one `httpx.AsyncClient` (lazy, reused; explicit `aclose()`), one `RateLimiter` (budget selected by mode per Technical Decision 4), and a single `httpx.Timeout` constant that sets all four phases explicitly (connect, read, write, pool) — no phase left at library default. Every request passes through the limiter, including retries.

Error classification is **complete over `httpx.HTTPError`**, not status-codes-only (review 261 F004 — connection-level failures on a new outbound I/O path must be enumerated, not implied): the request path catches `httpx.HTTPError` — the common base of `TransportError` and status errors — and re-raises as exactly one of the two provider errors. Every `httpx.TransportError` subclass (`ConnectError`, `ConnectTimeout`, `ReadTimeout`/`ReadError`, `WriteError`, `PoolTimeout`, `RemoteProtocolError`, TLS failures) is **transient**: each is retriable at least once, and if the condition persists, retries exhaust and the raised `ProviderTransientError` carries the underlying cause — surfacing as a nonzero pass exit under 263, per fail-loud/back-off-hard. HTTP 429/5xx are transient; all other 4xx and Pydantic validation failures are permanent. Nothing outside `httpx.HTTPError`/validation is caught — an unexpected exception propagating uncaught is a bug made visible, by design (no broad `except`).

Authenticated mode (Technical Decision 4a): when credentials are configured, every request additionally carries the three `KALSHI-ACCESS-*` headers, with the signature computed per the Discovery Findings mechanism (RSA-PSS/SHA-256 over `timestamp_ms + method + path`, query string excluded from the signed path). The private key loads once at client construction; a missing/unreadable key file or a partial credential pair is a construction-time error, never a runtime surprise. The signature itself (RSA-2048 PSS: measured 0.6 ms mean, 1.1–1.5 ms worst case on idle hardware — code review 261 F001) is computed in a worker thread via `asyncio.to_thread`, never on the event loop, per the project's <1 ms rule for synchronous work inside `async def`.

### Database schema (`kalshi` track)

Migration sequence (IDs indicative; content is the contract):

- `001_schema_migrations` — shared bootstrap (idempotent; already applied in production).
- `kalshi_001_schema` — `CREATE SCHEMA kalshi`; `GRANT USAGE` and DML table grants to `trading_app` (plain GRANTs — roles exist on both production and test clusters via `scripts/provision_roles.sql`; a missing role fails loudly, which is correct).
- `kalshi_002_catalog` — catalog tables:
  - `kalshi.series` — `ticker TEXT PK`, `frequency`, `title`, `category`, `tags JSONB`, `settlement_sources JSONB`, `fee_type`, `fee_multiplier NUMERIC`, `product_metadata JSONB`, `last_updated_ts TIMESTAMPTZ` (Kalshi's), `raw JSONB`, `first_seen_at`/`last_synced_at TIMESTAMPTZ` (ours).
  - `kalshi.events` — `event_ticker TEXT PK`, `series_ticker TEXT NOT NULL REFERENCES kalshi.series`, `title`, `sub_title`, `mutually_exclusive BOOLEAN`, `strike_date TIMESTAMPTZ`, `strike_period`, `collateral_return_type`, `product_metadata JSONB`, `last_updated_ts TIMESTAMPTZ`, `raw JSONB`, `first_seen_at`/`last_synced_at`.
  - `kalshi.markets` — `ticker TEXT PK`, `event_ticker TEXT NOT NULL REFERENCES kalshi.events`, `market_type`, `status TEXT NOT NULL` + CHECK derived from `MarketStatus`, lifecycle timestamps (`created_time`, `open_time`, `close_time`, `latest_expiration_time`, `updated_time` — all Kalshi's, TIMESTAMPTZ), settlement fields (`result`, `expiration_value`, `can_close_early`, `settlement_ts`, `settlement_value_dollars` — names confirmed from live responses), market economics as `NUMERIC` (`notional_value`, `volume`, `open_interest`, last/bid/ask prices), `rules_primary`/`rules_secondary TEXT`, `raw JSONB`, `first_seen_at`/`last_synced_at`.
  - Indexes: `markets(event_ticker)`, `markets(status)`, `markets(close_time)`, `events(series_ticker)`. Exact secondary-index set may be tuned in 262 when query shapes are real.
  - Exact optional-column list is finalized during implementation **from the recorded fixtures**, not from prose docs — required columns above are the contract. Each catalog table's column set equals its Pydantic model's field set plus the three bookkeeping columns (`raw`, `first_seen_at`, `last_synced_at`); an integration test enforces the parity (code review 261 F002) so 262's field→column upsert cannot silently skip either side.
- `kalshi_003_collection_state` — collection-state tables (created now so 262/264 write into a stable schema; row semantics are finalized by their consuming slices):
  - `kalshi.sync_state` — `surface TEXT PK` (values from a `Surface` StrEnum: `catalog`, `candlesticks`, `trades`; CHECK derived), `last_full_sync_at TIMESTAMPTZ`, `watermark_ts TIMESTAMPTZ`, `cursor TEXT`, `updated_at TIMESTAMPTZ NOT NULL`. Per-surface column semantics documented in the migration comment; 262 defines them operationally.
  - `kalshi.awaiting_settlement` — `market_ticker TEXT PK REFERENCES kalshi.markets`, `close_time TIMESTAMPTZ NOT NULL`, `entered_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `last_checked_at TIMESTAMPTZ`. Age (the architecture's mandatory visibility) is computed as `now() - close_time`; the stuck threshold is 262's decision.
  - `kalshi.market_candle_state` — `market_ticker TEXT REFERENCES kalshi.markets`, `period SMALLINT` (CHECK ∈ {1, 60, 1440}), `watermark_ts TIMESTAMPTZ`, `updated_at`, PK `(market_ticker, period)`. Populated by 264.

No hypertables, no candle/trade data tables (264/265), consistent with the slice plan.

Rollback posture: the runner has no down-migration mechanism and this slice does not add one (resist complexity). "Rolls back" is verified on throwaway databases as: apply → verify → `DROP SCHEMA kalshi CASCADE` (throwaway DB only, per the destructive-statement rules) removes everything the track created outside the shared ledger → re-apply from the ledger-cleaned state succeeds. Production never rolls back; it only moves forward.

### Fixtures and recording

- `test/fixtures/kalshi/` — one JSON file per consumed endpoint, recorded from the live API: series list, single series, events page (with cursor), single event, markets page (with cursor; including at least one `settled` market with its `result`), single market, candlesticks (one market, one period), trades page, historical cutoff — plus at least one non-2xx body (e.g. 404 unknown ticker) for error-path tests. Multi-page fixtures include a genuine follow-the-cursor second page so pagination tests exercise the real mechanism.
- `scripts/record_kalshi_fixtures.py` — manual, dev-run recorder that hits the live API through the client's own transport layer and writes the files. Committed fixtures are the test input; the script is rerun only to refresh them.
- Unit tests drive `KalshiClient` through `httpx.MockTransport` serving these fixtures — satisfying the project rule that parsers are tested against the actual production format.

## Integration Points

### Provides to Other Slices
- **262 (Catalog Sync):** the full client surface (series/events/markets iterators, `get_historical_cutoff`), the catalog tables it upserts into (on-ticker idempotency is schema-guaranteed by the PKs), `sync_state` and `awaiting_settlement`, and the `MarketStatus`/`Surface` enums. **Deployment handoff:** the kalshi track is applied on the test cluster only, never on production, by this slice — 262 owns applying it to production before its first run (`mt data migrate apply --track kalshi`; runbook 100 *Update procedure*, and the 262 prerequisite note in the 260 slice plan).
- **263 (Pass):** nothing direct; 263 composes 262's phase.
- **264/265 (Candles/Trades):** `get_market_candlesticks` / `iter_trades` with their fixtures; `market_candle_state`; the batch-candlesticks option noted in Discovery Findings.
- **266 (Backfill):** the gate decision (viable — proceed), the documented `/historical/*` surface, and `get_historical_cutoff()`.

### Consumes from Other Slices
Nothing unreleased — all prerequisites are complete initiatives (900, 100, 913 role split, test infra).

## Success Criteria

1. `ProviderType.KALSHI` registered; `mt provider list` (existing command) shows `kalshi` with `auth: none`; existing provider registry tests extended and passing.
2. `KalshiClient` implements every method in the client contract; each consumed endpoint has a committed real-response fixture; unit tests cover: successful parse of every fixture, cursor pagination across a multi-page fixture pair, transient classification for both status-code and transport failures — 429/5xx *and* mock-transport-raised `httpx.ConnectError`, `ReadTimeout`, and mid-response `ReadError`/`RemoteProtocolError` → `ProviderTransientError` (after bounded retry, original cause attached), other 4xx and malformed payload → `ProviderPermanentError`, and rate-limiter enforcement (N calls take ≥ the window time at the configured budget).
3. Every price/quantity field parses to `Decimal` from the fixed-point string forms — asserted against fixture values.
3a. Authenticated mode: unit tests verify the signature against a generated test key pair (correct signing input including query-string exclusion, correct headers), mode selection (both/neither/exactly-one credential — the last a construction-time error), and budget selection per mode. No test touches the real account credentials.
4. `TRACKS["kalshi"]` exists; `mt data migrate apply --track kalshi` applies it (maintenance credential path unchanged); `mt data migrate status --track kalshi` reports it; `--track` defaults preserve existing minute-track behavior byte-for-byte.
5. Integration test on a throwaway database: kalshi track applies from bare (bootstrap included), re-apply is a no-op, all `kalshi.*` tables/constraints/grants exist, FK and CHECK constraints reject bad rows, and the teardown/re-apply cycle described in the rollback posture passes.
6. Discovery findings recorded (this document) with the 266 gate decision stated.
7. `ruff` and strict `pyright` clean; exactly one new direct dependency (`cryptography`, for RSA-PSS signing) — everything else the slice uses (httpx, pydantic, psycopg) is already present.

## Verification Walkthrough

Executed at the end of Phase 6 (2026-08-24); commands and outputs below are what actually ran. Every database step targets a throwaway database on the test cluster — nothing here touches production.

```bash
# 1. Provider is registered
uv run mt provider list
#    → row: kalshi | kalshi | Kalshi event-contract market data (trade-api/v2) | (no aliases) | ✓
uv run mt provider status --json | python3 -c "import sys,json;print([p for p in json.load(sys.stdin) if p['name']=='kalshi'][0]['auth_type'])"
#    → none

# 2. Unit tests: client vs recorded real responses (no network, no database)
uv run pytest test/unit/data/kalshi -q
#    → 106 passed (constants/enums, models, request core incl. transport-error
#      taxonomy and rate-limit enforcement, endpoint methods, recorded-fixture
#      pass incl. genuine two-page cursor pairs and the 404 body, signing)

# 3. Migration on a throwaway database (never production)
#    MT_TIMESCALE_TEST_URL is the test-cluster admin URL (runbook:
#    user/runbooks/test-database-cluster.md); the reviewed runner copies in
#    only that tier's allowlisted variables.
uv run python scripts/run_tests.py integration -- -k kalshi_migrations -q
#    → 14 passed: bare apply bootstraps the ledger then applies
#      kalshi_001_schema / kalshi_002_catalog / kalshi_003_collection_state;
#      second apply returns []; tables, PKs, FKs, enum-derived CHECKs, indexes
#      and trading_app grants present; nothing references public; bad status /
#      unknown series / unknown period rejected; DROP SCHEMA kalshi CASCADE +
#      ledger cleanup + re-apply succeeds

# 4. Migration track through the CLI, against a throwaway database
#    (point BOTH URLs at a database you created on the test cluster; apply
#    resolves the maintenance URL per 913, status the application URL)
export MT_TIMESCALE_DB_URL=postgresql://trading_test_admin:...@host:5432/mt_walk_xxx
export MT_TIMESCALE_MAINTENANCE_URL=$MT_TIMESCALE_DB_URL
uv run mt data migrate apply --track kalshi
#    → Applied: kalshi_001_schema / kalshi_002_catalog / kalshi_003_collection_state
#      3 migration(s) applied
uv run mt data migrate status --track kalshi
#    → 001_schema_migrations, kalshi_001_schema, kalshi_002_catalog,
#      kalshi_003_collection_state all "applied"; "4 applied, 0 pending"
uv run mt data migrate apply --track kalshi --json      # → {"applied": []}
uv run mt data migrate status                           # → minute track, unchanged
#    (on the throwaway DB: the shared ledger's 4 rows applied, 54 minute
#     migrations pending — the minute view is the same output as before 261)
uv run mt data migrate apply --track bogus
#    → Invalid value for '--track': 'bogus' is not one of 'minute', 'daily', 'kalshi'.

# 5. One live smoke call (manual, optional — proves the real API matches fixtures)
uv run python scripts/record_kalshi_fixtures.py --only historical_cutoff --dry-run
#    → --- historical_cutoff (HTTP 200, 188 bytes)
#      {"market_positions_last_updated_ts":"2026-06-25T00:00:00Z",
#       "market_settled_ts":"2026-06-25T00:00:00Z", ...,
#       "trades_created_ts":"2026-06-25T00:00:00Z"}
```

Caveats discovered during implementation:

- **Type checking:** `pyproject.toml` configures `mypy` as this project's checker (the python-rules "or mypy" alternative); `pyright` is not installed in the environment. The slice was verified with both — `uv run --extra dev mypy` on every touched file, and strict `pyright` (via `npx pyright`, an ad-hoc config with `typeCheckingMode: strict`) on the kalshi package, its tests, the migration module, and the recorder — zero errors on each. Adding a repo-wide `[tool.pyright]` block remains the tracked chore noted in `pyproject.toml`.
- **Running tiers together** (`pytest test/unit test/integration` in one invocation) fails at collection on a pre-existing `from conftest import …` name collision between tiers; run tiers separately (`scripts/run_tests.py <tier>`), as the project already does.
- **Applying the kalshi track to production** (`mt data migrate apply --track kalshi` with the production maintenance URL) was *not* run — it is a PM action. The track is idempotent and additive (new schema only), so it can be applied whenever 262 is ready to write into it.
- The live series list for the recorded fixture uses `category=Health` (98 series, 78 KB) to keep the unpaginated response a sane size; the full unfiltered list is several MB.

There is no `mt data kalshi ...` command yet — that surface starts in 262. What the user can *prove* after this slice: the provider exists, the client speaks the real API (fixtures + optional smoke call), and the schema applies cleanly and idempotently.

## Risk Assessment

- **Unauthenticated rate budget is undocumented.** Mitigated by the conservative single-constant default, 429-as-transient handling, configurability — and by authenticated mode (documented budget) being available from this slice onward. If 429s appear at 5 req/s during unauthenticated fixture recording, lower the public default before merging.
- **Private key is a production secret.** The PEM file lives outside the repo, referenced by path; `.env` (gitignored) holds only the path and key ID. Gitleaks already scans the repo; the recording script and tests never read the real key.
- **Docs vs. reality drift** (the API changed materially three times in 2026). Mitigated by fixture recording being the source of truth for optional columns and model fields — implementation trusts recorded responses over prose documentation, and this document records where each claim came from.

## Implementation Notes

### Development Approach

Suggested order: constants + enums → provider registry (small, unblocks nothing but cheap) → Pydantic models against hand-fetched sample responses → recording script + commit fixtures → client (public mode) + unit tests → signing layer + signing tests → migration track + integration tests → `--track` CLI option → this document's findings cross-checked against what recording actually returned (update Discovery Findings if reality disagrees, and say so).

Branch: `261-slice.kalshi-provider-foundation` from `main` (no integration branch configured), per the git rules.

### Special Considerations
- The recording script performs live external requests — it must respect the same rate limiter and must never run in CI; tests consume only committed fixtures.
- Grants in `kalshi_001_schema` assume the 913 role split exists on the target cluster; the provision script (`scripts/provision_roles.sql`) is the prerequisite, and a missing role is a loud failure by design.
- `updated_time`/`last_updated_ts` fields observed on series, events, and markets are the raw material for 262's incremental sync — the schema stores them from day one so 262 starts with history.
