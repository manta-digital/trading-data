---
docType: tasks
slice: kalshi-provider-foundation
project: trading-data
lld: user/slices/261-slice.kalshi-provider-foundation.md
dependencies: []
projectState: >
  Initiatives 900 (CLI/config/logging/provider registry), 100 (storage +
  migration framework), 913 (role split), and 916 (supervised production
  form) are complete. No Kalshi code exists. Slice design 261 is approved
  with review findings folded in (a03fd71). This is the first slice of
  initiative 260; slices 262-266 build on its client and schema.
dateCreated: 20260824
dateUpdated: 20260824
status: in_progress
---

## Context Summary

- Working on **261 Kalshi Provider Foundation** — provider registration,
  `trade-api/v2` API client (public + optional authenticated signing), and
  the `kalshi` migration track (catalog + collection-state tables).
- Source of truth: the slice design at `user/slices/261-slice.kalshi-provider-foundation.md`.
  Its **Discovery Findings** section holds the verified endpoint surface,
  pagination parameters, auth mechanism, and rate-limit facts — tasks below
  reference it rather than restating endpoint details.
- Key patterns to reuse (do not reinvent): `manta_trading.providers.errors`
  (transient/permanent taxonomy), `manta_trading.util.ratelimiter.RateLimiter`,
  the bounded-retry shape in `data/adjustment/providers/_http.fetch_with_retry`,
  the migration runner in `market/schema/runner.py`, and the throwaway-DB
  fixtures in `test/conftest.py` (`MT_TIMESCALE_TEST_URL`).
- Storage decision: PostgreSQL schema `kalshi` on the shared `trading`
  database; migration IDs prefixed `kalshi_NNN_*` because the ledger is
  shared with the minute track. The `kalshi` schema must never reference
  `public` tables (extraction discipline, design Technical Decision 1).
- No collection logic in this slice. Next slice: 262 Catalog Sync with
  Settlement Capture.
- Branch per CLAUDE.md git rules: `261-slice.kalshi-provider-foundation`
  from `main`. Commit checkpoints are marked below; commit messages use
  semantic prefixes.

## Section 1: Package scaffolding, constants, and enums

- [x] **Task 1.1: Add the `cryptography` dependency** (effort: 1)
  - [x] Add `cryptography` to `[project.dependencies]` in `pyproject.toml`
        (needed for RSA-PSS request signing in Task 7.1; the only new direct
        dependency this slice is allowed — design Success Criterion 7).
  - [x] Run `uv sync` (or `uv lock` + sync) so `uv.lock` updates.
  - [x] Success: `uv run python -c "from cryptography.hazmat.primitives.asymmetric import padding"`
        exits 0; existing test suite still passes.

- [x] **Task 1.2: Create `src/manta_trading/data/kalshi/` package with `constants.py`** (effort: 2)
  - [x] Create `__init__.py` and `constants.py`. Every comparison value and
        magic number for the Kalshi domain lives here and only here:
  - [x] Base URL default `https://external-api.kalshi.com/trade-api/v2`
        (design: Discovery Findings, Base URLs).
  - [x] Endpoint path constants for every consumed endpoint (series list,
        series, events, event, markets, market, market candlesticks, trades,
        historical cutoff) — paths exactly as in the design's endpoint table.
  - [x] `KALSHI_PUBLIC_RATE_LIMIT = RateLimit(requests_per_minute=300)` and
        `KALSHI_AUTHENTICATED_RATE_LIMIT = RateLimit(requests_per_minute=1000)`
        (reuse the `RateLimit` dataclass from `providers/types.py`), each with
        a comment stating its provenance per design Technical Decision 4.
  - [x] A single request-timeout constant specified as an `httpx.Timeout`
        with all four phases (connect, read, write, pool) set explicitly —
        no phase left at library default (design: client contract).
  - [x] Env var name constants `MT_KALSHI_API_KEY_ID` and
        `MT_KALSHI_PRIVATE_KEY_PATH` (used in Task 7.1).
  - [x] `StrEnum`s: `MarketStatus` (unopened, open, paused, closed, settled),
        `CandlePeriod` (1, 60, 1440 — IntEnum acceptable given numeric API
        values), `Surface` (catalog, candlesticks, trades). Docstrings note
        that SQL CHECK constraints in the migration track derive from these
        (the `acquisition_state` precedent in `data/acquisition/state.py`).
  - [x] Success: module imports cleanly; `ruff` and `pyright` pass on it.

- [x] **Task 1.3: Unit tests for constants and enums** (effort: 1)
  - [x] New `test/unit/data/kalshi/test_constants.py`: enum members and string
        values match the design; both rate-limit constants exist with the
        designed values; timeout constant sets all four phases.
  - [x] Success: tests pass; no other module defines a Kalshi rate number,
        endpoint path, or status string (grep check recorded in the test or
        done manually).
  - [x] **Commit checkpoint**: `feat: add kalshi package constants and enums`.

## Section 2: Provider registry

- [x] **Task 2.1: Register `ProviderType.KALSHI`** (effort: 1)
  - [x] Add `KALSHI = "kalshi"` to `ProviderType` in
        `src/manta_trading/providers/types.py`.
  - [x] Success: enum member present; imports unchanged elsewhere.

- [x] **Task 2.2: Add the `kalshi` provider profile** (effort: 1)
  - [x] In `src/manta_trading/providers/profiles.py` add a `ProviderProfile`:
        `name="kalshi"`, `provider_type=ProviderType.KALSHI`, `base_url` and
        `rate_limit` referencing the Task 1.2 constants (public budget),
        `api_key_env=None`, `auth_type=AuthType.NONE`, short description.
  - [x] Comment on the profile: `AuthType.NONE` records what the provider
        *requires* (market data is public); optional signed mode is a client
        capability — design Technical Decision 4a.
  - [x] Success: `get_profile("kalshi")` returns the profile;
        `resolve_alias("kalshi")` passes through.

- [x] **Task 2.3: Extend provider registry tests** (effort: 1)
  - [x] Extend `test/unit/test_provider_types.py` and
        `test_provider_profiles.py`: KALSHI member exists; profile fields as
        designed (auth none, no api_key_env, rate limit is the shared
        constant object from `kalshi/constants.py`, not a copied number).
  - [x] Success: registry tests pass; `mt provider list` shows `kalshi`
        with auth `none` (manual check or CLI test).
  - [x] **Commit checkpoint**: `feat: register kalshi provider type and profile`.

## Section 3: Pydantic response models

- [x] **Task 3.1: Implement `models.py`** (effort: 3)
  - [x] Create `src/manta_trading/data/kalshi/models.py` with Pydantic models
        for: `Series`, `Event`, `Market`, `Trade`, `Candlestick` (with its
        nested yes_bid/yes_ask/price OHLC objects), `HistoricalCutoff`, and
        the page/list response wrappers (series list, events+cursor,
        markets+cursor, trades+cursor, candlesticks).
  - [x] Required fields = the columns the design's schema section names as
        the contract; everything else optional. `model_config` sets
        `extra="allow"` (design Technical Decision 5 — lenient by policy).
  - [x] All `*_dollars` / `*_fp` fixed-point string fields parse to
        `Decimal`; nullable price fields tolerated (candlesticks).
  - [x] Timestamps: ISO-8601 datetimes and Unix-seconds ints parse to aware
        `datetime` as served per endpoint — match the documented field types
        in the design's endpoint table, do not guess.
  - [x] Success: models import cleanly; `pyright` strict passes.

- [x] **Task 3.2: Model unit tests with hand-fetched samples** (effort: 2)
  - [x] New `test/unit/data/kalshi/test_models.py` using small inline sample
        payloads hand-fetched from the live API (a browser/curl grab is fine
        at this stage; full recorded fixtures arrive in Section 6 and the
        fixture-driven pass in Task 6.3 supersedes these samples as
        coverage).
  - [x] Cover: successful parse per model; `Decimal` values asserted exactly
        against the sample strings; unknown extra field tolerated; missing
        required field raises `ValidationError`.
  - [x] Success: tests pass.
  - [x] **Commit checkpoint**: `feat: add kalshi API response models`.

## Section 4: Client — core request path (public mode)

- [x] **Task 4.1: `KalshiClient` construction and request core** (effort: 3)
  - [x] Create `src/manta_trading/data/kalshi/client.py`: constructor takes
        base URL (default from constants), a `RateLimit`, and optional
        credentials (wired in Section 7; unused-for-now parameters may be
        omitted until then). Owns one lazy reused `httpx.AsyncClient`
        (explicit `aclose()`), one `RateLimiter` built from the given
        budget, the Task 1.2 timeout.
  - [x] `_request` core: rate-limiter acquire → GET → classify. Error
        classification **complete over `httpx.HTTPError`** per the design's
        client contract (revised for review 261 finding F004 — connection-
        level failures must be enumerated, not implied): every
        `httpx.TransportError` subclass and HTTP 429/5xx →
        `ProviderTransientError` after bounded retry (exponential backoff,
        small fixed attempt count, per the `fetch_with_retry` shape — reuse
        it if the signature fits); other 4xx → `ProviderPermanentError`;
        Pydantic `ValidationError` → `ProviderPermanentError`. Raised errors
        carry the underlying cause (`raise ... from exc`). Nothing else is
        caught.
  - [x] Retries pass through the rate limiter too (design: Data Flow).
  - [x] Success: module passes `ruff`/`pyright`; no bare or broad excepts
        (ruff BLE clean).

- [x] **Task 4.2: Request-core unit tests** (effort: 3)
  - [x] New `test/unit/data/kalshi/test_client_core.py` driving `_request`
        via `httpx.MockTransport`:
  - [x] `ConnectError`, `ReadTimeout`, and mid-response `ReadError` /
        `RemoteProtocolError` each → `ProviderTransientError` with cause
        attached, after the bounded retry count (assert attempt count).
  - [x] 429 and 503 → retried then `ProviderTransientError`; transient that
        succeeds on retry returns normally.
  - [x] 404 → `ProviderPermanentError`, no retry. Malformed JSON body →
        `ProviderPermanentError`.
  - [x] Rate-limiter enforcement: N+1 calls at budget N/period take ≥ period
        (pattern from `test/unit/util/testratelimiter.py`).
  - [x] Success: all pass; failure-path tests assert exception *type and
        cause*, not message text.
  - [x] **Commit checkpoint**: `feat: add kalshi client request core with error taxonomy`.

## Section 5: Client — endpoint methods

Separate sub-tasks per endpoint group; each implements the documented query
parameters from the design's endpoint table verbatim (no invented
abstractions). Paged endpoints get a single-page method plus an `iter_*`
async generator following `cursor` until absent/empty. Test-with applies
*within* this section: each endpoint task carries its own tests against the
shared harness and its own success line, so it is independently completable
(restructured per tasks-review finding F003 — verification was previously
batched into one trailing test task).

- [x] **Task 5.0: Shared endpoint test harness** (effort: 1)
  - [x] Create `test/unit/data/kalshi/test_client_endpoints.py` scaffolding:
        a helper building a `KalshiClient` over `httpx.MockTransport` from a
        path→payload route map (the Section 3 inline samples) that records
        each outgoing request for assertion.
  - [x] Success: harness imports; one smoke test through any route passes.
- [x] **Task 5.1: Series methods + tests** (effort: 1)
  - [x] Implement `get_series_list(...)` (filters: category, tags,
        min_updated_ts, include flags — no pagination) and
        `get_series(series_ticker)`.
  - [x] Tests: correct path and query string (captured request); responses
        parse to the series models.
  - [x] Success: tests pass; `ruff`/`pyright` clean.
- [x] **Task 5.2: Event methods + tests** (effort: 2)
  - [x] Implement `get_events(...)` + `iter_events(...)` (status,
        series_ticker, tickers, min_close_ts, min_updated_ts,
        with_nested_markets, limit, cursor) and `get_event(event_ticker)`.
  - [x] Tests: path/query assertions; `iter_events` follows a two-page
        cursor sequence and terminates.
  - [x] Success: tests pass; `ruff`/`pyright` clean.
- [x] **Task 5.3: Market methods + tests** (effort: 2)
  - [x] Implement `get_markets(...)` + `iter_markets(...)` (all documented
        filters, including the timestamp-range pairs and mve_filter) and
        `get_market(ticker)`.
  - [x] Tests: path/query assertions including at least one timestamp-range
        filter; `iter_markets` two-page cursor test.
  - [x] Success: tests pass; `ruff`/`pyright` clean.
- [x] **Task 5.4: Candlestick method + tests** (effort: 1)
  - [x] Implement `get_market_candlesticks(series_ticker, ticker, start_ts,
        end_ts, period_interval, include_latest_before_start=False)`;
        `period_interval` typed as `CandlePeriod`.
  - [x] Tests: path contains both tickers; required query parameters
        present; response parses to the candlestick models.
  - [x] Success: tests pass; `ruff`/`pyright` clean.
- [x] **Task 5.5: Trades methods + tests** (effort: 1)
  - [x] Implement `get_trades(...)` + `iter_trades(...)` (ticker, min_ts,
        max_ts, is_block_trade, limit, cursor).
  - [x] Tests: path/query assertions; `iter_trades` two-page cursor test.
  - [x] Success: tests pass; `ruff`/`pyright` clean.
- [x] **Task 5.6: Historical cutoff method + tests** (effort: 1)
  - [x] Implement `get_historical_cutoff()` → `HistoricalCutoff` model. No
        other `/historical/*` methods (they belong to slice 266).
  - [x] Tests: path assertion; response parses to `HistoricalCutoff`.
  - [x] Success: tests pass; every public client method now has at least
        one test; `ruff`/`pyright` clean.
  - [x] **Commit checkpoint**: `feat: add kalshi client endpoint methods`.

## Section 6: Recorded real-response fixtures

- [x] **Task 6.1: Fixture recording script** (effort: 2)
  - [x] Create `scripts/record_kalshi_fixtures.py`: drives `KalshiClient`
        methods against the live API (public mode) and writes raw response
        JSON to `test/fixtures/kalshi/`. Capture raw bodies via an httpx
        `event_hooks` response hook or a recording transport wrapper so what
        is written is the wire payload, not a model re-serialization.
  - [x] Supports `--only <name>` and `--dry-run` (print, don't write) —
        the design's verification walkthrough uses
        `--only historical_cutoff --dry-run` as the live smoke check.
  - [x] Respects the client's rate limiter; never runs in CI; module
        docstring states both.
  - [x] Success: `--dry-run` against the live API prints responses without
        writing; script passes lint/type checks.

- [x] **Task 6.2: Record and commit the fixture set** (effort: 2)
  - [x] Run the script; commit fixtures for every consumed endpoint per the
        design's fixtures list: series list, single series, events page,
        single event, markets page, single market, candlesticks (one market,
        one period), trades page, historical cutoff.
  - [x] Multi-page pairs with a **genuine** follow-the-cursor second page for
        events, markets, and trades.
  - [x] At least one settled market fixture including its `result`; at least
        one non-2xx body (e.g. 404 unknown ticker) for error-path tests.
  - [x] Success: files exist under `test/fixtures/kalshi/`; each is valid
        JSON; no credentials or personal data in any fixture.
  - [x] **Commit checkpoint**: `test: add recorded kalshi API fixtures`.

- [x] **Task 6.3: Fixture-driven test pass and discovery cross-check** (effort: 3)
  - [x] `test/unit/data/kalshi/test_fixtures.py`: every committed fixture
        parses through its model via the client (MockTransport serving the
        file); `Decimal` fields asserted against fixture strings; pagination
        test re-pointed at the real two-page fixture pairs; 404 fixture →
        `ProviderPermanentError`.
  - [x] Cross-check the design's Discovery Findings against what recording
        actually returned (field names, pagination behavior, cutoff response
        shape). If reality disagrees, update the design's Discovery Findings
        section and say so in the commit message; finalize any
        optional-column choices Task 8.2 needs.
  - [x] Success: all tests pass; design doc updated or confirmed unchanged.
  - [x] **Commit checkpoint**: `test: fixture-driven kalshi client coverage`.

## Section 7: Authenticated signing (optional mode)

- [x] **Task 7.1: Signing layer and mode selection** (effort: 3)
  - [x] Per the design's authentication mechanism (Discovery Findings):
        sign `timestamp_ms + method + path` — **query string excluded** —
        with RSA-PSS (SHA-256, MGF1-SHA256, digest-length salt), base64;
        send `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` (ms),
        `KALSHI-ACCESS-SIGNATURE` on every request when in authenticated
        mode.
  - [x] Credentials from the Task 1.2 env var constants: key ID +
        private-key PEM *path*. PEM loads once at client construction.
  - [x] Mode selection is explicit (design Technical Decision 4a): both set
        → authenticated; neither → public; exactly one → raise at
        construction. Missing/unreadable PEM file → raise at construction.
        Selected mode logged at construction.
  - [x] Budget selection by mode: authenticated mode uses
        `KALSHI_AUTHENTICATED_RATE_LIMIT`, public uses
        `KALSHI_PUBLIC_RATE_LIMIT`, unless an explicit budget was passed.
  - [x] Success: lint/type clean; public-mode behavior byte-identical to
        Sections 4–6 (existing tests untouched and passing).

- [x] **Task 7.2: Signing unit tests** (effort: 2)
  - [x] `test/unit/data/kalshi/test_client_auth.py` with a keypair
        *generated in the test* (never the real credentials):
  - [x] Signature verifies with the public key over the exact signed string;
        request with query parameters signs the bare path.
  - [x] Headers present and well-formed in authenticated mode; absent in
        public mode.
  - [x] Mode selection: both/neither/exactly-one (error) /missing-PEM-file
        (error); budget constant selected per mode.
  - [x] Success: all pass.
  - [x] **Commit checkpoint**: `feat: add optional kalshi authenticated signing`.

## Section 8: `kalshi` migration track

Consult `ai-project-guide/tool-guides/timescaledb/` if any TimescaleDB
behavior question arises; this slice creates plain relational tables only.

- [ ] **Task 8.1: Track module and schema migration** (effort: 2)
  - [ ] Create `src/manta_trading/market/schema/migrations/kalshi.py` with
        `KALSHI_MIGRATIONS`, entries in the existing dict shape.
  - [ ] Entry 1: `001_schema_migrations` — bootstrap SQL identical to the
        other tracks (idempotent; already recorded on production, needed for
        bare throwaway DBs).
  - [ ] Entry 2: `kalshi_001_schema` — `CREATE SCHEMA kalshi`; `GRANT USAGE
        ON SCHEMA kalshi TO trading_app` plus DML grants pattern for tables
        (plain GRANTs — a missing role fails loudly by design).
  - [ ] Success: module imports; SQL reviewed against the design's schema
        section.

- [ ] **Task 8.2: Catalog tables migration** (effort: 3)
  - [ ] Entry `kalshi_002_catalog`: `kalshi.series`, `kalshi.events`,
        `kalshi.markets` exactly per the design's Database schema section —
        PKs on tickers, FKs series←events←markets, `status` CHECK derived
        from `MarketStatus` (values interpolated from the enum, the
        `state.py`/`minute.py` precedent), `raw JSONB` + `first_seen_at` /
        `last_synced_at` on all three, `NUMERIC` for money/quantities,
        `TIMESTAMPTZ` throughout.
  - [ ] Optional columns per the Task 6.3 fixture findings; required columns
        are the design's contract.
  - [ ] Indexes: `markets(event_ticker)`, `markets(status)`,
        `markets(close_time)`, `events(series_ticker)`.
  - [ ] No FK, view, or reference to any `public` table (extraction
        discipline — design Technical Decision 1).
  - [ ] Success: the track so far applies on a throwaway database without
        error (invoke `apply_migrations` directly against the test-cluster
        fixture); catalog queries (`information_schema` / `pg_catalog`)
        show all three tables with the designed PKs, FKs, status CHECK,
        and indexes; no `kalshi.*` object references a `public` object.
- [ ] **Task 8.3: Collection-state tables migration** (effort: 2)
  - [ ] Entry `kalshi_003_collection_state`: `kalshi.sync_state` (PK
        `surface` with CHECK from `Surface`), `kalshi.awaiting_settlement`
        (PK `market_ticker` FK→markets, `close_time` NOT NULL, `entered_at`
        default now(), `last_checked_at`), `kalshi.market_candle_state`
        (PK `(market_ticker, period)`, FK→markets, period CHECK from
        `CandlePeriod`, `watermark_ts`) — columns per the design.
  - [ ] Migration comments document per-surface column semantics (finalized
        operationally by 262).
  - [ ] Success: full track applies on a throwaway database without error;
        catalog queries show the three state tables with the designed PKs,
        FKs to `kalshi.markets`, and the `surface`/`period` CHECK
        constraints deriving their value lists from the Task 1.2 enums.
- [ ] **Task 8.4: Register the track** (effort: 1)
  - [ ] Add `"kalshi": KALSHI_MIGRATIONS` to `TRACKS` in
        `market/schema/migrations/__init__.py`; update `__all__`.
  - [ ] Success: `TRACKS["kalshi"]` importable; existing tracks unchanged.

- [ ] **Task 8.5: Migration integration tests (throwaway DB)** (effort: 3)
  - [ ] New `test/integration/test_kalshi_migrations.py` using the existing
        throwaway-database fixtures (`MT_TIMESCALE_TEST_URL`; tests never
        read the production URL — guard tests enforce this).
  - [ ] Bare database: track applies including bootstrap; second apply is a
        no-op (returns empty list).
  - [ ] All `kalshi.*` tables, PKs, FKs, CHECKs, indexes, and `trading_app`
        grants exist (query catalogs).
  - [ ] Constraint rejection: market row with unknown status value fails
        CHECK; event row with unknown series fails FK.
  - [ ] Teardown/re-apply cycle per the design's rollback posture:
        `DROP SCHEMA kalshi CASCADE` on the throwaway DB removes everything
        the track created outside the ledger; after deleting the track's
        ledger rows, re-apply succeeds.
  - [ ] Success: integration tests pass against the test cluster.
  - [ ] **Commit checkpoint**: `feat: add kalshi migration track with catalog and state tables`.

## Section 9: CLI `--track` option

- [ ] **Task 9.1: Add `--track` to `mt data migrate apply|status`** (effort: 2)
  - [ ] In `cli/commands/data.py`: `--track` option on both commands,
        choices sourced from `TRACKS.keys()` (no string literals), default
        `"minute"` (defined once, e.g. alongside `TRACKS`).
  - [ ] Selected track's migration list is passed to the existing runner;
        credential behavior unchanged (apply → maintenance URL per 913,
        status → application URL).
  - [ ] Success: `mt data migrate status` output for the default is
        byte-identical to before; `--track kalshi` reports the kalshi track.
- [ ] **Task 9.2: CLI tests** (effort: 2)
  - [ ] Extend `test/unit/test_cli_data.py`: default-track behavior
        unchanged; `--track kalshi` selects `TRACKS["kalshi"]` (assert on
        the migration list handed to the mocked runner); invalid track name
        rejected with the available choices listed.
  - [ ] Success: CLI tests pass.
  - [ ] **Commit checkpoint**: `feat: add --track option to mt data migrate`.

## Section 10: Final validation and walkthrough

- [ ] **Task 10.1: Full-suite validation** (effort: 1)
  - [ ] `uv run pytest` (unit + integration) green; `ruff check` clean;
        `pyright` strict zero errors (src and tests).
  - [ ] Success criteria list in the design (items 1–7 incl. 3a) each
        verified and noted.
- [ ] **Task 10.2: Execute the verification walkthrough** (effort: 1)
  - [ ] Run the design's Verification Walkthrough steps 1–5 end to end
        (provider list, unit tests, throwaway-DB integration run,
        `mt data migrate status --track kalshi`, live cutoff smoke via
        `--only historical_cutoff --dry-run`).
  - [ ] Refine the walkthrough section in the design doc to match actual
        commands/output (it is a draft until Phase 6 completes).
- [ ] **Task 10.3: Close out** (effort: 1)
  - [ ] Delegate checklist updates to the task-checker agent; ensure all
        completed tasks are checked.
  - [ ] Success summary kept concise; **final commit** of any doc updates:
        `docs: refine 261 walkthrough post-implementation`.
