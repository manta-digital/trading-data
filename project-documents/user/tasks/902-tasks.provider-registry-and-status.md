---
docType: tasks
slice: provider-registry-and-status
project: trading
lld: user/slices/902-slice.provider-registry-and-status.md
dependencies: [900, 901]
projectState: Typer CLI scaffold complete with mt entry point, Settings class (log_level, log_format, alphavantage_api_key, databento_api_key, db_url), ConfigManager with TOML persistence, structured logging (setup_logging, get_logger), shared CLI output formatter (print_result, print_error, make_table). Stub mt status overview command exists. No provider registry or provider CLI commands yet.
dateCreated: 20260330
dateUpdated: 20260331
status: complete
---

# Tasks: Provider Registry and Status

## Context

Working on the Provider Registry and Status slice (902) of the Foundation & Cleanup initiative. Slices 900 (CLI scaffold, config) and 901 (logging, output formatter) are complete and merged to main. This slice adds a centralized provider registry with enum-keyed dispatch, frozen profile definitions, alias resolution, credential validation, and CLI commands for provider introspection and system status.

**Dependencies**: Slices 900 and 901 — complete.
**Delivers**: `providers/` package (types, profiles, auth, errors), `mt provider list/status/test` commands, `mt status` top-level health command.
**Next slice**: 903 (Deprecated Code Removal and httpx Migration).

**Reference implementation**: `~/source/repos/manta/squadron/src/squadron/providers/` — structural model for the provider registry, profiles, and auth patterns.

## Tasks

### Phase 1: Provider Types and Errors

- [x] **1.1 Create provider types module**
  - [x] Create `src/manta_trading/providers/types.py`
  - [x] Implement `ProviderType(StrEnum)` with members: `ALPHA_VANTAGE = "alphavantage"`, `DATABENTO = "databento"`, `FLAT_FILE = "flatfile"`
  - [x] Implement `AuthType(StrEnum)` with members: `API_KEY = "api_key"`, `NONE = "none"`
  - [x] Implement `RateLimit` frozen dataclass with fields: `requests_per_minute: int`, `daily_limit: int | None = None`
  - [x] Include `from __future__ import annotations`
  - [x] Success: all three types import cleanly, `ProviderType.ALPHA_VANTAGE.value == "alphavantage"`, `RateLimit` is frozen

- [x] **1.2 Create provider errors module**
  - [x] Create `src/manta_trading/providers/errors.py`
  - [x] Implement `ProviderError(Exception)` — base exception
  - [x] Implement `ProviderAuthError(ProviderError)` — credential errors
  - [x] Success: both exceptions import cleanly, `ProviderAuthError` is a subclass of `ProviderError`

- [x] **1.3 Test types and errors**
  - [x] Create `test/unit/test_provider_types.py`
  - [x] Test `ProviderType` is a `StrEnum` with 3 members; values are lowercase strings
  - [x] Test `ProviderType` members serialize to their string values (e.g., `str(ProviderType.ALPHA_VANTAGE) == "alphavantage"`)
  - [x] Test `AuthType` is a `StrEnum` with 2 members
  - [x] Test `RateLimit` is frozen (assigning to a field raises `FrozenInstanceError`)
  - [x] Test `RateLimit` default: `daily_limit` is `None` when not specified
  - [x] Test `ProviderAuthError` is a subclass of both `ProviderError` and `Exception`
  - [x] Success: all tests pass via `pytest test/unit/test_provider_types.py -v`

**Commit**: `feat: add provider types, enums, and error hierarchy`

### Phase 2: Provider Profiles

- [x] **2.1 Create profiles module**
  - [x] Create `src/manta_trading/providers/profiles.py`
  - [x] Implement `ProviderProfile` frozen dataclass with fields: `name: str`, `provider_type: ProviderType`, `base_url: str | None = None`, `api_key_env: str | None = None`, `rate_limit: RateLimit | None = None`, `aliases: tuple[str, ...] = ()`, `auth_type: AuthType = AuthType.API_KEY`, `description: str = ""`
  - [x] Define `BUILT_IN_PROFILES: dict[str, ProviderProfile]` with entries for alphavantage, databento, flatfile as specified in slice design
  - [x] Implement `get_all_profiles() -> dict[str, ProviderProfile]` — returns copy of `BUILT_IN_PROFILES`
  - [x] Implement `get_profile(name: str) -> ProviderProfile` — returns profile by canonical name, raises `KeyError` with available profile names if not found
  - [x] Implement `resolve_alias(name_or_alias: str) -> str` — maps alias to canonical name; canonical names pass through; raises `KeyError` with available names and aliases if not found
  - [x] Success: all functions import cleanly, `get_profile("alphavantage")` returns expected profile, `resolve_alias("av")` returns `"alphavantage"`

- [x] **2.2 Test profiles module**
  - [x] Create `test/unit/test_provider_profiles.py`
  - [x] Test `ProviderProfile` is frozen (assigning to a field raises)
  - [x] Test `BUILT_IN_PROFILES` contains 3 entries: alphavantage, databento, flatfile
  - [x] Test each built-in profile has correct `provider_type`, `api_key_env`, `auth_type`, and `aliases`
  - [x] Test `get_all_profiles()` returns all 3 profiles
  - [x] Test `get_profile("alphavantage")` returns correct profile
  - [x] Test `get_profile("nonexistent")` raises `KeyError` containing "Available" and list of profile names
  - [x] Test `resolve_alias("av")` returns `"alphavantage"`
  - [x] Test `resolve_alias("bento")` returns `"databento"`, `resolve_alias("db")` returns `"databento"`
  - [x] Test `resolve_alias("flat")` returns `"flatfile"`, `resolve_alias("file")` returns `"flatfile"`
  - [x] Test `resolve_alias("alphavantage")` returns `"alphavantage"` (canonical passthrough)
  - [x] Test `resolve_alias("nonexistent")` raises `KeyError` with available names and aliases
  - [x] Success: all tests pass via `pytest test/unit/test_provider_profiles.py -v`

**Commit**: `feat: add provider profiles with built-in definitions and alias resolution`

### Phase 3: Auth Strategy

- [x] **3.1 Create auth module**
  - [x] Create `src/manta_trading/providers/auth.py`
  - [x] Import `Settings` under `TYPE_CHECKING` guard to avoid circular imports
  - [x] Implement `AuthStrategy` as a `@runtime_checkable` Protocol with: `def is_valid(self) -> bool`, `active_source: str | None` property, `setup_hint: str` property
  - [x] Implement `NoAuthStrategy` — always `is_valid=True`, `active_source="none_required"`, `setup_hint=""`
  - [x] Implement `ApiKeyAuthStrategy` — constructor takes `env_var_name: str` and `settings: Settings`; reads credential via `getattr(settings, field_name, None)` where `field_name` is derived from env var name (strip `MT_` prefix, lowercase); `is_valid` returns `True` if credential is non-empty string; `active_source` returns `"env:{env_var_name}"` if valid else `None`; `setup_hint` returns `"Set {env_var_name} environment variable"`
  - [x] Implement `resolve_auth(profile: ProviderProfile, settings: Settings) -> AuthStrategy` — dispatches on `profile.auth_type`: `AuthType.NONE` → `NoAuthStrategy()`, `AuthType.API_KEY` → `ApiKeyAuthStrategy(profile.api_key_env, settings)`
  - [x] Success: `resolve_auth` returns correct strategy for each `AuthType`

- [x] **3.2 Test auth module**
  - [x] Create `test/unit/test_provider_auth.py`
  - [x] Test `NoAuthStrategy` is always valid, `active_source` is `"none_required"`, `setup_hint` is empty
  - [x] Test `ApiKeyAuthStrategy` with credential present: `is_valid=True`, `active_source` contains env var name
  - [x] Test `ApiKeyAuthStrategy` with credential missing (`None`): `is_valid=False`, `setup_hint` contains env var name
  - [x] Test `ApiKeyAuthStrategy` with empty string credential: `is_valid=False`
  - [x] Test `ApiKeyAuthStrategy` and `NoAuthStrategy` satisfy `isinstance(x, AuthStrategy)` check (runtime checkable protocol)
  - [x] Test `resolve_auth` with `AuthType.NONE` profile returns `NoAuthStrategy`
  - [x] Test `resolve_auth` with `AuthType.API_KEY` profile returns `ApiKeyAuthStrategy`
  - [x] Use mock `Settings` objects (or construct with env vars) to control credential presence
  - [x] Success: all tests pass via `pytest test/unit/test_provider_auth.py -v`

**Commit**: `feat: add auth strategy pattern with API key and no-auth support`

### Phase 4: Package Init and Wiring

- [x] **4.1 Create providers package init**
  - [x] Create `src/manta_trading/providers/__init__.py`
  - [x] Re-export public API: `ProviderType`, `AuthType`, `RateLimit`, `ProviderProfile`, `get_profile`, `get_all_profiles`, `resolve_alias`, `AuthStrategy`, `resolve_auth`, `ProviderError`, `ProviderAuthError`
  - [x] Success: `from manta_trading.providers import ProviderType, get_profile, resolve_auth` works

- [x] **4.2 Verify package and run all provider tests**
  - [x] Run `pytest test/unit/test_provider_types.py test/unit/test_provider_profiles.py test/unit/test_provider_auth.py -v`
  - [x] Success: all tests pass, no import errors

**Commit**: `feat: add providers package init with public API exports`

### Phase 5: CLI Provider Commands

- [x] **5.1 Create provider CLI sub-app**
  - [x] Create `src/manta_trading/cli/commands/provider.py`
  - [x] Define `provider_app = typer.Typer(name="provider", help="Data provider management", no_args_is_help=True)`
  - [x] Import and use `get_logger` for module logging
  - [x] Import `print_result`, `print_error`, `make_table` from `manta_trading.cli.output`

- [x] **5.2 Implement `mt provider list`**
  - [x] Add `provider_list` command to `provider_app`
  - [x] Parameters: `json_output: bool = typer.Option(False, "--json")`
  - [x] Logic: iterate `get_all_profiles()`, for each profile resolve auth using `resolve_auth(profile, settings)` where settings comes from `ctx.obj["settings"]`
  - [x] Text mode: Rich table with columns: Name, Type, Description, Aliases, Auth (green check or red X)
  - [x] JSON mode: array of objects with keys: name, provider_type, description, aliases, auth_valid, base_url
  - [x] Use `make_table` and `print_result` from output module
  - [x] Success: `mt provider list` displays table; `mt provider list --json` emits valid JSON array

- [x] **5.3 Implement `mt provider status`**
  - [x] Add `provider_status` command to `provider_app`
  - [x] Parameters: `name: str = typer.Argument(None)`, `json_output: bool = typer.Option(False, "--json")`
  - [x] If name provided: `resolve_alias(name)` → `get_profile()` → display detailed info (name, type, base_url, api_key_env, rate_limit, auth status with source/hint)
  - [x] If no name: show detailed status for all providers
  - [x] Error case: unknown name/alias → print error with available providers and aliases, exit code 1
  - [x] Use `print_result`, `print_error` from output module
  - [x] Success: `mt provider status av` shows alphavantage details; `mt provider status` shows all

- [x] **5.4 Implement `mt provider test`**
  - [x] Add `provider_test` command to `provider_app`
  - [x] Parameters: `name: str = typer.Argument(...)`, `json_output: bool = typer.Option(False, "--json")`
  - [x] Logic: `resolve_alias(name)` → `get_profile()` → `resolve_auth(profile, settings)` → report `is_valid`, `active_source`, `setup_hint`
  - [x] Text mode: green check + source if valid; red X + setup hint if not
  - [x] JSON mode: object with keys: provider, auth_valid, active_source, setup_hint
  - [x] Error case: unknown name/alias → print error with available providers, exit code 1
  - [x] Success: `mt provider test alphavantage` reports credential status

- [x] **5.5 Wire provider sub-app into main CLI**
  - [x] In `src/manta_trading/cli/app.py`, import `provider_app` from `manta_trading.cli.commands.provider`
  - [x] Add `app.add_typer(provider_app, name="provider")`
  - [x] Success: `mt provider --help` shows list, status, test commands

- [x] **5.6 Test provider CLI commands**
  - [x] Create `test/unit/test_cli_provider.py`
  - [x] Use `typer.testing.CliRunner` with mock Settings (same isolation pattern as `test_cli_config.py`)
  - [x] Test `mt provider list` — exit code 0, output contains "alphavantage", "databento", "flatfile"
  - [x] Test `mt provider list --json` — valid JSON array with 3 entries, each has required keys
  - [x] Test `mt provider status` (no arg) — exit code 0, output contains all provider names
  - [x] Test `mt provider status alphavantage` — exit code 0, shows alphavantage details
  - [x] Test `mt provider status av` — exit code 0, resolves alias to alphavantage
  - [x] Test `mt provider status nonexistent` — exit code 1, error mentions available providers
  - [x] Test `mt provider test alphavantage` without API key — reports not authenticated, shows setup hint
  - [x] Test `mt provider test alphavantage` with API key — reports authenticated
  - [x] Test `mt provider test nonexistent` — exit code 1
  - [x] Test `mt provider test alphavantage --json` — valid JSON with auth_valid field
  - [x] Success: all tests pass via `pytest test/unit/test_cli_provider.py -v`

**Commit**: `feat: add mt provider list/status/test CLI commands`

### Phase 6: Status Command

- [x] **6.1 Create status CLI command**
  - [x] Create `src/manta_trading/cli/commands/status.py`
  - [x] Define `status_app = typer.Typer(name="status", help="System status and health", no_args_is_help=False)`
  - [x] Implement default command (callback with `invoke_without_command=True`) with `json_output: bool = typer.Option(False, "--json")`
  - [x] **Provider health section**: iterate all profiles, resolve auth for each, display name + auth status
  - [x] **DB connectivity section**: check `settings.db_url` — if `None`, report "not configured"; if set, attempt a lightweight connection check (wrap in try/except, report connected or error message)
  - [x] Text mode: Rich output with section headers, green/red indicators
  - [x] JSON mode: structured object with `providers` (list of status objects) and `database` (object with `configured`, `connected`, `url` keys — redact credentials from URL)
  - [x] Use `print_result`, `make_table` from output module

- [x] **6.2 Replace stub status sub-app**
  - [x] In `src/manta_trading/cli/app.py`, remove the existing stub `status_app` and `status_overview` command
  - [x] Import `status_app` from `manta_trading.cli.commands.status`
  - [x] Keep `app.add_typer(status_app, name="status")`
  - [x] Success: `mt status` shows provider health and DB connectivity; `mt status --help` shows the real command

- [x] **6.3 Test status CLI command**
  - [x] Create `test/unit/test_cli_status.py`
  - [x] Use `typer.testing.CliRunner` with mock Settings
  - [x] Test `mt status` — exit code 0, output contains "Provider" and "Database" sections
  - [x] Test `mt status --json` — valid JSON with `providers` and `database` keys
  - [x] Test `mt status` with `db_url=None` — reports database not configured
  - [x] Test `mt status` with `db_url` set — reports connectivity status (mock the connection check)
  - [x] Test provider health shows auth status for each provider
  - [x] Success: all tests pass via `pytest test/unit/test_cli_status.py -v`

- [x] **6.4 Update test_cli_app.py for status changes**
  - [x] Update or remove `TestStatusSubApp` tests that reference the stub `status overview` command
  - [x] Add test that `mt status` invokes the real status command (not the stub)
  - [x] Success: `pytest test/unit/test_cli_app.py -v` passes

**Commit**: `feat: add mt status command with provider health and DB connectivity`

### Phase 7: Final Verification

- [x] **7.1 Run full test suite**
  - [x] Run `pytest test/unit/ -v` — all tests pass
  - [x] Confirm no regressions in existing tests (test_cli_app, test_cli_config, test_logging, test_cli_output)
  - [x] Success: zero failures

- [x] **7.2 Run verification walkthrough**
  - [x] Execute each step from slice design Verification Walkthrough section
  - [x] Update the walkthrough in the slice design with actual commands, output, and caveats
  - [x] Success: all walkthrough steps produce expected results

- [x] **7.3 Update slice and task status**
  - [x] Set `status: complete` and `dateUpdated` in this task file's frontmatter
  - [x] Set `status: complete` and `dateUpdated` in the slice design frontmatter
  - [x] Check off slice 902 in `user/architecture/900-slices.foundation-cleanup.md`
  - [x] Success: all status fields updated

- [x] **7.4 Update CHANGELOG.md**
  - [x] Add slice 902 entries under `[Unreleased]` section
  - [x] Include: provider registry, provider CLI commands, status command
  - [x] Success: CHANGELOG reflects 902 deliverables

- [x] **7.5 Run workflow check**
  - [x] Run `workflow_check` (or `cf check`) with fix parameter if available
  - [x] Success: 0 findings or all auto-fixed

**Commit**: `docs: complete slice 902 — update walkthrough, tasks, and changelog`
