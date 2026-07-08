---
docType: slice-design
slice: cli-scaffold-and-config-system
project: trading
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: []
interfaces: [901, 902, 903, 904]
dateCreated: 20260328
dateUpdated: 20260329
status: complete
---

# Slice 900: CLI Scaffold and Config System

## Overview

Establish the Typer CLI framework and dual config system that all subsequent slices and initiatives depend on. After this slice, `mt --help` works, `mt config` manages persistent settings, and the Settings class provides validated environment config. Every future command in the project hangs off this skeleton.

## Technical Decisions

### CLI Framework: Typer + Rich

- Root app: `typer.Typer(name="mt", help="Manta Trading CLI", no_args_is_help=True)`
- Sub-apps registered via `app.add_typer()` for command groups
- `@app.callback()` handles `--version` flag (eager option, exits immediately)
- Version read via `importlib.metadata.version("manta-trading")` with `"dev"` fallback
- Entry point: `[project.scripts] mt = "manta_trading.cli.app:app"` in pyproject.toml

### Directory Structure

```
src/manta_trading/
├── cli/
│   ├── __init__.py
│   ├── app.py              # Root Typer app, sub-app registration, callback
│   └── commands/
│       ├── __init__.py
│       └── config.py       # mt config set/get/list/path
├── config/
│   ├── __init__.py         # Settings class (pydantic-settings)
│   ├── keys.py             # CONFIG_KEYS definitions
│   └── manager.py          # load_config, get_config, set_config, config paths
└── ... (existing modules unchanged)
```

### Config System: Two Separate Systems

**Settings (env vars via pydantic-settings):**
- Class: `Settings(BaseSettings)` with `model_config = SettingsConfigDict(env_prefix="MT_")`
- Fields: `log_level: str = "INFO"`, `log_format: str = "text"`, `alphavantage_api_key: str | None = None`, `databento_api_key: str | None = None`, `db_url: str | None = None`
- Loaded from environment and `.env` file
- Validated at instantiation — pydantic raises `ValidationError` for type mismatches
- Created once in app callback, stored in `typer.Context` for downstream commands

**ConfigManager (TOML persistent config):**
- User config: `~/.config/manta-trading/config.toml`
- Project config: `.manta-trading.toml` (in cwd)
- Precedence: project TOML > user TOML > typed defaults
- Keys defined in `CONFIG_KEYS: dict[str, ConfigKey]` with name, type, default, description
- Initial keys: `default_provider`, `output_format` ("text" or "json"), `data_dir`
- Read: `tomllib` (stdlib). Write: `tomli_w`.
- Unknown keys rejected with available keys listed in error

### Wiring Pattern

```
mt invoked
  → app.callback(): load .env, create Settings, store in ctx.obj
  → command dispatched (e.g., mt config list)
    → command function receives ctx, reads Settings from ctx.obj
    → uses ConfigManager for TOML operations
```

The `ctx.obj` dict carries Settings and any other shared state. No globals, no singletons. Each `mt` invocation is a fresh process.

## Data Flows

### Config Read Flow (mt config get)
```
User: mt config get default_provider
  → config_get command
    → ConfigManager.get_config("default_provider")
      → load project TOML (if exists)
      → load user TOML (if exists)
      → merge with defaults from CONFIG_KEYS
      → return resolved value + source ("project", "user", or "default")
    → print value and source
```

### Config Write Flow (mt config set)
```
User: mt config set default_provider alpha_vantage --project
  → config_set command
    → validate key exists in CONFIG_KEYS
    → validate value type matches CONFIG_KEYS definition
    → ConfigManager.set_config("default_provider", "alpha_vantage", project=True)
      → read existing project TOML
      → update key
      → write project TOML
    → confirm to user
```

## Component Interactions

- **cli/app.py** imports sub-apps from `cli/commands/` and registers them
- **cli/app.py callback** creates `Settings` and stores in `ctx.obj`
- **cli/commands/config.py** uses `ConfigManager` from `config/manager.py`
- **config/keys.py** defines `ConfigKey` dataclass and `CONFIG_KEYS` dict — single source of truth
- **config/manager.py** handles TOML read/write, path resolution, precedence merge

No other existing modules are modified in this slice. The CLI scaffold sits alongside the existing code without disturbing it.

## Dependencies

- **New packages**: `typer[all]` (includes Rich), `pydantic-settings`, `tomli_w`
- **No cross-slice dependencies** — this is the foundation slice

## Success Criteria

1. `mt --help` displays app name, description, and available commands
2. `mt --version` displays version from package metadata (or "dev")
3. `mt config list` shows all CONFIG_KEYS with current values and sources
4. `mt config get <key>` returns value and indicates source (project/user/default)
5. `mt config set <key> <value>` persists to user TOML by default
6. `mt config set <key> <value> --project` persists to project TOML
7. `mt config path` shows resolved user and project config file locations
8. Invalid config key produces error listing available keys
9. `Settings` validates `MT_*` env vars and raises on type mismatch
10. Sub-app registration pattern works: placeholder `mt status` sub-app returns stub message (proves the pattern for 901+)

## Verification Walkthrough

```bash
# Install in development mode
uv sync --all-extras
# Actual: installs all deps including dev extras

# Verify entry point
uv run mt --help
# Actual: shows "Manta Trading CLI" header, lists: config, status commands
# Caveat: typer>=0.15.0 (not typer[all]) — the [all] extra was removed in newer versions

uv run mt --version
# Actual: "mt version 0.2.1" (reads from package metadata)

# Config management
uv run mt config path
# Actual: shows ~/.config/manta-trading/config.toml and .manta-trading.toml paths with existence status

uv run mt config list
# Actual: Rich table showing default_provider, output_format, data_dir with defaults and source column

uv run mt config set output_format json
# Actual: "Set output_format = json (user config)"

uv run mt config get output_format
# Actual: 'output_format = json  (source: user)'

uv run mt config set output_format text --project
# Actual: "Set output_format = text (project config)"

uv run mt config get output_format
# Actual: 'output_format = text  (source: project)' — project overrides user

uv run mt config set nonexistent_key foo
# Actual: error exit code 1, message: "Unknown config key: 'nonexistent_key'. Available keys: data_dir, default_provider, output_format"

# Verify Settings reads env vars
MT_LOG_LEVEL=DEBUG uv run mt config list
# Actual: command works (Settings created with DEBUG level, visible in future logging slice)

# Verify stub sub-app
uv run mt status overview
# Actual: "Status commands not yet implemented"
# Caveat: must use `mt status overview` (not just `mt status`) — the status sub-app has no_args_is_help=True

# Run tests
uv run pytest test/unit/test_settings.py test/unit/test_config_manager.py test/unit/test_cli_app.py test/unit/test_cli_config.py -v
# Actual: 39 passed
```

## Related Work

- **Squadron reference**: `cli/app.py`, `config/manager.py`, `config/keys.py` — structural patterns to replicate
- **Architecture**: `900-arch.foundation-cleanup.md` — full design context
- **Next slice**: 901 (Logging and Output Formatting) will add `--json` support to these commands and structured logging
