---
docType: tasks
slice: 141-universe-rebuild-from-eodhd-instruments-schema-migration
project: trading
lld: user/slices/141-slice.universe-rebuild-from-eodhd-instruments-schema-migration.md
dependencies: [131-slice.unified-schema-migration-tracking-across-both-databases]
projectState: >
  Slice 131 (schema migration framework, renumbered from 150) complete. instruments table has ~8k
  AV-seeded rows (NASDAQ/NYSE/NYSE_ARCA/BATS/NYSE_MKT venues). No lifecycle
  columns yet. EODHD bulk endpoints confirmed accessible 2026-04-30.
dateCreated: 20260430
dateUpdated: 20260430
status: complete
---

# Tasks: Slice 141 — Universe Rebuild from EODHD + Instruments Schema Migration

## Context Summary

- Rebuilds the `instruments` registry to ~57.6k symbols from EODHD bulk
  symbol-list endpoints, preserving all AV-seeded venues/canonical_ids.
- Adds lifecycle columns (`first_listing_date`, `first_data_date`,
  `delisted_date`, `eodhd_type`, `eodhd_exchange`, `delisted_at_eodhd`);
  drops `active`. Three new migrations: 015, 016, 017.
- New CLI: `mt data instruments rebuild [--dry-run] [--skip-finnhub]`.
- Finnhub enrichment populates `first_listing_date` and promotes `venue`
  from transient `'US'` to authoritative exchange.
- AV-orphan policy: delete rows EODHD doesn't know, after count+sample gate.
- Slice 142 depends on this completing first (pre-flight checks `eodhd_type
  IS NOT NULL` on every row).
- Key design decisions in LLD: D1–D11.
- **Task ordering note:** consumer code that references `instruments.active`
  must be updated (Task 9) before migration 017 is applied (Task 10), so
  the orchestrator never runs 017 against a codebase still referencing the
  dropped column.

---

## Task 1: Test infrastructure — conftest and fixtures

- [x] 1.1 In `test/unit/universe/`, create `conftest.py` with pytest fixtures:
  - `eodhd_us_response`: list of ≥100 dicts with `Code`, `Name`, `Country`,
    `Exchange`, `Currency`, `Type`; include ≥1 row each of the four kept types
    and ≥1 `Mutual Fund` row (to prove the filter removes it).
  - `eodhd_delisted_response`: small list of delisted symbols.
  - `eodhd_indx_response`: small list, mix of `Country='USA'` and `Country='GBR'`.
  - `finnhub_profile_aapl`: dict `{ipo: '1980-12-12', exchange: 'NASDAQ NMS - GLOBAL MARKET'}`.
  - `finnhub_profile_unknown`: dict `{ipo: '', exchange: ''}`.
- [x] 1.2 In `test/integration/`, confirm `conftest.py` has an
  `instruments_clean_db` fixture that truncates `instruments` before each
  integration test. Add if missing.
- [x] 1.3 Success: `pytest test/unit/universe/ --collect-only` exits 0 with
  no collection errors.

## Task 2: Migration 015 — add lifecycle columns

- [x] 2.1 In `src/manta_trading/market/schema/migrations/minute.py`, add entry
  `015_instruments_lifecycle_columns` with the DDL from D1 in the LLD
  (six `ADD COLUMN IF NOT EXISTS` statements; `delisted_at_eodhd` is
  `BOOLEAN NOT NULL DEFAULT FALSE`; remaining columns nullable).
- [x] 2.2 Apply against dev DB: `mt data migrate --db minute`.
- [x] 2.3 Success: `mt data migrate status --db minute` shows 015 applied;
  `\d instruments` in psql lists all six new columns.

## Task 3: Extend `Instrument` dataclass and `InstrumentRegistry` writes

- [x] 3.1 In `src/manta_trading/data/base/instrument_registry.py`:
  - Add the six new fields to the `Instrument` dataclass (nullable/defaulted).
  - Update `_INSTRUMENT_COLS` to include the new columns.
  - Add `upsert_eodhd_universe(symbols: list[dict]) -> tuple[int, int, int]`
    returning (inserted, updated, unchanged). Uses `ON CONFLICT (canonical_id)
    DO UPDATE SET` for EODHD-sourced fields only (`eodhd_type`,
    `eodhd_exchange`, `delisted_at_eodhd`, `currency`); never overwrites
    `venue`, `trading_calendar_id`, `canonical_id`.
- [x] 3.2 Do NOT remove `active` from `Instrument` or queries yet — that is Task 9.
- [x] 3.3 Test — `test/unit/test_instrument_registry.py` (unit, no DB):
  - [x] 3.3a `Instrument` dataclass instantiates with all six new fields.
  - [x] 3.3b `_INSTRUMENT_COLS` includes all six new columns.
- [x] 3.4 Test — `test/integration/test_instrument_registry.py` (real DB):
  - [x] 3.4a `upsert_eodhd_universe` inserts a new symbol; returns `(1, 0, 0)`.
  - [x] 3.4b Re-running same payload returns `(0, 0, 1)` (idempotent).
  - [x] 3.4c Existing row's `venue` and `canonical_id` are NOT overwritten.
  - [x] 3.4d `get_by_symbol('AAPL')` returns the AAPL row after upsert
    (verifies the operator-facing symbol lookup path, success criterion 7).
- [x] 3.5 Run `ruff check src/manta_trading/data/base/` and
  `pyright --strict src/manta_trading/data/base/`; fix all errors.
- [x] 3.6 Commit: `feat: add lifecycle columns to Instrument dataclass and upsert`

## Task 4: `EodhdType` StrEnum and classification filter

- [x] 4.1 Create `src/manta_trading/data/universe/__init__.py` (empty package marker).
- [x] 4.2 Create `src/manta_trading/data/universe/eodhd_classification.py`:
  - `EodhdType(StrEnum)` with four members: `COMMON_STOCK = 'Common Stock'`,
    `ETF = 'ETF'`, `PREFERRED_STOCK = 'Preferred Stock'`, `INDEX = 'INDEX'`.
    This is the single definition referenced by both the SQL CHECK constraint
    generator and the filter — one definition, two consumers (D3).
  - `filter_v1_universe(rows: list[dict]) -> list[dict]`: keeps rows whose
    `Type` matches an `EodhdType` value; adds `delisted_at_eodhd: bool` from
    each row's `_delisted` key (caller sets this before calling). Raises
    `ValueError` if `rows` is empty.
- [x] 4.3 Test — `test/unit/universe/test_eodhd_classification.py` (pure-function):
  - [x] 4.3a Rows with `Mutual Fund` type are removed by the filter.
  - [x] 4.3b All four `EodhdType` values pass through.
  - [x] 4.3c `delisted_at_eodhd` is `True` for rows marked `_delisted=True`.
  - [x] 4.3d Empty input raises `ValueError`.
- [x] 4.4 Run `ruff check` and `pyright --strict` on this module; fix all errors.
- [x] 4.5 Commit: `feat: add EodhdType StrEnum and v1 universe filter`

## Task 5: `venue_mapping.py` — Finnhub exchange → internal venue

- [x] 5.1 Create `src/manta_trading/data/universe/venue_mapping.py`:
  - `map_finnhub_exchange(exchange: str) -> tuple[str, str]` returning
    `(venue, trading_calendar_id)`. Known mappings (minimum set): NASDAQ
    variations → `('NASDAQ', 'NASDAQ')`; NYSE variations → `('NYSE', 'NYSE')`;
    NYSE ARCA variations → `('NYSE_ARCA', 'NYSE')`; BATS variations →
    `('BATS', 'NYSE')`; NYSE MKT variations → `('NYSE_MKT', 'NYSE')`.
  - Unknown exchange string: log `logger.warning` with the unrecognized value
    and return `('US', 'NYSE')`. No silent fallback to a wrong venue.
- [x] 5.2 Test — `test/unit/universe/test_venue_mapping.py` (pure-function):
  - [x] 5.2a Each of the five known venue groups maps to the correct pair.
  - [x] 5.2b Unknown string returns `('US', 'NYSE')` with a warning logged.
  - [x] 5.2c Empty string returns `('US', 'NYSE')`.
- [x] 5.3 Run `ruff check` and `pyright --strict` on this module; fix all errors.
- [x] 5.4 Commit: `feat: add Finnhub exchange → venue mapping`

## Task 6: `RetryPolicy` module

- [x] 6.1 Create `src/manta_trading/api/http_retry.py`:
  - `RetryPolicy` dataclass with defaults matching D11: connect timeout 10s,
    read timeout 30s, retries 3, backoff `[1, 2, 4]`, retryable status codes
    `{429, 502, 503, 504}`.
  - `build_transport(policy: RetryPolicy) -> httpx.AsyncHTTPTransport`:
    returns an httpx transport configured with the policy. No global state.
- [x] 6.2 Run `ruff check` and `pyright --strict` on this module; fix all errors.
- [x] 6.3 Success: `RetryPolicy()` instantiates with all defaults matching D11.

## Task 7: `EodhdSymbolListClient`

- [x] 7.1 Create `src/manta_trading/data/universe/eodhd_symbol_list_client.py`:
  - `EodhdSymbolListClient(api_key: str, http_policy: RetryPolicy)`.
  - `async preflight() -> None`: GET `/exchange-symbol-list/US`; assert 200 +
    list with `Code` field; raise `EodhdAccessError` on 403 or unexpected
    shape; raise `EodhdSchemaError` on malformed JSON.
  - `async fetch_active_us() -> list[dict]`
  - `async fetch_delisted_us() -> list[dict]`
  - `async fetch_indx() -> list[dict]` (caller post-filters by `Country='USA'`).
  - Define `EodhdAccessError(Exception)` and `EodhdSchemaError(Exception)` in
    this module.
- [x] 7.2 Test — `test/unit/universe/test_eodhd_symbol_list_client.py` (respx):
  - [x] 7.2a Successful fetch returns parsed list.
  - [x] 7.2b 403 on preflight raises `EodhdAccessError`.
  - [x] 7.2c Malformed JSON raises `EodhdSchemaError`.
  - [x] 7.2d 429 is retried per `RetryPolicy`; succeeds on second attempt.
- [x] 7.3 Run `ruff check` and `pyright --strict` on this module; fix all errors.
- [x] 7.4 Commit: `feat: add EodhdSymbolListClient with retry policy`

## Task 8: `finnhub_ipo_client.py` and Finnhub API client

- [x] 8.1 Create `src/manta_trading/api/finnhub/__init__.py` (package marker).
- [x] 8.2 Create `src/manta_trading/api/finnhub/finnhubapi.py`:
  - `FinnhubClient(api_key: str, http_policy: RetryPolicy)`.
  - `async fetch_profile(symbol: str) -> dict | None`: GET
    `/api/v1/stock/profile2?symbol={symbol}&token={key}`. Returns dict if
    `ipo` field is present and non-empty; returns `None` otherwise.
    On 403 raise `FinnhubAccessError`. On 429 retry per policy. After 3
    retries, log warning and return `None` (Finnhub is best-effort, D11).
  - Token-bucket rate limiter at 60/min using an `asyncio.Queue`-based
    token bucket (NOT a semaphore — a semaphore limits concurrency, not
    requests-per-minute). One token-bucket instance per client.
  - Define `FinnhubAccessError(Exception)` in this module.
- [x] 8.3 Create `src/manta_trading/data/universe/finnhub_ipo_client.py`:
  - `FinnhubIpoClient(finnhub_client: FinnhubClient, venue_mapper)`.
  - `async enrich(symbol: str) -> dict | None`: calls
    `finnhub_client.fetch_profile(symbol)`; maps `exchange` via `venue_mapper`;
    returns `{first_listing_date, venue, trading_calendar_id}` or `None`.
  - This thin wrapper keeps orchestrator logic out of the HTTP layer.
- [x] 8.4 Test — `test/unit/api/test_finnhubapi.py` (respx):
  - [x] 8.4a Successful profile with `ipo` returns dict.
  - [x] 8.4b Missing/empty `ipo` returns `None`.
  - [x] 8.4c 403 raises `FinnhubAccessError`.
  - [x] 8.4d 429 retries; success on second attempt returns dict.
  - [x] 8.4e Exhausted retries returns `None` with warning logged.
- [x] 8.5 Test — `test/unit/universe/test_finnhub_ipo_client.py` (pure/mocked):
  - [x] 8.5a `enrich` returns mapped venue when profile available.
  - [x] 8.5b `enrich` returns `None` when `FinnhubClient` returns `None`.
- [x] 8.6 Run `ruff check` and `pyright --strict` on both modules; fix all errors.
- [x] 8.7 Commit: `feat: add Finnhub API client and finnhub_ipo_client`

## Task 9: Remove `instruments.active` from consumers

*Must complete before migrations 016/017 are applied by the orchestrator.*

- [x] 9.1 In `src/manta_trading/data/base/instrument_registry.py`:
  - Remove `active` from `Instrument` dataclass.
  - Remove `active` from `_INSTRUMENT_COLS`.
  - Replace `WHERE active = TRUE` predicates with
    `WHERE delisted_at_eodhd = FALSE AND delisted_date IS NULL`.
  - Update `list_instruments`'s `active_only` param to use the new predicate
    (keep the param for caller compatibility).
- [x] 9.2 In `src/manta_trading/cli/commands/data.py` (instruments list, lines
  ~2095/2118): replace `i.active` with derived `Listed` boolean
  (`NOT delisted_at_eodhd AND delisted_date IS NULL`).
- [x] 9.3 Verify no remaining `instruments.active` references:
  `grep -rn '\.active\b' src/manta_trading/data/ src/manta_trading/market/ src/manta_trading/cli/`
  must return no hits outside AV-legacy modules.
- [x] 9.4 Test — update `test/unit/test_instrument_registry.py` and any
  integration tests referencing `Instrument.active` or `active_only` to use
  the new predicate. All existing tests must still pass.
- [x] 9.5 Run `ruff check src/` and `pyright --strict src/`; fix all errors.
- [x] 9.6 Commit: `refactor: replace instruments.active with eodhd lifecycle columns`

## Task 10: Migrations 016 and 017

*Must follow Task 9 (consumers updated) to ensure 017 does not drop a column
still referenced in code.*

- [x] 10.1 In `src/manta_trading/market/schema/migrations/minute.py`, add:
  - `016_instruments_eodhd_type_not_null`: ADD CHECK constraint on `eodhd_type`
    using values from `EodhdType`; SET NOT NULL on `eodhd_type` and
    `eodhd_exchange`. SQL from D3 in the LLD.
  - `017_instruments_drop_active`: `ALTER TABLE instruments DROP COLUMN IF
    EXISTS active;`. SQL from D2 in the LLD.
- [x] 10.2 These migrations are registered but not yet applied — the orchestrator
  applies them at runtime after the EODHD upsert and orphan delete.
- [x] 10.3 Success: `mt data migrate status --db minute` lists 016 and 017 as
  pending (not yet applied).

## Task 11: Rebuild orchestrator

- [x] 11.1 Create `src/manta_trading/data/universe/rebuild.py` with
  `async run_rebuild(db_url: str, dry_run: bool, skip_finnhub: bool) -> dict`.
  Implement the pipeline from the LLD Data Flow section:
  1. EODHD pre-flight (fatal: `EodhdAccessError` → non-zero exit, no DB mutation).
  2. Finnhub pre-flight (non-fatal: on 403 log warning, set `skip_finnhub=True`,
     continue — per D9).
  3. Apply migration 015 via `runner.apply_migrations`.
  4. Fetch 3 EODHD lists; mark each with `_delisted` flag; filter via
     `filter_v1_universe`.
  5. For each symbol: match by `symbol` against existing rows; build upsert dict
     per D4 rules — existing rows keep `venue`/`canonical_id`; new equity rows
     get `venue='US'`, `canonical_id='{symbol}.US'`; **INDX rows** get
     `venue='INDX'`, `canonical_id='{symbol}.INDX'` (not promoted by Finnhub).
  6. Call `InstrumentRegistry.upsert_eodhd_universe(symbols)`.
  7. Count orphans (`eodhd_type IS NULL`); print count + first-20 sample;
     pause 5s; DELETE orphans.
  8. Apply migrations 016 then 017 via `runner.apply_migrations`.
  9. If not `skip_finnhub`: enrichment loop
     `WHERE first_listing_date IS NULL OR venue = 'US'`; use `FinnhubIpoClient`
     to update `first_listing_date`, `venue`, `trading_calendar_id`,
     `canonical_id`; rate-limited at 60/min.
  10. Return summary dict: `{inserted, updated, unchanged, orphans_deleted,
      finnhub_populated, finnhub_not_found, finnhub_errors}`.
  - `--dry-run`: compute and print counts; skip all DB mutations and migrations
    016/017.
  - Finnhub 403 mid-run: log warning; return with EODHD half committed (D6).
- [x] 11.2 Test — `test/integration/test_rebuild_orchestrator.py` (EODHD/Finnhub
  responses from recorded fixtures; containerized empty `instruments` table):
  - [x] 11.2a Row counts within 5% of probe values; `eodhd_type IS NULL` = 0.
  - [x] 11.2b Re-run is no-op: `inserted=0`, `updated=0`.
  - [x] 11.2c AV-seeded (symbol, venue, canonical_id) tuples unchanged (snapshot
    diff of AAPL/MSFT/GE/JPM before and after).
  - [x] 11.2d `--dry-run` leaves `instruments` row count at pre-run value.
  - [x] 11.2e Migrations 015, 016, 017 applied in order after a full run.
  - [x] 11.2f EODHD 403 pre-flight exits non-zero; DB row count unchanged.
  - [x] 11.2g Finnhub 403 does NOT halt; EODHD upsert + migrations 015/016/017
    complete; exit code 0.
  - [x] 11.2h INDX-source rows have `venue='INDX'`, `canonical_id='{sym}.INDX'`;
    not included in the Finnhub enrichment loop.
  - [x] 11.2i Symbol present on run 1, absent from EODHD fixture on run 2: row
    is deleted as orphan on run 2 (simulates success criterion 11).
  - [x] 11.2j `--json` flag emits valid JSON with canonical summary keys
    (`inserted`, `updated`, `unchanged`, `orphans_deleted`, `finnhub_populated`,
    `finnhub_not_found`, `finnhub_errors`).
- [x] 11.3 Run `ruff check` and `pyright --strict` on `rebuild.py`; fix all errors.
- [x] 11.4 Commit: `feat: add rebuild orchestrator with migration sequencing`

## Task 12: CLI surface — `mt data instruments rebuild`

- [x] 12.1 In `src/manta_trading/cli/commands/data.py`, add `instruments rebuild`
  subcommand with `--dry-run`, `--skip-finnhub`, `--json` per the CLI spec
  in the LLD.
- [x] 12.2 Wire to `run_rebuild()`; convert `EodhdAccessError` to typer exit
  code 1; print Rich table by default, JSON if `--json`.
- [x] 12.3 Smoke test against dev DB:
  - `mt data instruments rebuild --dry-run` — confirm no DB mutation.
  - `mt data instruments rebuild --skip-finnhub` — confirm exit 0.
  - `mt data instruments rebuild --dry-run --json` — confirm output is valid JSON.
- [x] 12.4 Commit: `feat: add mt data instruments rebuild CLI command`

## Task 13: Verification walkthrough

- [x] 13.1 Run the end-to-end demo script from the LLD (steps 0–9), capturing
  all `# Expect:` outputs.
- [x] 13.2 Confirm each expectation is met:
  - 015/016/017 applied after `rebuild --skip-finnhub`.
  - `SELECT COUNT(*) FROM instruments WHERE eodhd_type IS NULL` = 0.
  - AAPL/MSFT/GE/JPM venues and canonical_ids unchanged from AV baseline.
  - `rebuild --skip-finnhub` re-run: `inserted=0, updated=0`.
  - After 90s smoke Finnhub run: AAPL → `first_listing_date=1980-12-12`,
    `venue=NASDAQ`, `canonical_id=AAPL.NASDAQ`.
  - `active` column absent from `\d instruments` output.
  - Bad EODHD key: exit non-zero, DB unchanged.
  - Bad Finnhub key: warning logged, EODHD upsert + migrations complete, exit 0.
- [x] 13.3 Record residual `venue='US'` count:
  `SELECT COUNT(*) FROM instruments WHERE venue='US'`; confirm small minority
  vs authoritative-venue rows.
- [x] 13.4 Final commit: `docs(141): add verification walkthrough results`
