---
docType: slice-plan
parent: user/architecture/900-arch.foundation-cleanup.md
project: trading
dateCreated: 20260328
dateUpdated: 20260401
status: complete
---

# Slice Plan: Foundation & Cleanup

## Parent Document
900-arch.foundation-cleanup.md — Cross-cutting project infrastructure: CLI framework, config, logging, provider registry, deprecated code removal, packaging.

## Foundation Work

1. [x] **(900) CLI Scaffold and Config System** — Typer root app with sub-app structure, `mt` console script entry point, pydantic-settings `Settings` class for env vars, TOML `ConfigManager` with typed `CONFIG_KEYS` and three-level precedence, `mt config set/get/list/path` commands. Establishes the skeleton that all subsequent slices and initiatives hang their commands on. Effort: 3/5

## Feature Slices

2. [x] **(901) Logging and Output Formatting** — Structured logging setup (`setup_logging`, JSON/text formatters, `get_logger` pattern). Shared Rich output formatter with `--json` support for all commands. Migrate existing `print()` and loguru calls to new logging. Dependencies: [900]. Risk: Low. Effort: 2/5

3. [x] **(902) Provider Registry and Status** — `ProviderType` enum, `ProviderProfile` frozen dataclass, built-in profiles for AlphaVantage/DataBento/FlatFile, alias resolution, auth strategy pattern for credential validation. `mt provider list/status/test` commands. `mt status` top-level command showing provider health, DB connectivity, and data freshness summary. Dependencies: [900, 901]. Risk: Low. Effort: 3/5

## Migration / Refactoring Slices

4. [x] **(903) Deprecated Code Removal and httpx Migration** — Delete `market/deprecated/` directory and all imports from it. Remove old CLI entry points (`ohlc.py` direct invocation, `newsoptions.py`). Replace aiohttp with httpx in AlphaVantage client. Wire existing daily pipeline (`marketdb.py`, `marketservice.py`) into new CLI as `mt data daily` subcommands. Dependencies: [900, 902]. Risk: Med. Effort: 3/5

## Integration Work

5. [x] **(904) Packaging and Version** — Completed incrementally across slices 900–903: `uv.lock` committed, `mt` entry point wired, `mt --version` implemented, `pip install -e .` and `uv sync` verified. No standalone slice design needed. Dependencies: [903]. Effort: 0/5

## Notes

- Slice 900 is the largest because it establishes both CLI and config systems together — they're co-dependent (CLI needs config for setup, config needs CLI for management commands). Splitting them would create circular dependencies.
- Slice 903 carries medium risk because the deprecated code removal touches the main entry point and the httpx migration changes the HTTP layer. Both are well-scoped but affect working functionality.
- The daily pipeline integration (903) is intentionally minimal — wire existing working code into new CLI commands, don't redesign it. That's Initiative 100's job.
- Squadron (~/source/repos/manta/squadron/) is the reference implementation for CLI, config, logging, and provider patterns. Structural replication, not domain logic.
