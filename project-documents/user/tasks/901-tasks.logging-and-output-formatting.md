---
docType: tasks
slice: logging-and-output-formatting
project: trading
lld: user/slices/901-slice.logging-and-output-formatting.md
dependencies: [900]
projectState: Typer CLI scaffold complete with mt entry point, Settings class (log_level, log_format), ConfigManager with TOML persistence, mt config commands. Existing code uses mixed loguru/print() for logging. loguru is a current dependency.
dateCreated: 20260329
dateUpdated: 20260329
status: complete
---

# Tasks: Logging and Output Formatting

## Context

Working on the Logging and Output Formatting slice (901) of the Foundation & Cleanup initiative. Slice 900 is complete — the Typer CLI skeleton, Settings class, and ConfigManager are in place. The Settings class already has `log_level` and `log_format` fields. This slice adds structured logging (`setup_logging`, `get_logger`, JSON/text formatters), a shared output formatter with `--json` support, and migrates existing loguru/print() calls to the new pattern.

**Dependencies**: Slice 900 (CLI Scaffold and Config System) — complete.
**Delivers**: Structured logging module, shared CLI output formatter with `--json`, loguru migration.
**Next slice**: 902 (Provider Registry and Status).

**Reference implementation**: `~/source/repos/manta/squadron/src/squadron/logging.py` — structural model for the logging module.

**Implementation Note**: Loguru migration (Phase 4) was expanded beyond the original 3 `data/base/` files to cover all ~35 source files and 7 test files across the entire codebase. The loguru dependency was fully removed.

## Tasks

### Phase 1: Logging Module

- [x] **1.1 Create logging module**
  - [x] Create `src/manta_trading/logging.py`
  - [x] Implement `_JsonFormatter(logging.Formatter)` that formats records as single-line JSON with fields: `timestamp` (UTC ISO), `level`, `name`, `message`, and `exception` (if present)
  - [x] Implement `setup_logging(settings: Settings)` — idempotent, clears existing handlers, configures root logger level and format from settings
  - [x] Text format string: `"%(asctime)s %(levelname)-8s %(name)s: %(message)s"`
  - [x] Level resolved via `getattr(logging, settings.log_level.upper(), logging.INFO)`
  - [x] Implement `get_logger(name: str) -> logging.Logger` — thin wrapper around `logging.getLogger(name)`
  - [x] Use `TYPE_CHECKING` guard for the Settings import to avoid circular imports
  - [x] Reference: `squadron/src/squadron/logging.py`
  - [x] Success: module imports cleanly, `get_logger("test")` returns a Logger instance

- [x] **1.2 Test logging module**
  - [x] Create `test/unit/test_logging.py`
  - [x] Test `_JsonFormatter` output is valid single-line JSON with required fields (timestamp, level, name, message)
  - [x] Test `_JsonFormatter` includes exception field when exc_info is present
  - [x] Test `setup_logging` with `log_format="json"` attaches `_JsonFormatter`
  - [x] Test `setup_logging` with `log_format="text"` attaches text formatter
  - [x] Test `setup_logging` respects `log_level` setting (e.g., DEBUG vs WARNING)
  - [x] Test `setup_logging` is idempotent — calling twice does not duplicate handlers
  - [x] Test `get_logger` returns a named logger
  - [x] Success: all tests pass, `uv run pytest test/unit/test_logging.py -v` green

- [x] **1.3 Wire setup_logging into CLI app callback**
  - [x] In `src/manta_trading/cli/app.py`, import `setup_logging` from `manta_trading.logging`
  - [x] Call `setup_logging(settings)` in the `main` callback after `Settings()` is created
  - [x] Success: `MT_LOG_LEVEL=DEBUG MT_LOG_FORMAT=text uv run mt config list` produces text log lines on stderr; `MT_LOG_FORMAT=json` produces JSON log lines on stderr

- [x] **1.4 Test CLI logging integration**
  - [x] Add tests in `test/unit/test_cli_app.py` (or new file if cleaner)
  - [x] Test that invoking a command with `MT_LOG_FORMAT=json` env var produces JSON log output on stderr (use CliRunner and capture stderr)
  - [x] Test that command stdout is not polluted by log output
  - [x] Success: tests pass

**Commit**: `feat: add structured logging module with JSON and text formatters`

### Phase 2: Output Formatter

- [x] **2.1 Create output formatter module**
  - [x] Create `src/manta_trading/cli/output.py`
  - [x] Implement `print_result(data: dict | list, *, json_mode: bool) -> None` — if `json_mode`, serialize to JSON (indent=2, default=str) on stdout; otherwise, pass-through to Rich (caller handles Rich formatting before this point for text mode)
  - [x] Implement `print_error(message: str, *, json_mode: bool) -> None` — if `json_mode`, emit `{"error": message}` to stderr; otherwise, use Rich `[red]Error: ...[/red]` to stderr
  - [x] Implement `make_table(title: str, columns: list[tuple[str, str]]) -> Table` — create a Rich Table with pre-configured columns (each tuple is `(header, style)`)
  - [x] Success: module imports cleanly

- [x] **2.2 Test output formatter**
  - [x] Create `test/unit/test_cli_output.py`
  - [x] Test `print_result` with `json_mode=True` outputs valid JSON to stdout
  - [x] Test `print_result` with `json_mode=False` does not emit JSON
  - [x] Test `print_error` with `json_mode=True` outputs `{"error": ...}` to stderr
  - [x] Test `print_error` with `json_mode=False` outputs Rich-formatted error to stderr
  - [x] Test `make_table` returns a Rich Table with expected columns
  - [x] Success: all tests pass, `uv run pytest test/unit/test_cli_output.py -v` green

**Commit**: `feat: add shared CLI output formatter with JSON support`

### Phase 3: Add --json Support to Config Commands

- [x] **3.1 Add --json to `mt config list`**
  - [x] In `src/manta_trading/cli/commands/config.py`, add `json_output: bool = typer.Option(False, "--json", help="Output as JSON")` parameter to `config_list`
  - [x] When `json_output` is True, build a list of dicts (key, value, source, description) and call `print_result(data, json_mode=True)`
  - [x] When `json_output` is False, use current Rich table output (refactor to use `make_table` from output.py)
  - [x] Success: `uv run mt config list --json` outputs valid JSON array; `uv run mt config list` outputs Rich table as before

- [x] **3.2 Add --json to `mt config get`**
  - [x] Add `json_output` parameter to `config_get`
  - [x] When `json_output` is True, emit `{"key": ..., "value": ..., "source": ...}` via `print_result`
  - [x] Error case: use `print_error` with json_mode when key is unknown
  - [x] Success: `uv run mt config get output_format --json` outputs valid JSON object

- [x] **3.3 Add --json to `mt config path`**
  - [x] Add `json_output` parameter to `config_path`
  - [x] When `json_output` is True, emit `{"user": {"path": ..., "exists": ...}, "project": {"path": ..., "exists": ...}}`
  - [x] Success: `uv run mt config path --json` outputs valid JSON

- [x] **3.4 Test --json on config commands**
  - [x] Update `test/unit/test_cli_config.py` with new tests
  - [x] Test `config list --json` returns valid JSON array with expected keys
  - [x] Test `config get <key> --json` returns valid JSON object with key, value, source
  - [x] Test `config get <bad_key> --json` returns JSON error
  - [x] Test `config path --json` returns valid JSON with user and project paths
  - [x] Test existing non-JSON commands still work as before (existing tests should cover this)
  - [x] Success: all config tests pass, `uv run pytest test/unit/test_cli_config.py -v` green

**Commit**: `feat: add --json output support to config commands`

### Phase 4: Loguru Migration

- [x] **4.1 Migrate session_classifier.py**
  - [x] In `src/manta_trading/data/base/session_classifier.py`, replace `from loguru import logger` with `from manta_trading.logging import get_logger` and `_logger = get_logger(__name__)`
  - [x] Replace all `logger.xxx()` calls with `_logger.xxx()` calls
  - [x] Success: `from manta_trading.data.base.session_classifier import ...` works, no loguru import

- [x] **4.2 Migrate instrument_registry.py**
  - [x] In `src/manta_trading/data/base/instrument_registry.py`, same pattern: replace loguru with `get_logger`
  - [x] Replace all `logger.xxx()` calls with `_logger.xxx()` calls
  - [x] Success: module imports cleanly with no loguru dependency

- [x] **4.3 Migrate trading_calendar.py**
  - [x] In `src/manta_trading/data/base/trading_calendar.py`, same pattern: replace loguru with `get_logger`
  - [x] Replace all `logger.xxx()` calls with `_logger.xxx()` calls
  - [x] Success: module imports cleanly with no loguru dependency

- [x] **4.4 Migrate print() calls in service_interface.py**
  - [x] In `src/manta_trading/data/base/service_interface.py`, replace operational `print()` calls (health checks, gap reports, completeness reports) with appropriate `_logger.info()` or `_logger.warning()` calls
  - [x] Add `from manta_trading.logging import get_logger` and `_logger = get_logger(__name__)`
  - [x] Success: no operational `print()` calls remain in the file

- [x] **4.5 Scan for remaining loguru/print usage**
  - [x] Run `grep -r "from loguru" src/` — expect no results
  - [x] Run `grep -r "import loguru" src/` — expect no results
  - [x] Review any remaining `print()` calls in `src/` — only acceptable in CLI output code (commands using `typer.echo` or `rprint`), not in library modules
  - [x] Success: no loguru imports remain in src/

- [x] **4.6 Remove loguru dependency**
  - [x] Remove `loguru` from `[project.dependencies]` in `pyproject.toml`
  - [x] Run `uv sync --all-extras` to update lockfile and uninstall loguru
  - [x] Verify: `uv run python -c "import loguru"` raises `ModuleNotFoundError`
  - [x] Success: loguru fully removed

- [x] **4.7 Test migration**
  - [x] Run full test suite: `uv run pytest test/ -v`
  - [x] All existing tests pass after migration
  - [x] No import errors or missing module issues
  - [x] Success: full test suite green

**Commit**: `refactor: migrate loguru and print() calls to structured logging`

### Phase 5: Final Verification

- [x] **5.1 Run verification walkthrough**
  - [x] Follow the Verification Walkthrough in the slice design document
  - [x] Verify logging formats (JSON and text) on stderr
  - [x] Verify `--json` output on config commands piped through `jq`
  - [x] Verify loguru removal
  - [x] Verify no loguru imports remain
  - [x] Verify full test suite passes
  - [x] Update the Verification Walkthrough section in the slice design with actual output, corrections, and caveats

- [x] **5.2 Update slice and plan status**
  - [x] Update `901-slice.logging-and-output-formatting.md` frontmatter: `status: complete`, `dateUpdated: today`
  - [x] Check off slice 901 entry in `900-slices.foundation-cleanup.md`
  - [x] Update this task file frontmatter: `status: complete`, `dateUpdated: today`

- [x] **5.3 Update CHANGELOG.md**
  - [x] Add entries for this slice under the [Unreleased] section
  - [x] Include: structured logging, output formatter, --json support, loguru migration

**Commit**: `docs: complete slice 901 — update walkthrough, tasks, and changelog`
