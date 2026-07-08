---
docType: review
layer: project
reviewType: code
slice: provider-registry-and-status
project: squadron
verdict: UNKNOWN
sourceDocument: project-documents/user/slices/902-slice.provider-registry-and-status.md
aiModel: minimax/minimax-m2.7
status: complete
dateCreated: 20260331
dateUpdated: 20260331
---

# Review: code — slice 902

**Verdict:** UNKNOWN
**Model:** minimax/minimax-m2.7

No specific findings.

---

## Debug: Prompt & Response

### System Prompt

You are a code reviewer. Review code against language-specific rules, testing
standards, and project conventions loaded from CLAUDE.md.

Focus areas:
- Project conventions (from CLAUDE.md)
- Language-appropriate style and correctness
- Test coverage patterns (test-with, not test-after)
- Error handling patterns
- Security concerns
- Naming, structure, and documentation quality

CRITICAL: Your verdict and findings MUST be consistent.
- If verdict is CONCERNS or FAIL, include at least one finding with that severity.
- If no CONCERN or FAIL findings exist, verdict MUST be PASS.
- Every finding MUST use the exact format: ### [SEVERITY] Title

Report your findings using severity levels:

## Summary
[overall assessment: PASS | CONCERNS | FAIL]

## Findings

### [PASS|CONCERN|FAIL] Finding title
Description with specific file and line references.


### User Prompt

Review code in the project at: ./project-documents/user

Run `git diff 0d04adcb5501c91e0b770a27e33e0082f4dcffdd...902-slice.provider-registry-and-status` to identify changed files, then review those files for quality and correctness.

Apply the project conventions from CLAUDE.md and language-specific best practices. Report your findings using the severity format described in your instructions.

## File Contents

### Git Diff

```
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 0a496d5..46c6ee8 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -16,7 +16,12 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 - ConfigManager with TOML persistence, three-level precedence (project > user > default)
 - `mt config list/get/set/path` commands for managing persistent configuration
 - ConfigKey registry with typed defaults for `default_provider`, `output_format`, `data_dir`
-- Stub `mt status overview` sub-app proving the sub-app registration pattern
+- Provider registry with `ProviderType`/`AuthType` StrEnums, `RateLimit` and `ProviderProfile` frozen dataclasses (slice 902)
+- Built-in profiles for alphavantage, databento, flatfile with alias resolution (slice 902)
+- `AuthStrategy` protocol with `ApiKeyAuthStrategy` and `NoAuthStrategy` implementations (slice 902)
+- `mt provider list/status/test` CLI commands for provider introspection (slice 902)
+- `mt status` command with provider health and DB connectivity checks (slice 902)
+- 88 new tests for provider types, profiles, auth, and CLI commands (slice 902)
 - 39 unit tests covering Settings, ConfigManager, CLI app, and config commands
 - Structured logging module with JSON and text formatters (`setup_logging`, `get_logger`) (slice 901)
 - Shared CLI output formatter with `print_result`, `print_error`, `make_table` (slice 901)
@@ -24,6 +29,7 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 - 32 new tests for logging, output formatting, and --json config commands
 
 ### Changed
+- Replaced stub `mt status overview` with real `mt status` health command (slice 902)
 - Migrated all ~35 source files and 7 test files from loguru to stdlib `logging` via `get_logger(__name__)` (slice 901)
 
 ### Removed
diff --git a/project-documents/user/architecture/900-slices.foundation-cleanup.md b/project-documents/user/architecture/900-slices.foundation-cleanup.md
index 9bd61dc..35cd3a3 100644
--- a/project-documents/user/architecture/900-slices.foundation-cleanup.md
+++ b/project-documents/user/architecture/900-slices.foundation-cleanup.md
@@ -20,7 +20,7 @@ status: in_progress
 
 2. [x] **(901) Logging and Output Formatting** — Structured logging setup (`setup_logging`, JSON/text formatters, `get_logger` pattern). Shared Rich output formatter with `--json` support for all commands. Migrate existing `print()` and loguru calls to new logging. Dependencies: [900]. Risk: Low. Effort: 2/5
 
-3. [ ] **(902) Provider Registry and Status** — `ProviderType` enum, `ProviderProfile` frozen dataclass, built-in profiles for AlphaVantage/DataBento/FlatFile, alias resolution, auth strategy pattern for credential validation. `mt provider list/status/test` commands. `mt status` top-level command showing provider health, DB connectivity, and data freshness summary. Dependencies: [900, 901]. Risk: Low. Effort: 3/5
+3. [x] **(902) Provider Registry and Status** — `ProviderType` enum, `ProviderProfile` frozen dataclass, built-in profiles for AlphaVantage/DataBento/FlatFile, alias resolution, auth strategy pattern for credential validation. `mt provider list/status/test` commands. `mt status` top-level command showing provider health, DB connectivity, and data freshness summary. Dependencies: [900, 901]. Risk: Low. Effort: 3/5
 
 ## Migration / Refactoring Slices
 
diff --git a/project-documents/user/slices/902-slice.provider-registry-and-status.md b/project-documents/user/slices/902-slice.provider-registry-and-status.md
index fecb61f..6b292c8 100644
--- a/project-documents/user/slices/902-slice.provider-registry-and-status.md
+++ b/project-documents/user/slices/902-slice.provider-registry-and-status.md
@@ -6,8 +6,8 @@ parent: user/architecture/900-slices.foundation-cleanup.md
 dependencies: [900, 901]
 interfaces: [903]
 dateCreated: 20260330
-dateUpdated: 20260330
-status: not_started
+dateUpdated: 20260331
+status: complete
 ---
 
 # Slice 902: Provider Registry and Status
@@ -275,27 +275,28 @@ Minimal hierarchy. More specific errors (API errors, timeout) can be added in sl
 
 ## Verification Walkthrough
 
-> This is the draft walkthrough. It will be refined with actual output during implementation.
+> Verified 2026-03-31. All steps produce expected results.
 
 ### 1. Provider listing
 
 ```bash
 mt provider list
-# Expected: Rich table with columns: Name, Type, Description, Aliases, Auth
-# alphavantage row should show auth status based on MT_ALPHAVANTAGE_API_KEY
+# Output: Rich table with columns: Name, Type, Description, Aliases, Auth
+# alphavantage ✗ (no key set), databento ✗, flatfile ✓ (no auth required)
 
 mt provider list --json
-# Expected: JSON array of profile objects
+# Output: JSON array of 3 profile objects with keys: name, provider_type,
+# description, aliases, auth_valid, base_url
 ```
 
 ### 2. Alias resolution
 
 ```bash
 mt provider status av
-# Expected: Detailed status for alphavantage (resolved from alias "av")
+# Output: Detailed status for alphavantage (resolved from alias "av")
 
 mt provider status bento
-# Expected: Detailed status for databento (resolved from alias "bento")
+# Output: Detailed status for databento (resolved from alias "bento")
 ```
 
 ### 3. Credential validation
@@ -303,40 +304,41 @@ mt provider status bento
 ```bash
 # Without API key set:
 mt provider test alphavantage
-# Expected: Red X, hint to set MT_ALPHAVANTAGE_API_KEY
+# Output: ✗ alphavantage: not authenticated — Set MT_ALPHAVANTAGE_API_KEY environment variable
 
 # With API key set:
 MT_ALPHAVANTAGE_API_KEY=demo mt provider test alphavantage
-# Expected: Green check, source shown as env:MT_ALPHAVANTAGE_API_KEY
+# Output: ✓ alphavantage: authenticated via env:MT_ALPHAVANTAGE_API_KEY
 ```
 
 ### 4. System status
 
 ```bash
 mt status
-# Expected: Sections for Providers and Database
-# Providers section shows auth status for each
-# Database section shows connectivity (or "not configured" if MT_DB_URL unset)
+# Output: Providers table (Name, Auth columns) + Database section
+# Database shows "not configured (MT_DB_URL)" when MT_DB_URL is unset
 
 mt status --json
-# Expected: Structured JSON with provider_health and db sections
+# Output: {"providers": [...], "database": {"configured": false, "connected": false, "url": null}}
 ```
 
+**Note**: JSON keys are `providers` and `database` (not `provider_health` and `db` as in the draft).
+
 ### 5. Error cases
 
 ```bash
 mt provider test nonexistent
-# Expected: Error with list of available providers
+# Output: Error with list of available providers and aliases, exit code 1
 
 mt provider status nonexistent
-# Expected: Error with list of available providers and aliases
+# Output: Error with list of available providers and aliases, exit code 1
 ```
 
 ### 6. Tests
 
 ```bash
 pytest test/unit/test_provider_types.py test/unit/test_provider_profiles.py test/unit/test_provider_auth.py test/unit/test_cli_provider.py test/unit/test_cli_status.py -v
-# Expected: All pass
+# Result: 88 passed
 ```
 
 ## Implementation Notes
diff --git a/project-documents/user/tasks/902-tasks.provider-registry-and-status.md b/project-documents/user/tasks/902-tasks.provider-registry-and-status.md
index 007b6f7..4ec24ef 100644
--- a/project-documents/user/tasks/902-tasks.provider-registry-and-status.md
+++ b/project-documents/user/tasks/902-tasks.provider-registry-and-status.md
@@ -6,8 +6,8 @@ lld: user/slices/902-slice.provider-registry-and-status.md
 dependencies: [900, 901]
 projectState: Typer CLI scaffold complete with mt entry point, Settings class (log_level, log_format, alphavantage_api_key, databento_api_key, db_url), ConfigManager with TOML persistence, structured logging (setup_logging, get_logger), shared CLI output formatter (print_result, print_error, make_table). Stub mt status overview command exists. No provider registry or provider CLI commands yet.
 dateCreated: 20260330
-dateUpdated: 20260330
-status: not_started
+dateUpdated: 20260331
+status: complete
 ---
 
 # Tasks: Provider Registry and Status
@@ -26,215 +26,215 @@ Working on the Provider Registry and Status slice (902) of the Foundation & Clea
 
 ### Phase 1: Provider Types and Errors
 
-- [ ] **1.1 Create provider types module**
-  - [ ] Create `src/manta_trading/providers/types.py`
-  - [ ] Implement `ProviderType(StrEnum)` with members: `ALPHA_VANTAGE = "alphavantage"`, `DATABENTO = "databento"`, `FLAT_FILE = "flatfile"`
-  - [ ] Implement `AuthType(StrEnum)` with members: `API_KEY = "api_key"`, `NONE = "none"`
-  - [ ] Implement `RateLimit` frozen dataclass with fields: `requests_per_minute: int`, `daily_limit: int | None = None`
-  - [ ] Include `from __future__ import annotations`
-  - [ ] Success: all three types import cleanly, `ProviderType.ALPHA_VANTAGE.value == "alphavantage"`, `RateLimit` is frozen
-
-- [ ] **1.2 Create provider errors module**
-  - [ ] Create `src/manta_trading/providers/errors.py`
-  - [ ] Implement `ProviderError(Exception)` — base exception
-  - [ ] Implement `ProviderAuthError(ProviderError)` — credential errors
-  - [ ] Success: both exceptions import cleanly, `ProviderAuthError` is a subclass of `ProviderError`
-
-- [ ] **1.3 Test types and errors**
-  - [ ] Create `test/unit/test_provider_types.py`
-  - [ ] Test `ProviderType` is a `StrEnum` with 3 members; values are lowercase strings
-  - [ ] Test `ProviderType` members serialize to their string values (e.g., `str(ProviderType.ALPHA_VANTAGE) == "alphavantage"`)
-  - [ ] Test `AuthType` is a `StrEnum` with 2 members
-  - [ ] Test `RateLimit` is frozen (assigning to a field raises `FrozenInstanceError`)
-  - [ ] Test `RateLimit` default: `daily_limit` is `None` when not specified
-  - [ ] Test `ProviderAuthError` is a subclass of both `ProviderError` and `Exception`
-  - [ ] Success: all tests pass via `pytest test/unit/test_provider_types.py -v`
+- [x] **1.1 Create provider types module**
+  - [x] Create `src/manta_trading/providers/types.py`
+  - [x] Implement `ProviderType(StrEnum)` with members: `ALPHA_VANTAGE = "alphavantage"`, `DATABENTO = "databento"`, `FLAT_FILE = "flatfile"`
+  - [x] Implement `AuthType(StrEnum)` with members: `API_KEY = "api_key"`, `NONE = "none"`
+  - [x] Implement `RateLimit` frozen dataclass with fields: `requests_per_minute: int`, `daily_limit: int | None = None`
+  - [x] Include `from __future__ import annotations`
+  - [x] Success: all three types import cleanly, `ProviderType.ALPHA_VANTAGE.value == "alphavantage"`, `RateLimit` is frozen
+
+- [x] **1.2 Create provider errors module**
+  - [x] Create `src/manta_trading/providers/errors.py`
+  - [x] Implement `ProviderError(Exception)` — base exception
+  - [x] Implement `ProviderAuthError(ProviderError)` — credential errors
+  - [x] Success: both exceptions import cleanly, `ProviderAuthError` is a subclass of `ProviderError`
+
+- [x] **1.3 Test types and errors**
+  - [x] Create `test/unit/test_provider_types.py`
+  - [x] Test `ProviderType` is a `StrEnum` with 3 members; values are lowercase strings
+  - [x] Test `ProviderType` members serialize to their string values (e.g., `str(ProviderType.ALPHA_VANTAGE) == "alphavantage"`)
+  - [x] Test `AuthType` is a `StrEnum` with 2 members
+  - [x] Test `RateLimit` is frozen (assigning to a field raises `FrozenInstanceError`)
+  - [x] Test `RateLimit` default: `daily_limit` is `None` when not specified
+  - [x] Test `ProviderAuthError` is a subclass of both `ProviderError` and `Exception`
+  - [x] Success: all tests pass via `pytest test/unit/test_provider_types.py -v`
 
 **Commit**: `feat: add provider types, enums, and error hierarchy`
 
 ### Phase 2: Provider Profiles
 
-- [ ] **2.1 Create profiles module**
-  - [ ] Create `src/manta_trading/providers/profiles.py`
-  - [ ] Implement `ProviderProfile` frozen dataclass with fields: `name: str`, `provider_type: ProviderType`, `base_url: str | None = None`, `api_key_env: str | None = None`, `rate_limit: RateLimit | None = None`, `aliases: tuple[str, ...] = ()`, `auth_type: AuthType = AuthType.API_KEY`, `description: str = ""`
-  - [ ] Define `BUILT_IN_PROFILES: dict[str, ProviderProfile]` with entries for alphavantage, databento, flatfile as specified in slice design
-  - [ ] Implement `get_all_profiles() -> dict[str, ProviderProfile]` — returns copy of `BUILT_IN_PROFILES`
-  - [ ] Implement `get_profile(name: str) -> ProviderProfile` — returns profile by canonical name, raises `KeyError` with available profile names if not found
-  - [ ] Implement `resolve_alias(name_or_alias: str) -> str` — maps alias to canonical name; canonical names pass through; raises `KeyError` with available names and aliases if not found
-  - [ ] Success: all functions import cleanly, `get_profile("alphavantage")` returns expected profile, `resolve_alias("av")` returns `"alphavantage"`
-
-- [ ] **2.2 Test profiles module**
-  - [ ] Create `test/unit/test_provider_profiles.py`
-  - [ ] Test `ProviderProfile` is frozen (assigning to a field raises)
-  - [ ] Test `BUILT_IN_PROFILES` contains 3 entries: alphavantage, databento, flatfile
-  - [ ] Test each built-in profile has correct `provider_type`, `api_key_env`, `auth_type`, and `aliases`
-  - [ ] Test `get_all_profiles()` returns all 3 profiles
-  - [ ] Test `get_profile("alphavantage")` returns correct profile
-  - [ ] Test `get_profile("nonexistent")` raises `KeyError` containing "Available" and list of profile names
-  - [ ] Test `resolve_alias("av")` returns `"alphavantage"`
-  - [ ] Test `resolve_alias("bento")` returns `"databento"`, `resolve_alias("db")` returns `"databento"`
-  - [ ] Test `resolve_alias("flat")` returns `"flatfile"`, `resolve_alias("file")` returns `"flatfile"`
-  - [ ] Test `resolve_alias("alphavantage")` returns `"alphavantage"` (canonical passthrough)
-  - [ ] Test `resolve_alias("nonexistent")` raises `KeyError` with available names and aliases
-  - [ ] Success: all tests pass via `pytest test/unit/test_provider_profiles.py -v`
+- [x] **2.1 Create profiles module**
+  - [x] Create `src/manta_trading/providers/profiles.py`
+  - [x] Implement `ProviderProfile` frozen dataclass with fields: `name: str`, `provider_type: ProviderType`, `base_url: str | None = None`, `api_key_env: str | None = None`, `rate_limit: RateLimit | None = None`, `aliases: tuple[str, ...] = ()`, `auth_type: AuthType = AuthType.API_KEY`, `description: str = ""`
+  - [x] Define `BUILT_IN_PROFILES: dict[str, ProviderProfile]` with entries for alphavantage, databento, flatfile as specified in slice design
+  - [x] Implement `get_all_profiles() -> dict[str, ProviderProfile]` — returns copy of `BUILT_IN_PROFILES`
+  - [x] Implement `get_profile(name: str) -> ProviderProfile` — returns profile by canonical name, raises `KeyError` with available profile names if not found
+  - [x] Implement `resolve_alias(name_or_alias: str) -> str` — maps alias to canonical name; canonical names pass through; raises `KeyError` with available names and aliases if not found
+  - [x] Success: all functions import cleanly, `get_profile("alphavantage")` returns expected profile, `resolve_alias("av")` returns `"alphavantage"`
+
+- [x] **2.2 Test profiles module**
+  - [x] Create `test/unit/test_provider_profiles.py`
+  - [x] Test `ProviderProfile` is frozen (assigning to a field raises)
+  - [x] Test `BUILT_IN_PROFILES` contains 3 entries: alphavantage, databento, flatfile
+  - [x] Test each built-in profile has correct `provider_type`, `api_key_env`, `auth_type`, and `aliases`
+  - [x] Test `get_all_profiles()` returns all 3 profiles
+  - [x] Test `get_profile("alphavantage")` returns correct profile
+  - [x] Test `get_profile("nonexistent")` raises `KeyError` containing "Available" and list of profile names
+  - [x] Test `resolve_alias("av")` returns `"alphavantage"`
+  - [x] Test `resolve_alias("bento")` returns `"databento"`, `resolve_alias("db")` returns `"databento"`
+  - [x] Test `resolve_alias("flat")` returns `"flatfile"`, `resolve_alias("file")` returns `"flatfile"`
+  - [x] Test `resolve_alias("alphavantage")` returns `"alphavantage"` (canonical passthrough)
+  - [x] Test `resolve_alias("nonexistent")` raises `KeyError` with available names and aliases
+  - [x] Success: all tests pass via `pytest test/unit/test_provider_profiles.py -v`
 
 **Commit**: `feat: add provider profiles with built-in definitions and alias resolution`
 
 ### Phase 3: Auth Strategy
 
-- [ ] **3.1 Create auth module**
-  - [ ] Create `src/manta_trading/providers/auth.py`
-  - [ ] Import `Settings` under `TYPE_CHECKING` guard to avoid circular imports
-  - [ ] Implement `AuthStrategy` as a `@runtime_checkable` Protocol with: `def is_valid(self) -> bool`, `active_source: str | None` property, `setup_hint: str` property
-  - [ ] Implement `NoAuthStrategy` — always `is_valid=True`, `active_source="none_required"`, `setup_hint=""`
-  - [ ] Implement `ApiKeyAuthStrategy` — constructor takes `env_var_name: str` and `settings: Settings`; reads credential via `getattr(settings, field_name, None)` where `field_name` is derived from env var name (strip `MT_` prefix, lowercase); `is_valid` returns `True` if credential is non-empty string; `active_source` returns `"env:{env_var_name}"` if valid else `None`; `setup_hint` returns `"Set {env_var_name} environment variable"`
-  - [ ] Implement `resolve_auth(profile: ProviderProfile, settings: Settings) -> AuthStrategy` — dispatches on `profile.auth_type`: `AuthType.NONE` → `NoAuthStrategy()`, `AuthType.API_KEY` → `ApiKeyAuthStrategy(profile.api_key_env, settings)`
-  - [ ] Success: `resolve_auth` returns correct strategy for each `AuthType`
-
-- [ ] **3.2 Test auth module**
-  - [ ] Create `test/unit/test_provider_auth.py`
-  - [ ] Test `NoAuthStrategy` is always valid, `active_source` is `"none_required"`, `setup_hint` is empty
-  - [ ] Test `ApiKeyAuthStrategy` with credential present: `is_valid=True`, `active_source` contains env var name
-  - [ ] Test `ApiKeyAuthStrategy` with credential missing (`None`): `is_valid=False`, `setup_hint` contains env var name
-  - [ ] Test `ApiKeyAuthStrategy` with empty string credential: `is_valid=False`
-  - [ ] Test `ApiKeyAuthStrategy` and `NoAuthStrategy` satisfy `isinstance(x, AuthStrategy)` check (runtime checkable protocol)
-  - [ ] Test `resolve_auth` with `AuthType.NONE` profile returns `NoAuthStrategy`
-  - [ ] Test `resolve_auth` with `AuthType.API_KEY` profile returns `ApiKeyAuthStrategy`
-  - [ ] Use mock `Settings` objects (or construct with env vars) to control credential presence
-  - [ ] Success: all tests pass via `pytest test/unit/test_provider_auth.py -v`
+- [x] **3.1 Create auth module**
+  - [x] Create `src/manta_trading/providers/auth.py`
+  - [x] Import `Settings` under `TYPE_CHECKING` guard to avoid circular imports
+  - [x] Implement `AuthStrategy` as a `@runtime_checkable` Protocol with: `def is_valid(self) -> bool`, `active_source: str | None` property, `setup_hint: str` property
+  - [x] Implement `NoAuthStrategy` — always `is_valid=True`, `active_source="none_required"`, `setup_hint=""`
+  - [x] Implement `ApiKeyAuthStrategy` — constructor takes `env_var_name: str` and `settings: Settings`; reads credential via `getattr(settings, field_name, None)` where `field_name` is derived from env var name (strip `MT_` prefix, lowercase); `is_valid` returns `True` if credential is non-empty string; `active_source` returns `"env:{env_var_name}"` if valid else `None`; `setup_hint` returns `"Set {env_var_name} environment variable"`
+  - [x] Implement `resolve_auth(profile: ProviderProfile, settings: Settings) -> AuthStrategy` — dispatches on `profile.auth_type`: `AuthType.NONE` → `NoAuthStrategy()`, `AuthType.API_KEY` → `ApiKeyAuthStrategy(profile.api_key_env, settings)`
+  - [x] Success: `resolve_auth` returns correct strategy for each `AuthType`
+
+- [x] **3.2 Test auth module**
+  - [x] Create `test/unit/test_provider_auth.py`
+  - [x] Test `NoAuthStrategy` is always valid, `active_source` is `"none_required"`, `setup_hint` is empty
+  - [x] Test `ApiKeyAuthStrategy` with credential present: `is_valid=True`, `active_source` contains env var name
+  - [x] Test `ApiKeyAuthStrategy` with credential missing (`None`): `is_valid=False`, `setup_hint` contains env var name
+  - [x] Test `ApiKeyAuthStrategy` with empty string credential: `is_valid=False`
+  - [x] Test `ApiKeyAuthStrategy` and `NoAuthStrategy` satisfy `isinstance(x, AuthStrategy)` check (runtime checkable protocol)
+  - [x] Test `resolve_auth` with `AuthType.NONE` profile returns `NoAuthStrategy`
+  - [x] Test `resolve_auth` with `AuthType.API_KEY` profile returns `ApiKeyAuthStrategy`
+  - [x] Use mock `Settings` objects (or construct with env vars) to control credential presence
+  - [x] Success: all tests pass via `pytest test/unit/test_provider_auth.py -v`
 
 **Commit**: `feat: add auth strategy pattern with API key and no-auth support`
 
 ### Phase 4: Package Init and Wiring
 
-- [ ] **4.1 Create providers package init**
-  - [ ] Create `src/manta_trading/providers/__init__.py`
-  - [ ] Re-export public API: `ProviderType`, `AuthType`, `RateLimit`, `ProviderProfile`, `get_profile`, `get_all_profiles`, `resolve_alias`, `AuthStrategy`, `resolve_auth`, `ProviderError`, `ProviderAuthError`
-  - [ ] Success: `from manta_trading.providers import ProviderType, get_profile, resolve_auth` works
+- [x] **4.1 Create providers package init**
+  - [x] Create `src/manta_trading/providers/__init__.py`
+  - [x] Re-export public API: `ProviderType`, `AuthType`, `RateLimit`, `ProviderProfile`, `get_profile`, `get_all_profiles`, `resolve_alias`, `AuthStrategy`, `resolve_auth`, `ProviderError`, `ProviderAuthError`
+  - [x] Success: `from manta_trading.providers import ProviderType, get_profile, resolve_auth` works
 
-- [ ] **4.2 Verify package and run all provider tests**
-  - [ ] Run `pytest test/unit/test_provider_types.py test/unit/test_provider_profiles.py test/unit/test_provider_auth.py -v`
-  - [ ] Success: all tests pass, no import errors
+- [x] **4.2 Verify package and run all provider tests**
+  - [x] Run `pytest test/unit/test_provider_types.py test/unit/test_provider_profiles.py test/unit/test_provider_auth.py -v`
+  - [x] Success: all tests pass, no import errors
 
 **Commit**: `feat: add providers package init with public API exports`
 
 ### Phase 5: CLI Provider Commands
 
-- [ ] **5.1 Create provider CLI sub-app**
-  - [ ] Create `src/manta_trading/cli/commands/provider.py`
-  - [ ] Define `provider_app = typer.Typer(name="provider", help="Data provider management", no_args_is_help=True)`
-  - [ ] Import and use `get_logger` for module logging
-  - [ ] Import `print_result`, `print_error`, `make_table` from `manta_trading.cli.output`
-
-- [ ] **5.2 Implement `mt provider list`**
-  - [ ] Add `provider_list` command to `provider_app`
-  - [ ] Parameters: `json_output: bool = typer.Option(False, "--json")`
-  - [ ] Logic: iterate `get_all_profiles()`, for each profile resolve auth using `resolve_auth(profile, settings)` where settings comes from `ctx.obj["settings"]`
-  - [ ] Text mode: Rich table with columns: Name, Type, Description, Aliases, Auth (green check or red X)
-  - [ ] JSON mode: array of objects with keys: name, provider_type, description, aliases, auth_valid, base_url
-  - [ ] Use `make_table` and `print_result` from output module
-  - [ ] Success: `mt provider list` displays table; `mt provider list --json` emits valid JSON array
-
-- [ ] **5.3 Implement `mt provider status`**
-  - [ ] Add `provider_status` command to `provider_app`
-  - [ ] Parameters: `name: str = typer.Argument(None)`, `json_output: bool = typer.Option(False, "--json")`
-  - [ ] If name provided: `resolve_alias(name)` → `get_profile()` → display detailed info (name, type, base_url, api_key_env, rate_limit, auth status with source/hint)
-  - [ ] If no name: show detailed status for all providers
-  - [ ] Error case: unknown name/alias → print error with available providers and aliases, exit code 1
-  - [ ] Use `print_result`, `print_error` from output module
-  - [ ] Success: `mt provider status av` shows alphavantage details; `mt provider status` shows all
-
-- [ ] **5.4 Implement `mt provider test`**
-  - [ ] Add `provider_test` command to `provider_app`
-  - [ ] Parameters: `name: str = typer.Argument(...)`, `json_output: bool = typer.Option(False, "--json")`
-  - [ ] Logic: `resolve_alias(name)` → `get_profile()` → `resolve_auth(profile, settings)` → report `is_valid`, `active_source`, `setup_hint`
-  - [ ] Text mode: green check + source if valid; red X + setup hint if not
-  - [ ] JSON mode: object with keys: provider, auth_valid, active_source, setup_hint
-  - [ ] Error case: unknown name/alias → print error with available providers, exit code 1
-  - [ ] Success: `mt provider test alphavantage` reports credential status
-
-- [ ] **5.5 Wire provider sub-app into main CLI**
-  - [ ] In `src/manta_trading/cli/app.py`, import `provider_app` from `manta_trading.cli.commands.provider`
-  - [ ] Add `app.add_typer(provider_app, name="provider")`
-  - [ ] Success: `mt provider --help` shows list, status, test commands
-
-- [ ] **5.6 Test provider CLI commands**
-  - [ ] Create `test/unit/test_cli_provider.py`
-  - [ ] Use `typer.testing.CliRunner` with mock Settings (same isolation pattern as `test_cli_config.py`)
-  - [ ] Test `mt provider list` — exit code 0, output contains "alphavantage", "databento", "flatfile"
-  - [ ] Test `mt provider list --json` — valid JSON array with 3 entries, each has required keys
-  - [ ] Test `mt provider status` (no arg) — exit code 0, output contains all provider names
-  - [ ] Test `mt provider status alphavantage` — exit code 0, shows alphavantage details
-  - [ ] Test `mt provider status av` — exit code 0, resolves alias to alphavantage
-  - [ ] Test `mt provider status nonexistent` — exit code 1, error mentions available providers
-  - [ ] Test `mt provider test alphavantage` without API key — reports not authenticated, shows setup hint
-  - [ ] Test `mt provider test alphavantage` with API key — reports authenticated
-  - [ ] Test `mt provider test nonexistent` — exit code 1
-  - [ ] Test `mt provider test alphavantage --json` — valid JSON with auth_valid field
-  - [ ] Success: all tests pass via `pytest test/unit/test_cli_provider.py -v`
+- [x] **5.1 Create provider CLI sub-app**
+  - [x] Create `src/manta_trading/cli/commands/provider.py`
+  - [x] Define `provider_app = typer.Typer(name="provider", help="Data provider management", no_args_is_help=True)`
+  - [x] Import and use `get_logger` for module logging
+  - [x] Import `print_result`, `print_error`, `make_table` from `manta_trading.cli.output`
+
+- [x] **5.2 Implement `mt provider list`**
+  - [x] Add `provider_list` command to `provider_app`
+  - [x] Parameters: `json_output: bool = typer.Option(False, "--json")`
+  - [x] Logic: iterate `get_all_profiles()`, for each profile resolve auth using `resolve_auth(profile, settings)` where settings comes from `ctx.obj["settings"]`
+  - [x] Text mode: Rich table with columns: Name, Type, Description, Aliases, Auth (green check or red X)
+  - [x] JSON mode: array of objects with keys: name, provider_type, description, aliases, auth_valid, base_url
+  - [x] Use `make_table` and `print_result` from output module
+  - [x] Success: `mt provider list` displays table; `mt provider list --json` emits valid JSON array
+
+- [x] **5.3 Implement `mt provider status`**
+  - [x] Add `provider_status` command to `provider_app`
+  - [x] Parameters: `name: str = typer.Argument(None)`, `json_output: bool = typer.Option(False, "--json")`
+  - [x] If name provided: `resolve_alias(name)` → `get_profile()` → display detailed info (name, type, base_url, api_key_env, rate_limit, auth status with source/hint)
+  - [x] If no name: show detailed status for all providers
+  - [x] Error case: unknown name/alias → print error with available providers and aliases, exit code 1
+  - [x] Use `print_result`, `print_error` from output module
+  - [x] Success: `mt provider status av` shows alphavantage details; `mt provider status` shows all
+
+- [x] **5.4 Implement `mt provider test`**
+  - [x] Add `provider_test` command to `provider_app`
+  - [x] Parameters: `name: str = typer.Argument(...)`, `json_output: bool = typer.Option(False, "--json")`
+  - [x] Logic: `resolve_alias(name)` → `get_profile()` → `resolve_auth(profile, settings)` → report `is_valid`, `active_source`, `setup_hint`
+  - [x] Text mode: green check + source if valid; red X + setup hint if not
+  - [x] JSON mode: object with keys: provider, auth_valid, active_source, setup_hint
+  - [x] Error case: unknown name/alias → print error with available providers, exit code 1
+  - [x] Success: `mt provider test alphavantage` reports credential status
+
+- [x] **5.5 Wire provider sub-app into main CLI**
+  - [x] In `src/manta_trading/cli/app.py`, import `provider_app` from `manta_trading.cli.commands.provider`
+  - [x] Add `app.add_typer(provider_app, name="provider")`
+  - [x] Success: `mt provider --help` shows list, status, test commands
+
+- [x] **5.6 Test provider CLI commands**
+  - [x] Create `test/unit/test_cli_provider.py`
+  - [x] Use `typer.testing.CliRunner` with mock Settings (same isolation pattern as `test_cli_config.py`)
+  - [x] Test `mt provider list` — exit code 0, output contains "alphavantage", "databento", "flatfile"
+  - [x] Test `mt provider list --json` — valid JSON array with 3 entries, each has required keys
+  - [x] Test `mt provider status` (no arg) — exit code 0, output contains all provider names
+  - [x] Test `mt provider status alphavantage` — exit code 0, shows alphavantage details
+  - [x] Test `mt provider status av` — exit code 0, resolves alias to alphavantage
+  - [x] Test `mt provider status nonexistent` — exit code 1, error mentions available providers
+  - [x] Test `mt provider test alphavantage` without API key — reports not authenticated, shows setup hint
+  - [x] Test `mt provider test alphavantage` with API key — reports authenticated
+  - [x] Test `mt provider test nonexistent` — exit code 1
+  - [x] Test `mt provider test alphavantage --json` — valid JSON with auth_valid field
+  - [x] Success: all tests pass via `pytest test/unit/test_cli_provider.py -v`
 
 **Commit**: `feat: add mt provider list/status/test CLI commands`
 
 ### Phase 6: Status Command
 
-- [ ] **6.1 Create status CLI command**
-  - [ ] Create `src/manta_trading/cli/commands/status.py`
-  - [ ] Define `status_app = typer.Typer(name="status", help="System status and health", no_args_is_help=False)`
-  - [ ] Implement default command (callback with `invoke_without_command=True`) with `json_output: bool = typer.Option(False, "--json")`
-  - [ ] **Provider health section**: iterate all profiles, resolve auth for each, display name + auth status
-  - [ ] **DB connectivity section**: check `settings.db_url` — if `None`, report "not configured"; if set, attempt a lightweight connection check (wrap in try/except, report connected or error message)
-  - [ ] Text mode: Rich output with section headers, green/red indicators
-  - [ ] JSON mode: structured object with `providers` (list of status objects) and `database` (object with `configured`, `connected`, `url` keys — redact credentials from URL)
-  - [ ] Use `print_result`, `make_table` from output module
-
-- [ ] **6.2 Replace stub status sub-app**
-  - [ ] In `src/manta_trading/cli/app.py`, remove the existing stub `status_app` and `status_overview` command
-  - [ ] Import `status_app` from `manta_trading.cli.commands.status`
-  - [ ] Keep `app.add_typer(status_app, name="status")`
-  - [ ] Success: `mt status` shows provider health and DB connectivity; `mt status --help` shows the real command
-
-- [ ] **6.3 Test status CLI command**
-  - [ ] Create `test/unit/test_cli_status.py`
-  - [ ] Use `typer.testing.CliRunner` with mock Settings
-  - [ ] Test `mt status` — exit code 0, output contains "Provider" and "Database" sections
-  - [ ] Test `mt status --json` — valid JSON with `providers` and `database` keys
-  - [ ] Test `mt status` with `db_url=None` — reports database not configured
-  - [ ] Test `mt status` with `db_url` set — reports connectivity status (mock the connection check)
-  - [ ] Test provider health shows auth status for each provider
-  - [ ] Success: all tests pass via `pytest test/unit/test_cli_status.py -v`
-
-- [ ] **6.4 Update test_cli_app.py for status changes**
-  - [ ] Update or remove `TestStatusSubApp` tests that reference the stub `status overview` command
-  - [ ] Add test that `mt status` invokes the real status command (not the stub)
-  - [ ] Success: `pytest test/unit/test_cli_app.py -v` passes
+- [x] **6.1 Create status CLI command**
+  - [x] Create `src/manta_trading/cli/commands/status.py`
+  - [x] Define `status_app = typer.Typer(name="status", help="System status and health", no_args_is_help=False)`
+  - [x] Implement default command (callback with `invoke_without_command=True`) with `json_output: bool = typer.Option(False, "--json")`
+  - [x] **Provider health section**: iterate all profiles, resolve auth for each, display name + auth status
+  - [x] **DB connectivity section**: check `settings.db_url` — if `None`, report "not configured"; if set, attempt a lightweight connection check (wrap in try/except, report connected or error message)
+  - [x] Text mode: Rich output with section headers, green/red indicators
+  - [x] JSON mode: structured object with `providers` (list of status objects) and `database` (object with `configured`, `connected`, `url` keys — redact credentials from URL)
+  - [x] Use `print_result`, `make_table` from output module
+
+- [x] **6.2 Replace stub status sub-app**
+  - [x] In `src/manta_trading/cli/app.py`, remove the existing stub `status_app` and `status_overview` command
+  - [x] Import `status_app` from `manta_trading.cli.commands.status`
+  - [x] Keep `app.add_typer(status_app, name="status")`
+  - [x] Success: `mt status` shows provider health and DB connectivity; `mt status --help` shows the real command
+
+- [x] **6.3 Test status CLI command**
+  - [x] Create `test/unit/test_cli_status.py`
+  - [x] Use `typer.testing.CliRunner` with mock Settings
+  - [x] Test `mt status` — exit code 0, output contains "Provider" and "Database" sections
+  - [x] Test `mt status --json` — valid JSON with `providers` and `database` keys
+  - [x] Test `mt status` with `db_url=None` — reports database not configured
+  - [x] Test `mt status` with `db_url` set — reports connectivity status (mock the connection check)
+  - [x] Test provider health shows auth status for each provider
+  - [x] Success: all tests pass via `pytest test/unit/test_cli_status.py -v`
+
+- [x] **6.4 Update test_cli_app.py for status changes**
+  - [x] Update or remove `TestStatusSubApp` tests that reference the stub `status overview` command
+  - [x] Add test that `mt status` invokes the real status command (not the stub)
+  - [x] Success: `pytest test/unit/test_cli_app.py -v` passes
 
 **Commit**: `feat: add mt status command with provider health and DB connectivity`
 
 ### Phase 7: Final Verification
 
-- [ ] **7.1 Run full test suite**
-  - [ ] Run `pytest test/unit/ -v` — all tests pass
-  - [ ] Confirm no regressions in existing tests (test_cli_app, test_cli_config, test_logging, test_cli_output)
-  - [ ] Success: zero failures
-
-- [ ] **7.2 Run verification walkthrough**
-  - [ ] Execute each step from slice design Verification Walkthrough section
-  - [ ] Update the walkthrough in the slice design with actual commands, output, and caveats
-  - [ ] Success: all walkthrough steps produce expected results
-
-- [ ] **7.3 Update slice and task status**
-  - [ ] Set `status: complete` and `dateUpdated` in this task file's frontmatter
-  - [ ] Set `status: complete` and `dateUpdated` in the slice design frontmatter
-  - [ ] Check off slice 902 in `user/architecture/900-slices.foundation-cleanup.md`
-  - [ ] Success: all status fields updated
-
-- [ ] **7.4 Update CHANGELOG.md**
-  - [ ] Add slice 902 entries under `[Unreleased]` section
-  - [ ] Include: provider registry, provider CLI commands, status command
-  - [ ] Success: CHANGELOG reflects 902 deliverables
-
-- [ ] **7.5 Run workflow check**
-  - [ ] Run `workflow_check` (or `cf check`) with fix parameter if available
-  - [ ] Success: 0 findings or all auto-fixed
+- [x] **7.1 Run full test suite**
+  - [x] Run `pytest test/unit/ -v` — all tests pass
+  - [x] Confirm no regressions in existing tests (test_cli_app, test_cli_config, test_logging, test_cli_output)
+  - [x] Success: zero failures
+
+- [x] **7.2 Run verification walkthrough**
+  - [x] Execute each step from slice design Verification Walkthrough section
+  - [x] Update the walkthrough in the slice design with actual commands, output, and caveats
+  - [x] Success: all walkthrough steps produce expected results
+
+- [x] **7.3 Update slice and task status**
+  - [x] Set `status: complete` and `dateUpdated` in this task file's frontmatter
+  - [x] Set `status: complete` and `dateUpdated` in the slice design frontmatter
+  - [x] Check off slice 902 in `user/architecture/900-slices.foundation-cleanup.md`
+  - [x] Success: all status fields updated
+
+- [x] **7.4 Update CHANGELOG.md**
+  - [x] Add slice 902 entries under `[Unreleased]` section
+  - [x] Include: provider registry, provider CLI commands, status command
+  - [x] Success: CHANGELOG reflects 902 deliverables
+
+- [x] **7.5 Run workflow check**
+  - [x] Run `workflow_check` (or `cf check`) with fix parameter if available
+  - [x] Success: 0 findings or all auto-fixed
 
 **Commit**: `docs: complete slice 902 — update walkthrough, tasks, and changelog`
diff --git a/src/manta_trading/cli/app.py b/src/manta_trading/cli/app.py
index 041c659..fd23f4d 100644
--- a/src/manta_trading/cli/app.py
+++ b/src/manta_trading/cli/app.py
@@ -7,6 +7,8 @@ import importlib.metadata
 import typer
 
 from manta_trading.cli.commands.config import config_app
+from manta_trading.cli.commands.provider import provider_app
+from manta_trading.cli.commands.status import status_app
 from manta_trading.config import Settings
 from manta_trading.logging import setup_logging
 
@@ -16,23 +18,9 @@ app = typer.Typer(
     no_args_is_help=True,
 )
 
-# -- Stub sub-apps for pattern verification ----------------------------------
-
-status_app = typer.Typer(
-    name="status",
-    help="System status",
-    no_args_is_help=True,
-)
-
-
-@status_app.command("overview")
-def status_overview() -> None:
-    """Show system status overview."""
-    typer.echo("Status commands not yet implemented")
-
-
 app.add_typer(status_app, name="status")
 app.add_typer(config_app, name="config")
+app.add_typer(provider_app, name="provider")
 
 
 # -- Version callback ---------------------------------------------------------
diff --git a/src/manta_trading/cli/commands/provider.py b/src/manta_trading/cli/commands/provider.py
new file mode 100644
index 0000000..7b669e2
--- /dev/null
+++ b/src/manta_trading/cli/commands/provider.py
@@ -0,0 +1,178 @@
+"""provider subcommand — data provider management and introspection."""
+
+from __future__ import annotations
+
+import typer
+
+from manta_trading.cli.output import make_table, print_error, print_result
+from manta_trading.logging import get_logger
+from manta_trading.providers.auth import resolve_auth
+from manta_trading.providers.profiles import (
+    get_all_profiles,
+    get_profile,
+    resolve_alias,
+)
+
+logger = get_logger(__name__)
+
+provider_app = typer.Typer(
+    name="provider",
+    help="Data provider management",
+    no_args_is_help=True,
+)
+
+
+@provider_app.command("list")
+def provider_list(
+    ctx: typer.Context,
+    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
+) -> None:
+    """Show all registered providers with auth status."""
+    settings = ctx.obj["settings"]
+    profiles = get_all_profiles()
+    rows = []
+
+    for name in sorted(profiles):
+        profile = profiles[name]
+        auth = resolve_auth(profile, settings)
+        rows.append({
+            "name": profile.name,
+            "provider_type": str(profile.provider_type),
+            "description": profile.description,
+            "aliases": ", ".join(profile.aliases) if profile.aliases else "",
+            "auth_valid": auth.is_valid(),
+            "base_url": profile.base_url or "",
+        })
+
+    if json_output:
+        print_result(rows, json_mode=True)
+        return
+
+    table = make_table(
+        "Providers",
+        [
+            ("Name", "cyan"),
+            ("Type", ""),
+            ("Description", ""),
+            ("Aliases", "dim"),
+            ("Auth", ""),
+        ],
+    )
+    for row in rows:
+        auth_icon = "[green]✓[/green]" if row["auth_valid"] else "[red]✗[/red]"
+        table.add_row(
+            row["name"],
+            row["provider_type"],
+            row["description"],
+            row["aliases"],
+            auth_icon,
+        )
+    print_result(table, json_mode=False)
+
+
+@provider_app.command("status")
+def provider_status(
+    ctx: typer.Context,
+    name: str = typer.Argument(None, help="Provider name or alias"),
+    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
+) -> None:
+    """Show detailed status for one or all providers."""
+    settings = ctx.obj["settings"]
+
+    if name is not None:
+        try:
+            canonical = resolve_alias(name)
+        except KeyError as exc:
+            print_error(str(exc), json_mode=json_output)
+            raise typer.Exit(code=1) from None
+        profiles = {canonical: get_profile(canonical)}
+    else:
+        profiles = get_all_profiles()
+
+    results = []
+    for canonical_name in sorted(profiles):
+        profile = profiles[canonical_name]
+        auth = resolve_auth(profile, settings)
+        results.append({
+            "name": profile.name,
+            "provider_type": str(profile.provider_type),
+            "base_url": profile.base_url,
+            "api_key_env": profile.api_key_env,
+            "rate_limit": (
+                {
+                    "requests_per_minute": profile.rate_limit.requests_per_minute,
+                    "daily_limit": profile.rate_limit.daily_limit,
+                }
+                if profile.rate_limit
+                else None
+            ),
+            "aliases": list(profile.aliases),
+            "auth_type": str(profile.auth_type),
+            "auth_valid": auth.is_valid(),
+            "active_source": auth.active_source,
+            "setup_hint": auth.setup_hint,
+        })
+
+    if json_output:
+        data = results[0] if name is not None else results
+        print_result(data, json_mode=True)
+        return
+
+    for info in results:
+        auth_icon = "[green]✓[/green]" if info["auth_valid"] else "[red]✗[/red]"
+        lines = [
+            f"[cyan bold]{info['name']}[/cyan bold]",
+            f"  Type:       {info['provider_type']}",
+            f"  Base URL:   {info['base_url'] or 'n/a'}",
+            f"  Auth:       {auth_icon} {info['active_source'] or info['setup_hint']}",
+        ]
+        if info["rate_limit"]:
+            rl = info["rate_limit"]
+            limit_str = f"{rl['requests_per_minute']} req/min"
+            if rl["daily_limit"]:
+                limit_str += f", {rl['daily_limit']}/day"
+            lines.append(f"  Rate Limit: {limit_str}")
+        if info["aliases"]:
+            lines.append(f"  Aliases:    {', '.join(info['aliases'])}")
+        print_result("\n".join(lines), json_mode=False)
+
+
+@provider_app.command("test")
+def provider_test(
+    ctx: typer.Context,
+    name: str = typer.Argument(..., help="Provider name or alias"),
+    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
+) -> None:
+    """Validate credentials for a specific provider."""
+    settings = ctx.obj["settings"]
+
+    try:
+        canonical = resolve_alias(name)
+    except KeyError as exc:
+        print_error(str(exc), json_mode=json_output)
+        raise typer.Exit(code=1) from None
+
+    profile = get_profile(canonical)
+    auth = resolve_auth(profile, settings)
+
+    result = {
+        "provider": profile.name,
+        "auth_valid": auth.is_valid(),
+        "active_source": auth.active_source,
+        "setup_hint": auth.setup_hint,
+    }
+
+    if json_output:
+        print_result(result, json_mode=True)
+        return
+
+    if auth.is_valid():
+        print_result(
+            f"[green]✓[/green] {profile.name}: authenticated via {auth.active_source}",
+            json_mode=False,
+        )
+    else:
+        print_result(
+            f"[red]✗[/red] {profile.name}: not authenticated — {auth.setup_hint}",
+            json_mode=False,
+        )
diff --git a/src/manta_trading/cli/commands/status.py b/src/manta_trading/cli/commands/status.py
new file mode 100644
index 0000000..63b612b
--- /dev/null
+++ b/src/manta_trading/cli/commands/status.py
@@ -0,0 +1,119 @@
+"""status subcommand — system health overview."""
+
+from __future__ import annotations
+
+from urllib.parse import urlparse
+
+import typer
+
+from manta_trading.cli.output import make_table, print_result
+from manta_trading.logging import get_logger
+from manta_trading.providers.auth import resolve_auth
+from manta_trading.providers.profiles import get_all_profiles
+
+logger = get_logger(__name__)
+
+status_app = typer.Typer(
+    name="status",
+    help="System status and health",
+    no_args_is_help=False,
+)
+
+
+def _redact_url(url: str) -> str:
+    """Redact credentials from a database URL."""
+    parsed = urlparse(url)
+    if parsed.password:
+        redacted = parsed._replace(
+            netloc=f"{parsed.username}:***@{parsed.hostname}"
+            + (f":{parsed.port}" if parsed.port else "")
+        )
+        return redacted.geturl()
+    return url
+
+
+def _check_db_connectivity(db_url: str) -> tuple[bool, str]:
+    """Attempt a lightweight DB connectivity check.
+
+    Returns (connected, message).
+    """
+    try:
+        from sqlalchemy import create_engine, text
+
+        engine = create_engine(db_url)
+        with engine.connect() as conn:
+            conn.execute(text("SELECT 1"))
+        engine.dispose()
+        return True, "connected"
+    except Exception as exc:  # noqa: BLE001
+        logger.debug("DB connectivity check failed: %s", exc)
+        return False, str(exc)
+
+
+@status_app.callback(invoke_without_command=True)
+def status_overview(
+    ctx: typer.Context,
+    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
+) -> None:
+    """Show system health overview."""
+    if ctx.invoked_subcommand is not None:
+        return
+
+    settings = ctx.obj["settings"]
+    profiles = get_all_profiles()
+
+    # Provider health
+    provider_results = []
+    for name in sorted(profiles):
+        profile = profiles[name]
+        auth = resolve_auth(profile, settings)
+        provider_results.append({
+            "name": profile.name,
+            "auth_valid": auth.is_valid(),
+            "active_source": auth.active_source,
+        })
+
+    # DB connectivity
+    db_info: dict = {"configured": False, "connected": False, "url": None}
+    if settings.db_url:
+        db_info["configured"] = True
+        db_info["url"] = _redact_url(settings.db_url)
+        connected, message = _check_db_connectivity(settings.db_url)
+        db_info["connected"] = connected
+        if not connected:
+            db_info["error"] = message
+
+    if json_output:
+        print_result(
+            {"providers": provider_results, "database": db_info},
+            json_mode=True,
+        )
+        return
+
+    # Text output — Providers section
+    table = make_table(
+        "Providers",
+        [("Name", "cyan"), ("Auth", "")],
+    )
+    for p in provider_results:
+        icon = "[green]✓[/green]" if p["auth_valid"] else "[red]✗[/red]"
+        source = p["active_source"] or ""
+        table.add_row(p["name"], f"{icon} {source}")
+    print_result(table, json_mode=False)
+
+    # Text output — Database section
+    if not db_info["configured"]:
+        print_result(
+            "\n[bold]Database[/bold]: [dim]not configured (MT_DB_URL)[/dim]",
+            json_mode=False,
+        )
+    elif db_info["connected"]:
+        print_result(
+            f"\n[bold]Database[/bold]: [green]connected[/green] ({db_info['url']})",
+            json_mode=False,
+        )
+    else:
+        print_result(
+            f"\n[bold]Database[/bold]: [red]error[/red] — {db_info.get('error', 'unknown')}",
+            json_mode=False,
+        )
diff --git a/src/manta_trading/providers/__init__.py b/src/manta_trading/providers/__init__.py
new file mode 100644
index 0000000..fde9039
--- /dev/null
+++ b/src/manta_trading/providers/__init__.py
@@ -0,0 +1,27 @@
+"""Providers package — centralized provider registry and auth."""
+
+from __future__ import annotations
+
+from manta_trading.providers.auth import AuthStrategy, resolve_auth
+from manta_trading.providers.errors import ProviderAuthError, ProviderError
+from manta_trading.providers.profiles import (
+    ProviderProfile,
+    get_all_profiles,
+    get_profile,
+    resolve_alias,
+)
+from manta_trading.providers.types import AuthType, ProviderType, RateLimit
+
+__all__ = [
+    "AuthStrategy",
+    "AuthType",
+    "ProviderAuthError",
+    "ProviderError",
+    "ProviderProfile",
+    "ProviderType",
+    "RateLimit",
+    "get_all_profiles",
+    "get_profile",
+    "resolve_alias",
+    "resolve_auth",
+]
diff --git a/src/manta_trading/providers/auth.py b/src/manta_trading/providers/auth.py
new file mode 100644
index 0000000..a90746d
--- /dev/null
+++ b/src/manta_trading/providers/auth.py
@@ -0,0 +1,70 @@
+"""Auth strategy protocol and implementations for credential resolution."""
+
+from __future__ import annotations
+
+from typing import TYPE_CHECKING, Protocol, runtime_checkable
+
+from manta_trading.providers.types import AuthType
+
+if TYPE_CHECKING:
+    from manta_trading.config import Settings
+    from manta_trading.providers.profiles import ProviderProfile
+
+
+@runtime_checkable
+class AuthStrategy(Protocol):
+    """Credential resolution strategy for a provider."""
+
+    def is_valid(self) -> bool: ...
+
+    @property
+    def active_source(self) -> str | None: ...
+
+    @property
+    def setup_hint(self) -> str: ...
+
+
+class NoAuthStrategy:
+    """No-op strategy for providers that don't require credentials."""
+
+    def is_valid(self) -> bool:
+        return True
+
+    @property
+    def active_source(self) -> str | None:
+        return "none_required"
+
+    @property
+    def setup_hint(self) -> str:
+        return ""
+
+
+class ApiKeyAuthStrategy:
+    """Resolve an API key credential from Settings."""
+
+    def __init__(self, env_var_name: str, settings: Settings) -> None:
+        self._env_var_name = env_var_name
+        # Derive Settings field name: MT_ALPHAVANTAGE_API_KEY → alphavantage_api_key
+        field_name = env_var_name.removeprefix("MT_").lower()
+        self._credential: str | None = getattr(settings, field_name, None)
+
+    def is_valid(self) -> bool:
+        return isinstance(self._credential, str) and len(self._credential) > 0
+
+    @property
+    def active_source(self) -> str | None:
+        if self.is_valid():
+            return f"env:{self._env_var_name}"
+        return None
+
+    @property
+    def setup_hint(self) -> str:
+        return f"Set {self._env_var_name} environment variable"
+
+
+def resolve_auth(profile: ProviderProfile, settings: Settings) -> AuthStrategy:
+    """Construct the appropriate auth strategy for a provider profile."""
+    if profile.auth_type == AuthType.NONE:
+        return NoAuthStrategy()
+    # AuthType.API_KEY
+    return ApiKeyAuthStrategy(profile.api_key_env, settings)  # type: ignore[arg-type]
diff --git a/src/manta_trading/providers/errors.py b/src/manta_trading/providers/errors.py
new file mode 100644
index 0000000..416a89b
--- /dev/null
+++ b/src/manta_trading/providers/errors.py
@@ -0,0 +1,11 @@
+"""Provider-related exception hierarchy."""
+
+from __future__ import annotations
+
+
+class ProviderError(Exception):
+    """Base exception for provider-related errors."""
+
+
+class ProviderAuthError(ProviderError):
+    """Authentication or credential errors."""
diff --git a/src/manta_trading/providers/profiles.py b/src/manta_trading/providers/profiles.py
new file mode 100644
index 0000000..2718deb
--- /dev/null
+++ b/src/manta_trading/providers/profiles.py
@@ -0,0 +1,98 @@
+"""Provider profiles — frozen definitions, lookup, and alias resolution."""
+
+from __future__ import annotations
+
+from dataclasses import dataclass
+
+from manta_trading.providers.types import AuthType, ProviderType, RateLimit
+
+
+@dataclass(frozen=True)
+class ProviderProfile:
+    """Immutable configuration preset for a data provider."""
+
+    name: str
+    provider_type: ProviderType
+    base_url: str | None = None
+    api_key_env: str | None = None
+    rate_limit: RateLimit | None = None
+    aliases: tuple[str, ...] = ()
+    auth_type: AuthType = AuthType.API_KEY
+    description: str = ""
+
+
+BUILT_IN_PROFILES: dict[str, ProviderProfile] = {
+    "alphavantage": ProviderProfile(
+        name="alphavantage",
+        provider_type=ProviderType.ALPHA_VANTAGE,
+        base_url="https://www.alphavantage.co/query",
+        api_key_env="MT_ALPHAVANTAGE_API_KEY",
+        rate_limit=RateLimit(requests_per_minute=30),
+        aliases=("av",),
+        description="Alpha Vantage market data API",
+    ),
+    "databento": ProviderProfile(
+        name="databento",
+        provider_type=ProviderType.DATABENTO,
+        base_url="https://hist.databento.com",
+        api_key_env="MT_DATABENTO_API_KEY",
+        rate_limit=None,
+        aliases=("db", "bento"),
+        description="Databento historical market data",
+    ),
+    "flatfile": ProviderProfile(
+        name="flatfile",
+        provider_type=ProviderType.FLAT_FILE,
+        base_url=None,
+        api_key_env=None,
+        rate_limit=None,
+        aliases=("flat", "file"),
+        auth_type=AuthType.NONE,
+        description="Local flat file data source",
+    ),
+}
+
+
+def get_all_profiles() -> dict[str, ProviderProfile]:
+    """Return a copy of all built-in provider profiles."""
+    return dict(BUILT_IN_PROFILES)
+
+
+def get_profile(name: str) -> ProviderProfile:
+    """Return a provider profile by canonical name.
+
+    Raises ``KeyError`` with available profile names if not found.
+    """
+    try:
+        return BUILT_IN_PROFILES[name]
+    except KeyError:
+        available = ", ".join(sorted(BUILT_IN_PROFILES))
+        msg = f"Unknown provider {name!r}. Available: {available}"
+        raise KeyError(msg) from None
+
+
+def resolve_alias(name_or_alias: str) -> str:
+    """Map an alias to its canonical provider name.
+
+    Canonical names pass through unchanged. Raises ``KeyError`` with
+    available names and aliases if not found.
+    """
+    if name_or_alias in BUILT_IN_PROFILES:
+        return name_or_alias
+
+    for canonical, profile in BUILT_IN_PROFILES.items():
+        if name_or_alias in profile.aliases:
+            return canonical
+
+    available = sorted(BUILT_IN_PROFILES)
+    all_aliases = sorted(
+        alias
+        for p in BUILT_IN_PROFILES.values()
+        for alias in p.aliases
+    )
+    msg = (
+        f"Unknown provider or alias {name_or_alias!r}. "
+        f"Available: {', '.join(available)}. "
+        f"Aliases: {', '.join(all_aliases)}"
+    )
+    raise KeyError(msg)
diff --git a/src/manta_trading/providers/types.py b/src/manta_trading/providers/types.py
new file mode 100644
index 0000000..5aab34a
--- /dev/null
+++ b/src/manta_trading/providers/types.py
@@ -0,0 +1,29 @@
+"""Provider type enums and rate limit dataclass."""
+
+from __future__ import annotations
+
+from dataclasses import dataclass
+from enum import StrEnum
+
+
+class ProviderType(StrEnum):
+    """Registered data provider identifiers."""
+
+    ALPHA_VANTAGE = "alphavantage"
+    DATABENTO = "databento"
+    FLAT_FILE = "flatfile"
+
+
+class AuthType(StrEnum):
+    """Authentication strategy identifiers."""
+
+    API_KEY = "api_key"
+    NONE = "none"
+
+
+@dataclass(frozen=True)
+class RateLimit:
+    """Provider rate limit constraints (static, from documentation)."""
+
+    requests_per_minute: int
+    daily_limit: int | None = None
diff --git a/test/unit/test_cli_app.py b/test/unit/test_cli_app.py
index f697fa6..fbfb688 100644
--- a/test/unit/test_cli_app.py
+++ b/test/unit/test_cli_app.py
@@ -37,15 +37,14 @@ class TestVersion:
 
 
 class TestStatusSubApp:
-    """Verify stub status sub-app is reachable."""
+    """Verify status sub-app is reachable."""
 
-    def test_status_overview(self):
-        result = runner.invoke(app, ["status", "overview"])
+    def test_status_runs(self):
+        result = runner.invoke(app, ["status"])
         assert result.exit_code == 0
-        assert "Status commands not yet implemented" in result.output
 
     def test_status_shows_help(self):
-        result = runner.invoke(app, ["status"])
+        result = runner.invoke(app, ["status", "--help"])
         assert "System status" in result.output
 
 
@@ -54,7 +53,7 @@ class TestSettingsInContext:
 
     def test_callback_runs_without_error(self):
         """Invoking a command exercises the callback, creating Settings."""
-        result = runner.invoke(app, ["status", "overview"])
+        result = runner.invoke(app, ["status"])
         assert result.exit_code == 0
 
 
@@ -64,18 +63,16 @@ class TestLoggingIntegration:
     def test_stdout_not_polluted_by_log_output(self):
         result = runner.invoke(
             app,
-            ["status", "overview"],
+            ["status"],
             env={"MT_LOG_LEVEL": "DEBUG", "MT_LOG_FORMAT": "text"},
         )
         assert result.exit_code == 0
-        # stdout should only contain command output
-        assert "Status commands not yet implemented" in result.output
 
     def test_setup_logging_is_called(self):
         """Verify the app callback invokes setup_logging without error."""
         result = runner.invoke(
             app,
-            ["status", "overview"],
+            ["status"],
             env={"MT_LOG_LEVEL": "WARNING", "MT_LOG_FORMAT": "json"},
         )
         assert result.exit_code == 0
diff --git a/test/unit/test_cli_provider.py b/test/unit/test_cli_provider.py
new file mode 100644
index 0000000..13a48f8
--- /dev/null
+++ b/test/unit/test_cli_provider.py
@@ -0,0 +1,127 @@
+"""Tests for mt provider CLI commands."""
+
+from __future__ import annotations
+
+import json
+
+from typer.testing import CliRunner
+
+from manta_trading.cli.app import app
+
+runner = CliRunner()
+
+
+class TestProviderList:
+    """Verify mt provider list."""
+
+    def test_list_exit_code(self):
+        result = runner.invoke(app, ["provider", "list"])
+        assert result.exit_code == 0
+
+    def test_list_contains_all_providers(self):
+        result = runner.invoke(app, ["provider", "list"])
+        assert "alphavantage" in result.output
+        assert "databento" in result.output
+        assert "flatfile" in result.output
+
+    def test_list_json_valid(self):
+        result = runner.invoke(app, ["provider", "list", "--json"])
+        assert result.exit_code == 0
+        data = json.loads(result.output)
+        assert isinstance(data, list)
+        assert len(data) == 3
+
+    def test_list_json_has_required_keys(self):
+        result = runner.invoke(app, ["provider", "list", "--json"])
+        data = json.loads(result.output)
+        for entry in data:
+            assert "name" in entry
+            assert "provider_type" in entry
+            assert "auth_valid" in entry
+            assert "base_url" in entry
+
+
+class TestProviderStatus:
+    """Verify mt provider status."""
+
+    def test_status_all_providers(self):
+        result = runner.invoke(app, ["provider", "status"])
+        assert result.exit_code == 0
+        assert "alphavantage" in result.output
+        assert "databento" in result.output
+        assert "flatfile" in result.output
+
+    def test_status_single_provider(self):
+        result = runner.invoke(app, ["provider", "status", "alphavantage"])
+        assert result.exit_code == 0
+        assert "alphavantage" in result.output
+
+    def test_status_alias_resolution(self):
+        result = runner.invoke(app, ["provider", "status", "av"])
+        assert result.exit_code == 0
+        assert "alphavantage" in result.output
+
+    def test_status_nonexistent_exits_1(self):
+        result = runner.invoke(app, ["provider", "status", "nonexistent"])
+        assert result.exit_code == 1
+
+    def test_status_nonexistent_shows_available(self):
+        result = runner.invoke(app, ["provider", "status", "nonexistent"])
+        assert "Available" in result.output or "available" in result.output.lower()
+
+    def test_status_json_single(self):
+        result = runner.invoke(
+            app, ["provider", "status", "alphavantage", "--json"]
+        )
+        assert result.exit_code == 0
+        data = json.loads(result.output)
+        assert isinstance(data, dict)
+        assert data["name"] == "alphavantage"
+
+    def test_status_json_all(self):
+        result = runner.invoke(app, ["provider", "status", "--json"])
+        assert result.exit_code == 0
+        data = json.loads(result.output)
+        assert isinstance(data, list)
+        assert len(data) == 3
+
+
+class TestProviderTest:
+    """Verify mt provider test."""
+
+    def test_test_without_api_key(self):
+        result = runner.invoke(app, ["provider", "test", "alphavantage"])
+        assert result.exit_code == 0
+        assert "not authenticated" in result.output or "✗" in result.output
+
+    def test_test_with_api_key(self):
+        result = runner.invoke(
+            app,
+            ["provider", "test", "alphavantage"],
+            env={"MT_ALPHAVANTAGE_API_KEY": "demo-key"},
+        )
+        assert result.exit_code == 0
+        assert "authenticated" in result.output or "✓" in result.output
+
+    def test_test_nonexistent_exits_1(self):
+        result = runner.invoke(app, ["provider", "test", "nonexistent"])
+        assert result.exit_code == 1
+
+    def test_test_json_output(self):
+        result = runner.invoke(
+            app, ["provider", "test", "alphavantage", "--json"]
+        )
+        assert result.exit_code == 0
+        data = json.loads(result.output)
+        assert "auth_valid" in data
+        assert data["provider"] == "alphavantage"
+
+    def test_test_flatfile_always_valid(self):
+        result = runner.invoke(app, ["provider", "test", "flatfile"])
+        assert result.exit_code == 0
+        assert "authenticated" in result.output or "✓" in result.output
+
+    def test_test_alias_resolution(self):
+        result = runner.invoke(app, ["provider", "test", "av"])
+        assert result.exit_code == 0
+        assert "alphavantage" in result.output
diff --git a/test/unit/test_cli_status.py b/test/unit/test_cli_status.py
new file mode 100644
index 0000000..d5fa41f
--- /dev/null
+++ b/test/unit/test_cli_status.py
@@ -0,0 +1,118 @@
+"""Tests for mt status CLI command."""
+
+from __future__ import annotations
+
+import json
+from unittest.mock import patch
+
+from typer.testing import CliRunner
+
+from manta_trading.cli.app import app
+
+runner = CliRunner()
+
+
+class TestStatusOverview:
+    """Verify mt status."""
+
+    def test_exit_code(self):
+        result = runner.invoke(app, ["status"])
+        assert result.exit_code == 0
+
+    def test_contains_provider_section(self):
+        result = runner.invoke(app, ["status"])
+        assert "Provider" in result.output or "provider" in result.output.lower()
+
+    def test_contains_database_section(self):
+        result = runner.invoke(app, ["status"])
+        assert "Database" in result.output or "database" in result.output.lower()
+
+    def test_shows_all_provider_names(self):
+        result = runner.invoke(app, ["status"])
+        assert "alphavantage" in result.output
+        assert "databento" in result.output
+        assert "flatfile" in result.output
+
+    def test_db_not_configured(self):
+        result = runner.invoke(app, ["status"])
+        assert "not configured" in result.output
+
+
+class TestStatusJson:
+    """Verify mt status --json."""
+
+    def test_json_valid(self):
+        result = runner.invoke(app, ["status", "--json"])
+        assert result.exit_code == 0
+        data = json.loads(result.output)
+        assert isinstance(data, dict)
+
+    def test_json_has_providers_key(self):
+        result = runner.invoke(app, ["status", "--json"])
+        data = json.loads(result.output)
+        assert "providers" in data
+        assert isinstance(data["providers"], list)
+        assert len(data["providers"]) == 3
+
+    def test_json_has_database_key(self):
+        result = runner.invoke(app, ["status", "--json"])
+        data = json.loads(result.output)
+        assert "database" in data
+        assert isinstance(data["database"], dict)
+
+    def test_json_db_not_configured(self):
+        result = runner.invoke(app, ["status", "--json"])
+        data = json.loads(result.output)
+        assert data["database"]["configured"] is False
+        assert data["database"]["connected"] is False
+
+    def test_json_provider_auth_status(self):
+        result = runner.invoke(app, ["status", "--json"])
+        data = json.loads(result.output)
+        for p in data["providers"]:
+            assert "name" in p
+            assert "auth_valid" in p
+
+
+class TestStatusDbConnectivity:
+    """Verify DB connectivity checks in mt status."""
+
+    def test_db_configured_connected(self):
+        with patch(
+            "manta_trading.cli.commands.status._check_db_connectivity",
+            return_value=(True, "connected"),
+        ):
+            result = runner.invoke(
+                app, ["status", "--json"],
+                env={"MT_DB_URL": "sqlite:///test.db"},
+            )
+        data = json.loads(result.output)
+        assert data["database"]["configured"] is True
+        assert data["database"]["connected"] is True
+
+    def test_db_configured_unreachable(self):
+        with patch(
+            "manta_trading.cli.commands.status._check_db_connectivity",
+            return_value=(False, "connection refused"),
+        ):
+            result = runner.invoke(
+                app, ["status", "--json"],
+                env={"MT_DB_URL": "postgresql://localhost/test"},
+            )
+        data = json.loads(result.output)
+        assert data["database"]["configured"] is True
+        assert data["database"]["connected"] is False
+        assert "error" in data["database"]
+
+    def test_db_url_redacted_in_output(self):
+        with patch(
+            "manta_trading.cli.commands.status._check_db_connectivity",
+            return_value=(True, "connected"),
+        ):
+            result = runner.invoke(
+                app, ["status", "--json"],
+                env={"MT_DB_URL": "postgresql://user:secret@localhost/db"},
+            )
+        data = json.loads(result.output)
+        assert "secret" not in data["database"]["url"]
+        assert "***" in data["database"]["url"]
diff --git a/test/unit/test_provider_auth.py b/test/unit/test_provider_auth.py
new file mode 100644
index 0000000..79b834c
--- /dev/null
+++ b/test/unit/test_provider_auth.py
@@ -0,0 +1,107 @@
+"""Tests for auth strategy protocol and implementations."""
+
+from __future__ import annotations
+
+from unittest.mock import MagicMock
+
+import pytest
+
+from manta_trading.providers.auth import (
+    ApiKeyAuthStrategy,
+    AuthStrategy,
+    NoAuthStrategy,
+    resolve_auth,
+)
+from manta_trading.providers.profiles import get_profile
+from manta_trading.providers.types import AuthType
+
+
+def _mock_settings(**kwargs) -> MagicMock:
+    """Create a mock Settings with specified attributes."""
+    settings = MagicMock()
+    # Clear all defaults so getattr returns None for unset fields
+    settings.configure_mock(**kwargs)
+    return settings
+
+
+class TestNoAuthStrategy:
+    """Verify NoAuthStrategy."""
+
+    def test_is_always_valid(self):
+        strategy = NoAuthStrategy()
+        assert strategy.is_valid() is True
+
+    def test_active_source(self):
+        strategy = NoAuthStrategy()
+        assert strategy.active_source == "none_required"
+
+    def test_setup_hint_is_empty(self):
+        strategy = NoAuthStrategy()
+        assert strategy.setup_hint == ""
+
+    def test_satisfies_protocol(self):
+        assert isinstance(NoAuthStrategy(), AuthStrategy)
+
+
+class TestApiKeyAuthStrategy:
+    """Verify ApiKeyAuthStrategy."""
+
+    def test_valid_with_credential(self):
+        settings = _mock_settings(alphavantage_api_key="demo-key")
+        strategy = ApiKeyAuthStrategy("MT_ALPHAVANTAGE_API_KEY", settings)
+        assert strategy.is_valid() is True
+
+    def test_active_source_with_credential(self):
+        settings = _mock_settings(alphavantage_api_key="demo-key")
+        strategy = ApiKeyAuthStrategy("MT_ALPHAVANTAGE_API_KEY", settings)
+        assert strategy.active_source == "env:MT_ALPHAVANTAGE_API_KEY"
+
+    def test_invalid_without_credential(self):
+        settings = MagicMock(spec=[])  # No attributes
+        strategy = ApiKeyAuthStrategy("MT_ALPHAVANTAGE_API_KEY", settings)
+        assert strategy.is_valid() is False
+
+    def test_active_source_none_without_credential(self):
+        settings = MagicMock(spec=[])
+        strategy = ApiKeyAuthStrategy("MT_ALPHAVANTAGE_API_KEY", settings)
+        assert strategy.active_source is None
+
+    def test_invalid_with_empty_string(self):
+        settings = _mock_settings(alphavantage_api_key="")
+        strategy = ApiKeyAuthStrategy("MT_ALPHAVANTAGE_API_KEY", settings)
+        assert strategy.is_valid() is False
+
+    def test_setup_hint_contains_env_var(self):
+        settings = MagicMock(spec=[])
+        strategy = ApiKeyAuthStrategy("MT_ALPHAVANTAGE_API_KEY", settings)
+        assert "MT_ALPHAVANTAGE_API_KEY" in strategy.setup_hint
+
+    def test_satisfies_protocol(self):
+        settings = _mock_settings(alphavantage_api_key="key")
+        assert isinstance(
+            ApiKeyAuthStrategy("MT_ALPHAVANTAGE_API_KEY", settings),
+            AuthStrategy,
+        )
+
+
+class TestResolveAuth:
+    """Verify resolve_auth dispatch."""
+
+    def test_none_auth_returns_no_auth_strategy(self):
+        profile = get_profile("flatfile")
+        settings = MagicMock()
+        result = resolve_auth(profile, settings)
+        assert isinstance(result, NoAuthStrategy)
+
+    def test_api_key_auth_returns_api_key_strategy(self):
+        profile = get_profile("alphavantage")
+        settings = _mock_settings(alphavantage_api_key="demo")
+        result = resolve_auth(profile, settings)
+        assert isinstance(result, ApiKeyAuthStrategy)
+
+    def test_api_key_strategy_reads_correct_credential(self):
+        profile = get_profile("alphavantage")
+        settings = _mock_settings(alphavantage_api_key="my-key")
+        result = resolve_auth(profile, settings)
+        assert result.is_valid() is True
+        assert result.active_source == "env:MT_ALPHAVANTAGE_API_KEY"
diff --git a/test/unit/test_provider_profiles.py b/test/unit/test_provider_profiles.py
new file mode 100644
index 0000000..925ad99
--- /dev/null
+++ b/test/unit/test_provider_profiles.py
@@ -0,0 +1,113 @@
+"""Tests for provider profiles, lookup, and alias resolution."""
+
+from __future__ import annotations
+
+import dataclasses
+
+import pytest
+
+from manta_trading.providers.profiles import (
+    BUILT_IN_PROFILES,
+    ProviderProfile,
+    get_all_profiles,
+    get_profile,
+    resolve_alias,
+)
+from manta_trading.providers.types import AuthType, ProviderType
+
+
+class TestProviderProfile:
+    """Verify ProviderProfile frozen dataclass."""
+
+    def test_is_frozen(self):
+        profile = get_profile("alphavantage")
+        with pytest.raises(dataclasses.FrozenInstanceError):
+            profile.name = "changed"  # type: ignore[misc]
+
+
+class TestBuiltInProfiles:
+    """Verify BUILT_IN_PROFILES contents."""
+
+    def test_contains_three_entries(self):
+        assert len(BUILT_IN_PROFILES) == 3
+
+    def test_keys(self):
+        assert set(BUILT_IN_PROFILES) == {"alphavantage", "databento", "flatfile"}
+
+    def test_alphavantage_profile(self):
+        p = BUILT_IN_PROFILES["alphavantage"]
+        assert p.provider_type == ProviderType.ALPHA_VANTAGE
+        assert p.api_key_env == "MT_ALPHAVANTAGE_API_KEY"
+        assert p.auth_type == AuthType.API_KEY
+        assert p.aliases == ("av",)
+
+    def test_databento_profile(self):
+        p = BUILT_IN_PROFILES["databento"]
+        assert p.provider_type == ProviderType.DATABENTO
+        assert p.api_key_env == "MT_DATABENTO_API_KEY"
+        assert p.auth_type == AuthType.API_KEY
+        assert p.aliases == ("db", "bento")
+
+    def test_flatfile_profile(self):
+        p = BUILT_IN_PROFILES["flatfile"]
+        assert p.provider_type == ProviderType.FLAT_FILE
+        assert p.api_key_env is None
+        assert p.auth_type == AuthType.NONE
+        assert p.aliases == ("flat", "file")
+
+
+class TestGetAllProfiles:
+    """Verify get_all_profiles."""
+
+    def test_returns_all_three(self):
+        profiles = get_all_profiles()
+        assert len(profiles) == 3
+
+    def test_returns_copy(self):
+        profiles = get_all_profiles()
+        assert profiles is not BUILT_IN_PROFILES
+
+
+class TestGetProfile:
+    """Verify get_profile lookup."""
+
+    def test_returns_correct_profile(self):
+        p = get_profile("alphavantage")
+        assert p.name == "alphavantage"
+        assert p.provider_type == ProviderType.ALPHA_VANTAGE
+
+    def test_nonexistent_raises_key_error(self):
+        with pytest.raises(KeyError, match="Available"):
+            get_profile("nonexistent")
+
+
+class TestResolveAlias:
+    """Verify alias resolution."""
+
+    def test_av_resolves_to_alphavantage(self):
+        assert resolve_alias("av") == "alphavantage"
+
+    def test_bento_resolves_to_databento(self):
+        assert resolve_alias("bento") == "databento"
+
+    def test_db_resolves_to_databento(self):
+        assert resolve_alias("db") == "databento"
+
+    def test_flat_resolves_to_flatfile(self):
+        assert resolve_alias("flat") == "flatfile"
+
+    def test_file_resolves_to_flatfile(self):
+        assert resolve_alias("file") == "flatfile"
+
+    def test_canonical_passthrough(self):
+        assert resolve_alias("alphavantage") == "alphavantage"
+        assert resolve_alias("databento") == "databento"
+        assert resolve_alias("flatfile") == "flatfile"
+
+    def test_nonexistent_raises_key_error(self):
+        with pytest.raises(KeyError, match="Available"):
+            resolve_alias("nonexistent")
+
+    def test_nonexistent_error_includes_aliases(self):
+        with pytest.raises(KeyError, match="Aliases"):
+            resolve_alias("nonexistent")
diff --git a/test/unit/test_provider_types.py b/test/unit/test_provider_types.py
new file mode 100644
index 0000000..82590fd
--- /dev/null
+++ b/test/unit/test_provider_types.py
@@ -0,0 +1,86 @@
+"""Tests for provider types, enums, and error hierarchy."""
+
+from __future__ import annotations
+
+import dataclasses
+from enum import StrEnum
+
+import pytest
+
+from manta_trading.providers.errors import ProviderAuthError, ProviderError
+from manta_trading.providers.types import AuthType, ProviderType, RateLimit
+
+
+class TestProviderType:
+    """Verify ProviderType enum."""
+
+    def test_is_str_enum(self):
+        assert issubclass(ProviderType, StrEnum)
+
+    def test_has_three_members(self):
+        assert len(ProviderType) == 3
+
+    def test_values_are_lowercase_strings(self):
+        for member in ProviderType:
+            assert member.value == member.value.lower()
+            assert isinstance(member.value, str)
+
+    def test_alpha_vantage_serializes(self):
+        assert str(ProviderType.ALPHA_VANTAGE) == "alphavantage"
+
+    def test_databento_serializes(self):
+        assert str(ProviderType.DATABENTO) == "databento"
+
+    def test_flat_file_serializes(self):
+        assert str(ProviderType.FLAT_FILE) == "flatfile"
+
+
+class TestAuthType:
+    """Verify AuthType enum."""
+
+    def test_is_str_enum(self):
+        assert issubclass(AuthType, StrEnum)
+
+    def test_has_two_members(self):
+        assert len(AuthType) == 2
+
+    def test_api_key_value(self):
+        assert str(AuthType.API_KEY) == "api_key"
+
+    def test_none_value(self):
+        assert str(AuthType.NONE) == "none"
+
+
+class TestRateLimit:
+    """Verify RateLimit frozen dataclass."""
+
+    def test_is_frozen(self):
+        rl = RateLimit(requests_per_minute=30)
+        with pytest.raises(dataclasses.FrozenInstanceError):
+            rl.requests_per_minute = 60  # type: ignore[misc]
+
+    def test_daily_limit_defaults_to_none(self):
+        rl = RateLimit(requests_per_minute=30)
+        assert rl.daily_limit is None
+
+    def test_daily_limit_can_be_set(self):
+        rl = RateLimit(requests_per_minute=30, daily_limit=500)
+        assert rl.daily_limit == 500
+
+
+class TestProviderErrors:
+    """Verify provider error hierarchy."""
+
+    def test_provider_auth_error_is_subclass_of_provider_error(self):
+        assert issubclass(ProviderAuthError, ProviderError)
+
+    def test_provider_auth_error_is_subclass_of_exception(self):
+        assert issubclass(ProviderAuthError, Exception)
+
+    def test_provider_error_can_be_raised(self):
+        with pytest.raises(ProviderError):
+            raise ProviderError("test")
+
+    def test_provider_auth_error_caught_by_provider_error(self):
+        with pytest.raises(ProviderError):
+            raise ProviderAuthError("auth failed")

```

### Rules Injected

None

### Raw Response

I'll start by examining the project structure and checking for CLAUDE.md to understand the project conventions, then review the changed files.
<minimax:tool_call>
[command:find ./project-documents/user -name "CLAUDE.md" 2>/dev/null | head -5]
[command:ls -la ./project-documents/user/ 2>/dev/null]
[/command]
