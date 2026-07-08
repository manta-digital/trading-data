---
docType: tasks
slice: instrument-registry-integration
project: trading
lld: user/slices/103-slice.instrument-registry-integration.md
dependencies: [102]
projectState: Slice 102 (schema) is complete. instruments, provider_symbol_mapping tables exist on TimescaleDB. migration runner is functional. InstrumentRegistry exists as a stub with NotImplementedError on all DB methods and a broken @lru_cache pattern. Settings provides timescale_db_url and market_db_url.
dateCreated: 20260403
dateUpdated: 20260403
status: complete
---

## Context Summary
- Working on slice 103: Instrument Registry Integration
- Slice 102 is complete: `instruments` and `provider_symbol_mapping` tables exist, migrations applied
- Slice 100 is complete: psycopg3 patterns, `Settings.timescale_db_url` and `market_db_url` available
- `InstrumentRegistry` at `src/manta_trading/data/base/instrument_registry.py` is a stub — all methods raise `NotImplementedError`; has `@lru_cache` bug on instance methods
- This slice rewrites the registry with psycopg3, fixes caching, adds seed module and CLI
- Next planned slices: 104 (Trading Calendar Integration), 105 (Tick Event Hypertable Schema)

---

## Tasks

### Task 1: Rewrite `InstrumentRegistry` with psycopg3 and per-instance cache

- [x] **Rewrite `src/manta_trading/data/base/instrument_registry.py`**
  - [x] Keep `Instrument` dataclass exactly as-is (no changes)
  - [x] Remove `@lru_cache` and `functools` imports from the module
  - [x] Rewrite `InstrumentRegistry.__init__(self, conninfo: str)` to create `psycopg_pool.ConnectionPool(conninfo, min_size=1, max_size=5)` and initialize `self._cache: dict[str, Instrument] = {}`
  - [x] Add `close(self) -> None` method that calls `self._pool.close()`
  - [x] Add `_invalidate_cache(self) -> None` that calls `self._cache.clear()`
  - [x] Add private `_row_to_instrument(self, row: dict) -> Instrument` that maps DB row keys to `Instrument` dataclass fields
  - [x] All public methods use `with self._pool.connection() as conn:` and `conn.cursor(row_factory=dict_row)` pattern
  - [x] All SQL uses `%s` parameterized placeholders — no f-string SQL
  - [x] Success: module imports without error; `InstrumentRegistry("postgresql://x")` does not raise on construction (pool creation may fail if URL is unreachable, which is acceptable)

### Task 2: Implement lookup methods on `InstrumentRegistry`

- [x] **Implement `get_by_symbol`, `get_by_canonical_id`, `get_by_provider_symbol`**
  - [x] `get_by_symbol(self, symbol: str) -> Instrument | None`
    - [x] Cache key: `f"symbol:{symbol}"`
    - [x] SQL: `SELECT ... FROM instruments WHERE symbol = %s AND active = TRUE LIMIT 1`
    - [x] Return `None` if no row found; cache hit returns immediately
  - [x] `get_by_canonical_id(self, canonical_id: str) -> Instrument | None`
    - [x] Cache key: `f"canonical:{canonical_id}"`
    - [x] SQL: `SELECT ... FROM instruments WHERE canonical_id = %s`
  - [x] `get_by_provider_symbol(self, provider: str, provider_symbol: str, as_of_date: date | None = None) -> Instrument | None`
    - [x] Cache key: `f"provider:{provider}:{provider_symbol}:{as_of_date}"`
    - [x] Use `date.today()` when `as_of_date` is None; pass actual date value as parameter
    - [x] SQL: JOIN `instruments i` and `provider_symbol_mapping psm` with `valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)`; see slice design for full query
  - [x] All three methods check cache first, query DB on miss, cache result, return `Instrument | None`
  - [x] Success: methods are callable; return `None` on miss without raising

### Task 3: Unit tests for lookup methods

- [x] **Create/rewrite `test/unit/data/base/test_instrument_registry.py`**
  - [x] Keep `TestInstrument` class for dataclass tests (already passing)
  - [x] Replace `TestInstrumentRegistry` stub tests with tests for actual behavior using `unittest.mock.MagicMock` to mock `psycopg_pool.ConnectionPool`
  - [x] Test `get_by_symbol` returns `Instrument` when DB row present
  - [x] Test `get_by_symbol` returns `None` when no row found
  - [x] Test `get_by_symbol` uses cache on second call (DB query called once, not twice)
  - [x] Test `get_by_canonical_id` returns `Instrument` on hit, `None` on miss
  - [x] Test `get_by_provider_symbol` returns `Instrument` on hit, `None` on miss
  - [x] Test `_invalidate_cache` clears cache so subsequent call queries DB again
  - [x] Test `close()` calls `pool.close()`
  - [x] Success: `uv run pytest test/unit/data/base/test_instrument_registry.py -v` — all tests pass

### Task 4: Implement write methods on `InstrumentRegistry`

- [x] **Implement `register_instrument`, `update_provider_mapping`, `list_instruments`**
  - [x] `register_instrument(self, canonical_id, symbol, asset_class, venue, currency="USD", tick_size=None, lot_size=1, trading_calendar_id=None, adjustment_policy="split_adjusted", metadata=None) -> Instrument`
    - [x] INSERT with `ON CONFLICT (canonical_id) DO NOTHING RETURNING ...`
    - [x] If `RETURNING` yields no row (conflict triggered), fetch existing row with `get_by_canonical_id(canonical_id)`
    - [x] Call `_invalidate_cache()` after successful insert
    - [x] Return `Instrument` dataclass
  - [x] `update_provider_mapping(self, instrument_id: int, provider: str, provider_symbol: str) -> None`
    - [x] INSERT into `provider_symbol_mapping` with `ON CONFLICT DO NOTHING`
    - [x] Call `_invalidate_cache()` after insert
  - [x] `list_instruments(self, *, asset_class=None, venue=None, active_only=True) -> list[Instrument]`
    - [x] Build parameterized WHERE clause without string concatenation: use `%s` with `None`-safe comparisons
    - [x] `WHERE (%s IS NULL OR asset_class = %s) AND (%s IS NULL OR venue = %s) AND (NOT %s OR active = TRUE)`
    - [x] ORDER BY symbol, venue
    - [x] Not cached (listing is a bulk read; caching would be stale)
  - [x] Success: methods callable with mocked DB; write methods call `_invalidate_cache`

### Task 5: Unit tests for write methods

- [x] **Extend `test/unit/data/base/test_instrument_registry.py` with write method tests**
  - [x] Test `register_instrument` calls INSERT and returns `Instrument`
  - [x] Test `register_instrument` on conflict (no RETURNING row) falls back to `get_by_canonical_id`
  - [x] Test `register_instrument` calls `_invalidate_cache`
  - [x] Test `update_provider_mapping` calls INSERT into `provider_symbol_mapping`
  - [x] Test `update_provider_mapping` calls `_invalidate_cache`
  - [x] Test `list_instruments` with no filters returns all rows
  - [x] Test `list_instruments` with `venue` filter passes venue param
  - [x] Test `list_instruments` with `active_only=False` includes inactive
  - [x] Success: `uv run pytest test/unit/data/base/test_instrument_registry.py -v` — all tests pass

**Commit:** `feat: rewrite InstrumentRegistry with psycopg3 and per-instance cache`

### Task 6: Create `instrument_seed.py` with mapping constants and seed logic

- [x] **Create `src/manta_trading/market/instrument_seed.py`**
  - [x] Define `VENUE_MAP: dict[str, str]` constant (case-insensitive key matching): maps AlphaVantage `exchange` values to canonical venue strings. Include at minimum: "NYSE"→"NYSE", "NASDAQ"→"NASDAQ", "NYSE ARCA"→"NYSE_ARCA", "NYSE MKT"→"NYSE_MKT", "BATS"→"BATS". Any unrecognized value maps to the original value (uppercased). Keys are normalized to uppercase for lookup.
  - [x] Define `CALENDAR_MAP: dict[str, str]` constant: maps venue to `trading_calendar_id`. NYSE family (NYSE, NYSE_ARCA, NYSE_MKT, BATS) → "NYSE"; NASDAQ → "NASDAQ"; default → "NYSE"
  - [x] Define `ASSET_CLASS_MAP: dict[str, str]` constant: "Stock"→"equity", "ETF"→"etf". Unknown values → lowercase of input.
  - [x] Implement `seed_instruments(registry: InstrumentRegistry, market_db_conninfo: str) -> dict` that:
    1. Opens a psycopg3 connection to MarketDB (`market_db_conninfo`)
    2. Reads all rows from `symbol_list`: `SELECT symbol, name, exchange, assettype, ipodate, delistingdate, status FROM symbol_list`
    3. For each row: map exchange → venue via `VENUE_MAP`, map assettype → asset_class via `ASSET_CLASS_MAP`, derive `canonical_id = f"{symbol}.{venue}"`, determine `trading_calendar_id` via `CALENDAR_MAP`, set `active = (delistingdate IS NULL)`
    4. Calls `registry.register_instrument(...)` for each — `ON CONFLICT DO NOTHING` handles duplicates
    5. Calls `registry.update_provider_mapping(instrument_id, "alphavantage", symbol)` for each
    6. Returns `{"total_read": N, "registered": N, "mappings_created": N}`
  - [x] Use a simple psycopg3 connection (not a pool) for the MarketDB read — this is a one-shot batch operation
  - [x] Success: module imports without error; `VENUE_MAP`, `CALENDAR_MAP`, `ASSET_CLASS_MAP` are accessible constants

### Task 7: Unit tests for `instrument_seed.py`

- [x] **Create `test/unit/test_instrument_seed.py`**
  - [x] Test `VENUE_MAP` lookup: "NYSE" → "NYSE", "NASDAQ" → "NASDAQ", "NYSE ARCA" → "NYSE_ARCA", unknown exchange → uppercased original
  - [x] Test `ASSET_CLASS_MAP`: "Stock" → "equity", "ETF" → "etf", unknown → lowercased
  - [x] Test `CALENDAR_MAP`: NYSE venues → "NYSE", "NASDAQ" → "NASDAQ"
  - [x] Test `seed_instruments` with mocked MarketDB connection (2 rows) and mocked `InstrumentRegistry`: verify `register_instrument` called once per row, `update_provider_mapping` called once per row, return dict has correct counts
  - [x] Test `seed_instruments` with a delisted symbol (`delistingdate` set): `register_instrument` called with `active=False`
  - [x] Test `seed_instruments` called twice with the same mocked rows: second call still calls `register_instrument` (which uses ON CONFLICT DO NOTHING internally), result dict shows 0 newly registered (mock returns existing instrument from `get_by_canonical_id` fallback)
  - [x] Success: `uv run pytest test/unit/test_instrument_seed.py -v` — all tests pass

**Commit:** `feat: add instrument seed module with venue and asset class mapping`

### Task 8: Add `mt data instruments` CLI subcommands

- [x] **Extend `src/manta_trading/cli/commands/data.py` with `instruments_app`**
  - [x] Create a `instruments_app = typer.Typer(name="instruments", help="Manage instrument registry")` sub-application
  - [x] Register it on `data_app`: `data_app.add_typer(instruments_app)`
  - [x] Implement `instruments_list` command registered as `instruments_app.command("list")`
    - [x] Options: `--venue TEXT` (optional filter), `--asset-class TEXT` (optional filter), `--inactive` flag (include inactive, default False), `--json` flag
    - [x] Creates `InstrumentRegistry` from `Settings().timescale_db_url`; fails explicitly if URL not configured
    - [x] Calls `registry.list_instruments(venue=venue, asset_class=asset_class, active_only=not inactive)`
    - [x] Default output: Rich table with columns: symbol, canonical_id, venue, asset_class, active
    - [x] `--json` output: JSON array of instrument dicts
    - [x] Calls `registry.close()` in `finally`
  - [x] Implement `instruments_seed` command registered as `instruments_app.command("seed")`
    - [x] Options: `--dry-run` flag, `--json` flag
    - [x] Requires both `Settings().timescale_db_url` and `Settings().market_db_url`; fails explicitly if either missing
    - [x] Calls `seed_instruments(registry, market_db_url)` from `instrument_seed.py`
    - [x] `--dry-run`: reads symbol_list and reports counts without writing; skips `register_instrument` and `update_provider_mapping` calls
    - [x] Default output: human-readable summary (total read, registered, mappings created)
    - [x] `--json` output: JSON result dict
    - [x] Calls `registry.close()` in `finally`
  - [x] Success: `uv run mt data instruments --help` shows list and seed subcommands; `uv run mt data instruments list --help` shows options

### Task 9: Unit tests for `instruments` CLI commands

- [x] **Add `TestInstrumentsList` and `TestInstrumentsSeed` to `test/unit/test_cli_data.py`**
  - [x] Use Typer `CliRunner` for all CLI tests; mock `InstrumentRegistry` and `seed_instruments`
  - [x] `TestInstrumentsList`:
    - [x] Test default output contains instrument rows (mock returns 2 instruments)
    - [x] Test `--json` returns valid JSON array
    - [x] Test `--venue NYSE` passes venue filter to `list_instruments`
    - [x] Test `--asset-class etf` passes asset_class filter
    - [x] Test missing `MT_TIMESCALE_DB_URL` exits with error message
  - [x] `TestInstrumentsSeed`:
    - [x] Test successful seed shows count summary
    - [x] Test `--json` returns JSON result dict
    - [x] Test `--dry-run` does not call `register_instrument`
    - [x] Test missing `MT_TIMESCALE_DB_URL` exits with error
    - [x] Test missing `MT_MARKET_DB_URL` exits with error
  - [x] Success: `uv run pytest test/unit/test_cli_data.py -v -k instruments` — all tests pass

**Commit:** `feat: add mt data instruments CLI commands (list, seed)`

### Task 10: Integration tests for `InstrumentRegistry`

- [x] **Create `test/integration/test_instrument_registry_integration.py`**
  - [x] All tests skip when `MT_TIMESCALE_DB_URL` is not set (use `pytest.mark.skipif` or `pytest.skip`)
  - [x] `test_register_and_retrieve_by_canonical_id`: register a test instrument, retrieve by canonical_id, assert fields match
  - [x] `test_register_idempotent`: register same canonical_id twice, assert second call returns same instrument without error
  - [x] `test_get_by_symbol`: register instrument, retrieve by symbol, assert returns correct instrument
  - [x] `test_get_by_symbol_not_found`: query for unknown symbol, assert returns `None`
  - [x] `test_provider_mapping_and_lookup`: register instrument, add provider mapping, retrieve via `get_by_provider_symbol`, assert correct instrument returned
  - [x] `test_list_instruments_filtered`: register 2 instruments with different venues, list with venue filter, assert only correct one returned
  - [x] `test_update_provider_mapping_idempotent`: call `update_provider_mapping` twice with same data, assert no error (ON CONFLICT DO NOTHING)
  - [x] Use unique canonical_ids per test run (e.g., prefix with `TEST.`) and clean up inserted rows in teardown
  - [x] Success: `MT_TIMESCALE_DB_URL=... uv run pytest test/integration/test_instrument_registry_integration.py -v` — all tests pass

**Commit:** `test: add integration tests for InstrumentRegistry`

### Task 11: Full test suite verification and final commit

- [x] **Verify no regressions in existing test suite**
  - [x] Run `uv run pytest test/unit/ -v` — all 538+ tests pass
  - [x] Confirm no new import errors or test collection warnings
  - [x] If any tests fail that were passing before, investigate and fix before proceeding

- [x] **Update slice 102 review doc if applicable** — no action needed if all tests pass cleanly

- [x] **Mark slice 103 complete in slice plan**
  - [x] Update `project-documents/user/architecture/100-slices.data-storage.md` — check off entry 4: `[ ] **(103)...` → `[x] **(103)...`
  - [x] Update `project-documents/user/slices/103-slice.instrument-registry-integration.md` frontmatter: `status: complete`
  - [x] Update this task file frontmatter: `status: complete`

**Commit:** `docs: mark slice 103 complete, update slice plan`
