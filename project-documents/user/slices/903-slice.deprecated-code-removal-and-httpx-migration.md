---
docType: slice-design
slice: deprecated-code-removal-and-httpx-migration
project: trading
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [900, 902]
interfaces: [904]
dateCreated: 20260331
dateUpdated: 20260401
status: complete
---

# Slice 903: Deprecated Code Removal and httpx Migration

## Overview

Remove deprecated code paths, replace aiohttp with httpx in AlphaVantage HTTP clients, remove old CLI entry points (`ohlc.py` direct invocation, `newsoptions.py`), and wire the existing daily pipeline (`marketdb.py`, `marketservice.py`) into the new CLI as `mt data daily` subcommands. After this slice, the codebase has no deprecated imports, a single HTTP client library (httpx), and all data operations are reachable through the `mt` CLI.

## Value

- **User-facing**: `mt data daily` commands replace direct `python ohlc.py` invocation with discoverable, documented CLI commands that support `--json` output and integrate with the provider registry for credential validation.
- **Developer-facing**: Removing 2,600+ lines of deprecated code and eliminating the aiohttp dependency simplifies the dependency tree and reduces cognitive overhead. httpx provides both sync and async from a single library, aligning with the project's sync-first architecture.
- **Architectural**: Unblocks slice 904 (packaging and version) by ensuring the codebase is clean of dead imports and has a single HTTP client dependency. Establishes the `mt data` sub-app pattern that Initiative 100 (daily pipeline redesign) will build upon.

## Technical Scope

**Included:**
- Delete `market/deprecated/slice025_2025_01/` directory and all imports from it
- Remove `market/ohlc.py` (old standalone entry point) and `market/ohlcoptions.py` (its argparse options)
- Remove `news/newsoptions.py` (old news CLI entry point) and its import in `news/news.py`
- Replace aiohttp with httpx in `api/alphavantage/alphavantageapi.py`
- Replace aiohttp with httpx in `data/historical_minute/providers/alphavantage.py`
- Remove `aiohttp` from `pyproject.toml` dependencies, add `httpx` if not already present
- Create `cli/commands/data.py` with `mt data daily` subcommands wiring to existing `MarketService`
- Wire `data_app` into `cli/app.py`
- Unit tests for all new and modified modules

**Excluded:**
- Redesigning the daily pipeline logic — the existing `MarketService` and `MarketDB` code stays as-is; we wrap it, not rewrite it
- Minute data CLI commands — those depend on Slice 750/751 foundation work and are out of scope
- Database schema changes
- News CLI commands — the news module retains its functionality but the old argparse entry point is removed; a `mt news` sub-app is deferred to its own initiative
- Refactoring `AlphavantageAPI` internals beyond the HTTP client swap (rate limiter, task queue, etc. remain unchanged)

## Dependencies

### Prerequisites
- Slice 900 (CLI scaffold, config system) — complete
- Slice 902 (provider registry) — complete: provides `ProviderProfile`, `resolve_alias`, `resolve_auth` for credential validation in CLI commands
- Existing: `MarketService` in `market/marketservice.py` — working daily OHLCV pipeline
- Existing: `MarketDB` in `market/marketdb.py` — PostgreSQL database layer
- Existing: `AlphavantageAPI` in `api/alphavantage/alphavantageapi.py` — API client (aiohttp → httpx)
- Existing: `Settings` class with credential fields

### Interfaces Required
- `Settings` from `manta_trading.config` — for reading `MT_ALPHAVANTAGE_API_KEY` and DB connection settings
- `get_logger` from `manta_trading.logging` — for module logging
- `print_result`, `print_error`, `make_table` from `manta_trading.cli.output` — for CLI output
- `get_profile`, `resolve_auth` from `manta_trading.providers` — for credential validation in `mt data daily` commands

## Architecture

### Component Structure

```
src/manta_trading/
├── api/alphavantage/
│   └── alphavantageapi.py          # MODIFY: aiohttp → httpx
├── cli/
│   ├── app.py                      # MODIFY: add data_app
│   └── commands/
│       └── data.py                 # CREATE: mt data daily subcommands
├── data/historical_minute/
│   └── providers/
│       └── alphavantage.py         # MODIFY: aiohttp → httpx
├── market/
│   ├── deprecated/                 # DELETE: entire directory
│   ├── ohlc.py                     # DELETE: old entry point
│   ├── ohlcoptions.py              # DELETE: old argparse options
│   ├── marketdb.py                 # UNCHANGED: wrapped by CLI
│   └── marketservice.py            # UNCHANGED: wrapped by CLI
└── news/
    ├── newsoptions.py              # DELETE: old argparse options
    └── news.py                     # MODIFY: remove newsoptions import
```

### Data Flow

**Daily OHLCV via new CLI:**
```
mt data daily update AAPL
  → cli/commands/data.py
    → resolve_auth(get_profile("alphavantage"), settings)  # validate creds
    → AlphavantageAPI(api_key=settings.alphavantage_api_key)
    → MarketDB(settings)
    → MarketService(api, db)
    → marketService.updateDailyOHLCVSimpleGap("AAPL", "compact")
```

**Daily OHLCV bulk update:**
```
mt data daily update-all [--age N]
  → Same chain but calls marketService.updateDailyOHLCVAll(outputSize, age)
```

**Symbol list refresh:**
```
mt data daily symbols
  → MarketService.updateSymbolList()
```

## Technical Decisions

### httpx Migration Strategy

**Why httpx over aiohttp:**
- httpx supports both sync and async from the same library (`httpx.Client` / `httpx.AsyncClient`)
- Aligns with project's sync-first architecture — future refactoring can switch from async to sync HTTP without changing libraries
- httpx is already declared in `pyproject.toml` (≥0.28.0) but unused; this slice activates it
- httpx has a `requests`-compatible API, reducing learning curve

**Migration mapping:**

| aiohttp | httpx |
|---------|-------|
| `aiohttp.ClientSession()` | `httpx.AsyncClient()` |
| `session.get(url, params=p)` | `client.get(url, params=p)` |
| `response.json()` (async) | `response.json()` (sync on Response) |
| `response.text()` (async) | `response.text` (property) |
| `response.status` | `response.status_code` |
| `response.raise_for_status()` | `response.raise_for_status()` |
| `aiohttp.ClientError` | `httpx.HTTPError` |
| `aiohttp.ClientTimeout(total=N)` | `httpx.Timeout(N)` |
| `async with session:` | `async with client:` |

**Session management change:**
- aiohttp: Manual session lifecycle (`session = aiohttp.ClientSession()` / `await session.close()`)
- httpx: Context manager pattern (`async with httpx.AsyncClient() as client:`)
- The `AlphavantageAPI` class currently manages a persistent session. The httpx migration preserves this pattern using `httpx.AsyncClient` stored as `self._client`, with `cleanup()` calling `await self._client.aclose()`.

**backoff decorator:**
- Currently: `@backoff.on_exception(backoff.expo, aiohttp.ClientError, max_tries=3)`
- After: `@backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=3)`

### Deprecated Code Removal Strategy

**What gets deleted:**
1. `market/deprecated/slice025_2025_01/` — entire directory (6 modules, ~2,600 lines). Replaced by Slice 750 foundation in `data/historical_minute/`.
2. `market/ohlc.py` — standalone entry point class (387 lines). All functionality either moves to new CLI commands or is already available via `MarketService` directly.
3. `market/ohlcoptions.py` — argparse options for `ohlc.py`. Replaced by Typer command parameters.
4. `news/newsoptions.py` — argparse options for news module. The news module's functionality is retained but the old CLI entry is removed.

**What stays:**
- `market/marketdb.py` — active database layer, wrapped by new CLI
- `market/marketservice.py` — active service layer, wrapped by new CLI
- `market/symbol_list_manager.py` — active utility
- `market/timescale_minute_coverage.py` — active utility (used by Slice 750 code)
- `news/news.py` — active module (remove only the `newsoptions` import)

**Consumer update map:**

| Deleted module | Consumer | Action |
|---------------|----------|--------|
| `deprecated.slice025_2025_01.minutedataservice` | `market/ohlc.py` | Consumer also deleted |
| `deprecated.slice025_2025_01.minutedatabackfill` | `market/ohlc.py` | Consumer also deleted |
| `deprecated.slice025_2025_01.timescale_minute_service` | `market/ohlc.py` | Consumer also deleted |
| `deprecated.slice025_2025_01.minute_command_processor` | `market/ohlc.py` | Consumer also deleted |
| `market/ohlcoptions` | `market/ohlc.py` | Consumer also deleted |
| `market/ohlcoptions` | `deprecated/.../minute_command_processor.py` | Consumer also deleted |
| `news/newsoptions` | `news/news.py` | Remove import line |

All consumers of deleted code are either also deleted or trivially updated. No circular dependency issues.

### CLI Command Design: `mt data daily`

**Command structure:**
```
mt data
  daily
    update <symbol> [--output-size compact|full]     # single symbol
    update-all [--output-size compact|full] [--age N] # all tracked symbols
    update-file <path> [--output-size compact|full]   # symbols from file
    symbols                                           # refresh symbol list
    migrate                                           # run DB migration
```

**Design rationale:**
- Commands mirror the existing `ohlc.py` options but with explicit, discoverable subcommands
- `--output-size` replaces the positional/flag `-o` from `OHLCOptions`
- The minute data commands from `ohlc.py` (--minute, --status, --coverage, --jobs) are NOT included — they depend on deprecated code and will be re-implemented under Initiative 100 with the Slice 750 foundation
- Each command validates provider credentials via `resolve_auth()` before proceeding, giving actionable error messages on missing API keys

**Settings integration:**
- API key: read from `settings.alphavantage_api_key` (env var `MT_ALPHAVANTAGE_API_KEY`)
- DB connection: read from `settings.database_url` (existing SQLAlchemy URL from Settings)
- The old direct `os.getenv()` calls in `ohlc.py` are replaced by Settings fields

**Error handling:**
- Missing API key → exit code 1 with setup hint from `resolve_auth()`
- DB connection failure → exit code 1 with redacted connection string
- API errors → logged and reported, non-zero exit code

## Implementation Details

### Migration Plan

**Phase 1: Delete deprecated directory and old entry points**
- Delete `market/deprecated/` directory tree
- Delete `market/ohlc.py` and `market/ohlcoptions.py`
- Delete `news/newsoptions.py`
- Update `news/news.py` to remove `newsoptions` import
- Verify: `python -c "import manta_trading"` succeeds, `pytest` passes (no import errors)

**Phase 2: httpx migration in AlphavantageAPI**
- Replace `import aiohttp` with `import httpx` in `api/alphavantage/alphavantageapi.py`
- Change `aiohttp.ClientSession()` → `httpx.AsyncClient()`
- Update `_getSession()` context manager for httpx client lifecycle
- Update `_makeRequest()`: `response.status` → `response.status_code`, `await response.json()` → `response.json()`, `await response.text()` → `response.text`
- Update `cleanup()`: `await session.close()` → `await client.aclose()`
- Update error handling: `aiohttp.ClientError` → `httpx.HTTPError`
- Update backoff decorator exception type
- Update timeout handling: `aiohttp.ClientTimeout` → `httpx.Timeout`
- Write unit tests mocking httpx responses

**Phase 3: httpx migration in historical minute provider**
- Same pattern as Phase 2 for `data/historical_minute/providers/alphavantage.py`
- Update session management and error types
- Write/update unit tests

**Phase 4: Remove aiohttp dependency**
- Remove `aiohttp>=3.9.5` from `pyproject.toml` dependencies
- Verify `httpx>=0.28.0` is already present (it is)
- Run `uv sync` to update lock file

**Phase 5: Create `mt data daily` CLI commands**
- Create `cli/commands/data.py` with `data_app` Typer sub-app
- Implement `update`, `update-all`, `update-file`, `symbols`, `migrate` commands
- Each command: validate credentials → initialize services → execute → format output
- Wire `data_app` into `cli/app.py`
- Write CLI tests using `CliRunner`

**Phase 6: Integration testing and cleanup**
- Verify all existing tests pass
- Verify `mt --help` shows `data` subcommand
- Verify `mt data daily --help` shows all subcommands
- Run full test suite

### Behavior Preservation

The daily pipeline logic is **not modified** — it's wrapped. The `MarketService` methods called by the new CLI commands are identical to those called by the old `ohlc.py`:

| Old invocation | New invocation | Same underlying call |
|---------------|----------------|---------------------|
| `python ohlc.py -s AAPL` | `mt data daily update AAPL` | `marketService.updateDailyOHLCVSimpleGap("AAPL", "compact")` |
| `python ohlc.py -a` | `mt data daily update-all` | `marketService.updateDailyOHLCVAll(outputSize, age)` |
| `python ohlc.py -f symbols.txt` | `mt data daily update-file symbols.txt` | `marketService.updateDailyOHLCVListFromFile(path, outputSize)` |
| `python ohlc.py --symbols` | `mt data daily symbols` | `marketService.updateSymbolList()` |
| `python ohlc.py --migrate` | `mt data daily migrate` | `db.verifyDatabase()` |

## Integration Points

### Provides to Other Slices
- `mt data` sub-app pattern — Initiative 100 will add more `mt data` subcommands (minute data, etc.)
- Clean httpx-based HTTP client pattern — future providers (DataBento) will follow the same pattern
- Proof that the provider registry integrates with actual data operations (credential validation before API calls)

### Consumes from Other Slices
- Slice 900: Typer app structure, Settings class, ConfigManager
- Slice 902: `get_profile()`, `resolve_auth()`, `ProviderType` for credential validation in CLI commands

## Success Criteria

### Functional Requirements
- `market/deprecated/` directory does not exist
- `market/ohlc.py`, `market/ohlcoptions.py`, `news/newsoptions.py` do not exist
- `import manta_trading` succeeds with no import errors
- No references to `aiohttp` in any source file under `src/`
- `httpx` is the only HTTP client library in `pyproject.toml` dependencies
- `mt data daily update AAPL` validates credentials and calls the existing daily pipeline
- `mt data daily update-all` calls the existing bulk update pipeline
- `mt data daily symbols` refreshes the symbol list
- `mt data daily --help` shows all available subcommands
- All commands support `--json` output

### Technical Requirements
- All new and modified code has unit tests
- Existing test suite passes (excluding pre-existing DB-dependent test failures)
- No `aiohttp` imports remain in the codebase
- httpx migration preserves retry/backoff behavior
- httpx migration preserves rate limiting behavior

### Verification Walkthrough

**1. Verify deprecated code removed:**
```bash
ls src/manta_trading/market/deprecated/
# Actual: ls: src/manta_trading/market/deprecated/: No such file or directory

ls src/manta_trading/market/ohlc.py
# Actual: ls: src/manta_trading/market/ohlc.py: No such file or directory

ls src/manta_trading/news/newsoptions.py
# Actual: ls: src/manta_trading/news/newsoptions.py: No such file or directory

python -c "import manta_trading; print('OK')"
# Actual: OK
```

**2. Verify aiohttp removed:**
```bash
grep -r "aiohttp" src/
# Actual: no output (zero matches)

grep -r "import httpx" src/
# Actual:
# src/manta_trading/data/historical_minute/providers/alphavantage.py:import httpx
# src/manta_trading/api/alphavantage/alphavantageapi.py:import httpx
```

**3. Verify CLI commands:**
```bash
mt --help
# Actual: shows status, config, data, provider commands

mt data daily --help
# Actual: shows update, update-all, update-file, symbols, migrate

mt data daily update --help
# Actual: shows SYMBOL argument and --output-size, --json options

MT_ALPHAVANTAGE_API_KEY="" mt data daily update AAPL
# Actual: Error: AlphaVantage credentials not configured. Set MT_ALPHAVANTAGE_API_KEY environment variable (exit 1)

MT_ALPHAVANTAGE_API_KEY="" mt data daily update AAPL --json
# Actual: {"error": "AlphaVantage credentials not configured. Set MT_ALPHAVANTAGE_API_KEY environment variable"} (exit 1)
```

**4. Verify tests pass:**
```bash
pytest test/unit/ -v
# Actual: 495 passed, 11 failed (all failures pre-existing DB-dependent: testmarketdb.py, testmarketservice.py, test_symbol_list_manager.py)
```

**Caveats:**
- `news/news.py` was updated to replace `NewsOptions` (argparse) with `NewsCommandOptions` (dataclass) — constructor signature changed from `test_args=` to `options=`
- Existing test files referencing `OHLC`, `OHLCOptions`, `NewsOptions` were deleted or updated
- `testalphavantage.py` was rewritten to mock httpx instead of aiohttp, calling internal methods directly to avoid TaskQueue hangs in tests

## Risk Assessment

### Technical Risks
- **httpx behavioral differences**: httpx has slightly different timeout semantics and connection pooling compared to aiohttp. The `timeout` parameter in httpx is a `Timeout` object, not a simple integer.
- **Async context manager differences**: httpx's `AsyncClient` uses `aclose()` not `close()`. Missing this causes resource leak warnings.

### Mitigation Strategies
- Write focused tests for the httpx migration that verify timeout handling, error types, and session lifecycle
- Test the `cleanup()` method explicitly to ensure proper resource cleanup
- The migration is contained to two files, limiting blast radius

## Implementation Notes

### Development Approach

**Suggested order:**
1. Deprecated code deletion (Phase 1) — clean break, immediate codebase simplification
2. httpx migration (Phases 2-4) — contained to two API files
3. CLI commands (Phase 5) — builds on clean foundation
4. Integration testing (Phase 6) — verify everything works together

**Testing strategy:**
- Phase 1: Verify no import errors, existing tests pass
- Phases 2-3: Mock httpx responses in unit tests; test error handling, timeouts, retries
- Phase 5: Use `CliRunner` for CLI tests, mock `MarketService` and `AlphavantageAPI`
- Phase 6: Full test suite run

### Special Considerations
- The `backoff` library's `on_exception` decorator needs the exception type updated from `aiohttp.ClientError` to `httpx.HTTPError` — this is a one-line change but easy to miss
- The `AlphavantageAPI._getSession()` context manager pattern changes significantly with httpx — the persistent client pattern should use `httpx.AsyncClient` stored on `self` rather than creating a new client per context manager call
- `news/news.py` imports `NewsOptions` from `newsoptions.py` — this import must be removed or the module will fail to import. Verify `news/news.py` has no other hard dependency on `NewsOptions` beyond the import
