---
docType: architecture
layer: project
project: trading
archIndex: 900
component: foundation-cleanup
relatedSlices: []
riskLevel: low
dateCreated: 20260327
dateUpdated: 20260809
status: complete
---

# Foundation & Cleanup Architecture

## Overview

This initiative establishes the cross-cutting project infrastructure that all other initiatives depend on: a modern CLI framework, centralized configuration, structured logging, provider registry, and cleanup of deprecated code paths. It transforms the project from a collection of scripts with ad-hoc argparse into a properly structured, discoverable CLI application.

**Scope**: CLI framework, configuration system, logging, provider registry, deprecated code removal, and project packaging. This is infrastructure work — it produces the foundation that initiatives 100-180 build upon.

**Scope extension — the maintenance band (PM, 2026-08-03)**: 900-999 is also the project's **maintenance band**, and this document is the architecture that band answers to. Once initiatives 100-180 have shipped and their bands are complete, defects found in delivered code have no live band to land in; reopening a closed initiative to host a bug fix is worse than hosting it here. Maintenance slices in this band may therefore touch **any** layer of the codebase, including acquisition, data-quality, and serving modules that a different initiative originally delivered.

Two constraints keep that from becoming a licence to do feature work under a maintenance label:

- **Corrective, not additive.** A maintenance slice fixes behavior that is already specified and already wrong — a defect, a mis-scoped constant, a misleading operator surface. New capability belongs to the initiative that owns the layer, even when the code sits in a file this band has previously touched.
- **The originating initiative's contracts are honored, not rewritten.** A maintenance slice consumes the interfaces its target layer already publishes (e.g. slice 145's `update_data_gaps`-as-single-writer rule) and may depend on them freely. It does not redefine them. A fix that requires changing a contract is escalated to the owning initiative rather than absorbed here.

The dependency-direction statement below — that 900 is a prerequisite for 100-180 — describes the **foundation** slices (900-909ish), which genuinely precede everything. It does not describe maintenance slices, which by construction come after the work they correct and depend on it.

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
