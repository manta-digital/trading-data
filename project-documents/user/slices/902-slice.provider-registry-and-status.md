---
docType: slice-design
slice: provider-registry-and-status
project: trading
parent: user/architecture/900-slices.foundation-cleanup.md
dependencies: [900, 901]
interfaces: [903]
dateCreated: 20260330
dateUpdated: 20260331
status: complete
---

# Slice 902: Provider Registry and Status

## Overview

Establish a centralized provider registry with enum-keyed dispatch, frozen profile definitions, alias resolution, and credential validation. Add CLI commands (`mt provider list/status/test`) for provider introspection and a top-level `mt status` command showing provider health, DB connectivity, and data freshness. After this slice, adding a new data provider means defining a `ProviderProfile` and registering it — not modifying conditional logic anywhere.

## Value

- **User-facing**: `mt provider list` shows available providers with auth status at a glance. `mt provider test <name>` validates credentials before running a long data job. `mt status` is the single entry point for "is the system healthy?"
- **Developer-facing**: Enum-keyed dispatch eliminates string-based provider identification scattered across the codebase. The `ProviderProfile` frozen dataclass centralizes provider metadata (base URL, credentials env var, rate limits, aliases) in one place per provider.
- **Architectural**: Slices 903 and 904 depend on the registry to wire providers into CLI commands (`mt data daily --provider alphavantage`) and to validate provider configuration during packaging.

## Technical Scope

**Included:**
- `src/manta_trading/providers/types.py` — `ProviderType` enum, `AuthType` enum, `RateLimit` dataclass
- `src/manta_trading/providers/profiles.py` — `ProviderProfile` frozen dataclass, built-in profiles dict, `get_profile()`, `get_all_profiles()`, `resolve_alias()`
- `src/manta_trading/providers/auth.py` — `AuthStrategy` protocol, `ApiKeyAuthStrategy` implementation, `resolve_auth()` dispatcher
- `src/manta_trading/providers/errors.py` — `ProviderError`, `ProviderAuthError`
- `src/manta_trading/providers/__init__.py` — public API re-exports
- `src/manta_trading/cli/commands/provider.py` — `mt provider list/status/test` commands
- `src/manta_trading/cli/commands/status.py` — `mt status` top-level command (provider health, DB connectivity, data freshness)
- Wire new sub-apps into `cli/app.py`
- Unit tests for all new modules

**Excluded:**
- Provider implementation code (actual HTTP clients, data fetching) — that's existing code in `api/` and `data/`
- Wiring existing AlphaVantage code to use the registry — that's slice 903's job
- User-defined profiles via TOML — deferred until there's demand; built-in profiles are sufficient for current providers
- DB migration or schema changes

## Dependencies

### Prerequisites
- Slice 900 (CLI scaffold, config system) — complete
- Slice 901 (logging, output formatter) — complete
- Existing: `Settings` class with `alphavantage_api_key`, `databento_api_key` fields
- Existing: `cli/output.py` with `print_result`, `print_error`, `make_table`

### Interfaces Required
- `Settings` from `manta_trading.config` — for reading credential env vars
- `get_logger` from `manta_trading.logging` — for module logging
- `print_result`, `print_error`, `make_table` from `manta_trading.cli.output` — for CLI output

## Architecture

### Component Structure

```
src/manta_trading/providers/
    __init__.py          # Re-exports: ProviderType, ProviderProfile, get_profile, etc.
    types.py             # ProviderType enum, AuthType enum, RateLimit dataclass
    profiles.py          # ProviderProfile, BUILT_IN_PROFILES, lookup functions
    auth.py              # AuthStrategy protocol, ApiKeyAuthStrategy, resolve_auth
    errors.py            # ProviderError, ProviderAuthError

src/manta_trading/cli/commands/
    provider.py          # mt provider list/status/test
    status.py            # mt status (top-level health overview)
```

### Data Flow

1. **Profile lookup**: CLI command receives provider name or alias → `resolve_alias()` maps to canonical name → `get_profile()` returns `ProviderProfile`
2. **Auth check**: `resolve_auth(profile, settings)` → constructs `ApiKeyAuthStrategy` using `profile.api_key_env` → reads credential from `Settings` → returns strategy with `is_valid` / `active_source` / `setup_hint`
3. **Status display**: `mt provider status` iterates all profiles → resolves auth for each → renders table with name, type, auth status, rate limits

## Technical Decisions

### ProviderType Enum

Use `StrEnum` (Python 3.11+, available in our 3.12 target) so enum values serialize cleanly to JSON and work in string contexts:

```python
class ProviderType(StrEnum):
    """Registered data provider identifiers."""
    ALPHA_VANTAGE = "alphavantage"
    DATABENTO = "databento"
    FLAT_FILE = "flatfile"
```

Values are lowercase, no underscores — these are the canonical string identifiers used in config files and CLI output. The enum member names use SCREAMING_SNAKE as is standard.

### AuthType Enum

```python
class AuthType(StrEnum):
    """Authentication strategy identifiers."""
    API_KEY = "api_key"
    NONE = "none"
```

Only two types needed for now. `NONE` covers providers like `FLAT_FILE` that don't need credentials. Adding OAuth or session-based auth later means adding an enum member and a strategy implementation — no existing code changes.

### RateLimit Dataclass

```python
@dataclass(frozen=True)
class RateLimit:
    """Provider rate limit constraints."""
    requests_per_minute: int
    daily_limit: int | None = None
```

This is the static constraint from the provider's documentation, not runtime usage tracking. Runtime rate limiting is the provider implementation's responsibility (existing `RateLimitInfo` in `data/historical_minute/provider.py` handles that).

### ProviderProfile

```python
@dataclass(frozen=True)
class ProviderProfile:
    """Immutable configuration preset for a data provider."""
    name: str
    provider_type: ProviderType
    base_url: str | None = None
    api_key_env: str | None = None
    rate_limit: RateLimit | None = None
    aliases: tuple[str, ...] = ()
    auth_type: AuthType = AuthType.API_KEY
    description: str = ""
```

Key design points:
- Frozen for immutability and safe use as dict values
- `api_key_env` is the **name** of the env var (e.g., `"MT_ALPHAVANTAGE_API_KEY"`), not the credential value
- `aliases` is a tuple (immutable) of short names for CLI convenience
- `provider_type` is the enum key — all dispatch uses this, never string comparison on `name`

### Built-In Profiles

```python
BUILT_IN_PROFILES: dict[str, ProviderProfile] = {
    "alphavantage": ProviderProfile(
        name="alphavantage",
        provider_type=ProviderType.ALPHA_VANTAGE,
        base_url="https://www.alphavantage.co/query",
        api_key_env="MT_ALPHAVANTAGE_API_KEY",
        rate_limit=RateLimit(requests_per_minute=30, daily_limit=None),
        aliases=("av",),
        description="Alpha Vantage market data API",
    ),
    "databento": ProviderProfile(
        name="databento",
        provider_type=ProviderType.DATABENTO,
        base_url="https://hist.databento.com",
        api_key_env="MT_DATABENTO_API_KEY",
        rate_limit=None,
        aliases=("db", "bento"),
        description="Databento historical market data",
    ),
    "flatfile": ProviderProfile(
        name="flatfile",
        provider_type=ProviderType.FLAT_FILE,
        base_url=None,
        api_key_env=None,
        rate_limit=None,
        aliases=("flat", "file"),
        auth_type=AuthType.NONE,
        description="Local flat file data source",
    ),
}
```

Keyed by canonical name (which matches `ProviderType.value`). This is the single source of truth for provider metadata.

### AuthStrategy Protocol

```python
@runtime_checkable
class AuthStrategy(Protocol):
    """Credential resolution strategy for a provider."""

    def is_valid(self) -> bool: ...
    @property
    def active_source(self) -> str | None: ...
    @property
    def setup_hint(self) -> str: ...
```

Lightweight protocol — just enough to answer "are credentials available?" and "how do I set them up?" for display purposes. No credential refresh or network calls at this layer.

### ApiKeyAuthStrategy

Resolution logic:
1. Read env var named by `profile.api_key_env` from `Settings`
2. If present and non-empty → `is_valid=True`, `active_source="env:MT_ALPHAVANTAGE_API_KEY"`
3. If missing → `is_valid=False`, `setup_hint="Set MT_ALPHAVANTAGE_API_KEY environment variable"`

The strategy reads the credential from the `Settings` instance (which already loaded env vars at startup). No additional env var reads.

### resolve_auth()

```python
def resolve_auth(profile: ProviderProfile, settings: Settings) -> AuthStrategy:
    """Construct the appropriate auth strategy for a provider profile."""
```

Dispatches on `profile.auth_type`. For `AuthType.NONE`, returns a no-op strategy that always reports valid. For `AuthType.API_KEY`, constructs `ApiKeyAuthStrategy` with the profile's env var name and the Settings instance.

### CLI Commands

**`mt provider list`** — Table of all profiles: name, type, description, aliases, auth status (green check / red X)
- `--json` emits array of profile objects

**`mt provider status [name]`** — Detailed status for one or all providers: credentials, base URL, rate limits
- Without argument: all providers
- With argument: single provider (resolves aliases)
- `--json` emits object(s)

**`mt provider test <name>`** — Validate credentials for a specific provider
- Resolves alias to profile
- Runs auth check
- Reports success/failure with actionable hint
- `--json` emits result object

**`mt status`** — Top-level system health overview
- Provider health: iterates profiles, checks auth status
- DB connectivity: attempts `Settings.db_url` connection check (if configured)
- Data freshness: placeholder for now (will be wired in later slices)
- `--json` emits structured health report

### Error Types

```python
class ProviderError(Exception):
    """Base exception for provider-related errors."""

class ProviderAuthError(ProviderError):
    """Authentication or credential errors."""
```

Minimal hierarchy. More specific errors (API errors, timeout) can be added in slice 903 when actual provider implementations are wired in.

## Integration Points

### Provides to Other Slices

- **Slice 903**: Uses `ProviderType` enum for dispatch when wiring AlphaVantage into `mt data daily --provider`. Uses `get_profile()` to look up provider config. Uses `resolve_auth()` to validate credentials before running data jobs.
- **Slice 904**: Uses `mt provider list --json` and `mt status --json` for verification during packaging checks.

### Consumes from Other Slices

- **Slice 900**: CLI app structure (`app.add_typer`), `Settings` class, config system
- **Slice 901**: `get_logger`, `print_result`/`print_error`/`make_table` output utilities

## Success Criteria

1. `ProviderType` is a `StrEnum` with members for ALPHA_VANTAGE, DATABENTO, FLAT_FILE
2. `ProviderProfile` is a frozen dataclass with all specified fields
3. `BUILT_IN_PROFILES` contains entries for alphavantage, databento, flatfile
4. `get_profile("alphavantage")` returns the correct profile; `get_profile("nonexistent")` raises `KeyError` listing available profiles
5. `resolve_alias("av")` returns `"alphavantage"`; `resolve_alias("bento")` returns `"databento"`
6. `resolve_alias("alphavantage")` returns `"alphavantage"` (canonical names pass through)
7. `AuthStrategy` protocol is satisfied by `ApiKeyAuthStrategy`; `is_valid`, `active_source`, `setup_hint` work correctly
8. `resolve_auth()` dispatches on `AuthType`, returns no-op for `NONE`, `ApiKeyAuthStrategy` for `API_KEY`
9. `mt provider list` shows all providers with auth status; `--json` emits valid JSON array
10. `mt provider status` shows detailed provider info; resolves aliases
11. `mt provider test alphavantage` reports credential status with actionable hint
12. `mt status` shows provider health section and DB connectivity section; `--json` emits structured report
13. All commands use `print_result`/`make_table` from `cli/output.py` — no ad-hoc JSON serialization
14. No string-based provider dispatch anywhere in new code — all dispatch uses `ProviderType` enum
15. Unit tests cover: type enums, profile lookup, alias resolution, auth strategies, CLI commands (all output modes)

## Verification Walkthrough

> Verified 2026-03-31. All steps produce expected results.

### 1. Provider listing

```bash
mt provider list
# Output: Rich table with columns: Name, Type, Description, Aliases, Auth
# alphavantage ✗ (no key set), databento ✗, flatfile ✓ (no auth required)

mt provider list --json
# Output: JSON array of 3 profile objects with keys: name, provider_type,
# description, aliases, auth_valid, base_url
```

### 2. Alias resolution

```bash
mt provider status av
# Output: Detailed status for alphavantage (resolved from alias "av")

mt provider status bento
# Output: Detailed status for databento (resolved from alias "bento")
```

### 3. Credential validation

```bash
# Without API key set:
mt provider test alphavantage
# Output: ✗ alphavantage: not authenticated — Set MT_ALPHAVANTAGE_API_KEY environment variable

# With API key set:
MT_ALPHAVANTAGE_API_KEY=demo mt provider test alphavantage
# Output: ✓ alphavantage: authenticated via env:MT_ALPHAVANTAGE_API_KEY
```

### 4. System status

```bash
mt status
# Output: Providers table (Name, Auth columns) + Database section
# Database shows "not configured (MT_DB_URL)" when MT_DB_URL is unset

mt status --json
# Output: {"providers": [...], "database": {"configured": false, "connected": false, "url": null}}
```

**Note**: JSON keys are `providers` and `database` (not `provider_health` and `db` as in the draft).

### 5. Error cases

```bash
mt provider test nonexistent
# Output: Error with list of available providers and aliases, exit code 1

mt provider status nonexistent
# Output: Error with list of available providers and aliases, exit code 1
```

### 6. Tests

```bash
pytest test/unit/test_provider_types.py test/unit/test_provider_profiles.py test/unit/test_provider_auth.py test/unit/test_cli_provider.py test/unit/test_cli_status.py -v
# Result: 88 passed
```

## Implementation Notes

### Development Approach

Suggested implementation order:
1. **Types and profiles** (`types.py`, `profiles.py`) — foundation with no dependencies beyond stdlib
2. **Auth** (`auth.py`) — depends on types and profiles
3. **Errors** (`errors.py`) — standalone
4. **Unit tests** for all above
5. **CLI commands** (`provider.py`, `status.py`) — depends on all above + output utilities
6. **CLI tests** — exercises full stack through Typer runner
7. **Wire into app** — add sub-apps to `cli/app.py`
8. **Integration verification** — run walkthrough

### Testing Strategy

- **Unit tests**: Each module gets its own test file. Profile lookup, alias resolution, and auth logic tested in isolation.
- **CLI tests**: Use `typer.testing.CliRunner` with mock `Settings` (same pattern as `test_cli_config.py`). Test both text and `--json` output modes.
- **Auth tests**: Mock `Settings` to control credential presence/absence. Verify `is_valid`, `active_source`, and `setup_hint` for each scenario.
- **DB connectivity** in `mt status`: Mock the connection check. Test both connected and unreachable states.
