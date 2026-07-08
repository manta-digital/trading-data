---
docType: review
layer: project
reviewType: slice
slice: logging-and-output-formatting
project: squadron
verdict: PASS
sourceDocument: project-documents/user/slices/901-slice.logging-and-output-formatting.md
aiModel: moonshotai/kimi-k2.5
status: complete
dateCreated: 20260329
dateUpdated: 20260329
---

# Review: slice — slice 901

**Verdict:** PASS
**Model:** moonshotai/kimi-k2.5

## Findings

### [PASS] Slice aligns with architectural goals for structured logging and CLI output

The slice correctly implements the architecture's requirements for structured logging (JSON/text formats via `MT_LOG_LEVEL`/`MT_LOG_FORMAT`), elimination of `print()` and loguru calls, and shared output formatting with `--json` support. The technical approach mirrors the Squadron pattern referenced in the architecture, using stdlib `logging` with `_JsonFormatter` and `setup_logging(settings)` called from the CLI app callback.

### [PASS] Correct separation of concerns between Settings and output formatting

The slice properly consumes the Settings class (pydantic-settings for environment variables) from Slice 900 for log configuration, respecting the architecture's mandate that "Runtime/environment config uses pydantic-settings from environment variables" and maintains separation from the TOML-based ConfigManager system. The distinction between stderr (logging) and stdout (command output) enables the piping scenarios envisioned in the architecture.

### [PASS] Scope appropriately excludes provider registry and new CLI commands

The slice correctly identifies its boundaries: it excludes "New CLI commands (that's slice 902+)" and focuses on the logging/output plumbing that Slice 900's CLI scaffold requires. The migration of existing loguru usage in `data/base/` modules aligns with the architecture's "Clean codebase" goal to remove mixed logging patterns.

### [PASS] Integration contract matches anticipated slice dependencies

The slice declares consuming from Slice 900 (Settings, app callback, ctx.obj pattern) and providing interfaces to Slices 902-904. This matches the architecture's anticipated slice breakdown where logging/output formatting is foundational infrastructure that provider registry and other slices depend upon. The per-command `--json` flag pattern is an acceptable implementation detail given Typer's sub-app limitations, and still satisfies the architectural requirement that "All commands support `--json` output via a shared output formatter."

---

## Debug: Prompt & Response

### System Prompt

You are an architectural reviewer. Your task is to evaluate whether a design
document aligns with a parent architecture document and its stated goals.

Evaluation criteria:
- Alignment with stated architectural goals and principles
- Violations of architectural boundaries or layer responsibilities
- Scope creep beyond what the architecture defines
- Dependency directions are correct
- Integration points match what consuming/providing slices expect
- Common antipatterns: over-engineering, under-specification, hidden dependencies

Important context:
- The `parent` field in slice frontmatter refers to the slice plan document,
  not the architecture document. Do not flag this as an error.

CRITICAL: Your verdict and findings MUST be consistent.
- If verdict is CONCERNS or FAIL, include at least one finding with that severity.
- If no CONCERN or FAIL findings exist, verdict MUST be PASS.
- Every finding MUST use the exact format: ### [SEVERITY] Title

Report your findings using severity levels:

## Summary
[overall assessment: PASS | CONCERNS | FAIL]

## Findings

### [PASS|CONCERN|FAIL] Finding title
Description with specific references.


### User Prompt

Review the following document for architectural alignment:

**Input document:** project-documents/user/slices/901-slice.logging-and-output-formatting.md
**Architecture document:** project-documents/user/architecture/900-arch.foundation-cleanup.md

Read both documents, then evaluate the input against the architecture.
Follow referenced files as needed to understand dependencies and integration points.
Report your findings using the severity format described in your instructions.


## File Contents

### input: 901-slice.logging-and-output-formatting.md

```
---
docType: slice-design
slice: logging-and-output-formatting
project: trading
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [900]
interfaces: [902, 903, 904]
dateCreated: 20260329
dateUpdated: 20260329
status: not_started
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

```bash
# Verify logging formats
MT_LOG_LEVEL=DEBUG MT_LOG_FORMAT=text uv run mt config list 2>/dev/null
# Expected: Rich table of config keys (no log output because we redirected stderr)

MT_LOG_LEVEL=DEBUG MT_LOG_FORMAT=text uv run mt config list >/dev/null
# Expected: text-formatted log lines on stderr (stdout suppressed)

MT_LOG_LEVEL=DEBUG MT_LOG_FORMAT=json uv run mt config list >/dev/null
# Expected: JSON log lines on stderr, one per line, each parseable by jq

# Verify --json output on config commands
uv run mt config list --json
# Expected: JSON array with objects containing key, value, source, description

uv run mt config list --json | jq '.[0].key'
# Expected: prints a config key name (proves valid JSON)

uv run mt config get output_format --json
# Expected: {"key": "output_format", "value": "text", "source": "default"}

uv run mt config get output_format --json | jq '.source'
# Expected: "default"

# Verify loguru removal
uv run python -c "import loguru"
# Expected: ModuleNotFoundError (loguru no longer installed)

# Verify no loguru imports remain
grep -r "from loguru" src/
# Expected: no output

# Run full test suite
uv run pytest test/ -v
# Expected: all tests pass
```

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

```

### against: 900-arch.foundation-cleanup.md

```
---
docType: architecture
layer: project
project: trading
archIndex: 900
component: foundation-cleanup
relatedSlices: []
riskLevel: low
dateCreated: 20260327
dateUpdated: 20260328
status: in_progress
---

# Foundation & Cleanup Architecture

## Overview

This initiative establishes the cross-cutting project infrastructure that all other initiatives depend on: a modern CLI framework, centralized configuration, structured logging, provider registry, and cleanup of deprecated code paths. It transforms the project from a collection of scripts with ad-hoc argparse into a properly structured, discoverable CLI application.

**Scope**: CLI framework, configuration system, logging, provider registry, deprecated code removal, and project packaging. This is infrastructure work — it produces the foundation that initiatives 100-180 build upon.

**Motivation**: The current codebase has a functional but ad-hoc CLI (raw argparse with if/elif dispatch), mixed logging (print + loguru), no centralized config, and deprecated code still in the import path. Every future initiative will need CLI commands, configuration, and logging. Building this foundation first prevents each initiative from solving these problems independently.

## Design Goals

- **Discoverable CLI**: Every capability is reachable via `mt --help` and subcommand help. Users (and AI agents) can explore the system without reading source code. All commands support `--json` for machine consumption.

- **Centralized configuration**: Two complementary config systems with distinct roles. Persistent preferences use TOML with clear precedence (CLI flags > project TOML > user TOML > defaults). Runtime/environment config (credentials, log level) uses pydantic-settings from environment variables. These do not overlap — there is no precedence conflict between them. Adding a new config key means editing one definition, not scattering defaults across modules.

- **Structured logging**: Consistent, configurable logging throughout the application. JSON format for structured analysis, text format for human debugging. No more `print()` statements for operational output.

- **Provider registry**: Enum-keyed provider registration with alias support. No string-based dispatch anywhere. Adding a new data provider means implementing a protocol and registering it — not modifying conditional logic.

- **Clean codebase**: Remove deprecated code paths, establish the src layout as canonical, and ensure the package is publishable to PyPI.

## Architectural Principles

- **Model on Squadron**: The CLI framework, config system, logging, and provider patterns are proven in the Squadron project. Replicate the structural patterns, not the domain logic. This saves design time and ensures consistency across the manta ecosystem.

- **No magic strings**: All dispatch, status values, provider names, and command identifiers use enums or typed constants defined in one place. This is a project-wide rule, but this initiative enforces it by establishing the pattern in the registry and config systems.

- **Explicit failure**: Config missing? Error. Provider not registered? Error with available options listed. DB not reachable? Error with connection details. Never silently fall back to a default that masks a misconfiguration.

- **CLI is the verification surface**: If a feature can't be exercised through `mt`, it doesn't exist yet. Every initiative's work must be visible through CLI commands before it's considered complete.

- **Minimal new dependencies**: Typer, Rich, and tomli_w are the only new dependencies this initiative introduces. httpx replaces aiohttp. pydantic-settings for environment config. No dependency bloat.

## Current State

The project has:
- Raw argparse CLI with nested if/elif command dispatch in `ohlcoptions.py` and `newsoptions.py`
- No `--help` discoverability beyond what argparse generates
- No `--json` output mode
- Environment variables loaded via dotenv with scattered defaults
- Mixed logging: `print()`, `logger.info()` (loguru), and `logger.success()`
- Deprecated code in `market/deprecated/slice025_2025_01/` still imported by the main entry point (`ohlc.py`)
- `src/manta_trading/` layout (just completed), package name `manta-trading`
- No console_scripts entry point — CLI invoked via `python -m`
- AlphaVantage provider partially abstracted but with string-based identification

## Envisioned State

After this initiative:

**CLI Layer** (`src/manta_trading/cli/`):
- Root Typer app registered as `mt` console script
- Sub-apps for command groups: `mt status`, `mt config`, `mt provider`, `mt data`, `mt db`
- Global `--version` flag, `no_args_is_help=True` on all groups
- All commands support `--json` output via a shared output formatter
- Rich tables for human-readable status display with color-coded health indicators

**Config Layer** (`src/manta_trading/config/`):

Two separate systems with no overlap:

- **Settings** (pydantic-settings): Environment/runtime config with `MT_` prefix. Handles credentials (`MT_ALPHAVANTAGE_API_KEY`), log level, log format. Read from env vars and `.env` file. Validated at instantiation — missing required values raise immediately. Created once in the app callback, passed to subsystems. Not a singleton — each CLI invocation is a fresh process with fresh Settings.

- **CONFIG_KEYS / ConfigManager**: Persistent user preferences via TOML. Handles default provider, output format, data directory paths. Three-level precedence: CLI flags > project `.manta-trading.toml` > user `~/.config/manta-trading/config.toml` > typed defaults. Credentials never go here.

- `mt config set/get/list/path` commands manage TOML config only. Environment config is managed by the user's shell environment or `.env` file.

**Logging** (`src/manta_trading/logging.py`):
- Centralized `setup_logging(settings)` called at CLI entry
- JSON and text formatters, configurable via `MT_LOG_FORMAT`
- `get_logger(__name__)` pattern throughout codebase
- All existing `print()` and loguru calls migrated

**Provider Registry** (`src/manta_trading/providers/`):
- `ProviderType` enum (ALPHA_VANTAGE, DATABENTO, FLAT_FILE, etc.)
- `ProviderProfile` frozen dataclass:
  - `name: str` — display name
  - `provider_type: ProviderType` — enum key, used for all dispatch
  - `base_url: str | None` — API endpoint
  - `api_key_env: str | None` — name of env var holding credential (e.g. "MT_DATABENTO_API_KEY")
  - `rate_limit: RateLimit | None` — requests/period constraint
  - `aliases: tuple[str, ...]` — short names for CLI (e.g. ("bento", "db"))
  - `description: str` — human-readable description
- Credential access: Settings class reads env var at instantiation. If `api_key_env` is set and the env var is missing, provider construction raises with a clear message naming the missing variable. This happens at provider instantiation, before any network call.
- Built-in profiles + user-defined profiles via TOML config
- Alias resolution: `mt status bento` → alias lookup → `ProviderType.DATABENTO`
- `mt provider list/status/test` commands
- Auth strategy pattern for credential validation per provider

**Package**:
- `[project.scripts] mt = "manta_trading.cli.app:app"` entry point
- Version via `importlib.metadata.version()`, fallback to `"dev"`
- uv.lock generated and committed
- Deprecated code removed (market/deprecated/ directory deleted)
- Old CLI entry points (ohlc.py, news.py) removed — sole user, no migration needed

## Technical Considerations

- **Typer + Rich compatibility**: Typer 0.9+ has built-in Rich integration. The `rich` parameter on Typer() enables automatic Rich formatting of help text. Verify version compatibility with current Python 3.12.

- **Config file locations**: Follow XDG convention on Linux/macOS. User config at `~/.config/manta-trading/config.toml`. Project config at `.manta-trading.toml` in project root. The `mt config path` command shows resolved locations.

- **Provider credentials**: API keys stay in environment variables (MT_ALPHAVANTAGE_API_KEY, MT_DATABENTO_API_KEY), never in TOML config files. The Settings class reads all `MT_*` env vars at instantiation via pydantic-settings. Provider profiles reference the env var name — the credential is read once when Settings is constructed in the app callback. Missing credentials fail explicitly at provider construction with the env var name in the error message.

- **Sync-first architecture**: The codebase was recently converted from async to sync (TimescaleDB layer, Jan 2026). CLI commands are sync Typer functions. The only async code is provider I/O (HTTP fetches) which uses `asyncio.run()` at the command boundary. The existing daily pipeline (`marketdb.py`, `marketservice.py`) is fully sync. No part of the application expects a pre-existing event loop.

- **Deprecated code removal**: The `market/deprecated/` directory and its imports in `ohlc.py` need to be removed. The existing daily pipeline (`marketdb.py`, `marketservice.py`) stays — it works and will be integrated into the new CLI. The minute data entry point switches from deprecated to the new service in `data/historical_minute/`.

- **Version management**: Use `importlib.metadata.version("manta-trading")` to read version from installed package metadata. Single source of truth in `pyproject.toml`. Falls back to `"dev"` if metadata unavailable (pre-install). No `__version__` string maintained separately.

- **httpx migration**: Replace aiohttp with httpx in the AlphaVantage client. httpx supports both sync and async from the same library, aligning with the project's sync-first approach. This is a contained change within `api/alphavantage/`.

## Anticipated Slices

- **CLI scaffold and config system**: Typer app structure, sub-app registration, config manager with TOML persistence, Settings class, `mt config` commands. This is the skeleton everything else hangs on.

- **Logging and output formatting**: Structured logging setup, Rich output formatter with `--json` support, migration of existing print/loguru calls. Shared output utilities that all future commands use.

- **Provider registry and status**: ProviderType enum, ProviderProfile, built-in profiles, alias resolution, `mt provider` and `mt status` commands. Auth strategy pattern for credential checking.

- **Deprecated code cleanup and packaging**: Remove deprecated directory, fix import paths, wire up console_scripts entry point, generate uv.lock, verify package installs cleanly.

## Related Work

- **Squadron** (`~/source/repos/manta/squadron/`): CLI framework, config system, logging, provider/auth patterns are directly modeled on Squadron's implementation. Key files: `cli/app.py`, `config/manager.py`, `config/keys.py`, `providers/profiles.py`, `logging.py`.
- **Concept**: `user/project-guides/000-concept.trading.md` — project goals, system boundaries, scaling path.
- **Initiative Plan**: `user/project-guides/001-initiative-plan.trading.md` — dependency graph showing 900 as prerequisite for all other initiatives.
- **Archived Architecture**: `user/archive/050-arch.data-storage-and-acquisition.md` — prior comprehensive architecture, Tier 1 content informs initiatives 100-160.

```

### Rules Injected

None

### Raw Response

 ## Summary
PASS

## Findings

### [PASS] Slice aligns with architectural goals for structured logging and CLI output
The slice correctly implements the architecture's requirements for structured logging (JSON/text formats via `MT_LOG_LEVEL`/`MT_LOG_FORMAT`), elimination of `print()` and loguru calls, and shared output formatting with `--json` support. The technical approach mirrors the Squadron pattern referenced in the architecture, using stdlib `logging` with `_JsonFormatter` and `setup_logging(settings)` called from the CLI app callback.

### [PASS] Correct separation of concerns between Settings and output formatting
The slice properly consumes the Settings class (pydantic-settings for environment variables) from Slice 900 for log configuration, respecting the architecture's mandate that "Runtime/environment config uses pydantic-settings from environment variables" and maintains separation from the TOML-based ConfigManager system. The distinction between stderr (logging) and stdout (command output) enables the piping scenarios envisioned in the architecture.

### [PASS] Scope appropriately excludes provider registry and new CLI commands
The slice correctly identifies its boundaries: it excludes "New CLI commands (that's slice 902+)" and focuses on the logging/output plumbing that Slice 900's CLI scaffold requires. The migration of existing loguru usage in `data/base/` modules aligns with the architecture's "Clean codebase" goal to remove mixed logging patterns.

### [PASS] Integration contract matches anticipated slice dependencies
The slice declares consuming from Slice 900 (Settings, app callback, ctx.obj pattern) and providing interfaces to Slices 902-904. This matches the architecture's anticipated slice breakdown where logging/output formatting is foundational infrastructure that provider registry and other slices depend upon. The per-command `--json` flag pattern is an acceptable implementation detail given Typer's sub-app limitations, and still satisfies the architectural requirement that "All commands support `--json` output via a shared output formatter."
