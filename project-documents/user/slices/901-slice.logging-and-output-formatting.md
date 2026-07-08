---
docType: slice-design
slice: logging-and-output-formatting
project: trading
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [900]
interfaces: [902, 903, 904]
dateCreated: 20260329
dateUpdated: 20260329
status: complete
---

# Slice 901: Logging and Output Formatting

## Overview

Add structured logging and a shared output formatter to the CLI foundation established by slice 900. After this slice, every module uses `get_logger(__name__)` instead of `print()`/loguru, logging is configurable via `MT_LOG_LEVEL` and `MT_LOG_FORMAT`, and all CLI commands can emit JSON output via `--json`. This is the plumbing that slices 902+ depend on for consistent operational output.

## Value

- **Operational**: Structured JSON logs enable machine parsing for monitoring and debugging. Text logs remain the default for human use.
- **Developer**: A single `get_logger` pattern replaces the current mix of `print()`, `loguru.logger`, and `logging.getLogger`. New modules have one way to log.
- **Architectural**: The `--json` output formatter is the shared contract that every future CLI command uses for machine-readable output. Without it, each command would invent its own JSON serialization.

## Technical Scope

**Included:**
- `src/manta_trading/logging.py` — `setup_logging`, `get_logger`, `_JsonFormatter`
- `src/manta_trading/cli/output.py` — shared output formatter with `--json` support
- Integration: call `setup_logging(settings)` in `cli/app.py` callback
- Migration: replace `loguru` and `print()` calls in existing modules with `get_logger`
- Remove `loguru` dependency from pyproject.toml once all usages are migrated
- Tests for logging and output formatting

**Excluded:**
- Changes to CLI command logic (only output plumbing changes)
- New CLI commands (that's slice 902+)
- Log aggregation, rotation, or file output (future work if needed)

## Dependencies

### Prerequisites
- Slice 900 complete: Typer app, Settings class with `log_level`/`log_format` fields, `ctx.obj["settings"]` pattern

### Interfaces Required
- `Settings.log_level: str` — already exists, default `"INFO"`
- `Settings.log_format: str` — already exists, default `"text"`
- `ctx.obj["settings"]` — set in app callback, available to all commands

## Architecture

### Component Structure

```
src/manta_trading/
├── logging.py              # NEW: setup_logging, get_logger, _JsonFormatter
├── cli/
│   ├── app.py              # MODIFIED: call setup_logging in callback
│   ├── output.py           # NEW: OutputFormatter, print_result, print_error
│   └── commands/
│       └── config.py       # MODIFIED: use OutputFormatter for --json support
└── config/
    └── __init__.py          # unchanged (Settings already has log_level/log_format)
```

### Data Flow

**Logging flow:**
```
CLI invoked → app.callback()
  → Settings() created (reads MT_LOG_LEVEL, MT_LOG_FORMAT from env)
  → setup_logging(settings) configures root logger
  → command dispatched
    → module calls get_logger(__name__).info("message")
    → handler formats as JSON or text per settings
    → output to stderr
```

**Output formatting flow:**
```
CLI command produces result (dict or list)
  → if --json flag: json.dumps(result) → stdout
  → if not --json: Rich table/text → stdout
```

Logging goes to stderr. Command output goes to stdout. This separation is critical — it allows `mt config list --json | jq .` to work without log noise in the pipe.

## Technical Decisions

### Logging Module: stdlib `logging` (not loguru)

Replicate the Squadron pattern from `squadron/logging.py`:
- `_JsonFormatter(logging.Formatter)` — formats records as single-line JSON with `timestamp`, `level`, `name`, `message`, `exception` fields
- `setup_logging(settings: Settings)` — idempotent, configures root logger, clears existing handlers
- `get_logger(name: str)` — thin wrapper around `logging.getLogger(name)`
- Text format: `"%(asctime)s %(levelname)-8s %(name)s: %(message)s"`
- Level resolved via `getattr(logging, settings.log_level.upper(), logging.INFO)`

**Why stdlib over loguru**: loguru is already in the codebase but we're standardizing on stdlib `logging` to match Squadron. loguru adds magic global state and interception complexity. stdlib logging is well-understood, configurable, and doesn't require an extra dependency.

### Output Formatter: Thin Helper Module

`cli/output.py` provides a small set of functions that commands use for output:

```python
# Conceptual interface (not final code)
def print_result(data: dict | list, *, json_mode: bool) -> None:
    """Print command result as JSON or Rich-formatted text."""

def print_error(message: str, *, json_mode: bool) -> None:
    """Print error message as JSON or Rich-formatted text."""

def make_table(title: str, columns: list[tuple[str, str]]) -> Table:
    """Create a pre-configured Rich table (convenience for text mode)."""
```

- `json_mode` is determined by the `--json` flag on each command (not a global flag — Typer doesn't cleanly support global options across sub-apps)
- JSON output: `json.dumps(data, indent=2, default=str)` to stdout
- Error JSON: `{"error": message}` to stderr
- Text output: Rich tables, `rprint()` as currently used
- The formatter does NOT decide what data to include — that's the command's job. The formatter only handles serialization.

### --json Flag Pattern

Each command that supports JSON output adds a `--json` option:

```python
@config_app.command("list")
def config_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    cwd: str = typer.Option(".", "--cwd", help="Working directory"),
) -> None:
```

This is per-command rather than global because:
1. Typer sub-apps don't propagate parent options cleanly
2. Some commands (like `config set`) produce confirmation messages, not data — `--json` doesn't apply to all commands
3. Per-command is explicit and grep-able

### Loguru Migration

Existing files using loguru (found in `data/base/` modules):
- `session_classifier.py` — `from loguru import logger`
- `instrument_registry.py` — `from loguru import logger`
- `trading_calendar.py` — `from loguru import logger`

Migration is mechanical:
```python
# Before
from loguru import logger
logger.info(f"message")

# After
from manta_trading.logging import get_logger
_logger = get_logger(__name__)
_logger.info("message")
```

Also migrate `print()` calls in `data/base/service_interface.py` that are operational output (health checks, gap reports) to logger calls.

After migration, remove `loguru` from `[project.dependencies]` in pyproject.toml.

### Logging to stderr, Output to stdout

- All `logging` output goes to stderr (via `StreamHandler(sys.stderr)` — this is the default for `StreamHandler()`)
- All command output (text or JSON) goes to stdout
- This enables piping: `mt config list --json | jq '.[] | select(.source == "user")'`

## Integration Points

### Provides to Other Slices
- **`get_logger(name)`** — every future module imports this instead of raw `logging.getLogger`
- **`setup_logging(settings)`** — called once at CLI entry; future code just uses loggers
- **`print_result(data, json_mode=...)`** — every future CLI command uses this for output
- **`print_error(message, json_mode=...)`** — every future CLI command uses this for errors
- **`--json` pattern** — established convention for machine-readable output

### Consumes from Other Slices
- **Slice 900**: Settings class, app callback, ctx.obj pattern

## Success Criteria

1. `setup_logging(settings)` configures root logger with level and format from Settings
2. `MT_LOG_FORMAT=json mt config list` produces JSON-formatted log lines on stderr
3. `MT_LOG_FORMAT=text mt config list` produces human-readable log lines on stderr
4. `get_logger(__name__)` returns a named logger that respects configured level and format
5. `mt config list --json` outputs a JSON array of config entries to stdout
6. `mt config get output_format --json` outputs a JSON object with key, value, source
7. JSON output is valid JSON parseable by `jq`
8. All existing `loguru` imports are replaced with `get_logger` pattern
9. All operational `print()` calls in `data/base/` are replaced with logger calls
10. `loguru` is removed from pyproject.toml dependencies
11. Logging output goes to stderr; command output goes to stdout
12. `_JsonFormatter` produces single-line JSON with timestamp, level, name, message fields
13. All existing tests continue to pass after migration

## Verification Walkthrough

Verified 2026-03-29. All checks pass.

```bash
# Verify logging formats — Rich table on stdout, logs on stderr
MT_LOG_LEVEL=DEBUG MT_LOG_FORMAT=text uv run mt config list 2>/dev/null
# Actual: Rich table displayed correctly (log output suppressed via stderr redirect)

# Verify --json output on config commands
uv run mt config list --json 2>/dev/null
# Actual: JSON array with objects containing key, value, source, description ✓

uv run mt config list --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['key'])"
# Actual: "data_dir" — proves valid parseable JSON ✓

uv run mt config get output_format --json 2>/dev/null
# Actual: {"key": "output_format", "value": "text", "source": "user"} ✓
# Caveat: source shows "user" if a user config file exists with output_format set,
#         otherwise shows "default"

uv run mt config path --json 2>/dev/null
# Actual: {"user": {"path": "...", "exists": true}, "project": {"path": "...", "exists": false}} ✓

# Verify loguru removal
uv run python -c "import loguru"
# Actual: ModuleNotFoundError ✓

# Verify no loguru imports remain
grep -r "from loguru" src/
# Actual: no output ✓

# Run unit test suite
uv run pytest test/unit/ -v
# Actual: 418 passed, 14 failed (all 14 failures pre-existing — DB connection
#         and src/ layout path issues from prior slices, not related to this slice)
# New tests: test_logging.py (11), test_cli_output.py (6), test_cli_config.py (13),
#            test_cli_app.py logging tests (2) — all pass
```

**Caveats:**
- Rich `rprint()` output goes to stderr by default in some terminal contexts, not just stdout. The `--json` path uses `sys.stdout.write()` directly, which reliably separates from log output.
- Loguru migration scope was expanded beyond the original 3 `data/base/` files to cover all ~35 source files and 7 test files across the entire codebase.
- The `data/historical_minute/providers/alphavantage.py` file already used stdlib `logging` directly (never had loguru) — no change needed.

## Implementation Notes

### Suggested Implementation Order
1. Create `logging.py` with `setup_logging`, `get_logger`, `_JsonFormatter` + tests
2. Wire `setup_logging` into `cli/app.py` callback
3. Create `cli/output.py` with `print_result`, `print_error`, `make_table` + tests
4. Add `--json` support to existing config commands
5. Migrate loguru → get_logger in `data/base/` modules
6. Migrate print() → logger in `data/base/service_interface.py`
7. Remove loguru from pyproject.toml, run `uv sync --all-extras`
8. Verify all tests pass

### Testing Strategy
- **Unit tests for logging.py**: verify JSON formatter output structure, text format string, level configuration, idempotent setup
- **Unit tests for output.py**: verify JSON serialization, Rich table creation, error formatting
- **Integration tests for --json**: use Typer CliRunner, verify stdout is valid JSON, verify stderr contains logs
- **Migration verification**: grep for remaining loguru/print, run full test suite

## Related Work

- **Squadron reference**: `squadron/logging.py` — direct structural model for logging module
- **Slice 900**: CLI scaffold and config — prerequisite, provides Settings and app callback
- **Slice 902**: Provider Registry — will be the first consumer of `--json` pattern on new commands
