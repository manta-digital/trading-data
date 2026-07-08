---
docType: tasks
slice: deprecated-code-removal-and-httpx-migration
project: trading
lld: user/slices/903-slice.deprecated-code-removal-and-httpx-migration.md
dependencies: [900, 902]
projectState: Typer CLI scaffold complete with mt entry point, Settings class, ConfigManager, structured logging, shared CLI output formatter, provider registry with ProviderType enum, ProviderProfile, alias resolution, auth strategy pattern, mt provider list/status/test commands, mt status command. aiohttp used in two AlphaVantage client files. Deprecated code in market/deprecated/ still imported by ohlc.py. Old argparse CLI entry points (ohlc.py, newsoptions.py) still present.
dateCreated: 20260331
dateUpdated: 20260401
status: complete
---

# Tasks: Deprecated Code Removal and httpx Migration

## Context

Working on the Deprecated Code Removal and httpx Migration slice (903) of the Foundation & Cleanup initiative. Slices 900 (CLI scaffold, config), 901 (logging, output formatter), and 902 (provider registry and status) are complete and merged to main.

This slice removes deprecated code, replaces aiohttp with httpx in AlphaVantage HTTP clients, removes old CLI entry points, and wires the daily pipeline into the new CLI as `mt data daily` subcommands.

**Dependencies**: Slices 900 and 902 — complete.
**Delivers**: Clean codebase (no deprecated imports, no aiohttp), `mt data daily` CLI commands wrapping existing `MarketService`.
**Next slice**: 904 (Packaging and Version).

## Tasks

### Phase 1: Deprecated Code Deletion

- [x] **1.1 Delete deprecated directory**
  - [x] Delete the entire `src/manta_trading/market/deprecated/` directory tree (includes `slice025_2025_01/` with 6 modules and `DEPRECATION_LOG.md`)
  - [x] Verify no other files in `src/` import from `manta_trading.market.deprecated` (aside from `ohlc.py` which is deleted in 1.2)
  - [x] Success: `src/manta_trading/market/deprecated/` does not exist

- [x] **1.2 Delete old market CLI entry points**
  - [x] Delete `src/manta_trading/market/ohlc.py` (standalone entry point, 387 lines)
  - [x] Delete `src/manta_trading/market/ohlcoptions.py` (argparse options)
  - [x] Verify no other files in `src/` import from `ohlc` or `ohlcoptions` (all consumers were in the deprecated directory or `ohlc.py` itself)
  - [x] Success: both files do not exist, no broken imports remain

- [x] **1.3 Delete old news CLI entry point and update consumer**
  - [x] Delete `src/manta_trading/news/newsoptions.py`
  - [x] Update `src/manta_trading/news/news.py`: remove `from manta_trading.news.newsoptions import NewsOptions` (line 14) and the `self.options = NewsOptions(test_args=test_args)` usage (line 26). Adjust `News.__init__` as needed — the class should still be instantiable but without argparse option parsing
  - [x] Verify no other files import `newsoptions` or `NewsOptions`
  - [x] Success: `newsoptions.py` does not exist, `news.py` imports cleanly

- [x] **1.4 Verify clean state after deletions**
  - [x] Run `python -c "import manta_trading; print('OK')"` — must succeed
  - [x] Run `pytest test/unit/ -v` — all existing unit tests pass (excluding pre-existing DB-dependent failures)
  - [x] Grep `src/` for any remaining references to deleted modules: `deprecated`, `ohlcoptions`, `newsoptions` — none should exist in import statements
  - [x] Success: package imports cleanly, test suite passes

**Commit**: `refactor: remove deprecated code, old CLI entry points, and dead imports`

### Phase 2: httpx Migration — AlphavantageAPI

- [x] **2.1 Migrate AlphavantageAPI from aiohttp to httpx**
  - [x] In `src/manta_trading/api/alphavantage/alphavantageapi.py`:
    1. Replace `import aiohttp` with `import httpx`
    2. Change session field: `self.session` → `self._client`, type `httpx.AsyncClient | None`
    3. Update `_getSession()` context manager: create `httpx.AsyncClient()` instead of `aiohttp.ClientSession()`. For client state checks, use `self._client is None` after `aclose()` rather than relying on an `is_closed` property — set `self._client = None` after closing
    4. Update `_makeRequest()`: `response.status` → `response.status_code`, `await response.json()` → `response.json()`, `await response.text()` → `response.text` (property, not method)
    5. Update `backoff` decorator: `aiohttp.ClientError` → `httpx.HTTPError`
    6. Update `_getNewsSentiment()`: replace inline `aiohttp.ClientSession()` usage with httpx equivalent
    7. Update `_getDailyOHLCV()`: `aiohttp.ClientError` → `httpx.HTTPError` in except clause
    8. Update `_getMinuteOHLCV()`: replace all `aiohttp.ClientError`, `aiohttp.ClientResponseError`, `aiohttp.ClientTimeout` → `httpx.HTTPError`, `httpx.HTTPStatusError`, `httpx.TimeoutException`
    9. Update `cleanup()`: `await self.session.close()` → `await self._client.aclose()`, replace `aiohttp.ClientError` reference in error handling
    10. Update `close()` method similarly
    11. Update timeout configuration: the existing `self.timeout = 10` passed to `session.get(..., timeout=self.timeout)` works with httpx (accepts int/float), but if any code uses `aiohttp.ClientTimeout(total=N)`, replace with `httpx.Timeout(N)`
  - [x] Verify no `aiohttp` references remain in the file: `grep aiohttp alphavantageapi.py` returns nothing
  - [x] Success: file has zero aiohttp references, uses httpx throughout

- [x] **2.2 Test AlphavantageAPI httpx migration**
  - [x] Create `test/unit/test_alphavantage_api_httpx.py`
  - [x] Test `_makeRequest()` with mocked httpx response (success case — JSON and text)
  - [x] Test `_makeRequest()` raises on non-200 status codes
  - [x] Test `backoff` decorator retries on `httpx.HTTPError`
  - [x] Test `cleanup()` calls `aclose()` on the client
  - [x] Test `cleanup()` handles case where client is `None` or already closed
  - [x] Test error handling in `_getDailyOHLCV()` catches `httpx.HTTPError`
  - [x] Test that `_makeRequest()` still invokes the rate limiter context manager (verify `RateLimiter` is called — the rate limiter is HTTP-library-agnostic but confirm the integration point is preserved)
  - [x] Success: all tests pass via `pytest test/unit/test_alphavantage_api_httpx.py -v`

**Commit**: `refactor: migrate AlphavantageAPI from aiohttp to httpx`

### Phase 3: httpx Migration — Historical Minute Provider

- [x] **3.1 Migrate historical minute AlphaVantage provider from aiohttp to httpx**
  - [x] In `src/manta_trading/data/historical_minute/providers/alphavantage.py`:
    1. Replace `import aiohttp` with `import httpx`
    2. Change session field: `self._session: aiohttp.ClientSession | None` → `self._client: httpx.AsyncClient | None`
    3. Update session creation: `aiohttp.ClientSession()` → `httpx.AsyncClient()`
    4. Update HTTP calls: `session.get()` → `client.get()`, response attribute changes per migration mapping in slice design
    5. Update error handling: `aiohttp.ClientError` → `httpx.HTTPError`, `aiohttp.ClientTimeout` → `httpx.TimeoutException`
    6. Update cleanup/close: `await self._session.close()` → `await self._client.aclose()`
  - [x] Verify no `aiohttp` references remain in the file
  - [x] Success: file has zero aiohttp references, uses httpx throughout

- [x] **3.2 Test historical minute provider httpx migration**
  - [x] Create or update tests in `test/unit/test_historical_minute_alphavantage.py`
  - [x] Test successful HTTP request with mocked httpx response
  - [x] Test error handling catches `httpx.HTTPError`
  - [x] Test timeout handling uses httpx timeout semantics
  - [x] Test client cleanup calls `aclose()`
  - [x] Success: all tests pass

**Commit**: `refactor: migrate historical minute AlphaVantage provider to httpx`

### Phase 4: Remove aiohttp Dependency

- [x] **4.1 Update pyproject.toml dependencies**
  - [x] Remove `aiohttp>=3.9.5` from `[project.dependencies]` in `pyproject.toml`
  - [x] Verify `httpx>=0.28.0` is already listed (it should be; if not, add it)
  - [x] Success: `aiohttp` not in pyproject.toml dependencies, `httpx` is present

- [x] **4.2 Verify no aiohttp references remain in codebase**
  - [x] Run `grep -r "aiohttp" src/` — must return no results
  - [x] Run `grep -r "import aiohttp" test/` — if any test files reference aiohttp, update them
  - [x] Run `python -c "import manta_trading; print('OK')"` — must succeed
  - [x] Run `pytest test/unit/ -v` — all tests pass
  - [x] Success: zero aiohttp references in `src/`, package imports cleanly, tests pass

**Commit**: `chore: remove aiohttp dependency, httpx is sole HTTP client`

### Phase 5: CLI Commands — `mt data daily`

- [x] **5.1 Create data CLI sub-app scaffold**
  - [x] Create `src/manta_trading/cli/commands/data.py`
  - [x] Define `data_app = typer.Typer(name="data", help="Data acquisition and management.")` with `no_args_is_help=True`
  - [x] Define `daily_app = typer.Typer(name="daily", help="Daily OHLCV data operations.")` with `no_args_is_help=True`
  - [x] Add `data_app.add_typer(daily_app, name="daily")`
  - [x] Wire `data_app` into `src/manta_trading/cli/app.py`: import and `app.add_typer(data_app, name="data")`
  - [x] Success: `mt data --help` shows "daily" subcommand, `mt data daily --help` shows help text

- [x] **5.2 Implement `mt data daily update` command**
  - [x] Add `update` command to `daily_app` accepting:
    - `symbol: str` (required argument)
    - `--output-size`: choice of `compact` or `full`, default `compact`
    - `--json`: bool flag for JSON output
  - [x] Command flow: get settings from `ctx.obj["settings"]` → validate AlphaVantage credentials via `resolve_auth(get_profile("alphavantage"), settings)` → if not valid, exit 1 with setup hint → initialize `AlphavantageAPI` and `MarketDB` from settings → create `MarketService(api, db)` → call `asyncio.run(marketService.updateDailyOHLCVSimpleGap(symbol, output_size))` → format result
  - [x] On missing credentials: print error with `resolve_auth().setup_hint`, exit code 1
  - [x] Support `--json` output for both success and error cases
  - [x] Success: `mt data daily update AAPL --help` shows expected options

- [x] **5.3 Implement `mt data daily update-all` command**
  - [x] Add `update-all` command to `daily_app` accepting:
    - `--output-size`: choice of `compact` or `full`, default `compact`
    - `--age`: optional int for filtering by age in days
    - `--json`: bool flag for JSON output
  - [x] Same credential validation and service initialization pattern as `update`
  - [x] Call `asyncio.run(marketService.updateDailyOHLCVAll(outputSize=output_size, age=age))`
  - [x] Success: `mt data daily update-all --help` shows expected options

- [x] **5.4 Implement `mt data daily update-file` command**
  - [x] Add `update-file` command to `daily_app` accepting:
    - `path: Path` (required argument, must exist)
    - `--output-size`: choice of `compact` or `full`, default `compact`
    - `--json`: bool flag for JSON output
  - [x] Same credential validation and service initialization pattern
  - [x] Call `asyncio.run(marketService.updateDailyOHLCVListFromFile(str(path), output_size))`
  - [x] Success: `mt data daily update-file --help` shows expected options

- [x] **5.5 Implement `mt data daily symbols` command**
  - [x] Add `symbols` command to `daily_app` accepting:
    - `--json`: bool flag for JSON output
  - [x] Same credential validation and service initialization pattern
  - [x] Call `asyncio.run(marketService.updateSymbolList())`
  - [x] Success: `mt data daily symbols --help` shows expected options

- [x] **5.6 Implement `mt data daily migrate` command**
  - [x] Add `migrate` command to `daily_app` accepting:
    - `--json`: bool flag for JSON output
  - [x] Initialize `MarketDB` from settings (no API key required)
  - [x] Call `db.verifyDatabase()`
  - [x] Success: `mt data daily migrate --help` shows expected options

- [x] **5.7 Test CLI data commands**
  - [x] Create `test/unit/test_cli_data.py`
  - [x] Test `mt data --help` shows "daily" subcommand (exit code 0)
  - [x] Test `mt data daily --help` shows all 5 subcommands (exit code 0)
  - [x] Test `mt data daily update AAPL` without API key exits 1 with credential message
  - [x] Test `mt data daily update AAPL --json` without API key returns JSON error
  - [x] Test `mt data daily update-all --help` shows `--output-size` and `--age` options
  - [x] Test `mt data daily update-file --help` shows PATH argument
  - [x] Test `mt data daily symbols --help` shows help text
  - [x] Test `mt data daily migrate --help` shows help text
  - [x] Test alias resolution if applicable (e.g., provider name in error messages)
  - [x] Use `CliRunner` from `typer.testing`, mock `MarketService` and `AlphavantageAPI` where needed
  - [x] Success: all tests pass via `pytest test/unit/test_cli_data.py -v`

**Commit**: `feat: add mt data daily CLI commands for daily OHLCV pipeline`

### Phase 6: Final Verification

- [x] **6.1 Run full test suite**
  - [x] Run `pytest test/unit/ -v` — all unit tests pass
  - [x] Verify no import errors across the package
  - [x] Success: clean test run (excluding pre-existing DB-dependent failures)

- [x] **6.2 Verify CLI integration**
  - [x] `mt --help` shows `data` subcommand alongside `status`, `config`, `provider`
  - [x] `mt data daily --help` lists: `update`, `update-all`, `update-file`, `symbols`, `migrate`
  - [x] `mt data daily update --help` shows SYMBOL argument and `--output-size` option
  - [x] Success: all commands discoverable and documented

- [x] **6.3 Verify codebase cleanup**
  - [x] `ls src/manta_trading/market/deprecated/` → No such file or directory
  - [x] `ls src/manta_trading/market/ohlc.py` → No such file or directory
  - [x] `ls src/manta_trading/market/ohlcoptions.py` → No such file or directory
  - [x] `ls src/manta_trading/news/newsoptions.py` → No such file or directory
  - [x] `grep -r "aiohttp" src/` → no output
  - [x] `grep -r "import httpx" src/` → matches in `alphavantageapi.py` and `data/.../alphavantage.py`
  - [x] Success: all deprecated code removed, aiohttp fully replaced

- [x] **6.4 Update slice design verification walkthrough**
  - [x] Update the Verification Walkthrough section of `903-slice.deprecated-code-removal-and-httpx-migration.md` with actual commands and output observed during implementation
  - [x] Update slice frontmatter: `status: complete`, `dateUpdated: <today>`
  - [x] Update task file frontmatter: `status: complete`, `dateUpdated: <today>`
  - [x] Check off slice 903 in `900-slices.foundation-cleanup.md`
  - [x] Update `CHANGELOG.md` with slice 903 entries
  - [x] Success: all documents updated consistently

**Commit**: `docs: complete slice 903 — update walkthrough, tasks, and changelog`
