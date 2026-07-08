---
docType: tasks
slice: cli-scaffold-and-config-system
project: trading
lld: user/slices/900-slice.cli-scaffold-and-config-system.md
dependencies: []
projectState: src/ layout complete, pyproject.toml renamed to manta-trading, no CLI framework, no config system, existing argparse CLI in market/ohlcoptions.py
dateCreated: 20260328
dateUpdated: 20260329
status: complete
---

# Tasks: CLI Scaffold and Config System

## Context

Working on the CLI Scaffold and Config System slice (900) of the Foundation & Cleanup initiative. The project has been restructured to `src/manta_trading/` with package name `manta-trading`. No CLI framework or config system exists yet — the current CLI is raw argparse. This slice establishes the Typer CLI skeleton and dual config system (Settings for env vars, ConfigManager for TOML) that all future slices depend on.

**Dependencies**: None — this is the foundation slice.
**Delivers**: Working `mt` CLI with `--help`, `--version`, `mt config` commands, Settings class, ConfigManager.
**Next slice**: 901 (Logging and Output Formatting).

## Tasks

### Phase 1: Dependencies and Project Setup

- [x] **1.1 Add new dependencies to pyproject.toml**
  - [x] Add `typer[all]` (includes Rich integration) to project dependencies
  - [x] Add `pydantic-settings` to project dependencies
  - [x] Add `tomli_w` to project dependencies
  - [x] Add `httpx` to project dependencies (replacing aiohttp, used starting slice 903 but added now to avoid churn)
  - [x] Run `uv sync` to install
  - [x] Success: `python -c "import typer; import pydantic_settings; import tomli_w; import httpx"` succeeds

- [x] **1.2 Create directory structure**
  - [x] Create `src/manta_trading/cli/__init__.py`
  - [x] Create `src/manta_trading/cli/commands/__init__.py`
  - [x] Create `src/manta_trading/config/__init__.py`
  - [x] Success: directories exist, `from manta_trading.cli import *` and `from manta_trading.config import *` do not error

- [x] **1.3 Commit**: `chore: add CLI and config dependencies and directory structure`

### Phase 2: Config System — Settings (env vars)

- [x] **2.1 Implement Settings class**
  - [x] Create `src/manta_trading/config/__init__.py` with `Settings(BaseSettings)` class
  - [x] Define `ENV_FILE = ".env"` constant in the module (not scattered as a string literal)
  - [x] Configure `SettingsConfigDict(env_prefix="MT_", env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")`
  - [x] Fields: `log_level: str = "INFO"`, `log_format: str = "text"`, `alphavantage_api_key: str | None = None`, `databento_api_key: str | None = None`, `db_url: str | None = None`
  - [x] Success: `Settings()` instantiates with defaults; `MT_LOG_LEVEL=DEBUG` env var overrides `log_level`

- [x] **2.2 Test Settings class**
  - [x] Test default values when no env vars set
  - [x] Test env var override with `MT_` prefix (e.g., `MT_LOG_LEVEL=DEBUG`)
  - [x] Test `.env` file loading (use tmp file in test)
  - [x] Test `extra="ignore"` — unknown `MT_UNKNOWN_KEY` does not raise
  - [x] Test type validation — `MT_LOG_LEVEL` accepts string, rejects non-string if typed
  - [x] Success: all tests pass

- [x] **2.3 Commit**: `feat: add Settings class with pydantic-settings`

### Phase 3: Config System — ConfigManager (TOML)

- [x] **3.1 Implement ConfigKey and CONFIG_KEYS**
  - [x] Create `src/manta_trading/config/keys.py`
  - [x] Define `ConfigKey` frozen dataclass: `name: str`, `type_: type`, `default: object`, `description: str`
  - [x] Define `CONFIG_KEYS: dict[str, ConfigKey]` with initial keys:
    - [x] `default_provider` (str, default: None, "Default data provider for commands")
    - [x] `output_format` (str, default: "text", "Output format: text or json")
    - [x] `data_dir` (str, default: None, "Base directory for local data files")
  - [x] Success: `CONFIG_KEYS` is importable, all keys have name/type/default/description

- [x] **3.2 Implement ConfigManager**
  - [x] Create `src/manta_trading/config/manager.py`
  - [x] Implement `user_config_path() -> Path` — returns `~/.config/manta-trading/config.toml`
  - [x] Implement `project_config_path(cwd: str = ".") -> Path` — returns `{cwd}/.manta-traded.toml`
  - [x] Implement `_read_toml(path: Path) -> dict` — reads TOML file, returns empty dict if not exists
  - [x] Implement `load_config(cwd: str = ".") -> dict[str, object]` — merge defaults → user → project
  - [x] Implement `get_config(key: str, cwd: str = ".") -> tuple[object, str]` — returns (value, source) where source is "project", "user", or "default"
  - [x] Implement `set_config(key: str, value: str, *, project: bool = False, cwd: str = ".") -> None` — validates key exists in CONFIG_KEYS, coerces type, writes TOML
  - [x] Unknown key in `set_config` raises `KeyError` with message listing available keys
  - [x] Success: can write and read back config values from both user and project TOML files

- [x] **3.3 Test ConfigManager**
  - [x] Test `load_config` returns defaults when no TOML files exist
  - [x] Test `set_config` + `get_config` roundtrip for user config (use tmp dir)
  - [x] Test `set_config` + `get_config` roundtrip for project config (use tmp dir)
  - [x] Test precedence: project TOML overrides user TOML overrides defaults
  - [x] Test unknown key raises `KeyError` with available keys listed
  - [x] Test `_read_toml` returns empty dict for nonexistent file (no error)
  - [x] Success: all tests pass

- [x] **3.4 Commit**: `feat: add ConfigManager with TOML persistence`

### Phase 4: CLI Root App

- [x] **4.1 Implement root Typer app**
  - [x] Create `src/manta_trading/cli/app.py`
  - [x] Create root app: `app = typer.Typer(name="mt", help="Manta Trading CLI", no_args_is_help=True)`
  - [x] Implement `@app.callback()` with:
    - [x] `--version` flag (eager option, prints version via `importlib.metadata.version("manta-trading")` with `"dev"` fallback, raises `typer.Exit()`)
    - [x] Create `Settings()` instance, store in `ctx.ensure_object(dict)["settings"]`
    - [x] No explicit `dotenv.load_dotenv()` — pydantic-settings handles `.env` loading via `env_file` in SettingsConfigDict
  - [x] Create stub status sub-app: `status_app = typer.Typer(name="status", help="System status", no_args_is_help=True)` with one `overview` command that prints "Status commands not yet implemented" and exits cleanly
  - [x] Register: `app.add_typer(status_app, name="status")`
  - [x] Success: `python -c "from manta_trading.cli.app import app"` succeeds without error

- [x] **4.2 Add console_scripts entry point**
  - [x] Add `[project.scripts]` section to pyproject.toml: `mt = "manta_trading.cli.app:app"`
  - [x] Run `pip install -e .` to register entry point
  - [x] Success: `mt --help` works from terminal, shows app name and available commands

- [x] **4.3 Test root app**
  - [x] Test `mt --help` output contains "Manta Trading CLI"
  - [x] Test `mt --version` output contains version string or "dev"
  - [x] Test `mt` with no args shows help (no_args_is_help)
  - [x] Test `mt status` sub-app is reachable
  - [x] Test Settings is stored in ctx.obj after callback (use `CliRunner`)
  - [x] Success: all tests pass

- [x] **4.4 Commit**: `feat: add Typer root app with mt entry point`

### Phase 5: Config CLI Commands

- [x] **5.1 Implement mt config commands**
  - [x] Create `src/manta_trading/cli/commands/config.py`
  - [x] Create `config_app = typer.Typer(name="config", help="Manage configuration", no_args_is_help=True)`
  - [x] Implement `mt config list` — displays table of all CONFIG_KEYS with current value and source (use Rich Table for now, `--json` comes in slice 901)
  - [x] Implement `mt config get <key>` — displays value and source for one key; error if key unknown
  - [x] Implement `mt config set <key> <value>` with `--project` flag — writes to user or project TOML
  - [x] Implement `mt config path` — displays user and project config file paths, indicates which exist
  - [x] Register in app.py: `app.add_typer(config_app, name="config")`
  - [x] Success: all four config subcommands work as described

- [x] **5.2 Test mt config commands**
  - [x] Test `mt config list` shows all keys with defaults
  - [x] Test `mt config get output_format` returns "text" (source: default)
  - [x] Test `mt config set output_format json` + `mt config get output_format` returns "json" (source: user)
  - [x] Test `mt config set output_format text --project` overrides user setting
  - [x] Test `mt config get nonexistent_key` produces error with available keys
  - [x] Test `mt config path` shows both file paths
  - [x] Use `CliRunner` with `tmp_path` for isolation — no side effects on real config
  - [x] Success: all tests pass

- [x] **5.3 Commit**: `feat: add mt config commands`

### Phase 6: Final Verification

- [x] **6.1 End-to-end verification**
  - [x] Run full verification walkthrough from slice design document
  - [x] Ensure `mt --help`, `mt --version`, `mt config list/get/set/path`, `mt status` all work
  - [x] Run full test suite: `pytest test/` — no regressions in existing tests
  - [x] Success: all verification steps pass, no test regressions
